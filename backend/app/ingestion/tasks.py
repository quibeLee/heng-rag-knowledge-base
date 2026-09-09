"""Celery 任务定义。
worker 是同步进程，业务流程都是 async，所以任务函数内部用 `asyncio.run`
起独立 event loop 跑 pipeline。
"""
from uuid import UUID
from app.celery_app import celery_app
from app.ingestion.pipeline import run_ingest_sync, run_reindex_sync


@celery_app.task(name="ingest_document", bind=True)
def ingest_document_task(self, document_id: str, task_id: str) -> None:
    """首次入库任务。"""
    run_ingest_sync(UUID(document_id), UUID(task_id))


@celery_app.task(name="reindex_document", bind=True)
def reindex_document_task(self, document_id: str, task_id: str) -> None:
    """增量重建任务。"""
    run_reindex_sync(UUID(document_id), UUID(task_id))
