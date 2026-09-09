from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import EvaluationItem, EvaluationRun, EvaluationRunStatus
from app.db.repositories.evaluation_repo import (
    EvaluationItemRepository,
    EvaluationRunRepository,
)
from app.evaluation import load_dataset
from app.evaluation.dataset import list_datasets


class EvaluationService:
    """评测业务动作（非异步执行部分）：CRUD + 列表筛选 + Bad Case PATCH。

    run 的异步执行由 Celery 任务负责（`app/evaluation/tasks.py` → `app/evaluation/runner.py`）。
    """

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
