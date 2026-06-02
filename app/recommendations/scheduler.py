import asyncio
import logging

from sqlmodel import Session, select

from app.database import engine
from app.redis_client import redis_client
from app.users.models import User
from app.recommendations.engine import refresh_recommendations_for_user

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 1800   # 30 minutes
USERS_PER_BATCH = 300
BATCH_DELAY = 0.05        # 50 ms between users to avoid DB spikes


async def run_recommendations_engine() -> None:
    """Background loop: refresh recommendations for active users every 30 minutes."""
    logger.info("Recommendations engine started")
    while True:
        try:
            await asyncio.sleep(REFRESH_INTERVAL)
            await _run_batch()
        except Exception:
            logger.exception("Recommendations engine batch failed")


async def _run_batch() -> None:
    # Distributed lock: only one worker runs the batch per cycle (skipped if Redis unavailable)
    lock_key = "recs:engine:lock"
    try:
        acquired = await redis_client.set(lock_key, "1", nx=True, ex=REFRESH_INTERVAL - 60)
        if not acquired:
            return
    except Exception:
        logger.debug("Redis unavailable, running recommendations batch without distributed lock")

    with Session(engine) as session:
        user_ids = session.exec(
            select(User.id).where(User.is_active == True).limit(USERS_PER_BATCH)
        ).all()

    logger.info(f"Refreshing recommendations for {len(user_ids)} users")

    for user_id in user_ids:
        try:
            await refresh_recommendations_for_user(user_id)
            await asyncio.sleep(BATCH_DELAY)
        except Exception:
            logger.exception(f"Failed to refresh recommendations for user {user_id}")
