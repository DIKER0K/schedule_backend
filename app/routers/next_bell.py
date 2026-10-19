from fastapi import APIRouter, HTTPException, Query

from app.services.next_bell_service import get_next_bell
from app.models.next_bell import NextBellResponse

router = APIRouter()


@router.get("/next-bell", response_model=NextBellResponse, summary="Следующий звонок")
async def next_bell(
    shift: int = Query(1, ge=1, le=2, description="Смена: 1 или 2"),
    at: str = Query(
        None,
        description="Отладка: 'HH:MM' или ISO-время ('2026-10-16T10:03') вместо текущего момента",
    ),
):
    """Когда следующий звонок и сколько до него осталось.

    Считается по расписанию звонков школы (не по расписанию группы), поэтому
    ответ одинаковый для всех. Смена влияет только на сетку звонков.
    """
    try:
        return await get_next_bell(shift=shift, at=at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))