from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select

from ..dependencies import SessionDep
from ..users.routers import get_current_active_user
from ..users.models import User
from ..reports.models import (
    Report,
    ReportCreate,
    ReportRead,
    ReportStatusUpdate,
    ReportTarget,
    ModerationAction,
)
from ..posts.models import Post, Comment
from ..loops.models import LoopProfile, LoopMessage
from ..config import settings

router = APIRouter(prefix="/reports", tags=["reports"])


def _get_admin_user(current_user: User) -> User:
    admins = set([u.strip() for u in getattr(settings, "ADMIN_USERNAMES", "").split(",") if u.strip()])
    if current_user.username in admins:
        return current_user
    raise HTTPException(status_code=403, detail="Admin access required")


def _resolve_reported_user_id(session: SessionDep, target_type: ReportTarget, target_id: str) -> str | None:
    if target_type == ReportTarget.user:
        user = session.get(User, target_id)
        return user.id if user else None
    if target_type == ReportTarget.post:
        post = session.get(Post, target_id)
        return post.posted_by if post else None
    if target_type == ReportTarget.comment:
        comment = session.get(Comment, target_id)
        return comment.user_id if comment else None
    if target_type == ReportTarget.loop_profile:
        lp = session.get(LoopProfile, target_id)
        return lp.user_id if lp else None
    if target_type == ReportTarget.loop_message:
        lm = session.get(LoopMessage, target_id)
        if not lm:
            return None
        # Find sender loop profile -> user
        sp = session.get(LoopProfile, lm.sender_profile_id)
        return sp.user_id if sp else None
    return None


@router.post("/", response_model=ReportRead)
def create_report(
    payload: ReportCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    # Prevent self-report for user target unless 'impersonation'
    if payload.target_type == ReportTarget.user and payload.target_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot report yourself")

    # Validate target and resolve reported_user_id
    reported_user_id = _resolve_reported_user_id(session, payload.target_type, payload.target_id)
    if payload.target_type != ReportTarget.user and reported_user_id is None:
        raise HTTPException(status_code=404, detail="Target not found")
    if payload.target_type == ReportTarget.user and session.get(User, payload.target_id) is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Idempotency: if an open report by same reporter for same target exists, return it
    existing = session.exec(
        select(Report).where(
            Report.reporter_id == current_user.id,
            Report.target_type == payload.target_type,
            Report.target_id == payload.target_id,
            Report.status.in_(["open", "under_review"]),
        )
    ).first()
    if existing:
        # Map attachments string to list for response consistency
        attachments_list = None
        if existing.attachments:
            try:
                import json

                attachments_list = json.loads(existing.attachments)
            except Exception:
                attachments_list = None
        return ReportRead(
            id=existing.id,
            reporter_id=existing.reporter_id,
            target_type=existing.target_type,
            target_id=existing.target_id,
            reported_user_id=existing.reported_user_id,
            reason=existing.reason,
            details=existing.details,
            attachments=attachments_list,
            status=existing.status,
            created_at=existing.created_at,
            reviewed_by=existing.reviewed_by,
            reviewed_at=existing.reviewed_at,
        )

    attachments_str = None
    if payload.attachments:
        import json

        attachments_str = json.dumps(payload.attachments)

    report = Report(
        reporter_id=current_user.id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        reported_user_id=reported_user_id,
        reason=payload.reason,
        details=payload.details,
        attachments=attachments_str,
    )
    session.add(report)
    session.commit()
    session.refresh(report)

    return ReportRead(
        id=report.id,
        reporter_id=report.reporter_id,
        target_type=report.target_type,
        target_id=report.target_id,
        reported_user_id=report.reported_user_id,
        reason=report.reason,
        details=report.details,
        attachments=payload.attachments,
        status=report.status,
        created_at=report.created_at,
        reviewed_by=report.reviewed_by,
        reviewed_at=report.reviewed_at,
    )


@router.get("/my")
def my_reports(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    from sqlmodel import func

    total = session.exec(
        select(func.count())
        .select_from(Report)
        .where(Report.reporter_id == current_user.id)
    ).one()

    items = session.exec(
        select(Report)
        .where(Report.reporter_id == current_user.id)
        .order_by(Report.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    out: list[ReportRead] = []
    for r in items:
        attachments_list = None
        if r.attachments:
            try:
                import json

                attachments_list = json.loads(r.attachments)
            except Exception:
                attachments_list = None
        out.append(
            ReportRead(
                id=r.id,
                reporter_id=r.reporter_id,
                target_type=r.target_type,
                target_id=r.target_id,
                reported_user_id=r.reported_user_id,
                reason=r.reason,
                details=r.details,
                attachments=attachments_list,
                status=r.status,
                created_at=r.created_at,
                reviewed_by=r.reviewed_by,
                reviewed_at=r.reviewed_at,
            )
        )
    return {"items": out, "total": total, "has_more": offset + limit < total}


@router.get("/", response_model=List[ReportRead])
def list_reports(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    status: str | None = Query(None),
    target_type: str | None = Query(None),
    reported_user_id: str | None = Query(None),
    limit: int = 50,
    offset: int = 0,
):
    _get_admin_user(current_user)
    stmt = select(Report)
    if status:
        stmt = stmt.where(Report.status == status)
    if target_type:
        stmt = stmt.where(Report.target_type == target_type)
    if reported_user_id:
        stmt = stmt.where(Report.reported_user_id == reported_user_id)
    stmt = stmt.order_by(Report.created_at.desc()).offset(offset).limit(limit)
    items = session.exec(stmt).all()
    out: list[ReportRead] = []
    for r in items:
        attachments_list = None
        if r.attachments:
            try:
                import json

                attachments_list = json.loads(r.attachments)
            except Exception:
                attachments_list = None
        out.append(
            ReportRead(
                id=r.id,
                reporter_id=r.reporter_id,
                target_type=r.target_type,
                target_id=r.target_id,
                reported_user_id=r.reported_user_id,
                reason=r.reason,
                details=r.details,
                attachments=attachments_list,
                status=r.status,
                created_at=r.created_at,
                reviewed_by=r.reviewed_by,
                reviewed_at=r.reviewed_at,
            )
        )
    return out


@router.patch("/{report_id}")
def update_report_status(
    report_id: str,
    payload: ReportStatusUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    _get_admin_user(current_user)
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    # Update status first
    report.status = payload.status
    report.reviewed_by = current_user.id
    from datetime import datetime
    report.reviewed_at = report.reviewed_at or datetime.now()
    session.add(report)
    session.commit()
    return {"detail": "Report updated"}


class ReportActionPayload(ReportStatusUpdate):
    action: str | None = None
    action_meta: dict | None = None


@router.patch("/{report_id}/action")
def apply_report_action(
    report_id: str,
    payload: ReportActionPayload,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    _get_admin_user(current_user)
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if not payload.action:
        raise HTTPException(status_code=400, detail="Action is required")

    # Apply side-effects based on action
    action = payload.action
    meta = payload.action_meta or {}
    from datetime import datetime, timedelta
    import json

    def commit_action(action_str: str, action_meta: dict | None = None):
        session.add(
            ModerationAction(
                report_id=report.id,
                moderator_id=current_user.id,
                action=action_str,
                action_meta=json.dumps(action_meta) if action_meta else None,
            )
        )
        session.commit()

    # Targets
    if action in {"hide_post", "remove_post"}:
        from ..posts.models import Post

        post = session.get(Post, report.target_id)
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        if action == "hide_post":
            post.is_hidden = True
        elif action == "remove_post":
            post.is_hidden = True
            post.deleted_at = datetime.now()
        session.add(post)
        commit_action(action, meta)
    elif action in {"hide_comment", "remove_comment"}:
        from ..posts.models import Comment

        comment = session.get(Comment, report.target_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        if action == "hide_comment":
            comment.is_hidden = True
        elif action == "remove_comment":
            comment.is_hidden = True
            comment.deleted_at = datetime.now()
        session.add(comment)
        commit_action(action, meta)
    elif action in {"suspend_user_temp", "suspend_user_perm"}:
        # reported_user_id must exist
        user = session.get(User, report.reported_user_id) if report.reported_user_id else None
        if not user:
            raise HTTPException(status_code=404, detail="Reported user not found")
        if action == "suspend_user_perm":
            user.is_active = False
            user.suspended_until = None
        else:
            # default 7 days if not provided
            days = int(meta.get("days", 7))
            user.is_active = False
            user.suspended_until = datetime.now() + timedelta(days=days)
        session.add(user)
        commit_action(action, meta)
    elif action in {"hide_loop_message"}:
        from ..loops.models import LoopMessage

        lm = session.get(LoopMessage, report.target_id)
        if not lm:
            raise HTTPException(status_code=404, detail="Loop message not found")
        lm.is_hidden = True
        session.add(lm)
        commit_action(action, meta)
    elif action in {"hide_loop_profile"}:
        from ..loops.models import LoopProfile

        lp = session.get(LoopProfile, report.target_id)
        if not lp:
            raise HTTPException(status_code=404, detail="Loop profile not found")
        lp.is_suspended = True
        session.add(lp)
        commit_action(action, meta)
    else:
        raise HTTPException(status_code=400, detail="Unsupported action")

    # Update report status to action_taken if provided
    if payload.status:
        report.status = payload.status
        report.reviewed_by = current_user.id
        from datetime import datetime as dt
        report.reviewed_at = report.reviewed_at or dt.now()
        session.add(report)
        session.commit()

    return {"detail": "Action applied"}
