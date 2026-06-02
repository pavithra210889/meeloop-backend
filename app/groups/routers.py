import logging
from typing import Annotated
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import select, or_, func

from ..users.models import User, UserBasic, UserDevice
from ..users.routers import get_current_active_user
from ..dependencies import SessionDep
from ..messages.models import (
    Chat, ChatType, ChatMember, ChatMemberRole, Message, MessageType, MessageStatus,
    MessageKey, GroupCreate, GroupUpdate, GroupMemberAdd, GroupMemberUpdate,
    GroupMemberResponse, GroupInfoResponse, MessageReadReceipt,
    SenderKey, SenderKeyDistribution, SenderKeyDistributionRequest,
    ChatResponseV2, ChatMute, GroupMessageSend, MessageResponse, ReactionResponse,
)
from ..messages.routers import build_message_response, _is_encrypted_blob, _strip_keys_from_blob, _extract_and_save_keys
from app.sockets.socketio_server import sio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["groups"])


# --- Helpers ---

def _get_group_or_404(chat_id: str, session) -> Chat:
    chat = session.get(Chat, chat_id)
    if not chat or chat.chat_type != ChatType.GROUP:
        raise HTTPException(status_code=404, detail="Group not found")
    return chat


def _get_active_membership(chat_id: str, user_id: str, session) -> ChatMember:
    member = session.exec(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id,
            ChatMember.is_active == True,
        )
    ).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this group")
    return member


def _require_admin(chat_id: str, user_id: str, session) -> ChatMember:
    member = _get_active_membership(chat_id, user_id, session)
    if member.role != ChatMemberRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return member


def _build_group_info(chat: Chat, session) -> GroupInfoResponse:
    members = session.exec(
        select(ChatMember).where(
            ChatMember.chat_id == chat.id,
            ChatMember.is_active == True,
        )
    ).all()
    member_responses = []
    for m in members:
        member_responses.append(GroupMemberResponse(
            user=UserBasic(
                id=m.user.id, username=m.user.username,
                name=m.user.name, profile_pic=m.user.profile_pic, bio=m.user.bio,
            ),
            role=m.role,
            joined_at=m.joined_at,
            is_active=m.is_active,
        ))
    creator = chat.created_by
    return GroupInfoResponse(
        id=chat.id,
        chat_type="group",
        group_name=chat.group_name or "",
        group_icon_url=chat.group_icon_url,
        group_description=chat.group_description,
        created_by=UserBasic(
            id=creator.id, username=creator.username,
            name=creator.name, profile_pic=creator.profile_pic, bio=creator.bio,
        ) if creator else UserBasic(id="", username="", name="Deleted User"),
        members=member_responses,
        member_count=len(member_responses),
        max_members=chat.max_members,
        join_mode=chat.join_mode,
        created_at=chat.created_at,
        last_message=chat.last_message,
        last_message_type=chat.last_message_type,
        last_message_datetime=chat.last_message_datetime,
        disappearing_timer=chat.disappearing_timer,
    )


def _create_system_message(chat_id: str, sender_id: str, text: str, session) -> Message:
    msg = Message(
        message=text,
        message_type=MessageType.SYSTEM,
        is_system_message=True,
        sender_id=sender_id,
        receiver_id=None,
        chat_id=chat_id,
    )
    session.add(msg)
    return msg


# --- List My Groups ---

@router.get("/my/", response_model=list[GroupInfoResponse])
def list_my_groups(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """List all groups the current user is a member of."""
    memberships = session.exec(
        select(ChatMember).where(
            ChatMember.user_id == current_user.id,
            ChatMember.is_active == True,
        )
    ).all()
    groups = []
    for m in memberships:
        chat = session.get(Chat, m.chat_id)
        if chat and chat.chat_type == ChatType.GROUP:
            groups.append(_build_group_info(chat, session))
    groups.sort(key=lambda g: g.last_message_datetime or datetime.min, reverse=True)
    return groups


# --- Group CRUD ---

@router.post("/", response_model=GroupInfoResponse)
def create_group(
    payload: GroupCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="Group name is required")
    if len(payload.member_ids) < 1:
        raise HTTPException(status_code=400, detail="At least one other member is required")

    # Validate all member IDs exist
    member_ids = list(set(payload.member_ids))
    if current_user.id in member_ids:
        member_ids.remove(current_user.id)
    users = session.exec(select(User).where(User.id.in_(member_ids))).all()
    if len(users) != len(member_ids):
        raise HTTPException(status_code=400, detail="One or more user IDs are invalid")

    # Create the group chat
    chat = Chat(
        chat_type=ChatType.GROUP,
        group_name=payload.name.strip(),
        group_icon_url=payload.icon_url,
        group_description=payload.description,
        join_mode=payload.join_mode if payload.join_mode in ("private", "invite_only", "public") else "private",
        created_by_id=current_user.id,
        participant_one_id=None,
        participant_two_id=None,
    )
    session.add(chat)
    session.flush()

    # Add creator as admin
    creator_member = ChatMember(
        chat_id=chat.id,
        user_id=current_user.id,
        role=ChatMemberRole.ADMIN,
        added_by_id=None,
    )
    session.add(creator_member)

    # Add other members
    for user in users:
        session.add(ChatMember(
            chat_id=chat.id,
            user_id=user.id,
            role=ChatMemberRole.MEMBER,
            added_by_id=current_user.id,
        ))

    # System message
    member_names = ", ".join(u.name or u.username for u in users[:5])
    if len(users) > 5:
        member_names += f" and {len(users) - 5} others"
    _create_system_message(
        chat.id, current_user.id,
        f"{current_user.name or current_user.username} created the group and added {member_names}",
        session,
    )

    chat.last_message = f"{current_user.name or current_user.username} created the group"
    chat.last_message_type = "system"
    chat.last_message_datetime = datetime.now(timezone.utc)

    session.commit()
    session.refresh(chat)

    # Notify members via socket
    for user in users:
        try:
            import asyncio
            asyncio.get_event_loop().create_task(
                sio.emit("group:created", {
                    "chat_id": chat.id,
                    "group_name": chat.group_name,
                    "added_by": current_user.id,
                }, room=f"user:{user.id}")
            )
        except Exception:
            pass

    return _build_group_info(chat, session)


@router.get("/{chat_id}/", response_model=GroupInfoResponse)
def get_group_info(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    chat = _get_group_or_404(chat_id, session)
    _get_active_membership(chat_id, current_user.id, session)
    return _build_group_info(chat, session)


@router.patch("/{chat_id}/", response_model=GroupInfoResponse)
def update_group(
    chat_id: str,
    payload: GroupUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    chat = _get_group_or_404(chat_id, session)
    _require_admin(chat_id, current_user.id, session)

    changes = []
    if payload.name is not None and payload.name.strip():
        chat.group_name = payload.name.strip()
        changes.append("name")
    if payload.icon_url is not None:
        chat.group_icon_url = payload.icon_url
        changes.append("icon")
    if payload.description is not None:
        chat.group_description = payload.description
        changes.append("description")

    if changes:
        chat.updated_at = datetime.now(timezone.utc)
        _create_system_message(
            chat.id, current_user.id,
            f"{current_user.name or current_user.username} updated the group {', '.join(changes)}",
            session,
        )
        session.commit()
        session.refresh(chat)

        # Notify group
        try:
            import asyncio
            asyncio.get_event_loop().create_task(
                sio.emit("group:updated", {
                    "chat_id": chat.id,
                    "group_name": chat.group_name,
                    "group_icon_url": chat.group_icon_url,
                    "group_description": chat.group_description,
                }, room=f"group:{chat.id}")
            )
        except Exception:
            pass

    return _build_group_info(chat, session)


# --- Member Management ---

@router.post("/{chat_id}/members/", response_model=GroupInfoResponse)
def add_members(
    chat_id: str,
    payload: GroupMemberAdd,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    chat = _get_group_or_404(chat_id, session)
    _require_admin(chat_id, current_user.id, session)

    # Check member limit
    active_count = session.exec(
        select(func.count(ChatMember.id)).where(
            ChatMember.chat_id == chat_id,
            ChatMember.is_active == True,
        )
    ).one()
    if active_count + len(payload.user_ids) > chat.max_members:
        raise HTTPException(status_code=400, detail=f"Group is limited to {chat.max_members} members")

    users = session.exec(select(User).where(User.id.in_(payload.user_ids))).all()
    added = []
    for user in users:
        # Check if already a member (possibly inactive — reactivate)
        existing = session.exec(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id,
                ChatMember.user_id == user.id,
            )
        ).first()
        if existing:
            if not existing.is_active:
                existing.is_active = True
                existing.left_at = None
                existing.joined_at = datetime.now(timezone.utc)
                existing.added_by_id = current_user.id
                added.append(user)
            # Already active — skip
        else:
            session.add(ChatMember(
                chat_id=chat_id,
                user_id=user.id,
                role=ChatMemberRole.MEMBER,
                added_by_id=current_user.id,
            ))
            added.append(user)

    if added:
        names = ", ".join(u.name or u.username for u in added[:5])
        if len(added) > 5:
            names += f" and {len(added) - 5} others"
        _create_system_message(
            chat.id, current_user.id,
            f"{current_user.name or current_user.username} added {names}",
            session,
        )
        # Invalidate sender keys — new members need fresh distribution
        session.exec(
            select(SenderKey).where(
                SenderKey.chat_id == chat_id,
                SenderKey.is_active == True,
            )
        )
        # Note: we don't invalidate on member ADD (new member just needs to receive existing keys)
        # Invalidation happens on member REMOVE for forward secrecy

        session.commit()
        session.refresh(chat)

        # Notify new members
        for user in added:
            try:
                import asyncio
                asyncio.get_event_loop().create_task(
                    sio.emit("group:member_added", {
                        "chat_id": chat.id,
                        "group_name": chat.group_name,
                        "user_id": user.id,
                        "added_by": current_user.id,
                    }, room=f"user:{user.id}")
                )
            except Exception:
                pass

        # Notify existing group members
        try:
            import asyncio
            asyncio.get_event_loop().create_task(
                sio.emit("group:members_changed", {
                    "chat_id": chat.id,
                    "action": "added",
                    "user_ids": [u.id for u in added],
                }, room=f"group:{chat.id}")
            )
        except Exception:
            pass

    return _build_group_info(chat, session)


@router.delete("/{chat_id}/members/{user_id}")
def remove_member(
    chat_id: str,
    user_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    chat = _get_group_or_404(chat_id, session)
    is_self_leave = user_id == current_user.id

    if not is_self_leave:
        _require_admin(chat_id, current_user.id, session)

    member = session.exec(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id,
            ChatMember.is_active == True,
        )
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Prevent last admin from leaving without promoting someone
    if member.role == ChatMemberRole.ADMIN:
        admin_count = session.exec(
            select(func.count(ChatMember.id)).where(
                ChatMember.chat_id == chat_id,
                ChatMember.role == ChatMemberRole.ADMIN,
                ChatMember.is_active == True,
            )
        ).one()
        if admin_count <= 1:
            active_members = session.exec(
                select(func.count(ChatMember.id)).where(
                    ChatMember.chat_id == chat_id,
                    ChatMember.is_active == True,
                )
            ).one()
            if active_members > 1:
                raise HTTPException(
                    status_code=400,
                    detail="You must promote another member to admin before leaving"
                )

    # Soft-remove
    member.is_active = False
    member.left_at = datetime.now(timezone.utc)

    removed_user = member.user
    if is_self_leave:
        _create_system_message(
            chat.id, current_user.id,
            f"{removed_user.name or removed_user.username} left the group",
            session,
        )
    else:
        _create_system_message(
            chat.id, current_user.id,
            f"{current_user.name or current_user.username} removed {removed_user.name or removed_user.username}",
            session,
        )

    # ROTATE sender keys — removed member must not decrypt future messages
    active_keys = session.exec(
        select(SenderKey).where(
            SenderKey.chat_id == chat_id,
            SenderKey.is_active == True,
        )
    ).all()
    for key in active_keys:
        key.is_active = False
    # All senders must re-distribute their keys (clients handle this on receiving key_rotation event)

    session.commit()

    # Notify group about removal and key rotation
    try:
        import asyncio
        asyncio.get_event_loop().create_task(
            sio.emit("group:member_removed", {
                "chat_id": chat.id,
                "user_id": user_id,
                "removed_by": current_user.id,
                "key_rotation_required": True,
            }, room=f"group:{chat.id}")
        )
    except Exception:
        pass

    return {"detail": "Left the group" if is_self_leave else "Member removed"}


@router.patch("/{chat_id}/members/{user_id}", response_model=GroupMemberResponse)
def update_member_role(
    chat_id: str,
    user_id: str,
    payload: GroupMemberUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    _get_group_or_404(chat_id, session)
    _require_admin(chat_id, current_user.id, session)

    member = session.exec(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id,
            ChatMember.is_active == True,
        )
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    old_role = member.role
    member.role = payload.role

    if old_role != payload.role:
        action = "promoted to admin" if payload.role == ChatMemberRole.ADMIN else "demoted to member"
        _create_system_message(
            chat_id, current_user.id,
            f"{current_user.name or current_user.username} {action} {member.user.name or member.user.username}",
            session,
        )
        session.commit()
        session.refresh(member)

    return GroupMemberResponse(
        user=UserBasic(
            id=member.user.id, username=member.user.username,
            name=member.user.name, profile_pic=member.user.profile_pic, bio=member.user.bio,
        ),
        role=member.role,
        joined_at=member.joined_at,
        is_active=member.is_active,
    )


# --- Group Device Keys (for E2E encryption) ---

@router.get("/{chat_id}/keys/")
def get_group_member_keys(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Get all device public keys for all active group members (for sender key distribution)."""
    _get_group_or_404(chat_id, session)
    _get_active_membership(chat_id, current_user.id, session)

    members = session.exec(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.is_active == True,
        )
    ).all()
    member_ids = [m.user_id for m in members]

    devices = session.exec(
        select(UserDevice).where(
            UserDevice.user_id.in_(member_ids),
            UserDevice.is_active == True,
            UserDevice.public_key.is_not(None),
        )
    ).all()
    return [
        {"device_id": d.device_id, "public_key": d.public_key, "user_id": d.user_id}
        for d in devices
    ]


# --- Sender Key Distribution ---

@router.post("/{chat_id}/sender-keys/distribute")
def distribute_sender_key(
    chat_id: str,
    payload: SenderKeyDistributionRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Sender distributes their chain key (RSA-encrypted) to all group members' devices."""
    _get_group_or_404(chat_id, session)
    _get_active_membership(chat_id, current_user.id, session)

    # Deactivate old sender key for this sender+device
    old_keys = session.exec(
        select(SenderKey).where(
            SenderKey.chat_id == chat_id,
            SenderKey.sender_id == current_user.id,
            SenderKey.sender_device_id == payload.sender_device_id,
            SenderKey.is_active == True,
        )
    ).all()
    for k in old_keys:
        k.is_active = False

    # Create new sender key
    sender_key = SenderKey(
        chat_id=chat_id,
        sender_id=current_user.id,
        sender_device_id=payload.sender_device_id,
        chain_key=payload.chain_key,
        iteration=payload.iteration,
    )
    session.add(sender_key)
    session.flush()

    # Store per-device distributions
    for dist in payload.distributions:
        session.add(SenderKeyDistribution(
            sender_key_id=sender_key.id,
            recipient_device_id=dist["device_id"],
            recipient_user_id=dist["user_id"],
            encrypted_chain_key=dist["encrypted_chain_key"],
        ))

    session.commit()
    return {"detail": "Sender key distributed", "sender_key_id": sender_key.id}


@router.get("/{chat_id}/sender-keys/")
def get_sender_keys(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    request: Request,
    session: SessionDep,
):
    """Get all active sender key distributions for the current user's device."""
    _get_group_or_404(chat_id, session)
    _get_active_membership(chat_id, current_user.id, session)

    device_id = request.headers.get("x-device-id")
    if not device_id:
        raise HTTPException(status_code=400, detail="x-device-id header required")

    distributions = session.exec(
        select(SenderKeyDistribution, SenderKey)
        .join(SenderKey, SenderKeyDistribution.sender_key_id == SenderKey.id)
        .where(
            SenderKey.chat_id == chat_id,
            SenderKey.is_active == True,
            SenderKeyDistribution.recipient_device_id == device_id,
            SenderKeyDistribution.recipient_user_id == current_user.id,
        )
    ).all()

    return [
        {
            "sender_id": sk.sender_id,
            "sender_device_id": sk.sender_device_id,
            "encrypted_chain_key": dist.encrypted_chain_key,
            "iteration": sk.iteration,
            "sender_key_id": sk.id,
        }
        for dist, sk in distributions
    ]


# --- Group Messaging ---

@router.post("/{chat_id}/messages/", response_model=MessageResponse)
def send_group_message(
    chat_id: str,
    payload: GroupMessageSend,
    current_user: Annotated[User, Depends(get_current_active_user)],
    request: Request,
    session: SessionDep,
):
    chat = _get_group_or_404(chat_id, session)
    _get_active_membership(chat_id, current_user.id, session)
    device_id = request.headers.get("x-device-id")

    # Auto-detect message type
    if payload.latitude is not None and payload.longitude is not None:
        message_type = MessageType.LOCATION
    elif payload.contact_name or payload.contact_phone or payload.contact_user_id:
        message_type = MessageType.CONTACT
    elif payload.shared_post_id:
        message_type = MessageType.POST
    elif payload.media_url:
        mt = (payload.media_type or "").lower()
        message_type = {"image": MessageType.IMAGE, "video": MessageType.VIDEO,
                        "audio": MessageType.AUDIO, "file": MessageType.FILE}.get(mt, MessageType.OTHER)
    else:
        message_type = payload.message_type or MessageType.TEXT

    # Handle encrypted blobs
    stored_message = payload.message
    stored_caption = payload.caption
    stored_media_enc = payload.media_encryption

    body_encrypted = _is_encrypted_blob(payload.message)
    caption_encrypted = _is_encrypted_blob(payload.caption)
    media_encrypted = _is_encrypted_blob(payload.media_encryption)

    if body_encrypted:
        stored_message = _strip_keys_from_blob(payload.message)
    if caption_encrypted:
        stored_caption = _strip_keys_from_blob(payload.caption)
    if media_encrypted:
        stored_media_enc = _strip_keys_from_blob(payload.media_encryption)

    msg = Message(
        message=stored_message,
        message_type=message_type,
        caption=stored_caption,
        link_url=payload.link_url,
        media_url=payload.media_url,
        media_type=payload.media_type,
        media_thumbnail_url=payload.media_thumbnail_url,
        file_size=payload.file_size,
        duration=payload.duration,
        shared_post_id=payload.shared_post_id,
        reply_to_id=payload.reply_to_id,
        forwarded_from_id=payload.forwarded_from_id,
        is_forwarded=payload.is_forwarded,
        media_encryption=stored_media_enc,
        latitude=payload.latitude,
        longitude=payload.longitude,
        location_name=payload.location_name,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        contact_user_id=payload.contact_user_id,
        sender_id=current_user.id,
        receiver_id=None,
        chat_id=chat.id,
    )

    # Set expiry if disappearing messages enabled
    if chat.disappearing_timer:
        from datetime import timedelta, timezone
        msg.expires_at = datetime.now(timezone.utc) + timedelta(seconds=chat.disappearing_timer)

    session.add(msg)
    session.flush()

    # Extract per-device keys from encrypted blobs
    if body_encrypted:
        _extract_and_save_keys(payload.message, msg.id, "body", session)
    if caption_encrypted:
        _extract_and_save_keys(payload.caption, msg.id, "caption", session)
    if media_encrypted:
        _extract_and_save_keys(payload.media_encryption, msg.id, "media", session)

    # Update chat preview
    from ..messages.routers import _LAST_MESSAGE_PREVIEWS
    if body_encrypted:
        chat.last_message = _LAST_MESSAGE_PREVIEWS.get(message_type, "Message")
    else:
        chat.last_message = f"{current_user.name or current_user.username}: {msg.message or ''}"[:100]
    chat.last_message_type = msg.message_type.value if msg.message_type else None
    chat.last_message_datetime = msg.created_at

    session.commit()
    session.refresh(msg)

    # Build response and emit to group room
    resp = build_message_response(msg, device_id=device_id, session=session, include_all_keys=True)
    resp_dict = resp.model_dump(mode="json")
    resp_dict["chat_type"] = "group"

    try:
        import asyncio
        asyncio.get_event_loop().create_task(
            sio.emit("message:new", resp_dict, room=f"group:{chat.id}")
        )
    except Exception as e:
        logger.error(f"Failed to emit group message: {e}")

    return build_message_response(msg, device_id=device_id, session=session)


@router.get("/{chat_id}/messages/", response_model=list[MessageResponse])
def get_group_messages(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    request: Request,
    session: SessionDep,
    limit: int = 50,
    offset: int = 0,
):
    _get_group_or_404(chat_id, session)
    _get_active_membership(chat_id, current_user.id, session)
    device_id = request.headers.get("x-device-id")

    messages = session.exec(
        select(Message).where(
            Message.chat_id == chat_id,
        ).order_by(Message.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return [build_message_response(m, device_id=device_id, session=session) for m in messages]


@router.post("/{chat_id}/messages/read")
def mark_group_messages_read(
    chat_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Mark all unread group messages as read for the current user."""
    _get_group_or_404(chat_id, session)
    _get_active_membership(chat_id, current_user.id, session)

    # Find unread messages (not sent by current user, no read receipt yet)
    unread = session.exec(
        select(Message).where(
            Message.chat_id == chat_id,
            Message.sender_id != current_user.id,
            ~Message.id.in_(
                select(MessageReadReceipt.message_id).where(
                    MessageReadReceipt.user_id == current_user.id
                )
            ),
        )
    ).all()

    for msg in unread:
        session.add(MessageReadReceipt(
            message_id=msg.id,
            user_id=current_user.id,
        ))

    session.commit()
    return {"detail": f"Marked {len(unread)} messages as read"}
