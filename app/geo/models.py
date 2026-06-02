from datetime import datetime
from sqlmodel import SQLModel, Field
from ..uuid_utils import generate_uuid


class IpInfoCache(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    ip: str = Field(index=True, unique=True)
    country: str | None = None
    region: str | None = None
    city: str | None = None
    timezone: str | None = None
    loc: str | None = None  # "lat,lon"
    org: str | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
