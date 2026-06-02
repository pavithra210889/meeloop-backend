from app.redis_client import redis_client

# Redis key helpers
_KEY_PREFIX = "active_user:"
_TTL = 90  # seconds — slightly longer than Socket.IO ping timeout (60s)


def _key(user_id: str) -> str:
    return f"{_KEY_PREFIX}{user_id}"


async def set_active(user_id: str, sid: str) -> None:
    """Mark a user as online with their socket session id."""
    await redis_client.set(_key(user_id), sid, ex=_TTL)


async def get_active_sid(user_id: str) -> str | None:
    """Return the socket sid for an online user, or None."""
    return await redis_client.get(_key(user_id))


async def is_active(user_id: str) -> bool:
    """Check if a user has an active socket connection."""
    return await redis_client.exists(_key(user_id)) == 1


async def remove_active(user_id: str, sid: str | None = None) -> None:
    """Remove a user from the active set.

    If *sid* is provided, only remove if the stored sid matches (prevents
    a reconnect on another worker from being clobbered by a late disconnect).
    """
    if sid:
        stored = await redis_client.get(_key(user_id))
        if stored != sid:
            return
    await redis_client.delete(_key(user_id))


async def pop_active(user_id: str) -> str | None:
    """Remove and return the sid (used by notification fallback)."""
    key = _key(user_id)
    sid = await redis_client.get(key)
    if sid:
        await redis_client.delete(key)
    return sid
