from uuid import UUID
from fastapi import APIRouter, Query, Response
from app.api.deps import DbSession
from app.api.schemas.evaluations import (
    BadCaseCategoryValue,
    DatasetInfo,
    DatasetListResponse,
    EvaluationItemPage,
    EvaluationItemRead,
    EvaluationItemUpdate,
    EvaluationRunCreate,
    EvaluationRunListItem,
    EvaluationRunPage,
    EvaluationRunRead,
)
from app.evaluation.tasks import execute_evaluation_run_task
from app.services.evaluation_service import EvaluationService
from app.api.deps import DbSession, get_current_admin
from fastapi import Depends
router = APIRouter(
    prefix="/evaluations",
    tags=["evaluations"],
    dependencies=[Depends(get_current_admin)],
)
@router.get(
    "/datasets",
    response_model=DatasetListResponse,
    operation_id="listEvaluationDatasets",
    summary="列出可用评测集（jsonl 文件名 + 条数）",
)
async def list_evaluation_datasets(session: DbSession) -> DatasetListResponse:
    service = EvaluationService(session)
    items = service.list_datasets()
    return DatasetListResponse(
        items=[DatasetInfo(name=name, size=size) for name, size in items]
    )
@router.post(
    "/runs",
    response_model=EvaluationRunRead,
    status_code=201,
    operation_id="createEvaluationRun",
    summary="创建评测 run 并派发 Celery 任务异步执行",
)
async def create_evaluation_run(
    payload: EvaluationRunCreate,
    session: DbSession,
) -> EvaluationRunRead:
    service = EvaluationService(session)
    run = await service.create_run(name=payload.name, dataset_name=payload.dataset_name)
    execute_evaluation_run_task.delay(str(run.id))
    return EvaluationRunRead.model_validate(run)

@router.get(
    "/runs",
    response_model=EvaluationRunPage,
    operation_id="listEvaluationRuns",
    summary="按创建时间倒序分页列出评测 run",
)
async def list_evaluation_runs(
    session: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> EvaluationRunPage:
    service = EvaluationService(session)
    items, total = await service.list_runs(page=page, page_size=page_size)
    return EvaluationRunPage(
        items=[EvaluationRunListItem.model_validate(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )
@router.get(
    "/runs/{run_id}",
    response_model=EvaluationRunRead,
    operation_id="getEvaluationRun",
)
async def get_evaluation_run(run_id: UUID, session: DbSession) -> EvaluationRunRead:
    service = EvaluationService(session)
    run = await service.get_run(run_id)
    return EvaluationRunRead.model_validate(run)
@router.delete(
    "/runs/{run_id}",
    status_code=204,
    operation_id="deleteEvaluationRun",
)
async def delete_evaluation_run(run_id: UUID, session: DbSession) -> Response:
    service = EvaluationService(session)
    await service.delete_run(run_id)
    return Response(status_code=204)

@router.get(
    "/runs/{run_id}/items",
    response_model=EvaluationItemPage,
    operation_id="listEvaluationItems",
    summary="分页列出 run 下的 case，支持仅看 Bad Case 与按归因筛选",
)
async def list_evaluation_items(
    run_id: UUID,
    session: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    bad_case_only: bool = Query(False),
    category: BadCaseCategoryValue | None = Query(None),
) -> EvaluationItemPage:
    service = EvaluationService(session)
    items, total = await service.list_items(
        run_id, page=page, page_size=page_size,
        bad_case_only=bad_case_only, category=category,
    )
    return EvaluationItemPage(
        items=[EvaluationItemRead.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )
@router.get(
    "/items/{item_id}",
    response_model=EvaluationItemRead,
    operation_id="getEvaluationItem",
)
async def get_evaluation_item(item_id: UUID, session: DbSession) -> EvaluationItemRead:
    service = EvaluationService(session)
    item = await service.get_item(item_id)
    return EvaluationItemRead.model_validate(item)
@router.patch(
    "/items/{item_id}",
    response_model=EvaluationItemRead,
    operation_id="updateEvaluationItem",
    summary="人工覆盖 Bad Case 归因 / 备注",
)
async def update_evaluation_item(
    item_id: UUID,
    payload: EvaluationItemUpdate,
    session: DbSession,
) -> EvaluationItemRead:
    service = EvaluationService(session)
    item = await service.update_item_bad_case(
        item_id,
        bad_case_category=payload.bad_case_category,
        bad_case_note=payload.bad_case_note,
        is_bad_case=payload.is_bad_case,
    )
    return EvaluationItemRead.model_validate(item)