import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.routers import bell_schedule, users, schedule, ai
from app.database import db
from fastapi.middleware.cors import CORSMiddleware
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting up...")

    # создаём индексы Mongo
    await db.schedules.create_index("group_name", unique=True)
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


@app.get("/")
def root():
    return {"message": "API is running!"}
