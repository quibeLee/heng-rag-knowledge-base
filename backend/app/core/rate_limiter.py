"""滑动窗口限流。
算法：Redis Sorted Set + pipeline 原子化
- 每个 key 一份用户的请求时间戳集合（score=now，member=uuid 保证不重复）
- 写之前先 zremrangebyscore 清掉过期时间戳
- zcard 拿到当前窗口内请求数
- zadd 把本次请求时间戳加入集合
- expire 给 key 续 window 秒 TTL，避免冷用户的 key 永驻 Redis
不引入 slowapi / fastapi-limiter 等装饰器库：滑动窗口算法是教程要展示的核心知识，
基于 redis-py 自己写 30 行透明可读，比黑盒装饰器更适合教学。
"""
import time
from functools import lru_cache
from uuid import uuid4
from app.core.config import settings
from app.core.exceptions import RateLimitedError
from app.core.logging import get_logger
from app.core.redis import get_redis
logger = get_logger(__name__)
# 限流窗口固定 60 秒，配合 RATE_LIMIT_PER_MINUTE 形成「每分钟 N 次」语义
_WINDOW_SECONDS = 60
class RateLimiter:
    def __init__(self) -> None:
        self._redis = get_redis()
    async def check(self, identity: str) -> None:
        """命中阈值时抛 RateLimitedError；未命中静默返回。"""
        now = time.time()
        key = f"rag:rate_limit:{identity}"
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.zremrangebyscore(key, 0, now - _WINDOW_SECONDS)
            await pipe.zcard(key)
            await pipe.zadd(key, {str(uuid4()): now})
            await pipe.expire(key, _WINDOW_SECONDS)
            _, count, *_ = await pipe.execute()
        limit = settings.rate_limit_per_minute
        # zcard 取的是 zadd 之前的计数，所以 count >= limit 即代表本次会越界
        if int(count) >= limit:
            logger.warning(
                "rate limit exceeded: identity=%s count=%s limit=%s",
                identity,
                count,
                limit,
            )
            raise RateLimitedError(
                f"请求过于频繁，每分钟最多 {limit} 次，请稍后再试"
            )
@lru_cache(maxsize=1)
def get_rate_limiter() -> RateLimiter:
    return RateLimiter()