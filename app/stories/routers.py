from fastapi.exceptions import HTTPException
from sqlmodel import select, func
from sqlalchemy.orm import selectinload
from typing import Annotated
from fastapi import APIRouter, Depends, Form, Query
from ..dependencies import SessionDep
from ..users.routers import get_current_active_user
from ..users.models import User, Follow, Block
from .models import Story, StoryMedia, StoryOut, StoryView, StoryViewerOut
from ..services.r2_service import r2_service

from datetime import datetime, timedelta
from collections import defaultdict


router = APIRouter(tags=["story"])


@router.post("/story/upload")
async def upload_story(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    text: str | None = Form(None),
    media_url: str = Form(...),
):
    story = Story(
        user_id=current_user.id,
        text=text,
    )
    session.add(story)
    session.commit()
    session.refresh(story)

    # Debug: Check if story was created successfully
    if story.id is None:
        raise HTTPException(
            status_code=500, detail="Failed to create story - story.id is None"
        )

    media_type = "image"
    file_key = r2_service.extract_file_key_from_url(media_url)
    if file_key:
        ct, _ = r2_service.get_content_type_and_length(file_key)
        if ct and ct.startswith("video/"):
            media_type = "video"

    try:
        media_item = StoryMedia(
            story_id=story.id,
            media_url=media_url,
            media_type=media_type,
        )
        session.add(media_item)
        session.commit()
        return {"message": "Story uploaded successfully", "story_id": story.id}
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to create story media: {str(e)}"
        )


@router.delete("/story/{story_id}")
async def delete_story(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    story_id: str,
):
    story = session.exec(
        select(Story).where(Story.id == story_id)
    ).first()

    if (
        not story
        or (datetime.now() - story.created_at) > timedelta(hours=24)
        or story.user_id != current_user.id
    ):
        raise HTTPException(status_code=404, detail="Story not found")
    
    # Cascade delete will automatically handle media_file and views
    session.delete(story)
    session.commit()
    return {"message": "Story deleted successfully"}


@router.get("/story/")
async def get_user_stories(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    following_ids = session.exec(
        select(Follow.following_id).where(Follow.follower_id == current_user.id)
    ).all()
    following_ids.append(current_user.id)

    # Remove blocked relationships from story authors
    you_blocked = set(
        b.blocked_id
        for b in session.exec(
            select(Block).where(Block.blocker_id == current_user.id)
        ).all()
    )
    blocked_you = set(
        b.blocker_id
        for b in session.exec(
            select(Block).where(Block.blocked_id == current_user.id)
        ).all()
    )
    blocked_union = you_blocked.union(blocked_you)
    if blocked_union:
        following_ids = [uid for uid in following_ids if uid not in blocked_union]

    users = session.exec(select(User).where(User.id.in_(following_ids))).all()
    user_map = {user.id: user for user in users}

    time_threshold = datetime.now() - timedelta(hours=24)
    stories_stmt = (
        select(Story)
        .where(Story.created_at >= time_threshold)
        .options(selectinload(Story.media_file), selectinload(Story.views))
        .order_by(Story.created_at.desc())
    )
    if following_ids:
        stories_stmt = stories_stmt.where(Story.user_id.in_(following_ids))
    stories = session.exec(stories_stmt).all()

    stories_by_user = defaultdict(list)
    for story in stories:
        stories_by_user[story.user_id].append(story)

    result = []
    for user_id, user_stories in stories_by_user.items():
        user = user_map.get(user_id)
        if user:
            # Convert stories to proper response format
            story_responses = []
            for story in user_stories:
                story_data = {
                    "id": story.id,
                    "text": story.text,
                    "created_at": story.created_at,
                    "user_id": story.user_id,
                    "expires_on": story.expires_on,
                    "updated_at": story.updated_at,
                }

                # Only include view_count if the story belongs to the current user
                if story.user_id == current_user.id:
                    story_data["view_count"] = len(story.views) if story.views else 0

                # Add media if it exists
                if story.media_file:
                    story_data["media_file"] = {
                        "id": story.media_file.id,
                        "media_url": story.media_file.media_url,
                        "media_type": story.media_file.media_type,
                    }
                else:
                    story_data["media_file"] = None

                story_responses.append(story_data)

            result.append(
                {
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "name": getattr(user, "name", "") or "",
                        "profile_pic": getattr(user, "profile_pic", None),
                    },
                    "stories": story_responses,
                }
            )

    return result


@router.post("/story/{story_id}/view")
async def record_story_view(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    story_id: str,
):
    """Record that the current user viewed a story."""
    story = session.exec(select(Story).where(Story.id == story_id)).first()
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")

    # Check if story is expired (older than 24 hours)
    if (datetime.now() - story.created_at) > timedelta(hours=24):
        raise HTTPException(status_code=404, detail="Story has expired")

    # Check if user has access to this story
    # User can view their own stories, or stories from users they follow (and not blocked)
    if story.user_id != current_user.id:
        # Check if user follows the story author
        is_following = session.exec(
            select(Follow).where(
                Follow.follower_id == current_user.id,
                Follow.following_id == story.user_id,
            )
        ).first()

        if not is_following:
            raise HTTPException(
                status_code=403, detail="You don't have access to this story"
            )

        # Check if there's a blocking relationship
        is_blocked = session.exec(
            select(Block).where(
                (
                    (Block.blocker_id == current_user.id)
                    & (Block.blocked_id == story.user_id)
                )
                | (
                    (Block.blocker_id == story.user_id)
                    & (Block.blocked_id == current_user.id)
                )
            )
        ).first()

        if is_blocked:
            raise HTTPException(
                status_code=403, detail="You don't have access to this story"
            )

    # Check if view already exists for this user and story
    existing_view = session.exec(
        select(StoryView).where(
            StoryView.story_id == story_id, StoryView.viewer_id == current_user.id
        )
    ).first()

    if existing_view:
        # Update viewed_at timestamp
        existing_view.viewed_at = datetime.now()
        session.add(existing_view)
        session.commit()
        return {"message": "Story view updated"}

    # Create new view record
    view = StoryView(story_id=story_id, viewer_id=current_user.id)
    session.add(view)
    session.commit()
    return {"message": "Story view recorded"}


@router.get("/story/{story_id}/viewers")
async def get_story_viewers(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    story_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Get list of users who viewed this story. Only accessible by story owner."""
    story = session.exec(select(Story).where(Story.id == story_id)).first()

    if not story:
        raise HTTPException(status_code=404, detail="Story not found")

    # Only the story owner can see who viewed their story
    if story.user_id != current_user.id:
        raise HTTPException(
            status_code=403, detail="You can only view viewers of your own stories"
        )

    # Check if story is expired
    if (datetime.now() - story.created_at) > timedelta(hours=24):
        raise HTTPException(status_code=404, detail="Story has expired")

    # Total count
    total = session.exec(
        select(func.count())
        .select_from(StoryView)
        .where(StoryView.story_id == story_id)
    ).one()

    # Get views with user information (paginated)
    views = session.exec(
        select(StoryView, User)
        .join(User, StoryView.viewer_id == User.id)
        .where(StoryView.story_id == story_id)
        .order_by(StoryView.viewed_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    viewers = [
        StoryViewerOut(
            viewer_id=view.viewer_id,
            username=user.username,
            name=getattr(user, "name", "") or "",
            profile_pic=getattr(user, "profile_pic", None),
            viewed_at=view.viewed_at,
        )
        for view, user in views
    ]

    return {
        "story_id": story_id,
        "viewer_count": total,
        "viewers": viewers,
        "has_more": offset + limit < total,
    }
