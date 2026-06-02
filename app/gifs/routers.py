import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..users.models import User
from ..users.routers import get_current_active_user
from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gifs", tags=["gifs"])

GIPHY_BASE = "https://api.giphy.com/v1/gifs"


class GifResult(BaseModel):
    id: str
    title: str = ""
    url: str  # URL to the GIF (downsized for chat)
    preview_url: str  # Static preview thumbnail
    width: int = 0
    height: int = 0


class GifSearchResponse(BaseModel):
    results: list[GifResult]
    next: str = ""


def _parse_giphy_results(data: dict) -> GifSearchResponse:
    results = []
    for item in data.get("data", []):
        images = item.get("images", {})
        # Use fixed_width for chat (200px wide, reasonable file size)
        gif = images.get("fixed_width", {})
        preview = images.get("fixed_width_still", gif)
        if gif.get("url"):
            results.append(GifResult(
                id=item.get("id", ""),
                title=item.get("title", ""),
                url=gif["url"],
                preview_url=preview.get("url", gif["url"]),
                width=int(gif.get("width", 0)),
                height=int(gif.get("height", 0)),
            ))
    pagination = data.get("pagination", {})
    offset = pagination.get("offset", 0)
    count = pagination.get("count", 0)
    total = pagination.get("total_count", 0)
    next_offset = str(offset + count) if offset + count < total else ""
    return GifSearchResponse(results=results, next=next_offset)


@router.get("/search", response_model=GifSearchResponse)
async def search_gifs(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    pos: str = Query("", description="Pagination offset from previous response"),
    current_user: Annotated[User, Depends(get_current_active_user)] = None,
):
    api_key = getattr(settings, "GIPHY_API_KEY", None)
    if not api_key:
        raise HTTPException(status_code=503, detail="GIPHY API key not configured")

    params: dict = {"api_key": api_key, "q": q, "limit": limit, "rating": "pg-13", "lang": "en"}
    if pos:
        params["offset"] = int(pos)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{GIPHY_BASE}/search", params=params)
            resp.raise_for_status()
            return _parse_giphy_results(resp.json())
    except httpx.HTTPError as e:
        logger.warning("GIPHY search failed: %s", e)
        raise HTTPException(status_code=502, detail="GIF search failed")


@router.get("/trending", response_model=GifSearchResponse)
async def trending_gifs(
    limit: int = Query(20, ge=1, le=50),
    pos: str = Query("", description="Pagination offset"),
    current_user: Annotated[User, Depends(get_current_active_user)] = None,
):
    api_key = getattr(settings, "GIPHY_API_KEY", None)
    if not api_key:
        raise HTTPException(status_code=503, detail="GIPHY API key not configured")

    params: dict = {"api_key": api_key, "limit": limit, "rating": "pg-13"}
    if pos:
        params["offset"] = int(pos)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{GIPHY_BASE}/trending", params=params)
            resp.raise_for_status()
            return _parse_giphy_results(resp.json())
    except httpx.HTTPError as e:
        logger.warning("GIPHY trending failed: %s", e)
        raise HTTPException(status_code=502, detail="GIF search failed")
