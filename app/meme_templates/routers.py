import json
from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select, func
from ..dependencies import SessionDep
from ..users.routers import get_current_user
from ..users.models import User
from .models import MemeTemplates, TemplateType
from .schemas import MemeTemplate, MemeTemplatePaginatedResponse

router = APIRouter(tags=["meme-templates"])


@router.get("/meme-templates/", response_model=MemeTemplatePaginatedResponse)
async def get_meme_templates(
    session: SessionDep,
    current_user: Annotated[User, Depends(get_current_user)],
    q: Optional[str] = Query(
        None, description="Optional search query for hashtags or content"
    ),
    type: Optional[TemplateType] = Query(
        None, description="Optional filter for specific template type"
    ),
    exclude_type: Optional[TemplateType] = Query(
        None, description="Optional filter to exclude a specific template type"
    ),
    limit: int = Query(10, ge=1, le=20, description="Number of templates to return"),
    offset: int = Query(0, ge=0, description="Number of templates to skip"),
    random: bool = Query(True, description="Return templates in random order"),
):
    """
    Get a paginated list of meme templates. If `q` is provided, perform a case-insensitive
    search against content and hashtags. If `type` is provided, filter the results.
    This endpoint requires authentication.
    """
    # If no search query, use DB-side pagination for performance
    if not q:
        query = select(MemeTemplates)
        count_query = select(func.count()).select_from(MemeTemplates)
        if type:
            query = query.where(MemeTemplates.template_type == type)
            count_query = count_query.where(MemeTemplates.template_type == type)
        if exclude_type:
            query = query.where(MemeTemplates.template_type != exclude_type)
            count_query = count_query.where(MemeTemplates.template_type != exclude_type)

        total_count = session.exec(count_query).one()

        order_clause = func.random() if random else MemeTemplates.created_at.desc()
        ordered = (
            query
            .order_by(order_clause)
            .offset(offset)
            .limit(limit)
        )
        rows = session.exec(ordered).all()

        items: list[MemeTemplate] = []
        for template in rows:
            urls = (
                template.urls
                if isinstance(template.urls, list)
                else json.loads(template.urls) if template.urls else []
            )
            hash_tags = (
                template.hash_tags
                if isinstance(template.hash_tags, list)
                else json.loads(template.hash_tags) if template.hash_tags else []
            )

            metadata_info = (
                template.metadata_info
                if isinstance(template.metadata_info, dict)
                else json.loads(template.metadata_info) if template.metadata_info else {}
            )

            items.append(
                MemeTemplate(
                    id=template.id,
                    template_type=template.template_type,
                    content=template.content or "",
                    urls=urls,
                    hash_tags=hash_tags,
                    metadata_info=metadata_info,
                    created_at=template.created_at,
                    updated_at=template.updated_at,
                )
            )

        has_next = (offset + limit) < total_count
        has_previous = offset > 0

        return MemeTemplatePaginatedResponse(
            items=items,
            total=total_count,
            limit=limit,
            offset=offset,
            has_next=has_next,
            has_previous=has_previous,
        )

    # With a search query, filter in Python for case-insensitive matching
    filtered = []
    search_lower = q.lower() if q else None
    
    query = select(MemeTemplates)
    if type:
        query = query.where(MemeTemplates.template_type == type)
    if exclude_type:
        query = query.where(MemeTemplates.template_type != exclude_type)

    all_templates = session.exec(
        query.order_by(MemeTemplates.created_at.desc())
    ).all()
    
    for template in all_templates:
        urls = (
            template.urls
            if isinstance(template.urls, list)
            else json.loads(template.urls) if template.urls else []
        )
        hash_tags = (
            template.hash_tags
            if isinstance(template.hash_tags, list)
            else json.loads(template.hash_tags) if template.hash_tags else []
        )

        if search_lower:
            content_match = search_lower in (template.content or "").lower()
            hashtag_match = any(search_lower in tag.lower() for tag in hash_tags)
            if not (content_match or hashtag_match):
                continue
                
        metadata_info = (
            template.metadata_info
            if isinstance(template.metadata_info, dict)
            else json.loads(template.metadata_info) if template.metadata_info else {}
        )

        filtered.append(
            MemeTemplate(
                id=template.id,
                template_type=template.template_type,
                content=template.content or "",
                urls=urls,
                hash_tags=hash_tags,
                metadata_info=metadata_info,
                created_at=template.created_at,
                updated_at=template.updated_at,
            )
        )

    # Pagination for search results
    total_count = len(filtered)
    items = filtered[offset : offset + limit]
    has_next = (offset + limit) < total_count
    has_previous = offset > 0

    return MemeTemplatePaginatedResponse(
        items=items,
        total=total_count,
        limit=limit,
        offset=offset,
        has_next=has_next,
        has_previous=has_previous,
    )


# Dedicated `/meme-templates/search/` endpoint removed. Use `/meme-templates/?q=...` with the
# same pagination parameters instead. The main endpoint now handles optional search.
