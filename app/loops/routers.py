from typing_extensions import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select, func
from sqlalchemy import or_, and_, nullslast
from app.loops.models import (
    LoopFriend,
    LoopRequest,
    LoopChat,
    LoopMessage,
    LoopReaction,
    LoopRequestStatus,
    RandomSession,
    LoopProfile,
    LoopProfilePhoto,
    LoopMessageType,
)
from app.loops.schemas import (
    LoopProfilePublic,
    LoopProfilePhotoResponse,
    LoopProfileSetupRequest,
    LoopProfilePhotoAdd,
    LoopProfilePhotoReorder,
    LoopRequestWithProfiles,
    LoopMessageCreate,
    LoopMessageResponse,
    LoopReactionCreate,
    LoopChatResponse,
    LoopLocationUpdate,
)
from typing import Literal
from app.database import get_session
from app.users.models import User
from app.users.routers import get_current_active_user
from app.sockets.socketio_server import sio
import asyncio
from typing import Optional

router = APIRouter(prefix="/loops", tags=["loops"])

MAX_PROFILE_PHOTOS = 8


def build_profile_public(profile: LoopProfile, session: Session) -> LoopProfilePublic:
    """Build a LoopProfilePublic with photos included."""
    photos = session.exec(
        select(LoopProfilePhoto)
        .where(LoopProfilePhoto.loop_profile_id == profile.id)
        .order_by(LoopProfilePhoto.order)
    ).all()
    return LoopProfilePublic(
        id=profile.id,
        displayname=profile.displayname,
        bio=profile.bio,
        profile_pic=profile.profile_pic,
        date_of_birth=profile.date_of_birth,
        gender=profile.gender,
        location_name=profile.location_name if profile.location_sharing_enabled else None,
        location_sharing_enabled=profile.location_sharing_enabled,
        photos=[
            LoopProfilePhotoResponse(
                id=p.id, photo_url=p.photo_url, order=p.order, is_primary=p.is_primary
            )
            for p in photos
        ],
    )


# Helper function to emit messages via WebSocket
async def emit_loop_message_async(payload, room: str):
    """Async function to emit socket messages"""
    try:
        await sio.emit("message:new", payload, room=room)
        print(f"✅ Message emitted to room {room}")
    except Exception as e:
        print(f"⚠️ Error emitting message: {e}")


def emit_loop_message_sync(payload, room: str):
    """Sync wrapper to emit socket messages"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(emit_loop_message_async(payload, room))
        else:
            asyncio.run(emit_loop_message_async(payload, room))
    except RuntimeError:
        asyncio.run(emit_loop_message_async(payload, room))


def save_loop_message(
    chat_id: str,
    sender_profile_id: str,
    content: str,
    message_type: LoopMessageType = LoopMessageType.TEXT,
    media_url: str | None = None,
    session: Session = None,
):
    """Helper to save loop message"""
    if not content and not media_url:
        raise HTTPException(status_code=400, detail="Message must have content or media")
    
    message = LoopMessage(
        chat_id=chat_id,
        sender_profile_id=sender_profile_id,
        content=content or "",
        message_type=message_type,
        media_url=media_url,
    )
    session.add(message)
    
    # Update chat metadata
    chat = session.get(LoopChat, chat_id)
    if chat:
        chat.last_message_at = message.created_at
        chat.last_message_content = "Media" if not content else content
        session.add(chat)

    session.commit()
    session.refresh(message)
    return message


def build_loop_message_response(message: LoopMessage) -> dict:
    """Build response for loop message"""
    return {
        "id": message.id,
        "chat_id": message.chat_id,
        "sender_profile_id": message.sender_profile_id,
        "content": message.content,
        "message_type": message.message_type,
        "media_url": message.media_url,
        "created_at": message.created_at,
        "reactions": [
            {"id": r.id, "emoji": r.emoji, "profile_id": r.profile_id}
            for r in message.reactions
        ],
    }


# Dependency to get the current user's loop profile
def get_current_loop_profile(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Session = Depends(get_session),
):
    profile = session.exec(
        select(LoopProfile).where(LoopProfile.user_id == current_user.id)
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Your loop profile not found")
    return profile


from app.users.models import User


# ── Profile Setup ──
@router.post("/profile/setup", response_model=LoopProfilePublic)
def setup_loop_profile(
    data: LoopProfileSetupRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Session = Depends(get_session),
):
    """Create a new loop profile with optional photos (up to 8)."""
    existing = session.exec(
        select(LoopProfile).where(LoopProfile.user_id == current_user.id)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Loop profile already exists")

    profile = LoopProfile(
        user_id=current_user.id,
        displayname=data.displayname,
        bio=data.bio,
        gender=data.gender,
    )
    if data.date_of_birth:
        from datetime import datetime as dt
        try:
            profile.date_of_birth = dt.fromisoformat(data.date_of_birth)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_of_birth format")

    session.add(profile)
    session.commit()
    session.refresh(profile)

    # Add photos
    for i, url in enumerate(data.photo_urls[:MAX_PROFILE_PHOTOS]):
        photo = LoopProfilePhoto(
            loop_profile_id=profile.id,
            photo_url=url,
            order=i,
            is_primary=(i == 0),
        )
        session.add(photo)

    if data.photo_urls:
        profile.profile_pic = data.photo_urls[0]
        session.add(profile)

    session.commit()
    session.refresh(profile)

    # Enable loops on user
    current_user.is_loop_enabled = True
    session.add(current_user)
    session.commit()

    return build_profile_public(profile, session)


# ── Profile Photos Management ──
@router.post("/profile/photos", response_model=LoopProfilePhotoResponse)
def add_profile_photo(
    data: LoopProfilePhotoAdd,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    """Add a photo to your loop profile (max 8)."""
    existing_count = len(
        session.exec(
            select(LoopProfilePhoto).where(
                LoopProfilePhoto.loop_profile_id == my_profile.id
            )
        ).all()
    )
    if existing_count >= MAX_PROFILE_PHOTOS:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_PROFILE_PHOTOS} photos allowed")

    photo = LoopProfilePhoto(
        loop_profile_id=my_profile.id,
        photo_url=data.photo_url,
        order=data.order if data.order else existing_count,
        is_primary=data.is_primary,
    )

    # If this is primary, unset other primaries
    if data.is_primary:
        existing_photos = session.exec(
            select(LoopProfilePhoto).where(
                LoopProfilePhoto.loop_profile_id == my_profile.id,
                LoopProfilePhoto.is_primary == True,
            )
        ).all()
        for p in existing_photos:
            p.is_primary = False
            session.add(p)
        my_profile.profile_pic = data.photo_url
        session.add(my_profile)

    session.add(photo)
    session.commit()
    session.refresh(photo)
    return LoopProfilePhotoResponse(
        id=photo.id, photo_url=photo.photo_url, order=photo.order, is_primary=photo.is_primary
    )


@router.delete("/profile/photos/{photo_id}")
def delete_profile_photo(
    photo_id: str,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    """Delete a photo from your loop profile."""
    photo = session.exec(
        select(LoopProfilePhoto).where(
            LoopProfilePhoto.id == photo_id,
            LoopProfilePhoto.loop_profile_id == my_profile.id,
        )
    ).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    was_primary = photo.is_primary
    session.delete(photo)
    session.commit()

    # If we deleted the primary, set the first remaining as primary
    if was_primary:
        remaining = session.exec(
            select(LoopProfilePhoto)
            .where(LoopProfilePhoto.loop_profile_id == my_profile.id)
            .order_by(LoopProfilePhoto.order)
        ).first()
        if remaining:
            remaining.is_primary = True
            my_profile.profile_pic = remaining.photo_url
            session.add(remaining)
            session.add(my_profile)
            session.commit()
        else:
            my_profile.profile_pic = None
            session.add(my_profile)
            session.commit()

    return {"detail": "Photo deleted"}


@router.put("/profile/photos/reorder")
def reorder_profile_photos(
    data: LoopProfilePhotoReorder,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    """Reorder photos. Pass photo IDs in desired order."""
    photos = session.exec(
        select(LoopProfilePhoto).where(
            LoopProfilePhoto.loop_profile_id == my_profile.id
        )
    ).all()
    photo_map = {p.id: p for p in photos}

    for i, photo_id in enumerate(data.photo_ids):
        if photo_id in photo_map:
            photo_map[photo_id].order = i
            photo_map[photo_id].is_primary = (i == 0)
            session.add(photo_map[photo_id])

    # Update profile_pic to the first photo
    if data.photo_ids and data.photo_ids[0] in photo_map:
        my_profile.profile_pic = photo_map[data.photo_ids[0]].photo_url
        session.add(my_profile)

    session.commit()
    return {"detail": "Photos reordered"}


@router.put("/profile/photos/{photo_id}/set-primary")
def set_primary_photo(
    photo_id: str,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    """Set a photo as the primary profile photo."""
    photo = session.exec(
        select(LoopProfilePhoto).where(
            LoopProfilePhoto.id == photo_id,
            LoopProfilePhoto.loop_profile_id == my_profile.id,
        )
    ).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # Unset all existing primary photos
    all_photos = session.exec(
        select(LoopProfilePhoto).where(
            LoopProfilePhoto.loop_profile_id == my_profile.id
        )
    ).all()
    for p in all_photos:
        p.is_primary = (p.id == photo_id)
        session.add(p)

    my_profile.profile_pic = photo.photo_url
    session.add(my_profile)
    session.commit()
    return {"detail": "Primary photo updated"}


# ── Location management ──
@router.put("/profile/me/location")
def update_loop_location(
    data: LoopLocationUpdate,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    """Update the location of your loop profile."""
    from geoalchemy2.elements import WKTElement
    from datetime import datetime

    my_profile.location = WKTElement(f"POINT({data.longitude} {data.latitude})", srid=4326)
    my_profile.location_name = data.location_name
    my_profile.location_updated_at = datetime.now()
    session.add(my_profile)
    session.commit()
    return {"detail": "Location updated"}


@router.delete("/profile/me/location")
def clear_loop_location(
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    """Clear your loop profile location."""
    my_profile.location = None
    my_profile.location_name = None
    my_profile.location_updated_at = None
    session.add(my_profile)
    session.commit()
    return {"detail": "Location cleared"}


# ── Nearby user discovery (PostGIS proximity) ──
@router.get("/nearby", response_model=list[LoopProfilePublic])
def get_nearby_users(
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
    skip: int = 0,
    limit: int = 20,
    radius_km: float = Query(default=50.0, ge=1, le=500, description="Search radius in km"),
):
    if my_profile.location is None:
        raise HTTPException(status_code=400, detail="Set your location first to discover nearby profiles")

    from sqlalchemy import select as sa_select
    from geoalchemy2.functions import ST_DWithin, ST_Distance

    radius_meters = radius_km * 1000
    distance_col = ST_Distance(LoopProfile.location, my_profile.location).label("distance_meters")

    stmt = (
        sa_select(LoopProfile, distance_col)
        .join(User, User.id == LoopProfile.user_id)
        .where(LoopProfile.is_suspended == False)
        .where(User.is_loop_enabled == True)
        .where(LoopProfile.id != my_profile.id)
        .where(LoopProfile.location.isnot(None))
        .where(LoopProfile.location_sharing_enabled == True)
        .where(ST_DWithin(LoopProfile.location, my_profile.location, radius_meters))
        .order_by(distance_col)
        .offset(skip)
        .limit(limit)
    )

    results = session.execute(stmt).all()

    profiles = []
    for profile, distance in results:
        pub = build_profile_public(profile, session)
        pub.distance_meters = round(distance, 1)
        profiles.append(pub)
    return profiles


@router.post("/request")
def send_loop_request(
    receiver_id: str,
    requester_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    if requester_profile.id == receiver_id:
        raise HTTPException(status_code=400, detail="Cannot send request to yourself")
    statement = select(LoopRequest).where(
        LoopRequest.receiver_profile_id == receiver_id,
        LoopRequest.requester_profile_id == requester_profile.id,
    )
    if session.exec(statement).first():
        raise HTTPException(status_code=400, detail="Request already sent")
    loop_request = LoopRequest(
        receiver_profile_id=receiver_id, requester_profile_id=requester_profile.id
    )
    session.add(loop_request)
    session.commit()
    session.refresh(loop_request)
    return loop_request


# Accept and reject loop requests
@router.post("/request/{request_id}")
def handle_loop_request(
    request_id: str,
    action: Literal["accepted", "rejected"],
    receiver_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    statement = select(LoopRequest).where(
        LoopRequest.id == request_id,
        LoopRequest.receiver_profile_id == receiver_profile.id,
    )
    loop_request = session.exec(statement).first()
    if not loop_request:
        raise HTTPException(status_code=404, detail="Request not found")
    if action not in ["accepted", "rejected"]:
        raise HTTPException(status_code=400, detail="Invalid action")
    loop_request.status = action
    session.commit()
    # If accepted, create bidirectional LoopFriend records
    if action == "accepted":
        existing_receiver_friend = session.exec(
            select(LoopFriend).where(
                LoopFriend.loop_profile_id == receiver_profile.id,
                LoopFriend.friend_profile_id == loop_request.requester_profile_id,
            )
        ).first()
        if not existing_receiver_friend:
            session.add(LoopFriend(
                loop_profile_id=receiver_profile.id,
                friend_profile_id=loop_request.requester_profile_id,
            ))

        existing_requester_friend = session.exec(
            select(LoopFriend).where(
                LoopFriend.loop_profile_id == loop_request.requester_profile_id,
                LoopFriend.friend_profile_id == receiver_profile.id,
            )
        ).first()
        if not existing_requester_friend:
            session.add(LoopFriend(
                loop_profile_id=loop_request.requester_profile_id,
                friend_profile_id=receiver_profile.id,
            ))

        session.commit()
    return loop_request


@router.get("/requests")
def get_my_loop_requests(
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    total = session.exec(
        select(func.count())
        .select_from(LoopRequest)
        .where(LoopRequest.receiver_profile_id == my_profile.id, LoopRequest.status == "pending")
    ).one()

    requests = session.exec(
        select(LoopRequest)
        .where(
            LoopRequest.receiver_profile_id == my_profile.id,
            LoopRequest.status == "pending",
        )
        .order_by(LoopRequest.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    # Fetch profile data for each request
    items = []
    for request in requests:
        requester_profile = session.exec(
            select(LoopProfile).where(LoopProfile.id == request.requester_profile_id)
        ).first()
        receiver_profile = session.exec(
            select(LoopProfile).where(LoopProfile.id == request.receiver_profile_id)
        ).first()

        if requester_profile and receiver_profile:
            items.append(LoopRequestWithProfiles(
                id=request.id,
                status=request.status,
                created_at=request.created_at,
                requester_profile=LoopProfilePublic(
                    id=requester_profile.id,
                    displayname=requester_profile.displayname,
                    bio=requester_profile.bio,
                    profile_pic=requester_profile.profile_pic,
                    date_of_birth=requester_profile.date_of_birth,
                    gender=requester_profile.gender
                ),
                receiver_profile=LoopProfilePublic(
                    id=receiver_profile.id,
                    displayname=receiver_profile.displayname,
                    bio=receiver_profile.bio,
                    profile_pic=receiver_profile.profile_pic,
                    date_of_birth=receiver_profile.date_of_birth,
                    gender=receiver_profile.gender
                )
            ))

    return {"items": items, "total": total, "has_more": offset + limit < total}


@router.get("/requests/sent")
def get_sent_loop_requests(
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    total = session.exec(
        select(func.count())
        .select_from(LoopRequest)
        .where(LoopRequest.requester_profile_id == my_profile.id, LoopRequest.status == "pending")
    ).one()

    requests = session.exec(
        select(LoopRequest)
        .where(LoopRequest.requester_profile_id == my_profile.id, LoopRequest.status == "pending")
        .order_by(LoopRequest.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    # Fetch profile data for each request
    items = []
    for request in requests:
        requester_profile = session.exec(
            select(LoopProfile).where(LoopProfile.id == request.requester_profile_id)
        ).first()
        receiver_profile = session.exec(
            select(LoopProfile).where(LoopProfile.id == request.receiver_profile_id)
        ).first()

        if requester_profile and receiver_profile:
            items.append(LoopRequestWithProfiles(
                id=request.id,
                status=request.status,
                created_at=request.created_at,
                requester_profile=LoopProfilePublic(
                    id=requester_profile.id,
                    displayname=requester_profile.displayname,
                    bio=requester_profile.bio,
                    profile_pic=requester_profile.profile_pic,
                    date_of_birth=requester_profile.date_of_birth,
                    gender=requester_profile.gender
                ),
                receiver_profile=LoopProfilePublic(
                    id=receiver_profile.id,
                    displayname=receiver_profile.displayname,
                    bio=receiver_profile.bio,
                    profile_pic=receiver_profile.profile_pic,
                    date_of_birth=receiver_profile.date_of_birth,
                    gender=receiver_profile.gender
                )
            ))

    return {"items": items, "total": total, "has_more": offset + limit < total}


@router.delete("/request/{request_id}")
def delete_loop_request(
    request_id: str,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    loop_request = session.exec(
        select(LoopRequest).where(
            LoopRequest.id == request_id,
            LoopRequest.requester_profile_id == my_profile.id,
        )
    ).first()
    if not loop_request:
        raise HTTPException(
            status_code=404, detail="Loop request not found or not owned by you"
        )
    session.delete(loop_request)
    session.commit()
    return {"detail": "Loop request deleted"}


@router.get("/friends")
def get_loop_friends(
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    total = session.exec(
        select(func.count())
        .select_from(LoopFriend)
        .where(LoopFriend.loop_profile_id == my_profile.id)
    ).one()

    friends = session.exec(
        select(LoopFriend)
        .where(LoopFriend.loop_profile_id == my_profile.id)
        .offset(offset)
        .limit(limit)
    ).all()

    # Return LoopProfile for each friend
    friend_profiles = []
    for friend in friends:
        profile = session.exec(
            select(LoopProfile).where(LoopProfile.id == friend.friend_profile_id)
        ).first()
        if profile:
            friend_profiles.append(profile)
    return {"items": friend_profiles, "total": total, "has_more": offset + limit < total}


@router.post("/chat/{chat_id}/message", response_model=dict)
def send_loop_message(
    chat_id: str,
    message_data: LoopMessageCreate,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    # Verify chat exists and user is part of it
    chat = session.get(LoopChat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    if not (chat.profile1_id == my_profile.id or chat.profile2_id == my_profile.id):
        raise HTTPException(status_code=403, detail="You are not part of this chat")
    
    message = save_loop_message(
        chat_id=chat_id,
        sender_profile_id=my_profile.id,
        content=message_data.content,
        message_type=message_data.message_type,
        media_url=message_data.media_url,
        session=session,
    )
    
    # Emit WebSocket event
    response = build_loop_message_response(message)
    emit_loop_message_sync(response, f"loop_chat_{chat_id}")
    
    return response


@router.get("/chat/{chat_id}/messages")
def get_loop_messages(
    chat_id: str,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
    before_id: str | None = Query(None, description="Return messages before this ID (cursor)"),
    limit: int = Query(50, ge=1, le=200),
):
    # Verify chat exists and user is part of it
    chat = session.get(LoopChat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if not (chat.profile1_id == my_profile.id or chat.profile2_id == my_profile.id):
        raise HTTPException(status_code=403, detail="You are not part of this chat")

    # Total count
    total = session.exec(
        select(func.count())
        .select_from(LoopMessage)
        .where(
            LoopMessage.chat_id == chat_id,
            (LoopMessage.deleted_for_profile_id.is_(None))
            | (LoopMessage.deleted_for_profile_id != my_profile.id),
        )
    ).one()

    # Cursor-based query (newest first, then reverse for chronological order)
    stmt = (
        select(LoopMessage)
        .where(
            LoopMessage.chat_id == chat_id,
            (LoopMessage.deleted_for_profile_id.is_(None))
            | (LoopMessage.deleted_for_profile_id != my_profile.id),
        )
        .order_by(LoopMessage.id.desc())
    )
    if before_id:
        stmt = stmt.where(LoopMessage.id < before_id)
    messages = list(reversed(session.exec(stmt.limit(limit)).all()))

    return {"items": [build_loop_message_response(msg) for msg in messages], "total": total, "has_more": len(messages) == limit}



@router.post("/chat/start/{target_profile_id}", response_model=LoopChatResponse)
def start_loop_chat(
    target_profile_id: str,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    from app.loops.schemas import LoopChatResponse

    # Prevent chatting with self
    if target_profile_id == my_profile.id:
        raise HTTPException(status_code=400, detail="Cannot start chat with yourself")

    # Check if chat already exists
    statement = select(LoopChat).where(
        (
            (LoopChat.profile1_id == my_profile.id)
            & (LoopChat.profile2_id == target_profile_id)
        )
        | (
            (LoopChat.profile1_id == target_profile_id)
            & (LoopChat.profile2_id == my_profile.id)
        )
    )
    existing_chat = session.exec(statement).first()

    if existing_chat:
        chat = existing_chat
    else:
        # Create new chat
        # Ensure consistent ordering of ids if needed, or just assign
        chat = LoopChat(
            profile1_id=my_profile.id,
            profile2_id=target_profile_id,
        )
        session.add(chat)
        session.commit()
        session.refresh(chat)

    # Prepare response
    other_profile = session.get(LoopProfile, target_profile_id)
    if not other_profile:
        raise HTTPException(status_code=404, detail="Target profile not found")

    return LoopChatResponse(
        id=chat.id,
        last_message_at=chat.last_message_at.isoformat() if chat.last_message_at else None,
        last_message_content=chat.last_message_content,
        other_profile=LoopProfilePublic(
            id=other_profile.id,
            displayname=other_profile.displayname,
            bio=other_profile.bio,
            profile_pic=other_profile.profile_pic,
            date_of_birth=other_profile.date_of_birth,
            gender=other_profile.gender,
        ),
    )


@router.get("/chats")
def get_loop_chats(
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
    before_id: str | None = Query(None, description="Return chats before this ID (cursor)"),
    limit: int = Query(20, ge=1, le=50),
):
    from app.loops.schemas import LoopChatResponse

    total = session.exec(
        select(func.count())
        .select_from(LoopChat)
        .where(
            (LoopChat.profile1_id == my_profile.id) | (LoopChat.profile2_id == my_profile.id)
        )
    ).one()

    base_filter = (LoopChat.profile1_id == my_profile.id) | (LoopChat.profile2_id == my_profile.id)

    statement = (
        select(LoopChat)
        .where(base_filter)
        # NULLs (no messages yet) sort after all real timestamps
        .order_by(nullslast(LoopChat.last_message_at.desc()), LoopChat.id.desc())
    )

    if before_id:
        cursor_chat = session.get(LoopChat, before_id)
        if cursor_chat:
            cursor_ts = cursor_chat.last_message_at
            cursor_id = cursor_chat.id
            if cursor_ts is not None:
                # Rows that come after (cursor_ts, cursor_id) in DESC order:
                #   - strictly older timestamp, OR
                #   - same timestamp but earlier id (UUID v7 tiebreak), OR
                #   - no messages yet (NULL always sorts last)
                statement = statement.where(
                    or_(
                        LoopChat.last_message_at < cursor_ts,
                        and_(LoopChat.last_message_at == cursor_ts, LoopChat.id < cursor_id),
                        LoopChat.last_message_at.is_(None),
                    )
                )
            else:
                # Cursor is already in the NULL section — only earlier ids remain
                statement = statement.where(
                    and_(LoopChat.last_message_at.is_(None), LoopChat.id < cursor_id)
                )

    chats = session.exec(statement.limit(limit)).all()

    items = []
    for chat in chats:
        other_profile_id = (
            chat.profile1_id if chat.profile2_id == my_profile.id else chat.profile2_id
        )
        other_profile = session.get(LoopProfile, other_profile_id)

        if other_profile:
            items.append(
                LoopChatResponse(
                    id=chat.id,
                    last_message_at=chat.last_message_at.isoformat() if chat.last_message_at else None,
                    last_message_content=chat.last_message_content,
                    other_profile=LoopProfilePublic(
                        id=other_profile.id,
                        displayname=other_profile.displayname,
                        bio=other_profile.bio,
                        profile_pic=other_profile.profile_pic,
                        date_of_birth=other_profile.date_of_birth,
                        gender=other_profile.gender,
                    ),
                )
            )
    return {"items": items, "total": total, "has_more": len(chats) == limit}

@router.delete("/message/{message_id}")
def delete_loop_message(
    message_id: str,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    message = session.get(LoopMessage, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    if message.sender_profile_id != my_profile.id:
        raise HTTPException(status_code=403, detail="You can only delete your own messages")
    
    # Soft delete
    message.deleted_for_profile_id = my_profile.id
    session.add(message)
    session.commit()
    
    # Emit WebSocket event
    try:
        asyncio.create_task(
            sio.emit("message:delete", {"message_id": message_id}, room=f"loop_chat_{message.chat_id}")
        )
    except Exception:
        pass
    
    return {"detail": "Message deleted"}


# Loop reactions
@router.post("/message/{message_id}/reaction", response_model=dict)
def add_loop_reaction(
    message_id: str,
    reaction_data: LoopReactionCreate,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    message = session.get(LoopMessage, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    chat = session.get(LoopChat, message.chat_id)
    if not chat or (chat.profile1_id != my_profile.id and chat.profile2_id != my_profile.id):
        raise HTTPException(status_code=403, detail="Not a participant of this chat")

    # Check if reaction already exists (toggle behavior)
    existing = session.exec(
        select(LoopReaction).where(
            LoopReaction.message_id == message_id,
            LoopReaction.profile_id == my_profile.id,
            LoopReaction.emoji == reaction_data.emoji,
        )
    ).first()
    
    if existing:
        # Remove reaction
        session.delete(existing)
        session.commit()
        event = "reaction:removed"
    else:
        # Add reaction
        reaction = LoopReaction(
            message_id=message_id,
            profile_id=my_profile.id,
            emoji=reaction_data.emoji,
        )
        session.add(reaction)
        session.commit()
        session.refresh(reaction)
        event = "reaction:added"
    
    # Emit WebSocket event
    try:
        asyncio.create_task(
            sio.emit(
                event,
                {
                    "message_id": message_id,
                    "emoji": reaction_data.emoji,
                    "profile_id": my_profile.id,
                },
                room=f"loop_chat_{message.chat_id}",
            )
        )
    except Exception:
        pass
    
    return {"detail": f"Reaction {event}"}


@router.delete("/message/{message_id}/reaction/{emoji}")
def remove_loop_reaction(
    message_id: str,
    emoji: str,
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    message = session.get(LoopMessage, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    chat = session.get(LoopChat, message.chat_id)
    if not chat or (chat.profile1_id != my_profile.id and chat.profile2_id != my_profile.id):
        raise HTTPException(status_code=403, detail="Not a participant of this chat")

    reaction = session.exec(
        select(LoopReaction).where(
            LoopReaction.message_id == message_id,
            LoopReaction.profile_id == my_profile.id,
            LoopReaction.emoji == emoji,
        )
    ).first()
    
    if reaction:
        session.delete(reaction)
        session.commit()
        
        # Emit WebSocket event
        try:
            asyncio.create_task(
                sio.emit(
                    "reaction:removed",
                    {
                        "message_id": message_id,
                        "emoji": emoji,
                        "profile_id": my_profile.id,
                    },
                    room=f"loop_chat_{message.chat_id}",
                )
            )
        except Exception:
            pass
    
    return {"detail": "Reaction removed"}


# Random chat/call
@router.post("/random/start")
def start_random_session(user_id: str, session: Session = Depends(get_session)):
    random_session = RandomSession(user_id=user_id)
    session.add(random_session)
    session.commit()
    session.refresh(random_session)
    return random_session


@router.get("/random/active")
def get_active_random_sessions(
    session: Session = Depends(get_session),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    total = session.exec(
        select(func.count()).select_from(RandomSession)
    ).one()
    items = session.exec(
        select(RandomSession).offset(offset).limit(limit)
    ).all()
    return {"items": items, "total": total, "has_more": offset + limit < total}


@router.get("/profile/me", response_model=LoopProfilePublic)
def get_my_loop_profile(
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
):
    return build_profile_public(my_profile, session)


@router.put("/profile/me", response_model=LoopProfilePublic)
def update_my_loop_profile(
    my_profile: Annotated[LoopProfile, Depends(get_current_loop_profile)],
    session: Session = Depends(get_session),
    displayname: Optional[str] = None,
    profile_pic: Optional[str] = None,
    bio: Optional[str] = None,
    gender: Optional[str] = None,
):
    if displayname is not None:
        my_profile.displayname = displayname
    if profile_pic is not None:
        my_profile.profile_pic = profile_pic
    if bio is not None:
        my_profile.bio = bio
    if gender is not None:
        my_profile.gender = gender
    session.add(my_profile)
    session.commit()
    session.refresh(my_profile)
    return build_profile_public(my_profile, session)


# ── Public Profile View (must be after /profile/me to avoid route conflict) ──
@router.get("/profile/{profile_id}", response_model=LoopProfilePublic)
def get_loop_profile_by_id(
    profile_id: str,
    session: Session = Depends(get_session),
):
    """View any user's loop profile with their photos."""
    profile = session.get(LoopProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Loop profile not found")
    return build_profile_public(profile, session)
