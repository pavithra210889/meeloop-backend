"""
Gorse Phase 2 Integration
=========================
Gorse (github.com/gorse-io/gorse) is an open-source recommendation engine.
It handles collaborative filtering, content-based, and hybrid recommendations.

Switch to Gorse when your user base grows past ~50K and you want ML-backed
ranking without building your own model.

HOW IT WORKS
------------
1. You forward every user interaction (like, follow, view) to Gorse as a "feedback"
2. Gorse trains collaborative filtering models in the background
3. You call GET /api/recommend/{user_id} to get ranked recommendations

SETUP (add to your docker-compose.yml)
---------------------------------------

  gorse-server:
    image: zhenghaoz/gorse-server:latest
    environment:
      GORSE_CACHE_STORE: redis://redis:6379
      GORSE_DATA_STORE: postgres://user:pass@db:5432/meeloop
    ports:
      - "8087:8087"   # REST API
      - "8088:8088"   # gRPC
    depends_on:
      - redis
      - db

  gorse-worker:
    image: zhenghaoz/gorse-worker:latest
    environment:
      GORSE_MASTER_HOST: gorse-master
      GORSE_MASTER_PORT: 8086
    depends_on:
      - gorse-master

  gorse-master:
    image: zhenghaoz/gorse-master:latest
    environment:
      GORSE_CACHE_STORE: redis://redis:6379
      GORSE_DATA_STORE: postgres://user:pass@db:5432/meeloop
    ports:
      - "8086:8086"
    depends_on:
      - redis
      - db

ENV VARS TO ADD
---------------
Add to .env:
  GORSE_API_URL=http://gorse-server:8087
  GORSE_API_KEY=your-secret-key   # set in gorse config

MIGRATION PLAN
--------------
Phase 1 (current):  heuristic scoring + Redis cache (this file's counterpart)
Phase 2 (this file): forward events to Gorse, use Gorse results for ranking,
                     keep heuristic as fallback
Phase 3:            replace heuristic entirely with Gorse + two-tower model
"""

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GORSE_TIMEOUT = 2.0  # seconds — fast enough to use in the hot path


class GorseClient:
    """Thin async wrapper around the Gorse REST API."""

    def __init__(self) -> None:
        self._base = getattr(settings, "GORSE_API_URL", None)
        self._key = getattr(settings, "GORSE_API_KEY", "")

    @property
    def enabled(self) -> bool:
        return bool(self._base)

    def _headers(self) -> dict:
        return {"X-API-Key": self._key} if self._key else {}

    async def insert_feedback(self, feedback_type: str, user_id: str, item_id: str) -> bool:
        """
        Forward an interaction to Gorse.

        feedback_type options (choose semantically meaningful names):
          "like"        — user liked a post
          "follow"      — user followed another user
          "view"        — user viewed a post / profile (implicit signal)
          "click"       — user clicked a recommendation
          "dismiss"     — user dismissed a recommendation (negative signal)

        Returns True on success, False on any error.
        """
        if not self.enabled:
            return False

        try:
            async with httpx.AsyncClient(timeout=GORSE_TIMEOUT) as client:
                res = await client.post(
                    f"{self._base}/api/insert/feedbacks",
                    headers=self._headers(),
                    json=[{
                        "FeedbackType": feedback_type,
                        "UserId": user_id,
                        "ItemId": item_id,
                    }],
                )
                return res.is_success
        except Exception:
            logger.warning(f"Gorse feedback insert failed for user={user_id} item={item_id}")
            return False

    async def get_user_recommendations(self, user_id: str, n: int = 20) -> list[str]:
        """
        Get top-N recommended item IDs for a user.
        Returns a list of item IDs (post IDs or user IDs depending on item type).
        Returns [] on error so the caller can fall back to the heuristic engine.
        """
        if not self.enabled:
            return []

        try:
            async with httpx.AsyncClient(timeout=GORSE_TIMEOUT) as client:
                res = await client.get(
                    f"{self._base}/api/recommend/{user_id}",
                    headers=self._headers(),
                    params={"n": n},
                )
                if res.is_success:
                    return res.json()  # list of item ID strings
        except Exception:
            logger.warning(f"Gorse recommendation fetch failed for user={user_id}")

        return []

    async def insert_item(self, item_id: str, labels: list[str], comment: str = "") -> bool:
        """
        Register a new item (post or user profile) with Gorse.
        Labels are used for content-based filtering (e.g. hashtags, categories).
        Call this when a new post is created.
        """
        if not self.enabled:
            return False

        try:
            async with httpx.AsyncClient(timeout=GORSE_TIMEOUT) as client:
                res = await client.post(
                    f"{self._base}/api/item",
                    headers=self._headers(),
                    json={
                        "ItemId": item_id,
                        "Labels": labels,
                        "Comment": comment,
                    },
                )
                return res.is_success
        except Exception:
            logger.warning(f"Gorse item insert failed for item={item_id}")
            return False

    async def insert_user(self, user_id: str, labels: list[str] = []) -> bool:
        """Register a user with Gorse (call on signup)."""
        if not self.enabled:
            return False

        try:
            async with httpx.AsyncClient(timeout=GORSE_TIMEOUT) as client:
                res = await client.post(
                    f"{self._base}/api/user",
                    headers=self._headers(),
                    json={"UserId": user_id, "Labels": labels},
                )
                return res.is_success
        except Exception:
            logger.warning(f"Gorse user insert failed for user={user_id}")
            return False


gorse = GorseClient()


# ── Drop-in integration points ────────────────────────────────────────────────
#
# Add these calls to your existing endpoints:
#
# 1. In posts/routers.py — when a user likes a post:
#    asyncio.create_task(gorse.insert_feedback("like", current_user.id, post_id))
#
# 2. In users/routers.py — when a user follows someone:
#    asyncio.create_task(gorse.insert_feedback("follow", current_user.id, followed_id))
#
# 3. In recommendations/routers.py — replace or blend with heuristic:
#    gorse_ids = await gorse.get_user_recommendations(me, n=limit)
#    if gorse_ids:
#        # use Gorse results (ML-backed)
#        ...
#    else:
#        # fall back to heuristic engine
#        await refresh_recommendations_for_user(me, session)
#
# 4. In posts/routers.py — when a post is created:
#    labels = extract_hashtags(post.caption)  # ["travel", "food", ...]
#    asyncio.create_task(gorse.insert_item(post.id, labels, post.caption or ""))
#
# 5. In users/routers.py — on signup:
#    asyncio.create_task(gorse.insert_user(user.id))
