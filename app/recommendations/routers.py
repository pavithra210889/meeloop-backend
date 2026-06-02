import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Query, Depends
from fastapi.exceptions import HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel

from app.dependencies import SessionDep
from app.users.routers import get_current_active_user
from app.users.models import User, Follow
from app.posts.models import Post
from app.redis_client import redis_client
from app.recommendations.models import (
    RecommendationEvent,
    RecommendationWeight,
    UserSuggestion,
    PostRecommendation,
)
from app.recommendations.engine import refresh_recommendations_for_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

CurrentUser = Annotated[User, Depends(get_current_active_user)]


# ── Request schema ────────────────────────────────────────────────────────────

class RecEventRequest(BaseModel):
    item_type: str   # "user" | "post"
    item_id: str
    event_type: str  # "impression" | "click" | "dismiss" | "follow" | "like"


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_unseen_ids(sorted_key: str, seen_key: str, limit: int, offset: int) -> list[str]:
    """Read Redis sorted set (high score first), filter seen items, apply pagination."""
    all_ids: list[str] = await redis_client.zrevrange(sorted_key, 0, -1)
    seen: set[str] = await redis_client.smembers(seen_key)
    unseen = [i for i in all_ids if i not in seen]
    return unseen[offset: offset + limit]


async def _record_impressions_bg(user_id: str, item_type: str, item_ids: list[str]) -> None:
    """Opens its own session — safe to run as a background task after the request ends."""
    from app.database import engine as db_engine
    try:
        with Session(db_engine) as s:
            for iid in item_ids:
                s.add(RecommendationEvent(
                    user_id=user_id, item_type=item_type,
                    item_id=iid, event_type="impression",
                ))
            s.commit()
    except Exception:
        logger.warning("Failed to persist impressions")


async def _update_weights_bg(user_id: str, event_type: str) -> None:
    """Opens its own session — safe to run as a background task after the request ends."""
    from app.database import engine as db_engine
    try:
        with Session(db_engine) as s:
            row = s.exec(
                select(RecommendationWeight).where(RecommendationWeight.user_id == user_id)
            ).first()
            if not row:
                row = RecommendationWeight(user_id=user_id)
                s.add(row)

            if event_type in ("follow", "click", "like"):
                row.mutual_follower_weight = min(row.mutual_follower_weight * 1.05, 3.0)
                row.recency_weight = min(row.recency_weight * 1.02, 2.0)
            elif event_type == "dismiss":
                row.mutual_follower_weight = max(row.mutual_follower_weight * 0.95, 0.3)

            s.commit()
    except Exception:
        logger.warning("Failed to update recommendation weights")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/users")
async def get_user_suggestions(
    session: SessionDep,
    current_user: CurrentUser,
    limit: int = Query(default=20, le=50),
    offset: int = Query(default=0),
):
    """
    Returns a ranked list of users the current user might want to follow.
    Served from Redis cache. Cache miss triggers a synchronous compute.
    """
    me = current_user.id
    redis_key = f"recs:users:{me}"
    seen_key = f"recs:seen:users:{me}"

    if not await redis_client.exists(redis_key):
        await refresh_recommendations_for_user(me, session)

    item_ids = await _get_unseen_ids(redis_key, seen_key, limit, offset)

    if not item_ids:
        # DB fallback (Redis cold or all items already seen)
        rows = session.exec(
            select(UserSuggestion)
            .where(UserSuggestion.user_id == me)
            .order_by(UserSuggestion.score.desc())  # type: ignore[union-attr]
            .offset(offset)
            .limit(limit)
        ).all()

        if not rows:
            return {"items": [], "has_more": False}

        suggested_ids = [r.suggested_user_id for r in rows]
        users = session.exec(
            select(User).where(User.id.in_(suggested_ids))
        ).all()
        user_map = {u.id: u for u in users}
        score_map = {r.suggested_user_id: (r.score, r.mutual_count, r.reason) for r in rows}

        items = [
            {
                "id": uid,
                "username": user_map[uid].username,
                "name": user_map[uid].name,
                "profile_pic": user_map[uid].profile_pic,
                "bio": user_map[uid].bio,
                "mutual_count": score_map[uid][1],
                "reason": score_map[uid][2],
            }
            for uid in suggested_ids
            if uid in user_map
        ]
        asyncio.create_task(_record_impressions_bg(me, "user", suggested_ids))
        return {"items": items, "has_more": len(items) == limit}

    # Fetch metadata from Redis hashes
    pipe = redis_client.pipeline()
    for uid in item_ids:
        pipe.hgetall(f"recs:user_meta:{me}:{uid}")
    meta_list = await pipe.execute()

    items = []
    db_fallback_ids = []

    for uid, meta in zip(item_ids, meta_list):
        if meta:
            items.append({k: (v if v != "" else None) for k, v in meta.items()})
        else:
            db_fallback_ids.append(uid)

    # DB fallback for any missing metadata
    if db_fallback_ids:
        users = session.exec(select(User).where(User.id.in_(db_fallback_ids))).all()
        for u in users:
            items.append({
                "id": u.id, "username": u.username, "name": u.name,
                "profile_pic": u.profile_pic, "bio": u.bio,
                "mutual_count": 0, "reason": "popular",
            })

    asyncio.create_task(_record_impressions_bg(me, "user", item_ids))
    return {"items": items, "has_more": len(item_ids) == limit}


@router.get("/posts")
async def get_post_recommendations(
    session: SessionDep,
    current_user: CurrentUser,
    limit: int = Query(default=20, le=50),
    offset: int = Query(default=0),
):
    """
    Returns a ranked list of posts for the For-You feed.
    Served from Redis cache. Cache miss triggers a synchronous compute.
    """
    me = current_user.id
    redis_key = f"recs:posts:{me}"
    seen_key = f"recs:seen:posts:{me}"

    if not await redis_client.exists(redis_key):
        await refresh_recommendations_for_user(me, session)

    item_ids = await _get_unseen_ids(redis_key, seen_key, limit, offset)

    if not item_ids:
        # DB fallback
        rows = session.exec(
            select(PostRecommendation)
            .where(PostRecommendation.user_id == me)
            .order_by(PostRecommendation.score.desc())  # type: ignore[union-attr]
            .offset(offset)
            .limit(limit)
        ).all()

        if not rows:
            return {"items": [], "has_more": False}

        post_ids = [r.post_id for r in rows]
        posts = session.exec(
            select(Post, User)
            .join(User, Post.posted_by == User.id)  # type: ignore[arg-type]
            .where(Post.id.in_(post_ids))
        ).all()

        items = [
            {
                "id": row.Post.id,
                "caption": row.Post.caption,
                "posted_by": row.Post.posted_by,
                "created_at": row.Post.created_at.isoformat(),
                "username": row.User.username,
                "name": row.User.name,
                "profile_pic": row.User.profile_pic,
            }
            for row in posts
        ]
        asyncio.create_task(_record_impressions_bg(me, "post", post_ids))
        return {"items": items, "has_more": len(items) == limit}

    # Fetch metadata from Redis hashes
    pipe = redis_client.pipeline()
    for pid in item_ids:
        pipe.hgetall(f"recs:post_meta:{me}:{pid}")
    meta_list = await pipe.execute()

    items = []
    db_fallback_ids = []

    for pid, meta in zip(item_ids, meta_list):
        if meta:
            items.append({k: (v if v != "" else None) for k, v in meta.items()})
        else:
            db_fallback_ids.append(pid)

    if db_fallback_ids:
        posts = session.exec(
            select(Post, User)
            .join(User, Post.posted_by == User.id)  # type: ignore[arg-type]
            .where(Post.id.in_(db_fallback_ids))
        ).all()
        for row in posts:
            items.append({
                "id": row.Post.id,
                "caption": row.Post.caption,
                "posted_by": row.Post.posted_by,
                "created_at": row.Post.created_at.isoformat(),
                "username": row.User.username,
                "name": row.User.name,
                "profile_pic": row.User.profile_pic,
            })

    asyncio.create_task(_record_impressions_bg(me, "post", item_ids))
    return {"items": items, "has_more": len(item_ids) == limit}


@router.post("/event")
async def record_recommendation_event(
    payload: RecEventRequest,
    session: SessionDep,
    current_user: CurrentUser,
):
    """
    Record a user interaction with a recommendation.
    Dismiss/follow/like marks the item as seen so it won't appear again.
    Positive events gently boost scoring weights; dismissals reduce them.
    """
    me = current_user.id

    if payload.item_type not in ("user", "post"):
        raise HTTPException(status_code=422, detail="item_type must be 'user' or 'post'")
    if payload.event_type not in ("impression", "click", "dismiss", "follow", "like"):
        raise HTTPException(status_code=422, detail="Invalid event_type")

    session.add(RecommendationEvent(
        user_id=me,
        item_type=payload.item_type,
        item_id=payload.item_id,
        event_type=payload.event_type,
    ))
    session.commit()

    # Mark as seen so it never resurfaces within the seen-TTL window
    if payload.event_type in ("click", "dismiss", "follow", "like"):
        seen_key = f"recs:seen:{payload.item_type}s:{me}"
        await redis_client.sadd(seen_key, payload.item_id)
        await redis_client.expire(seen_key, 604800)

    asyncio.create_task(_update_weights_bg(me, payload.event_type))

    return {"ok": True}
