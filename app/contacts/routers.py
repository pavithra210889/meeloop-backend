from .models import Contact, BaseContact
from ..users.routers import get_current_active_user
from ..users.models import User, UserBasic, Follow
from ..dependencies import SessionDep
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select, func
from typing import Annotated, List
from phonenumbers import parse, format_number, PhoneNumberFormat

router = APIRouter(tags=["contacts"])


def normalize_phone_number(raw_number: str, region="IN") -> str:
    parsed = parse(raw_number, region)
    return format_number(parsed, PhoneNumberFormat.E164)


@router.post("/contacts/bulk")
async def upload_contacts_bulk(
    contacts: List[BaseContact],
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
):
    if len(contacts) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 contacts per request")

    # Build a set of existing normalized numbers for this user to skip duplicates
    existing_rows = session.exec(
        select(Contact.normalized_number).where(
            Contact.contact_owner_id == current_user.id
        )
    ).all()
    existing_set = set(existing_rows or [])

    inserted = 0
    skipped = 0
    invalid = 0

    for contact in contacts:
        try:
            normalized = normalize_phone_number(contact.phone_num)
        except Exception:
            normalized = None

        # Skip invalid or empty numbers
        if not normalized:
            invalid += 1
            continue

        # Skip duplicates per owner based on normalized number
        if normalized in existing_set:
            skipped += 1
            continue

        new_contact = Contact(
            name=contact.name,
            email=contact.email,
            phone_num=contact.phone_num,
            normalized_number=normalized,
            contact_owner_id=current_user.id,
        )
        session.add(new_contact)
        existing_set.add(normalized)
        inserted += 1

    session.commit()
    return {
        "message": "Contacts processed",
        "inserted": inserted,
        "skipped": skipped,
        "invalid": invalid,
        "total": len(contacts),
    }


@router.get("/contacts/matches")
async def get_contact_matches(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Return users whose phone numbers match the current user's uploaded contacts.

    - Excludes the current user
    - Honors basic blocking rules in users router via helper (if needed later)
    """
    # Gather normalized numbers from this user's contacts
    numbers = session.exec(
        select(Contact.normalized_number).where(
            Contact.contact_owner_id == current_user.id
        )
    ).all()
    if not numbers:
        return {"items": [], "total": 0, "has_more": False}

    # Total count
    total = session.exec(
        select(func.count())
        .select_from(User)
        .where((User.id != current_user.id) & (User.phone_number.in_(numbers)))
    ).one()

    # Find users with matching phone numbers (excluding self and nulls)
    users = session.exec(
        select(User)
        .where((User.id != current_user.id) & (User.phone_number.in_(numbers)))
        .offset(offset)
        .limit(limit)
    ).all()

    following_ids = set(
        session.exec(
            select(Follow.following_id).where(Follow.follower_id == current_user.id)
        ).all()
    )

    items = [
        UserBasic(
            id=u.id,
            username=u.username,
            name=u.name,
            profile_pic=u.profile_pic,
            bio=u.bio,
            is_following=u.id in following_ids,
        )
        for u in users
        if u is not None
    ]
    return {"items": items, "total": total, "has_more": offset + limit < total}
