"""Celery 应用实例。
启动 worker：`uv run celery -A app.celery_app worker -l info`
任务定义见 `app.ingestion.tasks`，通过 `include` 让 worker 启动时自动发现。
"""
from celery import Celery
from app.core.config import settings
celery_app = Celery(
    "rag_knowledge_base",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.ingestion.tasks"],
)
# 文档入库是「长任务、最终一致」语义：拿到任务先 ack，业务侧用 ingestion_tasks
# 表自己跟踪状态，不靠 broker 重传保证不丢
celery_app.conf.update(
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)