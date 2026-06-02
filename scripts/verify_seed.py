"""
Meeloop Seed Data Verification Script
======================================
Queries all seeded data and displays counts + sample records.

Usage:
    cd backend
    source env/bin/activate
    python scripts/verify_seed.py           # Summary counts
    python scripts/verify_seed.py --detail  # Detailed sample data
"""

import sys
import os

# Add backend root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select, col, func
from app.database import engine

# Models
from app.users.models import (
    User, UserSession, UserPreference, Follow, Block, UserDevice
)
from app.meme_templates.models import MemeTemplates  # noqa: F401 — SQLAlchemy relationship resolution
from app.messages.models import Chat, Message, Reaction
from app.posts.models import Post, Media, Like, Comment, BookmarkFolder, Bookmark
from app.stories.models import Story, StoryMedia, StoryView
from app.calls.models import Call
from app.notifications.models import Notification, NotificationPreference
from app.loops.models import (
    LoopProfile, LoopProfilePhoto, LoopFriend, LoopRequest,
    LoopChat, LoopMessage, LoopReaction
)


def count_table(session, model, filter_col=None, filter_ids=None):
    """Count rows in a table, optionally filtered by user IDs."""
    stmt = select(func.count()).select_from(model)
    if filter_col and filter_ids:
        stmt = stmt.where(filter_col.in_(filter_ids))
    return session.exec(stmt).one()


def print_section(title, items):
    """Print a section header and items."""
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")
    for label, value in items:
        print(f"  {label:<35} {value}")


def main():
    detail = "--detail" in sys.argv

    print("=" * 60)
    print("  Meeloop Seed Data Verification")
    print("=" * 60)

    with Session(engine) as session:
        # Get test users
        test_users = session.exec(
            select(User).where(col(User.username).startswith("testuser")).order_by(User.username)
        ).all()
        test_user_ids = [u.id for u in test_users]

        if not test_user_ids:
            print("\n  No seed data found. Run: python seed_data.py")
            return

        # ── Users ──
        print_section("Users", [
            ("Test users", len(test_users)),
            ("Total users in DB", count_table(session, User)),
        ])

        if detail:
            print("\n  Sample users:")
            print(f"  {'Username':<15} {'Name':<20} {'Email':<30} {'Gender':<8} {'Loops'}")
            print(f"  {'─'*15} {'─'*20} {'─'*30} {'─'*8} {'─'*5}")
            for u in test_users[:5]:
                print(f"  {u.username:<15} {u.name:<20} {u.email:<30} {str(u.gender or '-'):<8} {'Yes' if u.is_loop_enabled else 'No'}")
            if len(test_users) > 5:
                print(f"  ... and {len(test_users) - 5} more")

        # ── Sessions ──
        session_count = count_table(session, UserSession, col(UserSession.user_id), test_user_ids)
        print_section("Auth Sessions", [
            ("Active sessions", session_count),
        ])

        if detail:
            sample_session = session.exec(
                select(UserSession).where(col(UserSession.user_id).in_(test_user_ids)).limit(1)
            ).first()
            if sample_session:
                print(f"\n  Sample session token (testuser1):")
                print(f"    Token: {sample_session.session_token[:20]}...")
                print(f"    Expires: {sample_session.expires_at}")

        # ── Preferences ──
        pref_count = count_table(session, UserPreference, col(UserPreference.user_id), test_user_ids)
        npref_count = count_table(session, NotificationPreference, col(NotificationPreference.user_id), test_user_ids)
        print_section("Settings", [
            ("User preferences", pref_count),
            ("Notification preferences", npref_count),
        ])

        if detail:
            prefs = session.exec(
                select(UserPreference).where(col(UserPreference.user_id).in_(test_user_ids)).limit(5)
            ).all()
            if prefs:
                print(f"\n  Sample preferences:")
                for p in prefs:
                    user = next((u for u in test_users if u.id == p.user_id), None)
                    uname = user.username if user else "?"
                    print(f"    {uname}: theme={p.theme_mode}, lang={p.language}, ui={p.ui_mode}")

        # ── Follows ──
        follow_count = count_table(session, Follow, col(Follow.follower_id), test_user_ids)
        block_count = count_table(session, Block, col(Block.blocker_id), test_user_ids)
        print_section("Social Graph", [
            ("Follow relationships", follow_count),
            ("Block relationships", block_count),
        ])

        if detail:
            # Show follower/following counts for first 5 users
            print(f"\n  Follower/Following counts:")
            for u in test_users[:5]:
                followers = session.exec(
                    select(func.count()).select_from(Follow).where(Follow.following_id == u.id)
                ).one()
                following = session.exec(
                    select(func.count()).select_from(Follow).where(Follow.follower_id == u.id)
                ).one()
                print(f"    {u.username}: {followers} followers, {following} following")

        # ── Posts ──
        post_count = count_table(session, Post, col(Post.posted_by), test_user_ids)
        test_posts = session.exec(
            select(Post).where(col(Post.posted_by).in_(test_user_ids))
        ).all()
        test_post_ids = [p.id for p in test_posts]

        media_count = count_table(session, Media, col(Media.post_id), test_post_ids) if test_post_ids else 0
        like_count = count_table(session, Like, col(Like.post_id), test_post_ids) if test_post_ids else 0
        comment_count = count_table(session, Comment, col(Comment.post_id), test_post_ids) if test_post_ids else 0

        # Count replies
        reply_count = 0
        if test_post_ids:
            reply_count = session.exec(
                select(func.count()).select_from(Comment).where(
                    col(Comment.post_id).in_(test_post_ids),
                    Comment.reply_to != None
                )
            ).one()

        print_section("Posts & Content", [
            ("Posts", post_count),
            ("Media files", media_count),
            ("Likes", like_count),
            ("Comments (total)", comment_count),
            ("  └─ Replies", reply_count),
        ])

        if detail:
            print(f"\n  Sample posts:")
            for p in test_posts[:5]:
                user = next((u for u in test_users if u.id == p.posted_by), None)
                uname = user.username if user else "?"
                caption = (p.caption[:40] + "...") if p.caption and len(p.caption) > 40 else (p.caption or "-")
                print(f"    [{uname}] {caption} ({p.created_at.strftime('%Y-%m-%d')})")

        # ── Bookmarks ──
        folder_count = count_table(session, BookmarkFolder, col(BookmarkFolder.created_by), test_user_ids)
        folders = session.exec(
            select(BookmarkFolder).where(col(BookmarkFolder.created_by).in_(test_user_ids))
        ).all()
        folder_ids = [f.id for f in folders]
        bookmark_count = count_table(session, Bookmark, col(Bookmark.bookmark_folder_id), folder_ids) if folder_ids else 0

        print_section("Bookmarks", [
            ("Bookmark folders", folder_count),
            ("Bookmarked posts", bookmark_count),
        ])

        # ── Chats & Messages ──
        chat_count = count_table(session, Chat, col(Chat.participant_one_id), test_user_ids)
        chats = session.exec(
            select(Chat).where(col(Chat.participant_one_id).in_(test_user_ids))
        ).all()
        chat_ids = [c.id for c in chats]
        msg_count = count_table(session, Message, col(Message.chat_id), chat_ids) if chat_ids else 0
        reaction_count = count_table(session, Reaction, col(Reaction.user_id), test_user_ids)

        print_section("Messaging", [
            ("Chats", chat_count),
            ("Messages", msg_count),
            ("Message reactions", reaction_count),
        ])

        if detail:
            print(f"\n  Sample chats:")
            for c in chats[:5]:
                u1 = next((u for u in test_users if u.id == c.participant_one_id), None)
                u2 = next((u for u in test_users if u.id == c.participant_two_id), None)
                u1name = u1.username if u1 else "?"
                u2name = u2.username if u2 else "?"
                last = (c.last_message[:30] + "...") if c.last_message and len(c.last_message) > 30 else (c.last_message or "-")
                print(f"    {u1name} <-> {u2name}: {last}")

        # ── Stories ──
        story_count = count_table(session, Story, col(Story.user_id), test_user_ids)
        stories = session.exec(select(Story).where(col(Story.user_id).in_(test_user_ids))).all()
        story_ids = [s.id for s in stories]
        view_count = count_table(session, StoryView, col(StoryView.story_id), story_ids) if story_ids else 0

        print_section("Stories", [
            ("Active stories", story_count),
            ("Story views", view_count),
        ])

        # ── Calls ──
        call_count = count_table(session, Call, col(Call.call_from), test_user_ids)
        print_section("Calls", [
            ("Call records", call_count),
        ])

        if detail:
            calls = session.exec(
                select(Call).where(col(Call.call_from).in_(test_user_ids)).limit(5)
            ).all()
            print(f"\n  Sample calls:")
            for c in calls:
                caller = next((u for u in test_users if u.id == c.call_from), None)
                callee = next((u for u in test_users if u.id == c.call_to), None)
                cname = caller.username if caller else "?"
                tname = callee.username if callee else "?"
                vtype = "Video" if c.is_video_call else "Audio"
                dur = f"{c.duration_seconds}s" if c.duration_seconds else "-"
                print(f"    {cname} -> {tname}: {vtype} | {c.call_status} | {dur}")

        # ── Notifications ──
        notif_count = count_table(session, Notification, col(Notification.recipient_id), test_user_ids)
        unread_count = session.exec(
            select(func.count()).select_from(Notification).where(
                col(Notification.recipient_id).in_(test_user_ids),
                Notification.is_read == False
            )
        ).one()
        print_section("Notifications", [
            ("Total notifications", notif_count),
            ("Unread", unread_count),
        ])

        # ── Loops ──
        loop_count = count_table(session, LoopProfile, col(LoopProfile.user_id), test_user_ids)
        loop_profiles = session.exec(
            select(LoopProfile).where(col(LoopProfile.user_id).in_(test_user_ids))
        ).all()
        loop_profile_ids = [lp.id for lp in loop_profiles]

        photo_count = count_table(session, LoopProfilePhoto, col(LoopProfilePhoto.loop_profile_id), loop_profile_ids) if loop_profile_ids else 0
        friend_count = count_table(session, LoopFriend, col(LoopFriend.loop_profile_id), loop_profile_ids) if loop_profile_ids else 0

        pending_requests = 0
        if loop_profile_ids:
            pending_requests = session.exec(
                select(func.count()).select_from(LoopRequest).where(
                    col(LoopRequest.receiver_profile_id).in_(loop_profile_ids),
                    LoopRequest.status == "pending"
                )
            ).one()

        loop_chat_count = count_table(session, LoopChat, col(LoopChat.profile1_id), loop_profile_ids) if loop_profile_ids else 0
        loop_chats = session.exec(
            select(LoopChat).where(col(LoopChat.profile1_id).in_(loop_profile_ids))
        ).all() if loop_profile_ids else []
        loop_chat_ids = [lc.id for lc in loop_chats]
        loop_msg_count = count_table(session, LoopMessage, col(LoopMessage.chat_id), loop_chat_ids) if loop_chat_ids else 0

        print_section("Loops", [
            ("Loop profiles", loop_count),
            ("Profile photos", photo_count),
            ("Friend connections", friend_count),
            ("Pending requests", pending_requests),
            ("Loop chats", loop_chat_count),
            ("Loop messages", loop_msg_count),
        ])

        # ── Summary ──
        total = (len(test_users) + session_count + pref_count + npref_count +
                 follow_count + block_count + post_count + media_count +
                 like_count + comment_count + folder_count + bookmark_count +
                 chat_count + msg_count + reaction_count + story_count +
                 view_count + call_count + notif_count + loop_count +
                 photo_count + friend_count + loop_chat_count + loop_msg_count)

        print(f"\n{'=' * 60}")
        print(f"  TOTAL RECORDS: {total}")
        print(f"{'=' * 60}")
        print()
        print(f"  Login credentials:")
        print(f"    Username: testuser1 .. testuser{len(test_users)}")
        print(f"    Password: Test@123")
        print(f"    Email:    testuser1@meeloop.com .. testuser{len(test_users)}@meeloop.com")
        print()


if __name__ == "__main__":
    main()
