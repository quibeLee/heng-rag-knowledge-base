"""腾讯云 COS 客户端封装。

cos-python-sdk-v5 是同步 SDK；本项目要求 IO 一律 async，所以用 asyncio.to_thread
把阻塞调用包成协程。后续章节再扩展 upload/download/get_url 等方法。
"""

import asyncio

from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosClientError, CosServiceError

from app.core.config import settings
from app.core.exceptions import ConfigurationError
from app.core.logging import get_logger

logger = get_logger(__name__)


class CosClient:
    """腾讯云 COS 客户端单例封装。"""

    def __init__(self) -> None:
        if not settings.cos_configured:
            raise ConfigurationError("腾讯云 COS 未配置，请在 .env 中填写 COS_* 变量")

        config = CosConfig(
            Region=settings.cos_region,
            SecretId=settings.cos_secret_id,
            SecretKey=settings.cos_secret_key,
        )
        self._client = CosS3Client(config)
        self._bucket = settings.cos_bucket

    async def ping(self) -> bool:
        """通过 head_bucket 验证凭据与桶可达性。"""
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
            return True
        except (CosClientError, CosServiceError) as exc:
            logger.warning("COS ping failed: %s", exc)
            return False

_cos_client: CosClient | None = None


def get_cos_client() -> CosClient:
    """惰性构造：未配置时直接抛 ConfigurationError，由全局错误处理器转 503。"""
    global _cos_client
    if _cos_client is None:
        _cos_client = CosClient()
    return _cos_client
