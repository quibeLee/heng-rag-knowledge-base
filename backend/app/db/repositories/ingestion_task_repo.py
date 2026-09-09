from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import IngestionTask, IngestionTaskStatus, IngestionTaskType
class IngestionTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
    async def create(
        self,
        document_id: UUID,
        task_type: IngestionTaskType,
    ) -> IngestionTask:
        task = IngestionTask(
            document_id=document_id,
            task_type=task_type,
            status=IngestionTaskStatus.PENDING,
        )
        self.session.add(task)
        await self.session.flush()
        return task
    async def get_latest_by_document(
        self, document_id: UUID
    ) -> IngestionTask | None:
        """详情页「最近一次任务」卡片用：取 created_at 最新一条。"""
        stmt = (
            select(IngestionTask)
            .where(IngestionTask.document_id == document_id)
            .order_by(IngestionTask.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
    async def mark_running(self, task_id: UUID) -> None:
        task = await self.session.get(IngestionTask, task_id)
        if task is None:
            return
        task.status = IngestionTaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc)
    async def mark_success(self, task_id: UUID) -> None:
        task = await self.session.get(IngestionTask, task_id)
        if task is None:
            return
        task.status = IngestionTaskStatus.SUCCESS
        task.finished_at = datetime.now(timezone.utc)
        task.error_message = None
    async def mark_failed(self, task_id: UUID, error_message: str) -> None:
        task = await self.session.get(IngestionTask, task_id)
        if task is None:
            return
        task.status = IngestionTaskStatus.FAILED
        task.finished_at = datetime.now(timezone.utc)
        task.error_message = error_message[:500]
    async def set_progress_total(self, task_id: UUID, total: int) -> None:
        task = await self.session.get(IngestionTask, task_id)
        if task is None:
            return
        task.progress_total = total
    async def increment_progress(self, task_id: UUID, delta: int) -> None:
        task = await self.session.get(IngestionTask, task_id)
        if task is None:
            return
        task.progress_done += delta