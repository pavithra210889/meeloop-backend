import json
from datetime import datetime
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel import select, func, exists, Session
from sqlalchemy import and_
from typing import Annotated, List
from fastapi import APIRouter, Body, Depends, Form, Query
from fastapi.exceptions import HTTPException
from ..users.routers import get_current_active_user
from ..users.models import Follow, User, UserBasic, Block
from ..dependencies import SessionDep
from .models import (
    Media,
    MediaResponse,
    Post,
    PostResponse,
    PostCreateRequest,
    BookmarkCreateRequest,
    BookmarkToggleRequest,
    BookmarkFolderCreateRequest,
    BookmarkFolderUpdateRequest,
    Like,
    Comment,
    CommentResponse,
    CommentCreateRequest,
    BookmarkFolder,
    Bookmark,
    BookmarkResponse,
    BookmarkFolderBasic,
    PostSeoResponse,
    SeoAuthor,
)
from ..services.r2_service import r2_service
from ..notifications.services.notification_service import notification_service
from ..notifications.enums import NotificationType

router = APIRouter(tags=["posts"])


def build_post_response(
    post: Post, current_user: User, session: SessionDep
) -> PostResponse:
    likes_count = session.exec(
        select(func.count())
        .select_from(Like)
        .where((Like.post_id == post.id) & (Like.liked == True))
    ).one()

    comments_count = session.exec(
        select(func.count()).select_from(Comment).where(Comment.post_id == post.id)
    ).one()

    # Get bookmark status with folder details
    bookmarked_folders = session.exec(
        select(BookmarkFolder)
        .join(Bookmark)
        .where(
            Bookmark.post_id == post.id,
            BookmarkFolder.created_by == current_user.id
        )
    ).all()
    
    is_bookmarked = len(bookmarked_folders) > 0

    is_liked = session.exec(
        select(
            exists().where(
                (Like.post_id == post.id)
                & (Like.user_id == current_user.id)
                & (Like.liked == True)
            )
        )
    ).one()

    return PostResponse(
        id=post.id,
        caption=post.caption,
        user=UserBasic(
            id=post.user.id,
            name=post.user.name,
            username=post.user.username,
            profile_pic=post.user.profile_pic,
            bio=post.user.bio,
        ),
        created_at=post.created_at,
        updated_at=post.updated_at,
        media_files=[
            MediaResponse(
                id=media.id,
                file_path=media.file_path,
                file_type=media.file_type,
            )
            for media in post.media_files
        ],
        likes_count=likes_count,
        comments_count=comments_count,
        is_liked=is_liked,
        is_bookmarked=is_bookmarked,
        bookmarked_folders=[
            BookmarkFolderBasic(
                id=folder.id,
                name=folder.name,
                is_bookmarked=True
            )
            for folder in bookmarked_folders
        ]
    )


@router.get("/feed", response_model=list[PostResponse])
def get_feed(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    limit: int = Query(20, ge=1, le=100),
    before_id: str | None = Query(None, description="Return posts before this ID (cursor)"),
):
    # Block enforcement: exclude posts by users you blocked or who blocked you
    you_blocked = set(
        b.blocked_id for b in session.exec(select(Block).where(Block.blocker_id == current_user.id)).all()
    )
    blocked_you = set(
        b.blocker_id for b in session.exec(select(Block).where(Block.blocked_id == current_user.id)).all()
    )
    blocked_union = you_blocked.union(blocked_you)
    followed_ids = session.exec(
        select(Follow.following_id).where(Follow.follower_id == current_user.id)
    ).all()

    base_query = (
        select(Post)
        .order_by(Post.id.desc())
        .options(selectinload(Post.user), selectinload(Post.media_files))
    )
    # Exclude hidden posts
    base_query = base_query.where(Post.is_hidden == False)

    if followed_ids:
        base_query = base_query.where(Post.posted_by.in_(followed_ids))
    else:
        base_query = base_query.where(Post.posted_by != current_user.id)

    if blocked_union:
        base_query = base_query.where(~Post.posted_by.in_(blocked_union))

    if before_id is not None:
        base_query = base_query.where(Post.id < before_id)

    posts = session.exec(base_query.limit(limit)).all()

    return [build_post_response(post, current_user, session) for post in posts]


@router.get("/users/{user_id}/posts/", response_model=list[PostResponse])
def get_posts(
    user_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    limit: int = Query(20, ge=1, le=100),
    before_id: str | None = Query(None, description="Return posts before this ID (cursor)"),
):
    # If blocked either way with the target user, forbid
    if session.exec(
        select(Block).where(
            (Block.blocker_id == current_user.id) & (Block.blocked_id == user_id)
            | (Block.blocker_id == user_id) & (Block.blocked_id == current_user.id)
        )
    ).first():
        raise HTTPException(status_code=403, detail="You cannot view posts for this user")

    stmt = (
        select(Post)
        .where(Post.posted_by == user_id, Post.is_hidden == False)
        .order_by(Post.id.desc())
        .options(selectinload(Post.user), selectinload(Post.media_files))
    )

    if before_id is not None:
        stmt = stmt.where(Post.id < before_id)

    posts = session.exec(stmt.limit(limit)).all()

    return [build_post_response(post, current_user, session) for post in posts]


@router.get("/posts/{post_id}/", response_model=PostResponse)
def get_post(
    post_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    # Check if blocking exists
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if session.exec(
        select(Block).where(
            (Block.blocker_id == current_user.id) & (Block.blocked_id == post.posted_by)
            | (Block.blocker_id == post.posted_by) & (Block.blocked_id == current_user.id)
        )
    ).first():
        raise HTTPException(status_code=403, detail="You cannot interact with this user's posts")
    
    # Ensure relationships are loaded
    session.refresh(post, attribute_names=["user", "media_files"])
    
    return build_post_response(post, current_user, session)


@router.post("/posts/", response_model=PostResponse)
def create_post(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    request: PostCreateRequest,
):
    post = Post(caption=request.caption, posted_by=current_user.id)
    session.add(post)
    session.commit()
    session.refresh(post)

    for url in request.media_urls:
        file_type = "application/octet-stream"
        # If URL is from our R2 public base, pull ContentType from head_object
        file_key = r2_service.extract_file_key_from_url(url)
        if file_key:
            ct, _ = r2_service.get_content_type_and_length(file_key)
            if ct:
                file_type = ct

        session.add(Media(file_path=url, file_type=file_type, post_id=post.id))

    session.commit()
    session.refresh(post)

    # load user & media
    session.refresh(post, attribute_names=["user", "media_files"])

    return build_post_response(post, current_user, session)


@router.delete("/posts/{post_id}/")
def delete_post(
    post_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.posted_by != current_user.id:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this post")

    # Delete related entities to avoid foreign key constraint failures
    likes = session.exec(select(Like).where(Like.post_id == post_id)).all()
    for like in likes:
        session.delete(like)

    # Delete reply comments first (self-referential FK: reply_to -> comment.id)
    # then parent comments, to avoid FK violations during autoflush
    all_comments = session.exec(select(Comment).where(Comment.post_id == post_id)).all()
    reply_comments = [c for c in all_comments if c.reply_to is not None]
    parent_comments = [c for c in all_comments if c.reply_to is None]
    for comment in reply_comments:
        session.delete(comment)
    session.flush()  # flush reply deletes before deleting parents
    for comment in parent_comments:
        session.delete(comment)
    session.flush()  # flush all comment deletes before querying bookmarks

    bookmarks = session.exec(select(Bookmark).where(Bookmark.post_id == post_id)).all()
    for bookmark in bookmarks:
        session.delete(bookmark)

    # Media files - explicitly delete to handle DB-level FK constraints
    for media in post.media_files:
        session.delete(media)

    session.delete(post)
    session.commit()

    return {"detail": "Post deleted successfully"}


@router.post("/posts/{post_id}/like/", response_model=PostResponse)
async def like_post(
    post_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    like = session.exec(
        select(Like).where(Like.user_id == current_user.id, Like.post_id == post_id)
    ).first()
    
    should_notify = False
    if not like:
        like = Like(user_id=current_user.id, post_id=post_id, liked=True)
        should_notify = True
    else:
        if not like.liked:
            should_notify = True
        like.liked = True
    session.add(like)
    session.commit()
    session.refresh(post)
    session.refresh(post, attribute_names=["user", "media_files"])

    if should_notify and post.posted_by != current_user.id:
        try:
            post_image = None
            if post.media_files:
                post_image = post.media_files[0].file_path
                
            await notification_service.create_notification(
                notification_type=NotificationType.POST_LIKED,
                recipient_id=post.posted_by,
                sender_id=current_user.id,
                title="New Like",
                message=f"{current_user.username} liked your post",
                image_url=current_user.profile_pic,
                meta={
                    "post_image": post_image,
                    "sender_user": {
                        "id": current_user.id,
                        "username": current_user.username,
                        "name": current_user.name,
                        "profile_pic": current_user.profile_pic
                    }
                },
                redirect_to=f"/p/{post.id}",
                redirect_type="post",
                redirect_id=post.id,
                session=session,
                group_key=f"post_like_{post.id}",
                aggregation_message_template="{sender_name} and {count} others liked your post"
            )
        except Exception as e:
            # Don't fail the request if notification fails
            print(f"Error sending like notification: {e}")

    return build_post_response(post, current_user, session)


@router.get("/posts/{post_id}/likes/")
def get_post_likes(
    post_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    before_id: str | None = Query(None, description="Return likes before this Like ID (cursor)"),
    limit: int = Query(20, ge=1, le=100),
):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Block enforcement
    you_blocked = set(
        b.blocked_id for b in session.exec(select(Block).where(Block.blocker_id == current_user.id)).all()
    )
    blocked_you = set(
        b.blocker_id for b in session.exec(select(Block).where(Block.blocked_id == current_user.id)).all()
    )
    blocked_union = you_blocked.union(blocked_you)

    # Total count of non-blocked likers
    count_stmt = (
        select(func.count())
        .select_from(Like)
        .join(User, Like.user_id == User.id)
        .where(Like.post_id == post_id, Like.liked == True)
    )
    if blocked_union:
        count_stmt = count_stmt.where(~Like.user_id.in_(blocked_union))
    total = session.exec(count_stmt).one()

    # Cursor-based query
    stmt = (
        select(Like)
        .where(Like.post_id == post_id, Like.liked == True)
        .order_by(Like.id.desc())
    )
    if blocked_union:
        stmt = stmt.where(~Like.user_id.in_(blocked_union))
    if before_id:
        stmt = stmt.where(Like.id < before_id)
    likes = session.exec(stmt.limit(limit)).all()

    # Build user list with follow status
    following_ids = set(
        session.exec(
            select(Follow.following_id).where(Follow.follower_id == current_user.id)
        ).all()
    )

    users = []
    for like in likes:
        user = session.get(User, like.user_id)
        if user:
            users.append(UserBasic(
                id=user.id,
                username=user.username,
                name=user.name,
                profile_pic=user.profile_pic,
                bio=user.bio,
                is_following=user.id in following_ids,
            ))

    return {"items": users, "total": total, "has_more": len(likes) == limit}


@router.post("/posts/{post_id}/unlike/", response_model=PostResponse)
def unlike_post(
    post_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    like = session.exec(
        select(Like).where(Like.user_id == current_user.id, Like.post_id == post_id)
    ).first()
    if like:
        like.liked = False
    else:
        like = Like(user_id=current_user.id, post_id=post_id, liked=False)
        session.add(like)

    session.commit()
    session.refresh(post)
    session.refresh(post, attribute_names=["user", "media_files"])
    return build_post_response(post, current_user, session)


@router.get("/comments/{post_id}/")
def get_comments(
    post_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    before_id: str | None = Query(None, description="Return comments before this ID (cursor)"),
    limit: int = Query(20, ge=1, le=100),
):
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    # Exclude comments authored by users you blocked or who blocked you
    you_blocked = set(
        b.blocked_id for b in session.exec(select(Block).where(Block.blocker_id == current_user.id)).all()
    )
    blocked_you = set(
        b.blocker_id for b in session.exec(select(Block).where(Block.blocked_id == current_user.id)).all()
    )
    blocked_union = you_blocked.union(blocked_you)

    # Total count
    count_stmt = (
        select(func.count())
        .select_from(Comment)
        .where(Comment.post_id == post_id, Comment.is_hidden == False)
    )
    if blocked_union:
        count_stmt = count_stmt.where(~Comment.user_id.in_(blocked_union))
    total = session.exec(count_stmt).one()

    # Cursor-based query
    stmt = (
        select(Comment)
        .where(Comment.post_id == post_id, Comment.is_hidden == False)
        .options(selectinload(Comment.commented_by))
        .order_by(Comment.id.desc())
    )
    if blocked_union:
        stmt = stmt.where(~Comment.user_id.in_(blocked_union))
    if before_id:
        stmt = stmt.where(Comment.id < before_id)
    comments = session.exec(stmt.limit(limit)).all()

    items = [
        CommentResponse(
            id=c.id,
            comment=c.comment,
            post_id=c.post_id,
            reply_to=c.reply_to,
            created_at=c.created_at,
            updated_at=c.updated_at,
            commented_by=UserBasic(
                id=c.commented_by.id,
                username=c.commented_by.username,
                name=c.commented_by.name,
                profile_pic=c.commented_by.profile_pic,
                bio=c.commented_by.bio,
            ),
        )
        for c in comments
    ]

    return {"items": items, "total": total, "has_more": len(comments) == limit}


@router.post("/comments/{post_id}/")
async def post_comment(
    post_id: str,
    comment_data: CommentCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
) -> CommentResponse:
    post = session.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    # Forbid commenting if poster is blocked either direction
    if session.exec(
        select(Block).where(
            (Block.blocker_id == current_user.id) & (Block.blocked_id == post.posted_by)
            | (Block.blocker_id == post.posted_by) & (Block.blocked_id == current_user.id)
        )
    ).first():
        raise HTTPException(status_code=403, detail="You cannot interact with this user's posts")
    
    # Verify reply_to if provided
    if comment_data.reply_to:
        parent_comment = session.get(Comment, comment_data.reply_to)
        if not parent_comment:
            raise HTTPException(status_code=404, detail="Parent comment not found")
        if parent_comment.post_id != post_id:
            raise HTTPException(status_code=400, detail="Parent comment does not belong to this post")
            
    comment_obj = Comment(
        comment=comment_data.comment, 
        user_id=current_user.id, 
        post_id=post.id,
        reply_to=comment_data.reply_to
    )
    session.add(comment_obj)
    session.commit()
    session.refresh(comment_obj)

    # 1. Notify Post Owner (if not self)
    if post.posted_by != current_user.id:
        try:
            post_image = None
            session.refresh(post, attribute_names=["media_files"]) # Ensure media loaded
            if post.media_files:
                post_image = post.media_files[0].file_path
                
            await notification_service.create_notification(
                notification_type=NotificationType.POST_COMMENTED,
                recipient_id=post.posted_by,
                sender_id=current_user.id,
                title="New Comment",
                message=f"{current_user.username} commented on your post",
                image_url=current_user.profile_pic,
                meta={
                    "post_image": post_image,
                    "sender_user": {
                        "id": current_user.id,
                        "username": current_user.username,
                        "name": current_user.name,
                        "profile_pic": current_user.profile_pic
                    }
                },
                redirect_to=f"/p/{post.id}",
                redirect_type="post",
                redirect_id=post.id,
                session=session,
                group_key=f"post_comment_{post.id}",
                aggregation_message_template="{sender_name} and {count} others commented on your post"
            )
        except Exception as e:
            print(f"Error sending comment notification: {e}")
            
    # 2. Notify Parent Comment Owner (Reply)
    if comment_data.reply_to:
        # We need to fetch parent_comment again or use the one from verification check if we kept it
        # Since we didn't keep it in a variable above scope, let's fetch carefully or assume valid
        # Optimization: Fetch it up top
        parent_comment = session.get(Comment, comment_data.reply_to)
        if parent_comment and parent_comment.user_id != current_user.id:
            # Don't notify if I reply to my own comment
            # Also, if parent comment owner IS the post owner, they already got "Commented on your post"
            # Should they get "Replied to your comment" too?
            # Typically yes, distinct interactions.
            try:
                # Get post image for context
                post_image = None
                if post.media_files:
                    post_image = post.media_files[0].file_path

                await notification_service.create_notification(
                    notification_type=NotificationType.COMMENT_REPLY,
                    recipient_id=parent_comment.user_id,
                    sender_id=current_user.id,
                    title="New Reply",
                    message=f"{current_user.username} replied to your comment",
                    image_url=current_user.profile_pic,
                    meta={
                        "post_image": post_image,
                        "sender_user": {
                            "id": current_user.id,
                            "username": current_user.username,
                            "profile_pic": current_user.profile_pic
                        }
                    },
                    redirect_to=f"/p/{post.id}", # Deep link to comment?
                    redirect_type="post",
                    redirect_id=post.id,
                    session=session,
                    group_key=f"comment_reply_{parent_comment.id}",
                    aggregation_message_template="{sender_name} and {count} others replied to your comment"
                )
            except Exception as e:
                print(f"Error sending reply notification: {e}")

    return CommentResponse(
        id=comment_obj.id,
        comment=comment_obj.comment,
        post_id=comment_obj.post_id,
        reply_to=comment_obj.reply_to,
        created_at=comment_obj.created_at,
        updated_at=comment_obj.updated_at,
        commented_by=UserBasic(
            id=current_user.id,
            username=current_user.username,
            name=current_user.name,
            profile_pic=current_user.profile_pic,
            bio=current_user.bio,
        ),
    )


@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    statement = select(Comment).where(
        Comment.id == comment_id, Comment.user_id == current_user.id
    )
    comment = session.exec(statement).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    session.delete(comment)
    session.commit()
    return {"detail": "comment is deleted"}


@router.get("/bookmark-folders/")
async def get_bookmark_folders(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    total = session.exec(
        select(func.count())
        .select_from(BookmarkFolder)
        .where(BookmarkFolder.created_by == current_user.id)
    ).one()
    items = session.exec(
        select(BookmarkFolder)
        .where(BookmarkFolder.created_by == current_user.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {"items": items, "total": total, "has_more": offset + limit < total}


@router.post("/bookmark-folders/")
async def create_bookmark_folder(
    request: BookmarkFolderCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    bookmark_folder = BookmarkFolder(name=request.name, created_by=current_user.id)
    session.add(bookmark_folder)
    session.commit()
    session.refresh(bookmark_folder)
    return bookmark_folder


@router.put("/bookmark-folders/{folder_id}")
async def update_bookmark_folder(
    folder_id: str,
    request: BookmarkFolderUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Update a bookmark folder name"""
    folder = session.exec(
        select(BookmarkFolder).where(
            BookmarkFolder.id == folder_id, BookmarkFolder.created_by == current_user.id
        )
    ).first()
    
    if not folder:
        raise HTTPException(
            status_code=404, detail="Folder doesn't exist or you don't have permission"
        )
    
    folder.name = request.name
    folder.updated_at = datetime.now()
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return folder


@router.delete("/bookmark-folders/{folder_id}")
async def delete_bookmark_folder(
    folder_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    folder = session.exec(
        select(BookmarkFolder).where(
            BookmarkFolder.id == folder_id, BookmarkFolder.created_by == current_user.id
        )
    ).first()
    if not folder:
        raise HTTPException(
            status_code=404, detail="Folder doesn't exist or you don't have permission"
        )
    session.delete(folder)
    session.commit()
    return {"detail": "Folder deleted successfully"}


@router.get("/posts/{post_id}/bookmarks/")
async def get_post_bookmarks(
    post_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    """Get all bookmarks for a specific post by the current user"""

    # Check if post exists
    post = session.exec(select(Post).where(Post.id == post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Total count
    total = session.exec(
        select(func.count())
        .select_from(Bookmark)
        .join(BookmarkFolder)
        .where(Bookmark.post_id == post_id, BookmarkFolder.created_by == current_user.id)
    ).one()

    # Get bookmarks for this post by current user
    bookmarks = session.exec(
        select(Bookmark)
        .join(BookmarkFolder)
        .where(
            Bookmark.post_id == post_id,
            BookmarkFolder.created_by == current_user.id
        )
        .options(
            selectinload(Bookmark.post).selectinload(Post.user),
            selectinload(Bookmark.post).selectinload(Post.media_files),
        )
        .offset(offset)
        .limit(limit)
    ).all()

    items = [
        BookmarkResponse(
            id=b.id,
            post=build_post_response(b.post, current_user, session),
            bookmark_folder_id=b.bookmark_folder_id,
            created_at=b.created_at,
        )
        for b in bookmarks
    ]
    return {"items": items, "total": total, "has_more": offset + limit < total}


@router.get("/posts/{post_id}/bookmark-status/")
async def get_post_bookmark_status(
    post_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Get which folders a post is bookmarked in for the current user"""
    
    # Check if post exists
    post = session.exec(select(Post).where(Post.id == post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Get all user's bookmark folders
    user_folders = session.exec(
        select(BookmarkFolder).where(BookmarkFolder.created_by == current_user.id)
    ).all()
    
    # Get folders where this post is bookmarked
    bookmarked_folders = session.exec(
        select(BookmarkFolder)
        .join(Bookmark)
        .where(
            Bookmark.post_id == post_id,
            BookmarkFolder.created_by == current_user.id
        )
    ).all()
    
    bookmarked_folder_ids = {folder.id for folder in bookmarked_folders}
    
    return {
        "post_id": post_id,
        "is_bookmarked": len(bookmarked_folder_ids) > 0,
        "bookmarked_in_folders": [
            {
                "id": folder.id,
                "name": folder.name,
                "is_bookmarked": folder.id in bookmarked_folder_ids
            }
            for folder in user_folders
        ]
    }


@router.post("/posts/{post_id}/bookmarks/")
async def bookmark_post(
    post_id: str,
    request: BookmarkCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Bookmark a post in a specific folder"""
    
    # Verify the post_id in URL matches the request
    if post_id != request.post_id:
        raise HTTPException(
            status_code=400, detail="Post ID in URL must match post ID in request body"
        )
    
    # Check if folder exists and belongs to user
    bookmark_folder = session.exec(
        select(BookmarkFolder).where(
            BookmarkFolder.id == request.folder_id, BookmarkFolder.created_by == current_user.id
        )
    ).first()
    
    if not bookmark_folder:
        raise HTTPException(
            status_code=404, detail="Folder doesn't exist or you don't have permission"
        )
    
    # Check if post exists
    post = session.exec(select(Post).where(Post.id == post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Check for duplicate bookmark
    existing_bookmark = session.exec(
        select(Bookmark).where(
            Bookmark.post_id == post_id,
            Bookmark.bookmark_folder_id == request.folder_id
        )
    ).first()
    
    if existing_bookmark:
        raise HTTPException(
            status_code=400, detail="Post is already bookmarked in this folder"
        )
    
    bookmark = Bookmark(post_id=post_id, bookmark_folder_id=request.folder_id)
    session.add(bookmark)
    session.commit()
    session.refresh(bookmark)
    return bookmark


@router.delete("/posts/{post_id}/bookmarks/")
async def unbookmark_post(
    post_id: str,
    request: BookmarkCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Remove bookmark for a post from a specific folder"""
    
    # Verify the post_id in URL matches the request
    if post_id != request.post_id:
        raise HTTPException(
            status_code=400, detail="Post ID in URL must match post ID in request body"
        )
    
    # Find the bookmark
    bookmark = session.exec(
        select(Bookmark)
        .join(BookmarkFolder)
        .where(
            Bookmark.post_id == post_id,
            Bookmark.bookmark_folder_id == request.folder_id,
            BookmarkFolder.created_by == current_user.id
        )
    ).first()
    
    if not bookmark:
        raise HTTPException(
            status_code=404, detail="Bookmark not found or you don't have permission"
        )
    
    session.delete(bookmark)
    session.commit()
    return {"detail": "Bookmark removed successfully"}


@router.get("/bookmarks/")
async def get_all_bookmarks(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    """Get all bookmarks for the current user"""
    total = session.exec(
        select(func.count())
        .select_from(Bookmark)
        .join(BookmarkFolder)
        .where(BookmarkFolder.created_by == current_user.id)
    ).one()

    bookmarks = session.exec(
        select(Bookmark)
        .join(BookmarkFolder)
        .where(BookmarkFolder.created_by == current_user.id)
        .options(
            selectinload(Bookmark.post).selectinload(Post.user),
            selectinload(Bookmark.post).selectinload(Post.media_files),
        )
        .offset(offset)
        .limit(limit)
    ).all()

    items = [
        BookmarkResponse(
            id=b.id,
            post=build_post_response(b.post, current_user, session),
            bookmark_folder_id=b.bookmark_folder_id,
            created_at=b.created_at,
        )
        for b in bookmarks
    ]
    return {"items": items, "total": total, "has_more": offset + limit < total}


@router.get("/bookmarks/{bookmark_id}")
async def get_bookmark(
    bookmark_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Get a specific bookmark by ID"""
    bookmark = session.exec(
        select(Bookmark)
        .join(BookmarkFolder)
        .where(
            Bookmark.id == bookmark_id,
            BookmarkFolder.created_by == current_user.id
        )
        .options(
            selectinload(Bookmark.post).selectinload(Post.user),
            selectinload(Bookmark.post).selectinload(Post.media_files),
        )
    ).first()
    
    if not bookmark:
        raise HTTPException(
            status_code=404, detail="Bookmark not found or you don't have permission"
        )
    
    return BookmarkResponse(
        id=bookmark.id,
        post=build_post_response(bookmark.post, current_user, session),
        bookmark_folder_id=bookmark.bookmark_folder_id,
        created_at=bookmark.created_at,
    )


@router.get("/bookmark-folders/{folder_id}/bookmarks/")
async def get_bookmarks_in_folder(
    folder_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    """Get all bookmarks in a specific folder"""
    folder = session.exec(
        select(BookmarkFolder).where(
            BookmarkFolder.id == folder_id, BookmarkFolder.created_by == current_user.id
        )
    ).first()

    if not folder:
        raise HTTPException(
            status_code=404, detail="Folder doesn't exist or you don't have permission"
        )

    total = session.exec(
        select(func.count())
        .select_from(Bookmark)
        .where(Bookmark.bookmark_folder_id == folder_id)
    ).one()

    bookmarks = session.exec(
        select(Bookmark)
        .where(Bookmark.bookmark_folder_id == folder_id)
        .options(
            selectinload(Bookmark.post).selectinload(Post.user),
            selectinload(Bookmark.post).selectinload(Post.media_files),
        )
        .offset(offset)
        .limit(limit)
    ).all()

    items = [
        BookmarkResponse(
            id=b.id,
            post=build_post_response(b.post, current_user, session),
            bookmark_folder_id=b.bookmark_folder_id,
            created_at=b.created_at,
        )
        for b in bookmarks
    ]
    return {"items": items, "total": total, "has_more": offset + limit < total}


@router.post("/bookmarks/")
async def create_bookmark(
    request: BookmarkCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Create a new bookmark"""
    # Check if folder exists and belongs to user
    bookmark_folder = session.exec(
        select(BookmarkFolder).where(
            BookmarkFolder.id == request.folder_id, BookmarkFolder.created_by == current_user.id
        )
    ).first()
    
    if not bookmark_folder:
        raise HTTPException(
            status_code=404, detail="Folder doesn't exist or you don't have permission"
        )
    
    # Check if post exists
    post = session.exec(select(Post).where(Post.id == request.post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Check for duplicate bookmark
    existing_bookmark = session.exec(
        select(Bookmark).where(
            Bookmark.post_id == request.post_id,
            Bookmark.bookmark_folder_id == request.folder_id
        )
    ).first()
    
    if existing_bookmark:
        raise HTTPException(
            status_code=400, detail="Post is already bookmarked in this folder"
        )
    
    bookmark = Bookmark(post_id=request.post_id, bookmark_folder_id=request.folder_id)
    session.add(bookmark)
    session.commit()
    session.refresh(bookmark)
    return bookmark


@router.post("/bookmarks/toggle")
async def toggle_bookmark(
    request: BookmarkToggleRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Toggle bookmark status for a post in a specific folder"""
    
    # Verify folder belongs to user
    folder = session.exec(
        select(BookmarkFolder).where(
            BookmarkFolder.id == request.folder_id,
            BookmarkFolder.created_by == current_user.id
        )
    ).first()
    
    if not folder:
        raise HTTPException(
            status_code=404, detail="Folder doesn't exist or you don't have permission"
        )
    
    # Check if post exists
    post = session.exec(select(Post).where(Post.id == request.post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Check if bookmark already exists
    existing_bookmark = session.exec(
        select(Bookmark).where(
            Bookmark.post_id == request.post_id,
            Bookmark.bookmark_folder_id == request.folder_id
        )
    ).first()
    
    if existing_bookmark:
        # Remove bookmark
        session.delete(existing_bookmark)
        session.commit()
        return {"action": "removed", "bookmark_id": existing_bookmark.id}
    else:
        # Add bookmark
        bookmark = Bookmark(post_id=request.post_id, bookmark_folder_id=request.folder_id)
        session.add(bookmark)
        session.commit()
        session.refresh(bookmark)
        return {"action": "added", "bookmark_id": bookmark.id}


@router.delete("/bookmarks/{bookmark_id}")
async def delete_bookmark(
    bookmark_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Delete a specific bookmark by ID"""
    bookmark = session.exec(
        select(Bookmark)
        .join(BookmarkFolder)
        .where(
            Bookmark.id == bookmark_id,
            BookmarkFolder.created_by == current_user.id
        )
    ).first()
    
    if not bookmark:
        raise HTTPException(
            status_code=404, detail="Bookmark not found or you don't have permission"
        )
    
    session.delete(bookmark)
    session.commit()
    return {"detail": "Bookmark deleted successfully"}


@router.get("/posts/{id}/seo")
async def get_post_seo(id: str, session: SessionDep):
    post = session.exec(
        select(Post)
        .where(Post.id == id)
        .options(selectinload(Post.user), selectinload(Post.media_files))
    ).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return PostSeoResponse(
        id=post.id,
        title=post.caption if post.caption else "Meeloop Post",
        meta_description=(
            post.caption if post.caption else "Check out this post on Meeloop"
        ),
        og_images=[media.file_path for media in post.media_files] if post.media_files else None,
        image_alt=post.caption if post.caption else "Meeloop Post",
        keywords="meeloop, post, social media",
        date_published=post.created_at,
        author=SeoAuthor(
            username=post.user.username,
            name=post.user.name,
            url=f"https://meeloop.com/u/{post.user.username}",
            image=post.user.profile_pic,
            description=post.user.bio,
        ),
    )
