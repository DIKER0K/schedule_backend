import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.routers import bell_schedule, users, schedule, ai, next_bell
from app.database import db
from fastapi.middleware.cors import CORSMiddleware
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


async def ensure_schedule_indexes():
    """Индексы коллекции расписаний.

    Расписание теперь хранится версиями: уникальность по паре
    (group_name, valid_from), а не по одной группе. Устаревший уникальный
    индекс по `group_name` снимаем — он остался от схемы «одна запись на
    группу» и не дал бы загрузить вторую версию расписания.
    """
    try:
        for name, spec in (await db.schedules.index_information()).items():
            if spec.get("key") == [("group_name", 1)] and spec.get("unique"):
                await db.schedules.drop_index(name)
                logger.info("Снят устаревший уникальный индекс schedules.%s", name)
    except Exception as exc:  # индексов ещё нет (новая база) — это нормально
        logger.info("Старые индексы schedules не проверены: %s", exc)

    await db.schedules.create_index(
        [("group_name", 1), ("valid_from", 1)],
        unique=True,
        name="group_name_valid_from",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting up...")

    # создаём индексы Mongo
    await ensure_schedule_indexes()
    await db.users.create_index("user_id", unique=True)

    yield

    logger.info("🛑 Shutting down...")
    db.client.close()


app = FastAPI(title="College Schedule Bot API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(schedule.router, prefix="/schedule", tags=["Schedule"])
app.include_router(
    bell_schedule.router, prefix="/bell_schedule", tags=["Bell schedule"]
)
app.include_router(ai.router, prefix="/ai", tags=["ai"])
app.include_router(next_bell.router, prefix="", tags=["Next bell"])


@app.get("/")
def root():
    return {"message": "API is running!"}
