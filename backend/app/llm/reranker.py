import dataclasses
from typing import Any
import httpx
from langsmith import traceable

from app.core.config import settings
from app.core.exceptions import ConfigurationError
from app.core.logging import get_logger
from app.retrieval.vector_retriever import RetrievedChunk

logger = get_logger(__name__)


class Reranker:
    """DashScope qwen3-rerank 客户端。
    单例持有 httpx.AsyncClient 复用连接池；rerank 是单次同步问答的一环，
    要求低延迟，所以超时设置得比 chat 短一些（默认 8s）。
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=settings.rerank_timeout)
        return self._client

    @traceable(name="Reranker.rerank", run_type="tool")
    async def rerank(
            self, query: str, candidates: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        if len(candidates) <= 1:
            return candidates
        api_key = settings.effective_rerank_api_key
        if not api_key:
            raise ConfigurationError(
                "Rerank API key 未配置，请在 .env 设置 RERANK_API_KEY 或 CHAT_API_KEY"
            )
        try:
            scores = await self._fetch_scores(query, candidates, api_key)
        except Exception:
            logger.exception("rerank 调用失败，降级为不重排：query=%r", query)
            return candidates
        ranked = [
            dataclasses.replace(chunk, rerank_score=score)
            for chunk, score in zip(candidates, scores, strict=False)
        ]
        ranked.sort(key=lambda c: c.rerank_score or 0.0, reverse=True)
        return ranked

    async def _fetch_scores(
            self, query: str, candidates: list[RetrievedChunk], api_key: str
    ) -> list[float]:
        """调 DashScope rerank 端点，返回与 candidates 同序的 relevance_score 列表。
        响应里 results 按相关度降序排好，并通过 index 指回原始 documents 数组，
        所以这里把 results 用 index 重排回输入顺序，再让上层按 rerank_score 排序，
        这样数据流更直观：candidates[i] ↔ scores[i]。
        """
        payload: dict[str, Any] = {
            "model": settings.rerank_model,
            "query": query,
            "documents": [c.content for c in candidates],
            "top_n": len(candidates),
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        client = self._get_client()
        response = await client.post(
            settings.rerank_base_url, json=payload, headers=headers
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results") or []
        if not isinstance(results, list) or len(results) == 0:
            raise ValueError(f"rerank 响应缺少 results：{data!r}")
        scores: list[float] = [0.0] * len(candidates)
        for item in results:
            idx = item.get("index")
            score = item.get("relevance_score")
            if not isinstance(idx, int) or not isinstance(score, int | float):
                continue
            if 0 <= idx < len(scores):
                scores[idx] = float(score)
        return scores


_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker
