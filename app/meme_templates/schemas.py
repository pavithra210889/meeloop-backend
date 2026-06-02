from datetime import datetime
from sqlmodel import SQLModel
from typing import List, Optional
from pydantic import BaseModel
from .models import TemplateType


class MemeTemplate(SQLModel):
    id: str
    template_type: TemplateType
    content: str
    urls: list[str]  # Changed from str to list[str]
    hash_tags: list[str]
    metadata_info: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class MemeTemplatePaginatedResponse(BaseModel):
    items: List[MemeTemplate]
    total: int
    limit: int
    offset: int
    has_next: bool
    has_previous: bool
