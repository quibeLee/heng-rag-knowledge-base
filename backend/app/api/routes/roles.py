from uuid import UUID
from fastapi import APIRouter, Response
from app.api.deps import CurrentAdmin, DbSession
from app.api.schemas.auth import RoleRead
from app.api.schemas.roles import RoleCreate, RoleUpdate
from app.services.role_service import RoleService

router = APIRouter(prefix="/roles", tags=["roles"])


@router.get("", response_model=list[RoleRead], operation_id="listRoles")
async def list_roles(_: CurrentAdmin, session: DbSession) -> list[RoleRead]:
    service = RoleService(session)
    roles = await service.list_roles()
    return [RoleRead.model_validate(r) for r in roles]


@router.post("", response_model=RoleRead, status_code=201, operation_id="createRole")
async def create_role(
        _: CurrentAdmin, session: DbSession, payload: RoleCreate,
) -> RoleRead:
    service = RoleService(session)
    role = await service.create_role(
        name=payload.name,
        description=payload.description,
        permission_tags=payload.permission_tags,
    )
    return RoleRead.model_validate(role)


@router.patch("/{role_id}", response_model=RoleRead, operation_id="updateRole")
async def update_role(
        _: CurrentAdmin, session: DbSession,
        role_id: UUID, payload: RoleUpdate,
) -> RoleRead:
    service = RoleService(session)
    role = await service.update_role(
        role_id, description=payload.description,
        permission_tags=payload.permission_tags,
    )
    return RoleRead.model_validate(role)


@router.delete("/{role_id}", status_code=204, operation_id="deleteRole")
async def delete_role(
        _: CurrentAdmin, session: DbSession, role_id: UUID,
) -> Response:
    service = RoleService(session)
    await service.delete_role(role_id)
    return Response(status_code=204)
