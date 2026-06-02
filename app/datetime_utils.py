"""
UTC datetime serialization for FastAPI/Pydantic v2 response schemas.

The database stores naive datetimes (SQLite/Postgres without timezone columns).
When serialized to JSON, naive datetimes omit the timezone suffix, causing
JavaScript to interpret them as local time instead of UTC.

Use `UTCDatetime` instead of `datetime` in all response schema fields.
It is identical to `datetime` for Python code, but forces a `Z` suffix
when serialized to JSON, so clients always receive unambiguous UTC timestamps.

DB model fields (`table=True`) should keep plain `datetime` so ORM behavior
is not affected. Only response schemas (BaseModel subclasses) need this.
"""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import PlainSerializer

UTCDatetime = Annotated[
    datetime,
    PlainSerializer(
        lambda dt: (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).isoformat(),
        return_type=str,
        when_used="json",
    ),
]
