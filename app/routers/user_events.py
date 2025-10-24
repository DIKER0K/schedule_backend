from fastapi import APIRouter
from app.database import db
from app.models.user_event import UserEvent
from datetime import datetime, timedelta

router = APIRouter()

# POST /user-events
@router.post("/", response_model=UserEvent)
async def add_event(event: UserEvent):
    await db.user_events.insert_one(event.dict())
    return event

# GET /user-events?user_id=123&days=7
@router.get("/")
async def get_events(user_id: int | None = None, days: int = 30):
    since = datetime.utcnow() - timedelta(days=days)
    query = {"ts": {"$gte": since}}
    if user_id:
        query["user_id"] = user_id
    events = await db.user_events.find(query).to_list(1000)
    return events

# GET /user-events/stats/top
@router.get("/stats/top")
async def get_top_active_users(days: int = 7):
    since = datetime.utcnow() - timedelta(days=days)
    pipeline = [
        {"$match": {"ts": {"$gte": since}}},
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    top = await db.user_events.aggregate(pipeline).to_list(10)
    return [{"user_id": d["_id"], "events": d["count"]} for d in top]
