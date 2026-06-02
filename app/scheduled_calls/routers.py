from datetime import datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel import select, or_, and_

from .models import ScheduledCall, ScheduledCallStatus
from .schemas import ScheduledCallCreate, ScheduledCallUpdate, ScheduledCallResponse
from ..users.models import User, UserBasic
from ..users.routers import get_current_active_user
from ..dependencies import SessionDep
from ..notifications.enums import NotificationType, NotificationCategory
from ..notifications.services.notification_service import notification_service
from ..sockets.socketio_server import sio
from ..sockets.socketio_events import user_room

router = APIRouter(tags=["scheduled-calls"])


def _build_response(sc: ScheduledCall, scheduler: User, participant: User) -> ScheduledCallResponse:
    return ScheduledCallResponse(
        id=sc.id,
        scheduler=UserBasic(**scheduler.model_dump()),
        participant=UserBasic(**participant.model_dump()),
        scheduled_at=sc.scheduled_at,
        is_video_call=sc.is_video_call,
        status=sc.status,
        note=sc.note,
        call_id=sc.call_id,
        created_at=sc.created_at,
        updated_at=sc.updated_at,
    )


@router.post("/scheduled-calls/", response_model=ScheduledCallResponse, status_code=201)
async def create_scheduled_call(
    body: ScheduledCallCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    if body.scheduled_at <= datetime.now() + timedelta(minutes=5):
        raise HTTPException(status_code=400, detail="Scheduled time must be at least 5 minutes from now")

    participant = session.get(User, body.participant_id)
    if not participant:
        raise HTTPException(status_code=404, detail="Participant not found")
    if participant.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot schedule a call with yourself")

    sc = ScheduledCall(
        scheduler_id=current_user.id,
        participant_id=body.participant_id,
        scheduled_at=body.scheduled_at,
        is_video_call=body.is_video_call,
        note=body.note,
    )
    session.add(sc)
    session.commit()
    session.refresh(sc)

    call_type = "video call" if sc.is_video_call else "voice call"

    # Notify participant
    try:
        await notification_service.create_notification(
            notification_type=NotificationType.SCHEDULED_CALL_CREATED,
            recipient_id=participant.id,
            sender_id=current_user.id,
            title=f"{current_user.name} scheduled a {call_type}",
            message=f"Scheduled for {sc.scheduled_at.strftime('%b %d at %I:%M %p')}",
            session=session,
            category=NotificationCategory.CALLS,
            redirect_to="/calls",
            redirect_type="scheduled_call",
            redirect_id=sc.id,
            meta={
                "scheduled_call_id": sc.id,
                "is_video_call": sc.is_video_call,
                "scheduled_at": sc.scheduled_at.isoformat(),
            },
            priority=1,
        )
    except Exception:
        pass

    # Socket event to participant
    try:
        await sio.emit(
            "scheduled_call:created",
            {
                "id": sc.id,
                "scheduler_id": current_user.id,
                "scheduler_name": current_user.name,
                "participant_id": participant.id,
                "scheduled_at": sc.scheduled_at.isoformat(),
                "is_video_call": sc.is_video_call,
                "note": sc.note,
            },
            room=user_room(participant.id),
        )
    except Exception:
        pass

    return _build_response(sc, current_user, participant)


@router.get("/scheduled-calls/", response_model=list[ScheduledCallResponse])
async def list_scheduled_calls(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    status: Optional[str] = Query(None, description="Filter by status"),
    with_user_id: Optional[str] = Query(None, description="Filter calls with a specific user"),
    limit: int = Query(50, ge=1, le=100),
):
    statement = select(ScheduledCall).where(
        or_(
            ScheduledCall.scheduler_id == current_user.id,
            ScheduledCall.participant_id == current_user.id,
        )
    )

    if status:
        statement = statement.where(ScheduledCall.status == status)

    if with_user_id:
        statement = statement.where(
            or_(
                and_(ScheduledCall.scheduler_id == current_user.id, ScheduledCall.participant_id == with_user_id),
                and_(ScheduledCall.scheduler_id == with_user_id, ScheduledCall.participant_id == current_user.id),
            )
        )

    statement = statement.order_by(ScheduledCall.scheduled_at.asc()).limit(limit)
    records = session.exec(statement).all()

    response = []
    for sc in records:
        scheduler = session.get(User, sc.scheduler_id)
        participant = session.get(User, sc.participant_id)
        response.append(_build_response(sc, scheduler, participant))
    return response


@router.get("/scheduled-calls/{scheduled_call_id}", response_model=ScheduledCallResponse)
async def get_scheduled_call(
    scheduled_call_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    sc = session.get(ScheduledCall, scheduled_call_id)
    if not sc:
        raise HTTPException(status_code=404, detail="Scheduled call not found")
    if sc.scheduler_id != current_user.id and sc.participant_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    scheduler = session.get(User, sc.scheduler_id)
    participant = session.get(User, sc.participant_id)
    return _build_response(sc, scheduler, participant)


@router.patch("/scheduled-calls/{scheduled_call_id}", response_model=ScheduledCallResponse)
async def update_scheduled_call(
    scheduled_call_id: str,
    body: ScheduledCallUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    sc = session.get(ScheduledCall, scheduled_call_id)
    if not sc:
        raise HTTPException(status_code=404, detail="Scheduled call not found")
    if sc.scheduler_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the scheduler can reschedule")
    if sc.status in (ScheduledCallStatus.COMPLETED, ScheduledCallStatus.CANCELLED, ScheduledCallStatus.EXPIRED):
        raise HTTPException(status_code=400, detail="Cannot update a finished scheduled call")

    if body.scheduled_at is not None:
        if body.scheduled_at <= datetime.now() + timedelta(minutes=5):
            raise HTTPException(status_code=400, detail="Scheduled time must be at least 5 minutes from now")
        sc.scheduled_at = body.scheduled_at
        sc.status = ScheduledCallStatus.PENDING
        sc.reminder_sent_at = None
        sc.trigger_sent_at = None

    if body.is_video_call is not None:
        sc.is_video_call = body.is_video_call
    if body.note is not None:
        sc.note = body.note

    sc.updated_at = datetime.now()
    session.add(sc)
    session.commit()
    session.refresh(sc)

    scheduler = session.get(User, sc.scheduler_id)
    participant = session.get(User, sc.participant_id)

    # Notify participant of reschedule
    try:
        call_type = "video call" if sc.is_video_call else "voice call"
        await notification_service.create_notification(
            notification_type=NotificationType.SCHEDULED_CALL_RESCHEDULED,
            recipient_id=participant.id,
            sender_id=current_user.id,
            title=f"{current_user.name} rescheduled the {call_type}",
            message=f"New time: {sc.scheduled_at.strftime('%b %d at %I:%M %p')}",
            session=session,
            category=NotificationCategory.CALLS,
            redirect_to="/calls",
            redirect_type="scheduled_call",
            redirect_id=sc.id,
            meta={
                "scheduled_call_id": sc.id,
                "is_video_call": sc.is_video_call,
                "scheduled_at": sc.scheduled_at.isoformat(),
            },
            priority=1,
        )
    except Exception:
        pass

    try:
        await sio.emit(
            "scheduled_call:rescheduled",
            {
                "id": sc.id,
                "scheduler_id": sc.scheduler_id,
                "scheduled_at": sc.scheduled_at.isoformat(),
                "is_video_call": sc.is_video_call,
                "note": sc.note,
            },
            room=user_room(participant.id),
        )
    except Exception:
        pass

    return _build_response(sc, scheduler, participant)


@router.delete("/scheduled-calls/{scheduled_call_id}")
async def cancel_scheduled_call(
    scheduled_call_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    sc = session.get(ScheduledCall, scheduled_call_id)
    if not sc:
        raise HTTPException(status_code=404, detail="Scheduled call not found")
    if sc.scheduler_id != current_user.id and sc.participant_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if sc.status in (ScheduledCallStatus.COMPLETED, ScheduledCallStatus.CANCELLED, ScheduledCallStatus.EXPIRED):
        raise HTTPException(status_code=400, detail="Scheduled call is already finished")

    sc.status = ScheduledCallStatus.CANCELLED
    sc.cancelled_by = current_user.id
    sc.updated_at = datetime.now()
    session.add(sc)
    session.commit()

    # Notify the other party
    other_id = sc.participant_id if current_user.id == sc.scheduler_id else sc.scheduler_id
    other_user = session.get(User, other_id)

    try:
        call_type = "video call" if sc.is_video_call else "voice call"
        await notification_service.create_notification(
            notification_type=NotificationType.SCHEDULED_CALL_CANCELLED,
            recipient_id=other_id,
            sender_id=current_user.id,
            title=f"{current_user.name} cancelled the scheduled {call_type}",
            message=f"The {call_type} scheduled for {sc.scheduled_at.strftime('%b %d at %I:%M %p')} was cancelled",
            session=session,
            category=NotificationCategory.CALLS,
            redirect_to="/calls",
            redirect_type="scheduled_call",
            redirect_id=sc.id,
            meta={"scheduled_call_id": sc.id, "is_video_call": sc.is_video_call},
            priority=0,
        )
    except Exception:
        pass

    try:
        await sio.emit(
            "scheduled_call:cancelled",
            {
                "id": sc.id,
                "cancelled_by": current_user.id,
            },
            room=user_room(other_id),
        )
    except Exception:
        pass

    return {"detail": "Scheduled call cancelled"}
