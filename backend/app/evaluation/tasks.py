"""评测 Celery 任务定义。

worker 是同步进程，执行流程是 async，任务函数内部用 `asyncio.run`
起独立 event loop 跑评测（与 `app.ingestion.tasks` 同一套模式）。
"""
from uuid import UUID
from app.celery_app import celery_app
from app.evaluation.runner import run_evaluation_sync


@celery_app.task(name="execute_evaluation_run", bind=True)
def execute_evaluation_run_task(self, run_id: str) -> None:
    """评测 run 异步执行任务。"""
    run_evaluation_sync(UUID(run_id))
