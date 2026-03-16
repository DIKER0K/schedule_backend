from fastapi import APIRouter, Body, HTTPException, Query
from app.database import db
from app.models.user import User, UserStats

router = APIRouter()


@router.get("/", response_model=list[User])
async def get_users(
    skip: int = Query(0, ge=0, description="Количество пропущенных записей"),
    limit: int = Query(
        100,
        gt=0,
        le=1000,
        description="Количество записей для получения (по умолчанию 100, максимум 1000)",
    ),
):
    users = await db.users.find().skip(skip).limit(limit).to_list(length=limit)
    return users


@router.get("/platform/{platform}", response_model=list[User])
async def get_users_by_platform(
    platform: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, gt=0, le=1000),
):
    users = (
        await db.users.find({"platform": platform})
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    return users


@router.get("/group/{platform}/{group_name}", response_model=list[User])
async def get_users_by_group(platform: str, group_name: str):

    users = await db.users.find(
        {
            "platform": platform,
            "group_name": group_name,
        }
    ).to_list(length=10000)

    return users


@router.get("/stats/{platform}", response_model=UserStats)
async def get_user_stats(platform: str):

    pipeline = [
        {"$match": {"platform": platform}},
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "students": {"$sum": {"$cond": [{"$eq": ["$role", "student"]}, 1, 0]}},
                "teachers": {"$sum": {"$cond": [{"$eq": ["$role", "teacher"]}, 1, 0]}},
                "admins": {"$sum": {"$cond": [{"$eq": ["$role", "admin"]}, 1, 0]}},
                "subscriptions": {"$sum": {"$cond": ["$schedule_enabled", 1, 0]}},
                "groups": {"$addToSet": "$group_name"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "total": 1,
                "students": 1,
                "teachers": 1,
                "admins": 1,
                "subscriptions": 1,
                "groups": {
                    "$size": {
                        "$filter": {
                            "input": "$groups",
                            "as": "g",
                            "cond": {"$ne": ["$$g", None]},
                        }
                    }
                },
            }
        },
    ]

    result = await db.users.aggregate(pipeline).to_list(length=1)

    if not result:
        return {
            "total": 0,
            "students": 0,
            "teachers": 0,
            "admins": 0,
            "groups": 0,
            "subscriptions": 0,
        }

    return result[0]


@router.get("/{platform}/{user_id}", response_model=User)
async def get_user(platform: str, user_id: int):
    user = await db.users.find_one({"user_id": user_id, "platform": platform})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


# ✅ обновляем частично
@router.put("/{platform}/{user_id}", response_model=User)
async def update_user(platform: str, user_id: int, data: dict = Body(...)):
    result = await db.users.update_one(
        {"user_id": user_id, "platform": platform}, {"$set": data}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    updated = await db.users.find_one({"user_id": user_id, "platform": platform})

    return updated


@router.post("/", response_model=User)
async def create_user(user: User):
    existing = await db.users.find_one(
        {"user_id": user.user_id, "platform": user.platform}
    )

    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    await db.users.insert_one(user.dict())
    return user


@router.delete("/{platform}/{user_id}")
async def delete_user(platform: str, user_id: int):
    result = await db.users.delete_one({"user_id": user_id, "platform": platform})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"message": "User deleted successfully"}


@router.get("/schedule/send/{platform}/{time}", response_model=list[User])
async def get_users_for_schedule(platform: str, time: str):

    users = await db.users.find(
        {
            "platform": platform,
            "schedule_enabled": True,
            "schedule_time": time,
        }
    ).to_list(length=10000)

    return users
