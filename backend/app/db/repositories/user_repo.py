from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.db.models import Role, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        # roles 走 lazy="selectin" 自动预加载，这里 session.get 直接拿即可
        return await self.session.get(User, user_id)

    async def get_by_username(self, username: str) -> User | None:
        stmt = (
            select(User)
            .where(User.username == username)
            .options(selectinload(User.roles))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_all(self) -> int:
        """启动期种子初始化用：库内无用户时才建 admin。"""
        return int(
            (await self.session.execute(select(func.count(User.id)))).scalar_one()
        )

    async def list_paginated(self, page: int, page_size: int) -> tuple[list[User], int]:
        page = max(page, 1)
        page_size = max(min(page_size, 100), 1)
        offset = (page - 1) * page_size
        items_stmt = (
            select(User)
            .order_by(User.created_at.asc(), User.id.asc())
            .offset(offset)
            .limit(page_size)
            .options(selectinload(User.roles))
        )
        count_stmt = select(func.count(User.id))
        items = (await self.session.execute(items_stmt)).scalars().all()
        total = (await self.session.execute(count_stmt)).scalar_one()
        return list(items), int(total)

    async def add(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def delete(self, user: User) -> None:
        await self.session.delete(user)
        await self.session.flush()

    async def set_roles(self, user: User, roles: list[Role]) -> None:
        """整体替换用户角色集合。"""
        user.roles = roles
        await self.session.flush()
