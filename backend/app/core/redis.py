"""Redis 异步客户端单例。
语义缓存与滑动窗口限流共用同一个连接池。Celery broker / backend 走另外的 db
索引，由 Celery 自己持有连接，与应用客户端不共享。
"""
from functools import lru_cache
import redis.asyncio as aioredis
from app.core.config import settings
@lru_cache(maxsize=1)
def get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)