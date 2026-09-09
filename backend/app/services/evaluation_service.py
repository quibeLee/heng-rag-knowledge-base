from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import EvaluationItem, EvaluationRun, EvaluationRunStatus
from app.db.repositories.evaluation_repo import (
    EvaluationItemRepository,
    EvaluationRunRepository,
)
from app.db.session import AsyncSessionLocal
from app.evaluation import (
    classify_bad_case,
    compute_citation_hit,
    compute_refusal_correct,
    load_dataset,
)
from app.evaluation.dataset import list_datasets
from app.evaluation.ragas_runner import RagasMetrics, RagasSample, evaluate_batch
from app.services.chat_service import ChatService, EvaluationAnswer

logger = get_logger(__name__)


class EvaluationService:
    """评测业务动作（非异步执行部分）：CRUD + 列表筛选 + Bad Case PATCH。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.run_repo = EvaluationRunRepository(session)
        self.item_repo = EvaluationItemRepository(session)

    async def create_run(self, *, name: str, dataset_name: str) -> EvaluationRun:
        # 先尝试加载评测集校验存在性 + 拿到 size，避免空 run 占位
        cases = load_dataset(dataset_name)
        if not cases:
            raise ValidationError(f"评测集 {dataset_name} 为空")
        run = EvaluationRun(
            name=name,
            dataset_name=dataset_name,
            dataset_size=len(cases),
            status=EvaluationRunStatus.RUNNING,
            progress_total=len(cases),
        )
        await self.run_repo.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def get_run(self, run_id: UUID) -> EvaluationRun:
        run = await self.run_repo.get(run_id)
        if run is None:
            raise NotFoundError("评测 run 不存在")
        return run

    async def list_runs(self, page: int, page_size: int):
        return await self.run_repo.list_page(page=page, page_size=page_size)

    async def delete_run(self, run_id: UUID) -> None:
        deleted = await self.run_repo.delete(run_id)
        if not deleted:
            raise NotFoundError("评测 run 不存在")
        await self.session.commit()

    async def list_items(
            self,
            run_id: UUID,
            page: int,
            page_size: int,
            *,
            bad_case_only: bool,
            category: str | None,
    ):
        # 先确认 run 存在，避免空 list 和"run 不存在"混在一起
        await self.get_run(run_id)
        return await self.item_repo.list_page(
            run_id, page=page, page_size=page_size,
            bad_case_only=bad_case_only, category=category,
        )

    async def get_item(self, item_id: UUID) -> EvaluationItem:
        item = await self.item_repo.get(item_id)
        if item is None:
            raise NotFoundError("评测 case 不存在")
        return item

    async def update_item_bad_case(
            self,
            item_id: UUID,
            *,
            bad_case_category: str | None,
            bad_case_note: str | None,
            is_bad_case: bool | None,
    ) -> EvaluationItem:
        """前端覆盖 Bad Case 归因：
        - 显式传 is_bad_case=False 时把误判 case 标回非 Bad Case 并清空归因
        - bad_case_category 非空时自动把 is_bad_case 置 True（即使前端没传）
        """
        item = await self.get_item(item_id)
        if is_bad_case is False:
            item.is_bad_case = False
            item.bad_case_category = None
        else:
            if bad_case_category is not None:
                item.bad_case_category = bad_case_category
                item.is_bad_case = True
            elif is_bad_case is True:
                item.is_bad_case = True
        if bad_case_note is not None:
            item.bad_case_note = bad_case_note
        await self.session.commit()
        await self.session.refresh(item)
        return item

    def list_datasets(self) -> list[tuple[str, int]]:
        return list_datasets()


async def execute_evaluation_run(run_id: UUID) -> None:
    """评测 run 异步执行器：BackgroundTasks 调用。
    流程：
    1. 加载评测集 cases
    2. 逐条调 ChatService.answer_for_evaluation，每条结束就 INSERT 一行 item
       （前端可立即轮询到进度变化）
    3. 全部跑完后批量调 RAGAS evaluate_batch 算 4 指标，回填到 items
    4. 算 Bad Case 归因 + 聚合指标，更新 run.status=completed
    任意阶段失败：run.status=failed + error_message，已写入的 items 保留。
    """
    logger.info("evaluation run start: run_id=%s", run_id)
    try:
        async with AsyncSessionLocal() as session:
            run_repo = EvaluationRunRepository(session)
            run = await run_repo.get(run_id)
            if run is None:
                logger.warning("evaluation run not found, skip: %s", run_id)
                return
            run.started_at = datetime.now(timezone.utc)
            await session.commit()
            dataset_name = run.dataset_name
        cases = load_dataset(dataset_name)
        # 阶段 1：逐条跑 RAG，每条独立事务写一行 item（前端能轮询到进度）
        for case in cases:
            await _run_single_case(run_id, case)
        # 阶段 2：批量算 RAGAS + Bad Case 归因 + 聚合
        await _finalize_run(run_id)
        async with AsyncSessionLocal() as session:
            run = await EvaluationRunRepository(session).get(run_id)
            if run is not None:
                run.status = EvaluationRunStatus.COMPLETED
                run.finished_at = datetime.now(timezone.utc)
                await session.commit()
        logger.info("evaluation run done: run_id=%s", run_id)
    except Exception as exc:
        logger.exception("evaluation run failed: run_id=%s", run_id)
        async with AsyncSessionLocal() as session:
            run = await EvaluationRunRepository(session).get(run_id)
            if run is not None:
                run.status = EvaluationRunStatus.FAILED
                run.finished_at = datetime.now(timezone.utc)
                run.error_message = (str(exc).strip() or exc.__class__.__name__)[:500]
                await session.commit()

async def _run_single_case(run_id: UUID, case) -> None:
    """跑一条 case：独立 session + 独立事务写一行 item。"""
    async with AsyncSessionLocal() as session:
        chat_service = ChatService(session)
        answer: EvaluationAnswer = await chat_service.answer_for_evaluation(case.question)
        citation_hit = (
            None
            if case.should_refuse
            else compute_citation_hit(
                actual_citations=answer.citations,
                expected_document_names=case.expected_document_names,
                expected_keywords=case.expected_keywords,
            )
        )
        refusal_correct = compute_refusal_correct(answer.refused, case.should_refuse)
        item = EvaluationItem(
            run_id=run_id,
            case_id=case.case_id,
            question=case.question,
            expected_answer=case.expected_answer,
            expected_document_names=case.expected_document_names,
            expected_keywords=case.expected_keywords,
            should_refuse=case.should_refuse,
            tags=case.tags,
            actual_answer=answer.answer,
            actual_refused=answer.refused,
            citations=answer.citations,
            retrieved_chunks_meta=[_chunk_meta(c) for c in answer.chunks],
            query_route=answer.query_route or None,
            agent_steps=answer.agent_steps or None,
            verify_result=_verify_payload(answer.verify_result),
            trace_id=answer.trace_id,
            latency_ms=answer.latency_ms,
            first_token_latency_ms=answer.first_token_latency_ms,
            error_message=answer.error_message,
            citation_hit=citation_hit,
            refusal_correct=refusal_correct,
        )
        await EvaluationItemRepository(session).add(item)
        # 同事务更新 run 进度
        run = await EvaluationRunRepository(session).get(run_id)
        if run is not None:
            run.progress_completed += 1
            if answer.error_message:
                run.progress_failed += 1
        await session.commit()

async def _finalize_run(run_id: UUID) -> None:
    """跑完所有 case 后：一次性算 RAGAS → Bad Case 归因 → 聚合指标。"""
    async with AsyncSessionLocal() as session:
        item_repo = EvaluationItemRepository(session)
        items = await item_repo.list_by_run(run_id)
        if not items:
            return
        # 拒答 case 不喂给 RAGAS（没有 retrieved_contexts，4 指标也无意义）
        # 但仍占位以保持 items / metrics 顺序对齐
        samples_with_index: list[tuple[int, RagasSample]] = []
        for idx, item in enumerate(items):
            if item.should_refuse or item.error_message or not item.retrieved_chunks_meta:
                continue
            samples_with_index.append(
                (
                    idx,
                    RagasSample(
                        question=item.question,
                        answer=item.actual_answer,
                        retrieved_contexts=[
                            str(c.get("content", "")) for c in item.retrieved_chunks_meta
                        ],
                        reference_answer=item.expected_answer,
                    ),
                )
            )
        metrics_list: list[RagasMetrics | None] = [None] * len(items)
        if samples_with_index:
            indexed_metrics = await evaluate_batch([s for _, s in samples_with_index])
            for (idx, _), m in zip(samples_with_index, indexed_metrics, strict=True):
                metrics_list[idx] = m
        for item, metrics in zip(items, metrics_list, strict=True):
            if metrics is not None:
                item.faithfulness = metrics.faithfulness
                item.answer_relevancy = metrics.answer_relevancy
                item.context_precision = metrics.context_precision
                item.context_recall = metrics.context_recall
            rule = classify_bad_case(
                should_refuse=item.should_refuse,
                actual_refused=item.actual_refused,
                refusal_correct=item.refusal_correct,
                citation_hit=item.citation_hit,
                faithfulness=item.faithfulness,
                answer_relevancy=item.answer_relevancy,
                context_precision=item.context_precision,
                context_recall=item.context_recall,
                has_error=bool(item.error_message),
            )
            item.is_bad_case = rule.is_bad_case
            item.bad_case_category = rule.category
        # 聚合指标：None 不参与平均
        run = await EvaluationRunRepository(session).get(run_id)
        if run is not None:
            run.faithfulness = _avg([i.faithfulness for i in items])
            run.answer_relevancy = _avg([i.answer_relevancy for i in items])
            run.context_precision = _avg([i.context_precision for i in items])
            run.context_recall = _avg([i.context_recall for i in items])
            # 引用命中率分母排除应拒答 case
            non_refusal_hits = [i.citation_hit for i in items if i.citation_hit is not None]
            run.citation_hit_rate = (
                sum(1 for h in non_refusal_hits if h) / len(non_refusal_hits)
                if non_refusal_hits
                else None
            )
            run.refusal_accuracy = sum(1 for i in items if i.refusal_correct) / len(items)
            run.avg_latency_ms = sum(i.latency_ms for i in items) / len(items)
            first_token_values = [
                i.first_token_latency_ms for i in items if i.first_token_latency_ms is not None
            ]
            run.avg_first_token_latency_ms = (
                sum(first_token_values) / len(first_token_values)
                if first_token_values
                else None
            )
        await session.commit()

def _chunk_meta(chunk) -> dict:
    """把 RetrievedChunk 序列化成评测 items.retrieved_chunks_meta 用的轻量 dict。
    保留 content 是给 RAGAS 当 retrieved_contexts 用的；其余字段便于详情页定位。
    """
    return {
        "chunk_id": str(chunk.chunk_id),
        "document_id": str(chunk.document_id),
        "document_name": chunk.document_name,
        "page_no": chunk.page_no,
        "section_path": chunk.section_path,
        "content": chunk.content,
        "vector_score": chunk.vector_score,
        "rerank_score": chunk.rerank_score,
        "rrf_score": chunk.rrf_score,
    }
def _verify_payload(result) -> dict | None:
    if result is None:
        return None
    return {"verified": result.verified, "reason": result.reason or None}
def _avg(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)