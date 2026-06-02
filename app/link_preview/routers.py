import re
import logging
from typing import Annotated, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..users.models import User
from ..users.routers import get_current_active_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/link-preview", tags=["link-preview"])


class LinkPreviewResponse(BaseModel):
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None
    site_name: Optional[str] = None


# Regex patterns for OG meta tags (handles both attribute orderings)
_OG_META = re.compile(
    r'<meta\s+[^>]*?property=["\']og:(\w+)["\'][^>]*?content=["\']([^"\']*)["\']',
    re.IGNORECASE | re.DOTALL,
)
_OG_META_REV = re.compile(
    r'<meta\s+[^>]*?content=["\']([^"\']*)["\'][^>]*?property=["\']og:(\w+)["\']',
    re.IGNORECASE | re.DOTALL,
)
_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

MAX_FETCH_BYTES = 512 * 1024  # 512 KB
FETCH_TIMEOUT = 5.0


@router.get("/", response_model=LinkPreviewResponse)
async def get_link_preview(
    url: str = Query(..., min_length=1),
    current_user: Annotated[User, Depends(get_current_active_user)] = None,
):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "MeeloopBot/1.0"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type.lower():
                raise HTTPException(status_code=400, detail="URL does not point to an HTML page")

            html = resp.text[:MAX_FETCH_BYTES]
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch URL %s: %s", url, exc)
        raise HTTPException(status_code=400, detail="Failed to fetch URL")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Unexpected error fetching %s: %s", url, exc)
        raise HTTPException(status_code=400, detail="Failed to fetch URL")

    # Parse OG tags
    og: dict[str, str] = {}
    for m in _OG_META.finditer(html):
        key = m.group(1).lower()
        if key not in og:
            og[key] = m.group(2)
    for m in _OG_META_REV.finditer(html):
        key = m.group(2).lower()
        if key not in og:
            og[key] = m.group(1)

    # Fallback to <title>
    title = og.get("title")
    if not title:
        title_match = _TITLE_TAG.search(html)
        if title_match:
            title = title_match.group(1).strip()

    # Resolve relative image URL
    image = og.get("image")
    if image and not image.startswith(("http://", "https://")):
        from urllib.parse import urljoin
        image = urljoin(url, image)

    return LinkPreviewResponse(
        url=url,
        title=title,
        description=og.get("description"),
        image=image,
        site_name=og.get("site_name"),
    )
