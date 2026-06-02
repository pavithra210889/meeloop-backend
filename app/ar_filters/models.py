from datetime import datetime, timezone
from typing import Any
from sqlmodel import SQLModel, Field, JSON
from sqlalchemy import Column
from ..uuid_utils import generate_uuid


class ArFilter(SQLModel, table=True):
    __tablename__ = "arfilter"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    filter_key: str = Field(unique=True, index=True)
    filter_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    is_active: bool = Field(default=True)
    sort_order: int = Field(default=0)
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ArGameConfig(SQLModel, table=True):
    __tablename__ = "argameconfig"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    game_id: str = Field(unique=True, index=True)
    config_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    version: int = Field(default=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
