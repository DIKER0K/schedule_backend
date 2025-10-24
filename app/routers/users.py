from fastapi import APIRouter, Body, HTTPException, Query
from app.database import db
from app.models.user import User

router = APIRouter()

@router.get("/", response_model=list[User])
async def get_users(
    skip: int = Query(0, ge=0, description="Количество пропущенных записей"),
    limit: int = Query(100, gt=0, le=1000, description="Количество записей для получения (по умолчанию 100, максимум 1000)")
):
    users = await db.users.find().skip(skip).limit(limit).to_list(length=limit)
    return users

@router.get("/{user_id}", response_model=User)
async def get_user(user_id: int):
    user = await db.users.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# ✅ обновляем частично
@router.put("/{user_id}", response_model=User)
async def update_user(user_id: int, data: dict = Body(...)):
    result = await db.users.update_one({"user_id": user_id}, {"$set": data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    updated = await db.users.find_one({"user_id": user_id})
    return updated

@router.post("/", response_model=User)
async def create_user(user: User):
    existing = await db.users.find_one({"user_id": user.user_id})
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")
    await db.users.insert_one(user.dict())
    return user

@router.delete("/{user_id}")
async def delete_user(user_id: int):
    result = await db.users.delete_one({"user_id": user_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted successfully"}
