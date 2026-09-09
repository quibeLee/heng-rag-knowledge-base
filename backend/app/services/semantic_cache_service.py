import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from redisvl.extensions.cache.llm import SemanticCache
from redisvl.query.filter import Tag
from redisvl.utils.vectorize import CustomVectorizer
from app.core.config import settings
from app.core.logging import get_logger

_CACHE_NAME = "rag_semantic_cache"
_SCOPE_FIELD = "permission_scope"


def _scope_key(permission_scope: list[str]) -> str:
    """把权限集合序列化成「集合相等」可比较的 Tag 值。
    RediSearch Tag 字段天然是「any-of」语义；要表达「集合完全相等」最稳的做法
    是对排序后的权限串做 hash，作为单一 Tag 值存入。这样不同顺序、不同子集
    都会落到不同的 Tag，命中查询天然要求集合完全一致。
    """
    canonical = "\x00".join(sorted(permission_scope))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stub_embed(text: str, **_: object) -> list[float]:
    """占位 vectorizer：仅用于让 RedisVL 在建索引时知道向量维度。
    主链路始终预先调用项目自己的 embedder 算好 query_embedding，
    再通过 `vector=` 参数直接传给 acheck/astore，永远不会触发到这里。
    """
    return [0.0] * settings.embedding_dim


logger = get_logger(__name__)


@dataclass(frozen=True)
class CachedAnswer:
    answer: str
    citations: list[dict]
    cached_question: str


class SemanticCacheService:
    def __init__(self) -> None:
        self._cache = SemanticCache(
            name=_CACHE_NAME,
            redis_url=settings.redis_url,
            # RediSearch 用「余弦距离 = 1 - 余弦相似度」做 KNN 排序；阈值同步转换
            distance_threshold=1.0 - settings.semantic_cache_min_similarity,
            ttl=settings.semantic_cache_ttl_seconds,
            vectorizer=CustomVectorizer(_stub_embed),  # pyright: ignore[reportCallIssue]
            filterable_fields=[{"name": _SCOPE_FIELD, "type": "tag"}],
            overwrite=False,
        )

    async def lookup(
            self,
            query_embedding: list[float],
            permission_scope: list[str],
    ) -> CachedAnswer | None:
        """Redis 内 KNN 查询：相似度阈值 + 权限范围 Tag 同时满足才返回。"""
        scope = _scope_key(permission_scope)
        try:
            hits = await self._cache.acheck(
                vector=query_embedding,
                filter_expression=Tag(_SCOPE_FIELD) == scope,
                num_results=1,
                return_fields=["prompt", "response", "metadata"],
            )
        except Exception:
            logger.exception("semantic cache lookup failed, treat as miss")
            return None
        if not hits:
            return None
        hit = hits[0]
        metadata = hit.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        return CachedAnswer(
            answer=hit.get("response", ""),
            citations=metadata.get("citations", []),
            cached_question=hit.get("prompt", ""),
        )

    async def save(
            self,
            *,
            question: str,
            query_embedding: list[float],
            answer: str,
            citations: list[dict],
            permission_scope: list[str],
    ) -> None:
        try:
            await self._cache.astore(
                prompt=question,
                response=answer,
                vector=query_embedding,
                metadata={"citations": citations},
                filters={_SCOPE_FIELD: _scope_key(permission_scope)},
            )
        except Exception:
            # 写缓存失败不影响主链路：用户已经拿到答案，缓存只是优化
            logger.exception("semantic cache save failed, skip")


@lru_cache(maxsize=1)
def get_semantic_cache() -> SemanticCacheService:
    return SemanticCacheService()
