from dataclasses import dataclass
from typing import Literal

# Bad Case 归因类别。与 PRD「Bad Case 归因」12 类对齐。
BadCaseCategory = Literal[
    "document_parse_failed",
    "chunk_split_bad",
    "embedding_recall_miss",
    "keyword_recall_miss",
    "rrf_fusion_error",
    "rerank_order_error",
    "context_judge_too_loose",
    "context_judge_too_strict",
    "prompt_constraint_weak",
    "generation_off_context",
    "citation_parse_failed",
    "permission_filter_error",
    "other",
]
# 指标低于此值视为"显著低"，触发对应归因。
# 按指标类型分别调阈值
_LOW_SCORE_THRESHOLD = 0.5


@dataclass(frozen=True)
class BadCaseRule:
    """一条规则归因结论。"""
    is_bad_case: bool
    category: BadCaseCategory | None

def compute_citation_hit(
    actual_citations: list[dict],
    expected_document_names: list[str],
    expected_keywords: list[str],
) -> bool:
    """引用命中率判定：
    1. 任一 citation 的 document_name 出现在 expected_document_names → 命中
    2. 任一 expected_keyword 出现在 citations 的 quote 拼接里 → 命中
    3. 否则未命中
    """
    if not actual_citations:
        return False
    actual_doc_names = {c.get("document_name", "") for c in actual_citations}
    if any(name in actual_doc_names for name in expected_document_names if name):
        return True
    if expected_keywords:
        # 拼一次而不是 N 次 in 调用，O(L*K) → O(L+K) 量级
        quote_blob = "\n".join(str(c.get("quote", "")) for c in actual_citations)
        if any(kw in quote_blob for kw in expected_keywords if kw):
            return True
    return False
def compute_refusal_correct(actual_refused: bool, should_refuse: bool) -> bool:
    """拒答正确率：实际拒答状态 == 期望。"""
    return actual_refused == should_refuse

def classify_bad_case(
    *,
    should_refuse: bool,
    actual_refused: bool,
    refusal_correct: bool,
    citation_hit: bool | None,
    faithfulness: float | None,
    answer_relevancy: float | None,
    context_precision: float | None,
    context_recall: float | None,
    has_error: bool,
) -> BadCaseRule:
    if has_error:
        return BadCaseRule(is_bad_case=True, category="other")
    # 拒答错误的两种方向：该拒不拒 / 不该拒却拒了
    if not refusal_correct:
        if should_refuse and not actual_refused:
            return BadCaseRule(is_bad_case=True, category="context_judge_too_loose")
        return BadCaseRule(is_bad_case=True, category="context_judge_too_strict")
    # 非拒答 case 才考察引用命中：拒答 case 的 citation_hit 本就 None
    if citation_hit is False:
        return BadCaseRule(is_bad_case=True, category="embedding_recall_miss")
    # RAGAS 指标顺序：先看召回（context_recall）→ 排序（context_precision）→
    # 生成忠实度（faithfulness）→ 答案相关性（answer_relevancy）
    if _is_low(context_recall):
        return BadCaseRule(is_bad_case=True, category="embedding_recall_miss")
    if _is_low(context_precision):
        return BadCaseRule(is_bad_case=True, category="rerank_order_error")
    if _is_low(faithfulness):
        return BadCaseRule(is_bad_case=True, category="generation_off_context")
    if _is_low(answer_relevancy):
        return BadCaseRule(is_bad_case=True, category="prompt_constraint_weak")
    return BadCaseRule(is_bad_case=False, category=None)
def _is_low(score: float | None) -> bool:
    return score is not None and score < _LOW_SCORE_THRESHOLD
