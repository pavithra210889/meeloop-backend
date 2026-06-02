import socketio
from app.config import settings
import logging

logger = logging.getLogger(__name__)

# Configure Redis for multi-worker support
# This allows Socket.IO to work across multiple Gunicorn workers
mgr = socketio.AsyncRedisManager(settings.REDIS_URL)

# Disable verbose logging in production for performance
verbose_logging = settings.ENVIRONMENT != "production"

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.SOCKETIO_CORS_ORIGINS,
    client_manager=mgr,
    logger=verbose_logging,
    engineio_logger=verbose_logging,
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=1000000,
)
