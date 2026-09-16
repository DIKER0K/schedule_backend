from fastapi import APIRouter
from app.services.next_bell_service import get_next_bell
from app.models.next_bell import NextBellResponse

router = APIRouter()


@router.get("/next-bell", response_model=NextBellResponse, summary="Следующий звонок")
async def next_bell():
    return await get_next_bell()
