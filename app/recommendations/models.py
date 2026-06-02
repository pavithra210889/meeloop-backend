from datetime import datetime, timezone
from sqlmodel import SQLModel, Field
from app.uuid_utils import generate_uuid


class UserSuggestion(SQLModel, table=True):
    """Pre-computed user suggestions refreshed by the background engine."""

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    suggested_user_id: str = Field(foreign_key="user.id")
    score: float = Field(default=0.0)
    mutual_count: int = Field(default=0)
    reason: str = Field(default="mutual_followers")
    computed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


class PostRecommendation(SQLModel, table=True):
    """Pre-computed post recommendations refreshed by the background engine."""

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    post_id: str = Field(foreign_key="post.id")
    score: float = Field(default=0.0)
    social_boost: bool = Field(default=False)
    computed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


class RecommendationEvent(SQLModel, table=True):
    """Every impression, click, dismiss, follow, or like from a recommendation."""

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    item_type: str  # "user" | "post"
    item_id: str
    event_type: str  # "impression" | "click" | "dismiss" | "follow" | "like"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


class RecommendationWeight(SQLModel, table=True):
    """Per-user scoring weight adjustments updated by the feedback loop."""

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True)
    mutual_follower_weight: float = Field(default=1.0)
    recency_weight: float = Field(default=1.0)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
