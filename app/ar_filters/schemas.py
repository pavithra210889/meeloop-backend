from typing import Any
from pydantic import BaseModel


class ArFiltersResponse(BaseModel):
    version: int
    filters: list[dict[str, Any]]


class ArGameConfigRead(BaseModel):
    game_id: str
    config_data: dict[str, Any]
    version: int


class ArFilterUpsertRequest(BaseModel):
    filter_key: str
    filter_data: dict[str, Any]
    is_active: bool = True
    sort_order: int = 0


class ArFilterUpdateRequest(BaseModel):
    filter_data: dict[str, Any] | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class ArGameConfigUpdateRequest(BaseModel):
    config_data: dict[str, Any]
