from sqlmodel import SQLModel, Field, Relationship
from pydantic import BaseModel
from datetime import datetime, timezone
from ..datetime_utils import UTCDatetime
from ..users.models import User, UserBasic
from typing import Optional, Union, List
from ..uuid_utils import generate_uuid


class PostCreateRequest(BaseModel):
    caption: str | None = None
    media_urls: List[str]


class CommentCreateRequest(BaseModel):
    comment: str
    reply_to: str | None = None


class BookmarkCreateRequest(BaseModel):
    post_id: str
    folder_id: str


class BookmarkToggleRequest(BaseModel):
    post_id: str
    folder_id: str


class BookmarkFolderCreateRequest(BaseModel):
    name: str


class BookmarkFolderUpdateRequest(BaseModel):
    name: str


class BasePost(SQLModel):
    caption: str


class Post(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    caption: str | None = Field(default=None)
    posted_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_hidden: bool = Field(default=False)
    deleted_at: datetime | None = Field(default=None)

    user: User = Relationship(back_populates="posts")
    media_files: list["Media"] = Relationship(
        back_populates="post", cascade_delete=True
    )
    bookmarks: list["Bookmark"] = Relationship(back_populates="post")
    shared_in_messages: list["Message"] = Relationship(back_populates="shared_post")

    def __str__(self) -> str:
        return (self.caption or "")[:30] or f"Post:{self.id[:8]}"


class Media(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    file_path: str
    file_type: str
    post_id: str | None = Field(default=None, foreign_key="post.id")
    post: Post | None = Relationship(back_populates="media_files")


class Like(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id")
    post_id: str = Field(foreign_key="post.id")
    liked: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Comment(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    comment: str
    user_id: str = Field(foreign_key="user.id")
    post_id: str = Field(foreign_key="post.id")
    reply_to: str | None = Field(default=None, foreign_key="comment.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_hidden: bool = Field(default=False)
    deleted_at: datetime | None = Field(default=None)

    commented_by: User = Relationship(back_populates="comments")


class BookmarkFolder(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    name: str
    created_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def __str__(self) -> str:
        return self.name

    bookmarks: list["Bookmark"] = Relationship(back_populates="bookmark_folder")


class Bookmark(SQLModel, table=True):
    id: str = Field(default_factory=generate_uuid, primary_key=True)
    post_id: str = Field(foreign_key="post.id")
    bookmark_folder_id: str = Field(foreign_key="bookmarkfolder.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    bookmark_folder: BookmarkFolder = Relationship(back_populates="bookmarks")
    post: Post = Relationship(back_populates="bookmarks")


class MediaResponse(BaseModel):
    id: str
    file_path: str
    file_type: str


class BookmarkFolderBasic(BaseModel):
    id: str
    name: str
    is_bookmarked: bool


class PostResponse(BaseModel):
    id: str
    caption: str | None
    user: UserBasic
    created_at: UTCDatetime
    updated_at: UTCDatetime
    media_files: list[MediaResponse]

    likes_count: int
    comments_count: int
    is_liked: bool
    is_bookmarked: bool
    bookmarked_folders: list[BookmarkFolderBasic] = []


class CommentResponse(BaseModel):
    id: str
    comment: str
    post_id: str
    reply_to: str | None
    created_at: UTCDatetime
    updated_at: UTCDatetime
    commented_by: UserBasic


class BookmarkResponse(BaseModel):
    id: str
    post: PostResponse
    bookmark_folder_id: str
    created_at: UTCDatetime


class SeoAuthor(BaseModel):
    username: str
    name: Optional[str]
    url: Optional[str]
    image: Optional[str]
    description: Optional[str]


class PostSeoResponse(BaseModel):
    id: str
    title: str
    meta_description: str
    og_images: Optional[List[str]] = None
    image_alt: str
    keywords: Union[str, list[str]]
    date_published: UTCDatetime
    author: SeoAuthor
