from fastapi import FastAPI
from fastapi.concurrency import asynccontextmanager
from app.routers import users, schedule, user_events
from app.database import db
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="College Schedule Bot API")

from app.services.schedule_parser import start_background_schedule_updater

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@asynccontextmanager
async def startup_event():
    start_background_schedule_updater()

app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(schedule.router, prefix="/schedule", tags=["Schedule"])
app.include_router(user_events.router, prefix="/user-events", tags=["User Events"])

@app.get("/")
def root():
    return {"message": "API is running!"}
