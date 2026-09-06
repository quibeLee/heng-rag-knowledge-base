from collections.abc import Sequence
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import AnswerCitation
class AnswerCitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
    async def bulk_add(self, citations: Sequence[AnswerCitation]) -> None:
        if not citations:
            return
        self.session.add_all(citations)
        await self.session.flush()