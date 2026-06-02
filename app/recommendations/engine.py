import math
import logging
from datetime import datetime, timezone, timedelta

from sqlmodel import Session, select
from sqlalchemy import func

from app.database import engine as db_engine
from app.redis_client import redis_client
from app.users.models import Follow, User
from app.posts.models import Post, Like, Comment
from app.recommendations.models import (
    PostRecommendation,
    RecommendationWeight,
    UserSuggestion,
)

logger = logging.getLogger(__name__)

REDIS_TTL = 7200        # 2 hours
SEEN_TTL = 604800       # 7 days
POST_MAX_AGE_DAYS = 7
MIN_ENGAGEMENT = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _age_hours(created_at: datetime) -> float:
    now = _utcnow()
    ts = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
    return max(0.0, (now - ts).total_seconds() / 3600)


def _get_weights(user_id: str, session: Session) -> dict:
    row = session.exec(
        select(RecommendationWeight).where(RecommendationWeight.user_id == user_id)
    ).first()
    if not row:
        return {"mutual_follower_weight": 1.0, "recency_weight": 1.0}
    return {
        "mutual_follower_weight": row.mutual_follower_weight,
        "recency_weight": row.recency_weight,
    }


def compute_user_suggestions(user_id: str, session: Session, weights: dict) -> list[dict]:
    """
    Friend-of-friend scoring:
    score = (mutual_followers × 30 × w_mutual) + (has_pic × 5)
    Falls back to most-followed accounts when graph is empty.
    """
    # IDs of users I already follow
    my_following = set(
        session.exec(select(Follow.following_id).where(Follow.follower_id == user_id)).all()
    )
    excluded = my_following | {user_id}

    suggestions: list[dict] = []

    if my_following:
        # People followed by users I follow, grouped by how many of my follows follow them
        mutual_rows = session.exec(
            select(Follow.following_id, func.count(Follow.follower_id).label("mutual_count"))
            .where(
                Follow.follower_id.in_(my_following),
                ~Follow.following_id.in_(excluded),
            )
            .group_by(Follow.following_id)
            .order_by(func.count(Follow.follower_id).desc())
            .limit(100)
        ).all()

        if mutual_rows:
            mutual_map = {row[0]: row[1] for row in mutual_rows}
            suggested_ids = list(mutual_map.keys())
            users = session.exec(
                select(User).where(User.id.in_(suggested_ids), User.is_active == True)
            ).all()

            for u in users:
                mutual_count = mutual_map.get(u.id, 0)
                score = (
                    mutual_count * 30 * weights.get("mutual_follower_weight", 1.0)
                    + (5 if u.profile_pic else 0)
                )
                suggestions.append({
                    "id": u.id,
                    "username": u.username,
                    "name": u.name,
                    "profile_pic": u.profile_pic,
                    "bio": u.bio,
                    "mutual_count": mutual_count,
                    "score": round(score, 2),
                    "reason": "mutual_followers",
                })

    if not suggestions:
        # Cold start: most-followed accounts the user doesn't follow yet
        suggestions = _cold_start_users(user_id, excluded, session)

    suggestions.sort(key=lambda x: x["score"], reverse=True)
    return suggestions[:50]


def compute_post_recommendations(user_id: str, session: Session, weights: dict) -> list[dict]:
    """
    Engagement velocity scoring with social boost:
    score = (likes×2 + comments×3) × exp(-age_hours/12) × social_boost × recency_weight
    """
    my_following = set(
        session.exec(select(Follow.following_id).where(Follow.follower_id == user_id)).all()
    )

    cutoff = _utcnow() - timedelta(days=POST_MAX_AGE_DAYS)

    posts_query = (
        select(Post, User)
        .join(User, Post.posted_by == User.id)  # type: ignore[arg-type]
        .where(
            Post.deleted_at.is_(None),  # type: ignore[union-attr]
            Post.is_hidden == False,
            Post.posted_by != user_id,
            Post.created_at >= cutoff,
        )
    )
    if my_following:
        posts_query = posts_query.where(~Post.posted_by.in_(my_following))  # type: ignore[union-attr]

    candidates = session.exec(posts_query.limit(300)).all()

    if not candidates:
        return _cold_start_posts(user_id, my_following, session)

    post_ids = [row.Post.id for row in candidates]

    # Batch fetch engagement counts
    like_counts = dict(
        session.exec(
            select(Like.post_id, func.count(Like.id).label("cnt"))
            .where(Like.post_id.in_(post_ids), Like.liked == True)
            .group_by(Like.post_id)
        ).all()
    )
    comment_counts = dict(
        session.exec(
            select(Comment.post_id, func.count(Comment.id).label("cnt"))
            .where(Comment.post_id.in_(post_ids), Comment.deleted_at.is_(None))
            .group_by(Comment.post_id)
        ).all()
    )

    # Posts liked by people I follow (social signal)
    social_posts: set[str] = set()
    if my_following:
        social_posts = set(
            session.exec(
                select(Like.post_id).where(
                    Like.post_id.in_(post_ids),
                    Like.user_id.in_(my_following),
                    Like.liked == True,
                )
            ).all()
        )

    posts: list[dict] = []
    rw = weights.get("recency_weight", 1.0)

    for row in candidates:
        post = row.Post
        user = row.User
        likes = like_counts.get(post.id, 0)
        comments = comment_counts.get(post.id, 0)
        engagement = likes * 2 + comments * 3

        if engagement < MIN_ENGAGEMENT:
            continue

        decay = math.exp(-_age_hours(post.created_at) / 12.0)
        boosted = post.id in social_posts
        score = engagement * decay * (1.5 if boosted else 1.0) * rw

        posts.append({
            "id": post.id,
            "caption": post.caption,
            "posted_by": post.posted_by,
            "created_at": post.created_at.isoformat(),
            "username": user.username,
            "name": user.name,
            "profile_pic": user.profile_pic,
            "like_count": likes,
            "comment_count": comments,
            "social_boost": boosted,
            "score": round(score, 4),
        })

    if not posts:
        return _cold_start_posts(user_id, my_following, session)

    posts.sort(key=lambda x: x["score"], reverse=True)
    return posts[:100]


def _cold_start_users(user_id: str, excluded: set, session: Session) -> list[dict]:
    """Fallback for new users: return most-followed active accounts."""
    rows = session.exec(
        select(User, func.count(Follow.id).label("follower_count"))
        .join(Follow, Follow.following_id == User.id)  # type: ignore[arg-type]
        .where(User.is_active == True, ~User.id.in_(excluded))
        .group_by(User.id)
        .order_by(func.count(Follow.id).desc())
        .limit(20)
    ).all()

    return [
        {
            "id": row.User.id,
            "username": row.User.username,
            "name": row.User.name,
            "profile_pic": row.User.profile_pic,
            "bio": row.User.bio,
            "mutual_count": 0,
            "score": float(row.follower_count),
            "reason": "popular",
        }
        for row in rows
    ]


def _cold_start_posts(user_id: str, excluded_authors: set, session: Session) -> list[dict]:
    """Fallback: trending posts from the last 24 hours."""
    cutoff = _utcnow() - timedelta(hours=24)

    posts_q = (
        select(Post, User)
        .join(User, Post.posted_by == User.id)  # type: ignore[arg-type]
        .where(
            Post.deleted_at.is_(None),  # type: ignore[union-attr]
            Post.is_hidden == False,
            Post.posted_by != user_id,
            Post.created_at >= cutoff,
        )
    )
    if excluded_authors:
        posts_q = posts_q.where(~Post.posted_by.in_(excluded_authors))  # type: ignore[union-attr]

    candidates = session.exec(posts_q.limit(100)).all()
    if not candidates:
        return []

    post_ids = [r.Post.id for r in candidates]
    like_counts = dict(
        session.exec(
            select(Like.post_id, func.count(Like.id))
            .where(Like.post_id.in_(post_ids), Like.liked == True)
            .group_by(Like.post_id)
        ).all()
    )
    comment_counts = dict(
        session.exec(
            select(Comment.post_id, func.count(Comment.id))
            .where(Comment.post_id.in_(post_ids), Comment.deleted_at.is_(None))
            .group_by(Comment.post_id)
        ).all()
    )

    posts = []
    for row in candidates:
        post, user = row.Post, row.User
        score = like_counts.get(post.id, 0) * 2 + comment_counts.get(post.id, 0) * 3
        if score >= MIN_ENGAGEMENT:
            posts.append({
                "id": post.id,
                "caption": post.caption,
                "posted_by": post.posted_by,
                "created_at": post.created_at.isoformat(),
                "username": user.username,
                "name": user.name,
                "profile_pic": user.profile_pic,
                "like_count": like_counts.get(post.id, 0),
                "comment_count": comment_counts.get(post.id, 0),
                "social_boost": False,
                "score": float(score),
            })

    posts.sort(key=lambda x: x["score"], reverse=True)
    return posts[:20]


async def refresh_recommendations_for_user(
    user_id: str, session: Session | None = None
) -> None:
    """Compute and cache recommendations for one user (Redis + DB).

    If a session is provided (e.g., from a request context) it is used directly
    so tests can use the test DB. Otherwise a new session is opened from db_engine.
    """
    if session is not None:
        weights = _get_weights(user_id, session)
        user_recs = compute_user_suggestions(user_id, session, weights)
        post_recs = compute_post_recommendations(user_id, session, weights)
        await _write_to_redis(user_id, user_recs, post_recs)
        _write_to_db(user_id, user_recs, post_recs, session)
    else:
        with Session(db_engine) as s:
            weights = _get_weights(user_id, s)
            user_recs = compute_user_suggestions(user_id, s, weights)
            post_recs = compute_post_recommendations(user_id, s, weights)
            await _write_to_redis(user_id, user_recs, post_recs)
            _write_to_db(user_id, user_recs, post_recs, s)


async def _write_to_redis(user_id: str, user_recs: list, post_recs: list) -> None:
    import json

    try:
        pipe = redis_client.pipeline()

        if user_recs:
            ukey = f"recs:users:{user_id}"
            pipe.delete(ukey)
            pipe.zadd(ukey, {r["id"]: r["score"] for r in user_recs})
            pipe.expire(ukey, REDIS_TTL)
            for r in user_recs:
                mkey = f"recs:user_meta:{user_id}:{r['id']}"
                pipe.hset(mkey, mapping={k: str(v) if v is not None else "" for k, v in r.items()})
                pipe.expire(mkey, REDIS_TTL)

        if post_recs:
            pkey = f"recs:posts:{user_id}"
            pipe.delete(pkey)
            pipe.zadd(pkey, {r["id"]: r["score"] for r in post_recs})
            pipe.expire(pkey, REDIS_TTL)
            for r in post_recs:
                mkey = f"recs:post_meta:{user_id}:{r['id']}"
                pipe.hset(mkey, mapping={k: str(v) if v is not None else "" for k, v in r.items()})
                pipe.expire(mkey, REDIS_TTL)

        await pipe.execute()
    except Exception:
        logger.warning(f"Redis write failed for user {user_id}, falling back to DB only")


def _write_to_db(
    user_id: str, user_recs: list, post_recs: list, session: Session
) -> None:
    # Clear stale results then insert fresh ones
    existing_u = session.exec(
        select(UserSuggestion).where(UserSuggestion.user_id == user_id)
    ).all()
    for row in existing_u:
        session.delete(row)

    existing_p = session.exec(
        select(PostRecommendation).where(PostRecommendation.user_id == user_id)
    ).all()
    for row in existing_p:
        session.delete(row)

    session.flush()

    for r in user_recs:
        session.add(UserSuggestion(
            user_id=user_id,
            suggested_user_id=r["id"],
            score=r["score"],
            mutual_count=r["mutual_count"],
            reason=r["reason"],
        ))
    for r in post_recs:
        session.add(PostRecommendation(
            user_id=user_id,
            post_id=r["id"],
            score=r["score"],
            social_boost=r["social_boost"],
        ))

    session.commit()
