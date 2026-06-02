from redis.asyncio import Redis
from app.config import settings

# Global Redis client for general use (caching, signaling, etc.)
redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
