from fastapi import APIRouter, Depends, Query
from app.schemas.reasoning_schema import ReasoningRequest, ReasoningResponse
from app.services.ai_service import AIService
from app.services.schedule_service import ScheduleService

router = APIRouter()


def get_ai_service():
    return AIService()


@router.post("/", response_model=ReasoningResponse)
async def reasoning_endpoint(
    request: ReasoningRequest,
    service: AIService = Depends(get_ai_service),
):

    answer = service.ask(
        user_message=request.user_message, system_message=request.system_message
    )

    return ReasoningResponse(answer=answer)


@router.get("/schedule/{group_name}", summary="AI описание расписания группы")
async def get_ai_schedule_description(
    group_name: str,
    day: str | None = Query(None),
    ai_service: AIService = Depends(get_ai_service),
):

    schedule = await ScheduleService.get_schedule_by_group(group_name, day)

    ai_text = await ai_service.describe_schedule(schedule, day)

    return {"group": group_name, "day": day, "description": ai_text}


@router.get("/teacher/{fio}", summary="AI описание расписания преподавателя")
async def get_ai_teacher_schedule_description(
    fio: str,
    day: str | None = Query(None),
    ai_service: AIService = Depends(get_ai_service),
):

    schedule = await ScheduleService.get_teacher_schedule(fio, day)

    ai_text = await ai_service.describe_teacher_schedule(
        schedule=schedule, fio=fio, day=day
    )

    return {"teacher": fio, "day": day, "description": ai_text}
