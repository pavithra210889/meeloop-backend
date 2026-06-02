import base64
import hashlib
import hmac
import time
from typing import Annotated, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..config import settings
from ..users.models import User
from ..users.routers import get_current_active_user

router = APIRouter(tags=["turn"])


class TurnCredentials(BaseModel):
    username: str
    credential: str
    ttl: int
    uris: List[str]


class IceServerDTO(BaseModel):
    url: str
    username: str | None = None
    credential: str | None = None


@router.get("/turn/credentials", response_model=TurnCredentials)
async def get_turn_credentials(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """
    Generate short-lived HMAC-based TURN credentials for coturn.

    Uses coturn's use-auth-secret mechanism:
    - username = {expiry_timestamp}:{user_id}
    - credential = base64(HMAC-SHA1(username, static-auth-secret))
    """
    ttl = settings.TURN_TTL
    expiry = int(time.time()) + ttl
    username = f"{expiry}:{current_user.id}"

    # HMAC-SHA1 of the username using the shared secret
    hmac_digest = hmac.new(
        settings.TURN_SECRET.encode("utf-8"),
        username.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    credential = base64.b64encode(hmac_digest).decode("utf-8")

    server = settings.TURN_SERVER
    uris = []
    if settings.TURN_SECRET and server:
        uris += [
            f"stun:{server}",
            f"turn:{server}?transport=udp",
            f"turn:{server}?transport=tcp",
        ]

    return TurnCredentials(
        username=username,
        credential=credential,
        ttl=ttl,
        uris=uris,
    )


@router.get("/webrtc/ice-servers", response_model=List[IceServerDTO])
async def get_ice_servers(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    """
    Return ICE servers in the legacy format expected by Android/iOS clients.

    Generates fresh HMAC credentials and returns them in the IceServerDTO
    format that existing mobile clients already understand.
    """
    ttl = settings.TURN_TTL
    expiry = int(time.time()) + ttl
    username = f"{expiry}:{current_user.id}"

    hmac_digest = hmac.new(
        settings.TURN_SECRET.encode("utf-8"),
        username.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    credential = base64.b64encode(hmac_digest).decode("utf-8")

    server = settings.TURN_SERVER

    servers = []

    if settings.TURN_SECRET and settings.TURN_SERVER:
        servers += [
            IceServerDTO(url=f"stun:{server}"),
            IceServerDTO(
                url=f"turn:{server}?transport=udp",
                username=username,
                credential=credential,
            ),
            IceServerDTO(
                url=f"turn:{server}?transport=tcp",
                username=username,
                credential=credential,
            ),
        ]

    return servers
