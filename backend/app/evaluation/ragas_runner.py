import asyncio
import warnings
from dataclasses import dataclass
# ragas 0.4 把 metrics import 路径迁到 collections，但小写实例 API 在 v1.0 前都可用；
# 这里集中静默 DeprecationWarning，避免污染日志，等 ragas 1.0 发布后再升级
warnings.filterwarnings("ignore", category=DeprecationWarning, module="ragas")
from ragas import evaluate  # noqa: E402
from ragas.dataset_schema import EvaluationDataset, SingleTurnSample  # noqa: E402
from ragas.metrics import (  # noqa: E402
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)
from app.core.logging import get_logger  # noqa: E402
from app.ingestion.embedder import get_embeddings  # noqa: E402
from app.llm.models import get_chat_model  # noqa: E402
logger = get_logger(__name__)
@dataclass(frozen=True)
class RagasMetrics:
    """单条 case 的 RAGAS 4 指标结果，全部 [0, 1] ∪ {None}。
    None 表示 RAGAS 本次未能算出该指标（异常 / 缺关键输入），下游按缺失处理。
    """
    faithfulness: float | None
    answer_relevancy: float | None
    context_precision: float | None
    context_recall: float | None
@dataclass(frozen=True)
class RagasSample:
    """喂给 evaluate 的单条样本。"""
    question: str
    answer: str
    retrieved_contexts: list[str]
    reference_answer: str
_METRICS = [faithfulness, answer_relevancy, context_precision, context_recall]

async def evaluate_batch(samples: list[RagasSample]) -> list[RagasMetrics]:
    """对一批样本算 RAGAS 指标，返回长度与 samples 一致的指标列表。
    任一条样本因为字段缺失（空 retrieved_contexts / 空 reference）会被 RAGAS
    某些指标判 NaN，这里统一转 None；整批异常时全部样本返回 None 占位。
    """
    if not samples:
        return []
    dataset = EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=s.question,
                response=s.answer or " ",
                retrieved_contexts=s.retrieved_contexts or [" "],
                reference=s.reference_answer or " ",
            )
            for s in samples
        ]
    )
    try:
        result = await asyncio.to_thread(
            evaluate,
            dataset=dataset,
            metrics=_METRICS,
            llm=get_chat_model(),
            embeddings=get_embeddings(),
            raise_exceptions=False,
            show_progress=False,
        )
    except Exception:
        logger.exception("RAGAS evaluate 整批失败，返回 None 占位")
        return [_empty_metrics() for _ in samples]
    return _extract_metrics(result, expected=len(samples))
def _extract_metrics(result, expected: int) -> list[RagasMetrics]:
    rows = getattr(result, "scores", None) or []
    if len(rows) != expected:
        logger.warning(
            "RAGAS 返回行数 %d 与样本数 %d 不一致，按可用值对齐", len(rows), expected
        )
    metrics: list[RagasMetrics] = []
    for i in range(expected):
        row = rows[i] if i < len(rows) else {}
        metrics.append(
            RagasMetrics(
                faithfulness=_pick(row, "faithfulness"),
                answer_relevancy=_pick(row, "answer_relevancy"),
                context_precision=_pick(row, "context_precision"),
                context_recall=_pick(row, "context_recall"),
            )
        )
    return metrics
def _pick(row: dict, key: str) -> float | None:
    """从 RAGAS 结果行里取分数；NaN / 缺失 / 非数值都视为 None。"""
    value = row.get(key)
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    if as_float != as_float:  # NaN
        return None
    return as_float
def _empty_metrics() -> RagasMetrics:
    return RagasMetrics(
        faithfulness=None, answer_relevancy=None,
        context_precision=None, context_recall=None,
    )