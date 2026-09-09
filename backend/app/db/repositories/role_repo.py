"""roles 表访问层。"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role


class RoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, role_id: UUID) -> Role | None:
        return await self.session.get(Role, role_id)

    async def get_by_name(self, name: str) -> Role | None:
        stmt = select(Role).where(Role.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[Role]:
        """角色总量天然小（通常个位数），不分页。"""
        stmt = select(Role).order_by(Role.created_at.asc(), Role.id.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_many(self, role_ids: Sequence[UUID]) -> list[Role]:
        if not role_ids:
            return []
        stmt = select(Role).where(Role.id.in_(list(role_ids)))
        return list((await self.session.execute(stmt)).scalars().all())

    async def add(self, role: Role) -> Role:
        self.session.add(role)
        await self.session.flush()
        return role

    async def delete(self, role: Role) -> None:
        await self.session.delete(role)
        await self.session.flush()
