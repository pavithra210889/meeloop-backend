import logging

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maps", tags=["maps"])

GOOGLE_MAPS_BASE = "https://maps.googleapis.com/maps/api/staticmap"


@router.get("/static")
async def static_map(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    width: int = Query(default=400, ge=100, le=640),
    height: int = Query(default=200, ge=50, le=400),
    zoom: int = Query(default=15, ge=1, le=20),
):
    api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", None)
    if not api_key:
        raise HTTPException(status_code=503, detail="Maps not configured")

    params = {
        "center": f"{lat},{lon}",
        "zoom": zoom,
        "size": f"{width}x{height}",
        "scale": 2,
        "markers": f"color:red|{lat},{lon}",
        "format": "jpg",
        "key": api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(GOOGLE_MAPS_BASE, params=params)
            resp.raise_for_status()
            return StreamingResponse(
                content=resp.aiter_bytes(),
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except httpx.HTTPError as e:
        logger.warning("Google Maps static map failed: %s", e)
        raise HTTPException(status_code=502, detail="Map fetch failed")
