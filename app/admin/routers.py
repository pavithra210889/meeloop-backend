from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, func
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime, timezone
import json
import logging
import os

from app.dependencies import get_session
from app.admin.dependencies import SuperAdminDep
from app.users.models import FCMToken, User, UserSession
from app.services.firebase_service import firebase_service
from app.admin.models import AdminAuditLog
from app.database import engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Setup Jinja2 templates
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_dir)


# Custom dependency to check SQLAdmin session
async def check_sqladmin_session(request: Request) -> User:
    """Check if user is authenticated via SQLAdmin session"""
    user_id = request.session.get("admin_user_id")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Please login at /sqladmin/login"
        )

    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user or not user.is_superadmin or not user.is_active:
            raise HTTPException(
                status_code=403,
                detail="Super admin access required"
            )

    return user


# ── Schemas ───────────────────────────────────────────────────────────────────

class SendNotificationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="Notification title")
    message: str = Field(..., min_length=1, max_length=1000, description="Notification message")
    target: Literal[
        "all",
        "android",
        "ios",
        "active_users",
        "inactive_users",
        "new_users",
        "mobile_only",
        "desktop_only",
        "specific_users"
    ] = Field(..., description="Target audience")
    user_ids: Optional[List[str]] = Field(default=None, description="List of user IDs (required when target=specific_users)")


class RecipientDetail(BaseModel):
    user_id: str
    username: str
    email: str
    device_type: str
    status: str  # "success" or "failed"
    error: Optional[str] = None


class SendNotificationResponse(BaseModel):
    success: bool
    message: str
    stats: dict
    recipients: List[RecipientDetail] = []


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/push-notification-page", response_class=HTMLResponse)
async def push_notification_page(
    request: Request,
    admin: User = Depends(check_sqladmin_session),
):
    """
    Render the push notification form page
    Requires super admin authentication via SQLAdmin session
    """
    return templates.TemplateResponse(
        "custom/send_notification.html",
        {"request": request, "admin": admin}
    )


@router.post("/send-notification", response_model=SendNotificationResponse)
async def send_push_notification(
    request: SendNotificationRequest,
    admin: User = Depends(check_sqladmin_session),
    session: Session = Depends(get_session),
):
    """
    Send push notification to users based on target filter

    Requires super admin authentication via SQLAdmin session
    """
    try:
        # Validate user_ids when target is specific_users
        if request.target == "specific_users":
            if not request.user_ids or len(request.user_ids) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="user_ids is required when target is 'specific_users'"
                )

        # Build query to fetch FCM tokens with user details
        from sqlmodel import col
        from datetime import timedelta

        query = (
            select(FCMToken, User)
            .join(User, FCMToken.user_id == User.id)
            .where(FCMToken.is_active == True)
        )

        # Calculate date thresholds for activity filters
        now = datetime.now(timezone.utc)
        seven_days_ago = now - timedelta(days=7)
        thirty_days_ago = now - timedelta(days=30)

        # Apply filters based on target
        if request.target == "android":
            query = query.where(FCMToken.device_type == "android")

        elif request.target == "ios":
            query = query.where(FCMToken.device_type == "ios")

        elif request.target == "active_users":
            # Users with active sessions in last 7 days
            # Subquery to get users with recent session activity
            active_user_ids = (
                select(UserSession.user_id)
                .where(
                    UserSession.is_active == True,
                    UserSession.last_activity >= seven_days_ago
                )
                .distinct()
            )
            query = query.where(User.id.in_(active_user_ids))

        elif request.target == "inactive_users":
            # Users without any recent sessions (30+ days or never)
            # Subquery to get users with recent activity
            recent_user_ids = (
                select(UserSession.user_id)
                .where(
                    UserSession.is_active == True,
                    UserSession.last_activity >= thirty_days_ago
                )
                .distinct()
            )
            # Filter to users NOT in recent_user_ids
            query = query.where(~User.id.in_(recent_user_ids))

        elif request.target == "new_users":
            # Users with first session created in last 7 days (proxy for "new users")
            # Subquery to get users whose earliest session is within 7 days
            new_user_ids = (
                select(UserSession.user_id)
                .group_by(UserSession.user_id)
                .having(func.min(UserSession.created_at) >= seven_days_ago)
            )
            query = query.where(User.id.in_(new_user_ids))

        elif request.target == "mobile_only":
            # Android + iOS devices only
            query = query.where(FCMToken.device_type.in_(["android", "ios"]))

        elif request.target == "desktop_only":
            # Desktop/web devices only
            query = query.where(
                (FCMToken.device_type == "web") |
                (FCMToken.device_type == "desktop") |
                (FCMToken.device_type == None)  # Legacy tokens without device_type
            )

        elif request.target == "specific_users":
            query = query.where(FCMToken.user_id.in_(request.user_ids))

        # "all" doesn't need additional filters

        # Execute query and build token-to-user mapping
        results = session.exec(query).all()
        fcm_tokens = [fcm_token for fcm_token, user in results]

        # Create mapping: token -> user info for later lookup
        token_to_user = {
            fcm_token.token: {
                "user_id": str(user.id),
                "username": user.username,
                "email": user.email,
                "device_type": fcm_token.device_type
            }
            for fcm_token, user in results
        }

        if not fcm_tokens:
            # Log audit even for zero tokens
            audit_log = AdminAuditLog(
                admin_id=admin.id,
                action="send_push_notification",
                target_type="fcm_notification",
                target_id=None,
                meta=json.dumps({
                    "title": request.title,
                    "message": request.message,
                    "target": request.target,
                    "user_ids": request.user_ids if request.target == "specific_users" else None,
                    "tokens_found": 0,
                    "result": "no_tokens_found"
                }),
                ip_address=None,
            )
            session.add(audit_log)
            session.commit()

            raise HTTPException(
                status_code=404,
                detail=f"No active FCM tokens found for target: {request.target}"
            )

        # Extract token strings
        token_strings = [fcm_token.token for fcm_token in fcm_tokens]

        logger.info(f"Admin {admin.username} sending notification to {len(token_strings)} devices")

        # Send notification via Firebase
        push_result = await firebase_service.send_notification(
            fcm_tokens=token_strings,
            title=request.title,
            body=request.message,
            data={
                "type": "admin_broadcast",
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "sent_by": admin.username
            },
            session=session
        )

        # Build detailed recipients list with success/failure status
        recipients = []
        success_indices = []
        failure_indices = []

        # Parse Firebase response to determine which tokens succeeded/failed
        if "responses" in push_result:
            # If Firebase returned individual responses
            for i, resp in enumerate(push_result["responses"]):
                if i < len(token_strings):
                    token = token_strings[i]
                    user_info = token_to_user.get(token, {})
                    recipient = {
                        "user_id": user_info.get("user_id", "unknown"),
                        "username": user_info.get("username", "unknown"),
                        "email": user_info.get("email", "unknown"),
                        "device_type": user_info.get("device_type", "unknown"),
                        "status": "success" if resp.get("success") else "failed",
                        "error": str(resp.get("exception")) if not resp.get("success") else None
                    }
                    recipients.append(recipient)
                    if resp.get("success"):
                        success_indices.append(i)
                    else:
                        failure_indices.append(i)
        else:
            # Fallback: assume first N succeeded based on success_count
            success_count = push_result.get("success_count", 0)
            for i, token in enumerate(token_strings):
                user_info = token_to_user.get(token, {})
                # Simple heuristic: first success_count are successful
                status = "success" if i < success_count else "failed"
                recipient = {
                    "user_id": user_info.get("user_id", "unknown"),
                    "username": user_info.get("username", "unknown"),
                    "email": user_info.get("email", "unknown"),
                    "device_type": user_info.get("device_type", "unknown"),
                    "status": status,
                    "error": None if status == "success" else "Unknown error"
                }
                recipients.append(recipient)

        # Log the action in audit log with detailed recipients
        audit_log = AdminAuditLog(
            admin_id=admin.id,
            action="send_push_notification",
            target_type="fcm_notification",
            target_id=None,
            meta=json.dumps({
                "title": request.title,
                "message": request.message,
                "target": request.target,
                "user_ids": request.user_ids if request.target == "specific_users" else None,
                "tokens_found": len(token_strings),
                "success_count": push_result.get("success_count", 0),
                "failure_count": push_result.get("failure_count", 0),
                "recipients": recipients,  # Detailed list of all recipients with status
                "errors": push_result.get("errors", [])[:5]  # Limit to first 5 errors
            }),
            ip_address=None,
        )
        session.add(audit_log)
        session.commit()

        logger.info(f"Notification sent: {push_result.get('success_count', 0)} success, {push_result.get('failure_count', 0)} failed")

        return SendNotificationResponse(
            success=True,
            message=f"Notification sent successfully to {push_result.get('success_count', 0)} devices",
            stats={
                "total_tokens": len(token_strings),
                "success_count": push_result.get("success_count", 0),
                "failure_count": push_result.get("failure_count", 0),
                "target": request.target
            },
            recipients=recipients
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending push notification: {e}")

        # Log failed attempt
        try:
            audit_log = AdminAuditLog(
                admin_id=admin.id,
                action="send_push_notification_failed",
                target_type="fcm_notification",
                target_id=None,
                meta=json.dumps({
                    "title": request.title,
                    "message": request.message,
                    "target": request.target,
                    "error": str(e)
                }),
                ip_address=None,
            )
            session.add(audit_log)
            session.commit()
        except Exception as audit_error:
            logger.error(f"Failed to log audit: {audit_error}")

        raise HTTPException(
            status_code=500,
            detail=f"Failed to send notification: {str(e)}"
        )
