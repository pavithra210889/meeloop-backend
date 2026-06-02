import asyncio
import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select, or_

from app.database import engine
from app.users.models import User
from app.notifications.enums import NotificationType, NotificationCategory
from app.notifications.services.notification_service import notification_service
from app.sockets.socketio_server import sio
from app.sockets.socketio_events import user_room
from app.redis_client import redis_client
from .models import ScheduledCall, ScheduledCallStatus

logger = logging.getLogger(__name__)

POLL_INTERVAL = 30  # seconds
REMINDER_MINUTES = 15
EXPIRY_MINUTES = 10


async def _acquire_lock(call_id: str, action: str) -> bool:
    """Acquire a Redis lock to prevent duplicate processing in multi-worker setups."""
    key = f"scheduled_call_lock:{call_id}:{action}"
    return await redis_client.set(key, "1", nx=True, ex=120)


async def _send_reminder(sc: ScheduledCall, session: Session) -> None:
    """Send 15-minute reminder to both participants."""
    scheduler = session.get(User, sc.scheduler_id)
    participant = session.get(User, sc.participant_id)
    if not scheduler or not participant:
        return

    call_type = "video call" if sc.is_video_call else "voice call"
    time_str = sc.scheduled_at.strftime("%I:%M %p")

    for user, other in [(scheduler, participant), (participant, scheduler)]:
        try:
            await notification_service.create_notification(
                notification_type=NotificationType.SCHEDULED_CALL_REMINDER,
                recipient_id=user.id,
                sender_id=other.id,
                title=f"Call with {other.name} in {REMINDER_MINUTES} minutes",
                message=f"Scheduled {call_type} at {time_str}",
                session=session,
                category=NotificationCategory.CALLS,
                redirect_to="/calls",
                redirect_type="scheduled_call",
                redirect_id=sc.id,
                meta={
                    "scheduled_call_id": sc.id,
                    "is_video_call": sc.is_video_call,
                    "scheduled_at": sc.scheduled_at.isoformat(),
                    "peer_id": other.id,
                    "peer_name": other.name,
                },
                priority=1,
            )
        except Exception as e:
            logger.error(f"Failed to send reminder notification to {user.id}: {e}")

        try:
            await sio.emit(
                "scheduled_call:reminder",
                {
                    "id": sc.id,
                    "peer_id": other.id,
                    "peer_name": other.name,
                    "scheduled_at": sc.scheduled_at.isoformat(),
                    "is_video_call": sc.is_video_call,
                },
                room=user_room(user.id),
            )
        except Exception as e:
            logger.error(f"Failed to emit reminder socket to {user.id}: {e}")

    sc.status = ScheduledCallStatus.REMINDED
    sc.reminder_sent_at = datetime.now()
    sc.updated_at = datetime.now()
    session.add(sc)
    session.commit()
    logger.info(f"Sent reminder for scheduled call {sc.id}")


async def _send_trigger(sc: ScheduledCall, session: Session) -> None:
    """Send 'starting now' notification to both participants."""
    scheduler = session.get(User, sc.scheduler_id)
    participant = session.get(User, sc.participant_id)
    if not scheduler or not participant:
        return

    call_type = "video call" if sc.is_video_call else "voice call"

    # Scheduler gets "Tap to call" prompt
    try:
        await notification_service.create_notification(
            notification_type=NotificationType.SCHEDULED_CALL_STARTING,
            recipient_id=scheduler.id,
            sender_id=participant.id,
            title=f"Time to call {participant.name}",
            message=f"Your scheduled {call_type} is starting now",
            session=session,
            category=NotificationCategory.CALLS,
            redirect_to="/calls",
            redirect_type="scheduled_call",
            redirect_id=sc.id,
            meta={
                "scheduled_call_id": sc.id,
                "is_video_call": sc.is_video_call,
                "peer_id": participant.id,
                "peer_name": participant.name,
                "action": "call_now",
            },
            priority=2,
        )
    except Exception as e:
        logger.error(f"Failed to send trigger to scheduler {scheduler.id}: {e}")

    # Participant gets "Expecting call" notification
    try:
        await notification_service.create_notification(
            notification_type=NotificationType.SCHEDULED_CALL_STARTING,
            recipient_id=participant.id,
            sender_id=scheduler.id,
            title=f"Expecting call from {scheduler.name}",
            message=f"Your scheduled {call_type} is starting now",
            session=session,
            category=NotificationCategory.CALLS,
            redirect_to="/calls",
            redirect_type="scheduled_call",
            redirect_id=sc.id,
            meta={
                "scheduled_call_id": sc.id,
                "is_video_call": sc.is_video_call,
                "peer_id": scheduler.id,
                "peer_name": scheduler.name,
                "action": "expect_call",
            },
            priority=2,
        )
    except Exception as e:
        logger.error(f"Failed to send trigger to participant {participant.id}: {e}")

    # Socket events
    for user, other, action in [
        (scheduler, participant, "call_now"),
        (participant, scheduler, "expect_call"),
    ]:
        try:
            await sio.emit(
                "scheduled_call:starting",
                {
                    "id": sc.id,
                    "peer_id": other.id,
                    "peer_name": other.name,
                    "is_video_call": sc.is_video_call,
                    "action": action,
                },
                room=user_room(user.id),
            )
        except Exception as e:
            logger.error(f"Failed to emit trigger socket to {user.id}: {e}")

    sc.status = ScheduledCallStatus.TRIGGERED
    sc.trigger_sent_at = datetime.now()
    sc.updated_at = datetime.now()
    session.add(sc)
    session.commit()
    logger.info(f"Sent trigger for scheduled call {sc.id}")


async def run_scheduled_call_checker() -> None:
    """Background polling loop that checks for due scheduled calls."""
    logger.info("Scheduled call checker started")
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)
            now = datetime.now()
            reminder_threshold = now + timedelta(minutes=REMINDER_MINUTES)

            with Session(engine) as session:
                # 1. Send reminders for calls due within 15 minutes
                pending_for_reminder = session.exec(
                    select(ScheduledCall).where(
                        ScheduledCall.status == ScheduledCallStatus.PENDING,
                        ScheduledCall.scheduled_at <= reminder_threshold,
                        ScheduledCall.reminder_sent_at.is_(None),
                    )
                ).all()

                for sc in pending_for_reminder:
                    if sc.scheduled_at <= now:
                        # Already past scheduled time, skip reminder and go to trigger
                        continue
                    if await _acquire_lock(sc.id, "reminder"):
                        await _send_reminder(sc, session)

                # 2. Trigger calls that are due now
                due_for_trigger = session.exec(
                    select(ScheduledCall).where(
                        or_(
                            ScheduledCall.status == ScheduledCallStatus.PENDING,
                            ScheduledCall.status == ScheduledCallStatus.REMINDED,
                        ),
                        ScheduledCall.scheduled_at <= now,
                    )
                ).all()

                for sc in due_for_trigger:
                    if await _acquire_lock(sc.id, "trigger"):
                        await _send_trigger(sc, session)

                # 3. Expire triggered calls that were never completed
                expiry_threshold = now - timedelta(minutes=EXPIRY_MINUTES)
                expired = session.exec(
                    select(ScheduledCall).where(
                        ScheduledCall.status == ScheduledCallStatus.TRIGGERED,
                        ScheduledCall.scheduled_at <= expiry_threshold,
                    )
                ).all()

                for sc in expired:
                    sc.status = ScheduledCallStatus.EXPIRED
                    sc.updated_at = datetime.now()
                    session.add(sc)
                session.commit()

                if expired:
                    logger.info(f"Expired {len(expired)} scheduled calls")

        except Exception as e:
            logger.error(f"Scheduled call checker error: {e}")
