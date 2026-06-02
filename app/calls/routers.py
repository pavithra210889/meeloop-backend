from .models import Call, CallResponse
from sqlmodel import select, Session, and_, or_
from ..users.models import User, UserBasic
from ..users.routers import get_current_active_user
from ..dependencies import SessionDep
from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Annotated, Optional

router = APIRouter(tags=["calls"])


@router.get("/calls/", response_model=list[CallResponse])
async def get_call_history(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    user_id: Optional[str] = Query(None, description="Filter calls with a specific user"),
    limit: int = Query(50, ge=1, le=100),
    before_id: Optional[str] = Query(None, description="Return calls before this ID (cursor)"),
):
    if user_id is not None:
        statement = (
            select(Call)
            .where(
                or_(
                    and_(Call.call_from == current_user.id, Call.call_to == user_id),
                    and_(Call.call_from == user_id, Call.call_to == current_user.id),
                )
            )
            .order_by(Call.id.desc())
        )
    else:
        statement = (
            select(Call)
            .where((Call.call_from == current_user.id) | (Call.call_to == current_user.id))
            .order_by(Call.id.desc())
        )

    if before_id is not None:
        statement = statement.where(Call.id < before_id)

    call_records = session.exec(statement.limit(limit)).all()
    response = []
    for call in call_records:
        from_user = session.get(User, call.call_from)
        to_user = session.get(User, call.call_to)
        call_read = CallResponse(
            id=call.id,
            call_status=call.call_status,
            duration_seconds=call.duration_seconds,
            is_video_call=call.is_video_call,
            created_at=call.created_at,
            updated_at=call.updated_at,
            call_from=UserBasic(**from_user.model_dump()),
            call_to=UserBasic(**to_user.model_dump()),
        )

        response.append(call_read)
    return response


@router.delete("/calls/")
async def clear_call_history(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    user_id: Optional[str] = Query(None, description="Clear calls with a specific user only"),
):
    """Delete call history for current user, optionally scoped to a specific user"""
    if user_id is not None:
        statement = select(Call).where(
            or_(
                and_(Call.call_from == current_user.id, Call.call_to == user_id),
                and_(Call.call_from == user_id, Call.call_to == current_user.id),
            )
        )
    else:
        statement = select(Call).where(
            (Call.call_from == current_user.id) | (Call.call_to == current_user.id)
        )
    calls = session.exec(statement).all()
    for call in calls:
        session.delete(call)
    session.commit()
    return {"detail": f"Deleted {len(calls)} call records"}


@router.delete("/calls/{call_id}")
async def delete_call(
    call_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    """Delete a single call record"""
    call = session.get(Call, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if call.call_from != current_user.id and call.call_to != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    session.delete(call)
    session.commit()
    return {"detail": "Call deleted"}
