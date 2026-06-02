"""
Meeloop Seed Data Script
========================
Interactive script to populate the database with test data at any scale.

Usage:
    cd backend
    source env/bin/activate
    python scripts/seed_data.py                           # Interactive mode — asks what & how many
    python scripts/seed_data.py --all                     # Seed everything with default counts
    python scripts/seed_data.py --all --users 200 --posts 500 --messages 50000
    python scripts/seed_data.py --for-user nanne --all    # Seed data FOR a specific existing user
    python scripts/seed_data.py --for-user nanne --all --posts 50 --messages 1000
    python scripts/seed_data.py --clean                   # Interactive: pick what to delete
    python scripts/seed_data.py --clean --all             # Delete ALL seed data
    python scripts/seed_data.py --clean --only posts,stories  # Delete only posts & stories

Login Pattern:
    Username: testuser1 .. testuser{N}
    Password: Test@123
    Email:    testuser1@meeloop.com .. testuser{N}@meeloop.com

--for-user Mode:
    When --for-user is specified, seed data is biased towards the target user.
    The target user gets ~40% of posts, ~50% of incoming follows, messages, etc.
    Test users are still created as the "other people" in the data.

Note: Only deletes test data (testuser* accounts). Your real data is never touched.
"""

import sys
import os
import random
import time
import argparse
from datetime import datetime, timedelta, timezone

# Add backend root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select, col, text
from app.database import engine
from app.uuid_utils import generate_uuid
from app.security import get_password_hash, generate_session_token

# Models — all must be imported for SQLAlchemy relationship resolution
from app.users.models import (
    User, UserSession, UserPreference, Follow, Block, UserDevice, LoginActivity
)
from app.messages.models import Chat, Message, MessageType, MessageStatus, Reaction
from app.posts.models import Post, Media, Like, Comment, BookmarkFolder, Bookmark
from app.stories.models import Story, StoryMedia, StoryView
from app.calls.models import Call
from app.notifications.models import Notification, NotificationPreference
from app.loops.models import (
    LoopProfile, LoopProfilePhoto, LoopFriend, LoopRequest,
    LoopChat, LoopMessage, LoopReaction, GenderEnum, LoopRequestStatus
)
from app.meme_templates.models import MemeTemplates  # noqa: F401

# ─── Constants ──────────────────────────────────────────────────────────────

PASSWORD = "Test@123"
EMAIL_DOMAIN = "meeloop.com"
BATCH_SIZE = 500  # Commit every N records for large inserts

FIRST_NAMES = [
    "Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Sneha", "Arjun", "Kavya",
    "Rahul", "Diya", "Aditya", "Meera", "Karthik", "Ishita", "Nikhil", "Pooja",
    "Siddharth", "Riya", "Varun", "Tanvi", "Amit", "Divya", "Raj", "Nisha",
    "Sahil", "Aisha", "Dev", "Simran", "Kunal", "Tara", "Manish", "Prachi",
    "Akash", "Shruti", "Vivek", "Megha", "Harsh", "Neha", "Gaurav", "Pallavi",
    "Suresh", "Deepa", "Rakesh", "Swati", "Mohit", "Ankita", "Saurabh", "Ritika",
    "Ankit", "Jyoti",
]
LAST_NAMES = [
    "Sharma", "Patel", "Mehta", "Singh", "Reddy", "Gupta", "Nair", "Joshi",
    "Verma", "Kapoor", "Kumar", "Iyer", "Rao", "Malhotra", "Das", "Agarwal",
    "Bhat", "Chandra", "Mishra", "Desai", "Shah", "Pillai", "Pandey", "Bose",
    "Thakur", "Kaur", "Banerjee", "Menon", "Kulkarni", "Chopra",
]

BIOS = [
    "Living life one moment at a time", "Coffee addict & code enthusiast",
    "Exploring the world, one city at a time", "Music is my therapy",
    "Photographer | Traveler | Foodie", "Dream big, work hard",
    "Just here for the memes", "Tech nerd by day, gamer by night",
    "Art lover & creative soul", "Fitness freak & health junkie",
    "Reading is my superpower", "Plant parent", "Startup life hustle",
    "Minimalist. Maximalist ideas.", "Dogs > Everything",
    "Building cool things with code", "Wanderlust & chai lover",
    "Into crypto and Web3", "Full-stack developer", "Making the internet better",
]

POST_CAPTIONS = [
    "Just had the best weekend ever!", "New project coming soon... stay tuned",
    "Morning vibes", "Throwback to this amazing sunset", "Working on something exciting!",
    "Can't believe it's already 2026", "This view though", "Code. Coffee. Repeat.",
    "Finally finished my side project!", "Weekend plans: Netflix and chill",
    "Food is love, food is life", "New haircut, who dis?", "Gym progress update",
    "Road trip adventures", "Late night coding session",
    "Grateful for amazing friends", "Monday motivation", "Beach day!",
    "Just launched my app!", "Sunrise at 5am was worth it",
    "Home office setup upgrade", "Street food tour was epic",
    "Learning Kotlin is actually fun", "Hiking trail discovery",
    "That feeling when your code compiles first try", "Bookshelf update",
    "Festival vibes", "New music recommendations please",
    "Startup life: the good, the bad, and the ugly", "Sunday brunch at the new cafe",
    "Productivity hack that changed my life", "Coffee shop coding",
    "Just got my new phone", "Book recommendation thread",
    "Meal prep Sunday", "The grind never stops", "Design inspo",
    "My workspace tour", "Travel plans for next month", "Throwback Thursday",
]

COMMENTS_TEXT = [
    "Love this!", "Amazing shot!", "So cool!", "This is awesome",
    "Wow, great work!", "Keep it up!", "Looks incredible", "Where is this?",
    "Need to try this!", "Goals!", "So pretty!", "This made my day",
    "Can't wait to see more!", "Absolutely stunning", "Best thing I've seen today",
    "You're killing it!", "This is next level", "So inspiring",
    "I need this in my life", "Tell me more!", "Brilliant!", "Way too good",
    "This deserves more likes", "How did you do this?", "Tutorial please!",
    "Saved this for later", "My favorite post today", "Pure vibes",
    "Take me there!", "Wish I was there too", "Incredible work",
    "What camera did you use?", "Looks delicious!", "Dream destination",
    "This is everything", "Living your best life!", "Iconic",
    "Need this energy", "So relatable", "Facts!",
]

MESSAGES_TEXT = [
    "Hey! How's it going?", "What's up?", "Did you see the new update?",
    "Let's catch up soon!", "Thanks for sharing that", "Haha that's hilarious",
    "Sure, sounds good!", "I'll be there in 10", "Good morning!",
    "Can you send me that link?", "Omg yes!", "No way!", "That's great news!",
    "I was just thinking about that", "Let me check and get back to you",
    "Movie tonight?", "Have you tried the new restaurant?", "Happy birthday!",
    "Miss you!", "See you tomorrow", "Running late, sorry!",
    "Check out this meme", "What do you think about this?",
    "Just finished reading that book you recommended", "Game night this weekend?",
    "The weather is amazing today", "Did you hear about the event?",
    "Working from home today", "Just woke up lol", "Goodnight!",
    "Can we reschedule?", "Perfect timing", "That makes sense",
    "I'll send it over", "Thanks a lot!", "Sounds like a plan",
    "Let me know when you're free", "On my way!", "Already done",
    "Interesting perspective", "I agree completely", "Not sure about that",
    "Will do!", "Noted", "Let's discuss tomorrow", "How's work going?",
    "Any updates?", "Just saw your story", "Nice pic!", "lol",
]

STORY_TEXTS = [
    "What a beautiful day!", "Cooking something special tonight",
    "Mood: caffeinated", "New week, new goals", "Late night thoughts",
    "Sunset vibes", "Monday mood", "Weekend loading...",
    None, None, None, None, None,  # Some stories without text
]

LOOP_BIOS = [
    "Looking for interesting conversations", "Music enthusiast",
    "Foodie seeking fellow food lovers", "New in town",
    "Adventure seeker", "Let's talk about tech", "Movie buff",
    "Fitness partner wanted", "Book club anyone?", "Just vibing",
]

NOTIFICATION_TEMPLATES = [
    ("like", "posts", "liked your post", "post"),
    ("comment", "posts", "commented on your post", "post"),
    ("follow", "social", "started following you", "profile"),
    ("message", "messages", "sent you a message", "chat"),
    ("comment_reply", "posts", "replied to your comment", "post"),
    ("story_view", "social", "viewed your story", "story"),
    ("mention", "posts", "mentioned you in a post", "post"),
    ("call_missed", "calls", "tried to call you", "call"),
]

PLACEHOLDER_IMAGES = [f"/defaults/posts/sample{i}.jpg" for i in range(1, 6)]
PLACEHOLDER_AVATARS = [f"/defaults/profile/avatar{i}.png" for i in range(1, 6)]
EMOJIS = ["❤️", "😂", "😍", "🔥", "👍", "😮", "😢", "🎉", "💯", "🙏"]

# ─── Helpers ────────────────────────────────────────────────────────────────

def rdt(days_back=90):
    """Random past datetime."""
    return datetime.now() - timedelta(seconds=random.randint(0, days_back * 86400))


def rdt_recent(days_back=7):
    """Random recent datetime."""
    return datetime.now() - timedelta(seconds=random.randint(0, days_back * 86400))


def progress(current, total, label=""):
    """Print a progress bar."""
    pct = current / total * 100
    bar_len = 30
    filled = int(bar_len * current / total)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"\r   {bar} {pct:5.1f}% ({current}/{total}) {label}", end="", flush=True)
    if current >= total:
        print()


def batch_commit(session, counter, force=False):
    """Commit every BATCH_SIZE records."""
    if force or counter % BATCH_SIZE == 0:
        session.commit()


def pick_user(users, target_user=None, bias=0.4):
    """Pick a random user, biased toward target_user if provided."""
    if target_user and random.random() < bias:
        return target_user
    return random.choice(users)


def pick_other(users, exclude_user, target_user=None, bias=0.4):
    """Pick a random user that isn't exclude_user, biased toward target_user."""
    if target_user and target_user.id != exclude_user.id and random.random() < bias:
        return target_user
    candidates = [u for u in users if u.id != exclude_user.id]
    return random.choice(candidates) if candidates else random.choice(users)


# ─── Clean ──────────────────────────────────────────────────────────────────

def clean_seed_data(session: Session, categories: list[str] | None = None):
    """Remove seed data using raw SQL (avoids FK issues with ORM delete).

    Args:
        categories: If None or empty, delete ALL seed data.
                    Otherwise, only delete the specified categories.
                    Note: 'users' deletes everything since users own all data.
    """
    if categories:
        print(f"Cleaning seed data: {', '.join(categories)}...")
    else:
        print("Cleaning ALL seed data...")

    # Get all test user IDs (always needed)
    test_users = session.exec(
        select(User).where(col(User.username).startswith("testuser"))
    ).all()
    test_user_ids = [u.id for u in test_users]

    if not test_user_ids:
        print("   No seed data found.")
        return

    delete_all = not categories or "users" in categories

    # Temporarily disable FK constraints for clean deletion
    conn = session.connection()
    conn.execute(text("PRAGMA foreign_keys=OFF"))

    # Get related IDs (only fetch what we need)
    lp_ids, post_ids, story_ids, chat_ids, bfolder_ids, lc_ids = [], [], [], [], [], []

    cats = categories or []
    need_loops = delete_all or "loops" in cats
    need_posts = delete_all or any(c in cats for c in ["posts", "likes", "comments", "bookmarks"])
    need_stories = delete_all or "stories" in cats
    need_messages = delete_all or "messages" in cats
    need_bookmarks = delete_all or "bookmarks" in cats

    if need_loops:
        loop_profiles = session.exec(
            select(LoopProfile).where(col(LoopProfile.user_id).in_(test_user_ids))
        ).all()
        lp_ids = [lp.id for lp in loop_profiles]
        if lp_ids:
            loop_chats = session.exec(select(LoopChat).where(col(LoopChat.profile1_id).in_(lp_ids))).all()
            lc_ids = [lc.id for lc in loop_chats]

    if need_posts or need_bookmarks:
        posts = session.exec(select(Post).where(col(Post.posted_by).in_(test_user_ids))).all()
        post_ids = [p.id for p in posts]

    if need_stories:
        stories = session.exec(select(Story).where(col(Story.user_id).in_(test_user_ids))).all()
        story_ids = [s.id for s in stories]

    if need_messages:
        chats = session.exec(select(Chat).where(col(Chat.participant_one_id).in_(test_user_ids))).all()
        chat_ids = [c.id for c in chats]

    if need_bookmarks:
        bfolders = session.exec(select(BookmarkFolder).where(col(BookmarkFolder.created_by).in_(test_user_ids))).all()
        bfolder_ids = [bf.id for bf in bfolders]

    # Build deletion plan based on categories
    # Each entry: (category, table_name, condition_template, ids)
    all_tables = [
        # Loops
        ("loops", "loopreaction", "message_id IN (SELECT id FROM loopmessage WHERE chat_id IN ({}))", lc_ids),
        ("loops", "loopmessage", "chat_id IN ({})", lc_ids),
        ("loops", "loopchat", "profile1_id IN ({}) OR profile2_id IN ({})", lp_ids),
        ("loops", "loopfriend", "loop_profile_id IN ({}) OR friend_profile_id IN ({})", lp_ids),
        ("loops", "looprequest", "requester_profile_id IN ({}) OR receiver_profile_id IN ({})", lp_ids),
        ("loops", "loopprofilephoto", "loop_profile_id IN ({})", lp_ids),
        ("loops", "loopprofile", "user_id IN ({})", test_user_ids),
        # Notifications
        ("notifications", "notification", "recipient_id IN ({}) OR sender_id IN ({})", test_user_ids),
        ("notifications", "notificationpreference", "user_id IN ({})", test_user_ids),
        # Calls
        ("calls", "call", "call_from IN ({}) OR call_to IN ({})", test_user_ids),
        # Stories
        ("stories", "storyview", "story_id IN ({})", story_ids),
        ("stories", "storymedia", "story_id IN ({})", story_ids),
        ("stories", "story", "user_id IN ({})", test_user_ids),
        # Bookmarks
        ("bookmarks", "bookmark", "bookmark_folder_id IN ({})", bfolder_ids),
        ("bookmarks", "bookmarkfolder", "created_by IN ({})", test_user_ids),
        # Comments (also deleted with posts)
        ("comments", "comment", "post_id IN ({})", post_ids),
        # Likes (also deleted with posts)
        ("likes", "like", "post_id IN ({})", post_ids),
        # Posts (includes media)
        ("posts", "media", "post_id IN ({})", post_ids),
        ("posts", "post", "posted_by IN ({})", test_user_ids),
        # Messages
        ("messages", "reaction", "user_id IN ({})", test_user_ids),
        ("messages", "message", "chat_id IN ({})", chat_ids),
        ("messages", "chat", "participant_one_id IN ({}) OR participant_two_id IN ({})", test_user_ids),
        # Follows
        ("follows", "follow", "follower_id IN ({}) OR following_id IN ({})", test_user_ids),
        ("follows", "block", "blocker_id IN ({}) OR blocked_id IN ({})", test_user_ids),
        # Users (and user-owned tables)
        ("users", "chatmute", "user_id IN ({})", test_user_ids),
        ("users", "userpreference", "user_id IN ({})", test_user_ids),
        ("users", "usersession", "user_id IN ({})", test_user_ids),
        ("users", "userdevice", "user_id IN ({})", test_user_ids),
        ("users", "loginactivity", "user_id IN ({})", test_user_ids),
        ("users", "fcmtoken", "user_id IN ({})", test_user_ids),
        ("users", "user", "id IN ({})", test_user_ids),
    ]

    # Filter tables based on selected categories
    if delete_all:
        tables_to_clean = all_tables
    else:
        # When deleting posts, also delete likes/comments/bookmarks that depend on them
        expanded = set(categories)
        if "posts" in expanded:
            expanded.update(["likes", "comments", "bookmarks"])
        tables_to_clean = [t for t in all_tables if t[0] in expanded]

    deleted_total = 0
    for entry in tables_to_clean:
        _category = entry[0]
        table_name = entry[1]
        condition_template = entry[2]
        ids = entry[3]

        if not ids:
            continue

        placeholders = ",".join(f"'{id}'" for id in ids)
        # Handle conditions with multiple IN clauses
        condition = condition_template.replace("{}", placeholders)

        try:
            result = conn.execute(text(f"DELETE FROM {table_name} WHERE {condition}"))
            if result.rowcount > 0:
                deleted_total += result.rowcount
                print(f"   Deleted {result.rowcount} from {table_name}")
        except Exception as e:
            print(f"   Warning: Could not clean {table_name}: {e}")

    conn.execute(text("PRAGMA foreign_keys=ON"))
    session.commit()
    print(f"   Total: {deleted_total} records deleted")


# ─── Seed Functions ─────────────────────────────────────────────────────────

def seed_users(session: Session, count: int) -> list[User]:
    """Create test users with sessions, preferences, and notification settings."""
    print(f"Creating {count} users...")
    hashed_pw = get_password_hash(PASSWORD)
    users = []

    for i in range(count):
        idx = i + 1
        fname = FIRST_NAMES[i % len(FIRST_NAMES)]
        lname = LAST_NAMES[i % len(LAST_NAMES)]
        # Make names unique for >50 users
        if i >= len(FIRST_NAMES):
            name = f"{fname} {lname} {i // len(FIRST_NAMES) + 1}"
        else:
            name = f"{fname} {lname}"

        gender = "female" if i % 2 == 1 else "male"
        user = User(
            id=generate_uuid(),
            name=name,
            username=f"testuser{idx}",
            email=f"testuser{idx}@{EMAIL_DOMAIN}",
            password=hashed_pw,
            is_active=True,
            is_verified=True,
            bio=BIOS[i % len(BIOS)],
            profile_pic=PLACEHOLDER_AVATARS[i % len(PLACEHOLDER_AVATARS)],
            date_of_birth=datetime(1990 + i % 15, (i % 12) + 1, (i % 28) + 1),
            gender=GenderEnum(gender),
            is_loop_enabled=(i < int(count * 0.6)),  # 60% have loops
            auth_provider="local",
        )
        session.add(user)
        users.append(user)

        if (i + 1) % BATCH_SIZE == 0:
            session.flush()
            progress(i + 1, count)

    session.commit()
    progress(count, count)

    # Sessions
    print(f"   Creating {count} login sessions...")
    for user in users:
        session.add(UserSession(
            id=generate_uuid(),
            user_id=user.id,
            session_token=generate_session_token(),
            device_id=f"seed-{user.username}",
            user_agent="MeeloopSeed/1.0",
            ip_address="127.0.0.1",
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(days=30),
            last_activity=datetime.now(),
            is_active=True,
        ))
    session.commit()

    # Preferences
    themes = ["SYSTEM", "LIGHT", "DARK"]
    languages = ["en", "hi", "es", "fr", "de", "pt", "ja", "ko"]
    for user in users:
        session.add(UserPreference(
            id=generate_uuid(), user_id=user.id,
            ui_mode="MODERN", theme_mode=random.choice(themes),
            language=random.choice(languages),
        ))
        session.add(NotificationPreference(
            id=generate_uuid(), user_id=user.id,
            notifications_enabled=True,
            quiet_hours_enabled=random.choice([True, False]),
        ))
    session.commit()

    print(f"   Done: {count} users + sessions + preferences")
    return users


def seed_follows(session: Session, users: list[User], count: int, target_user=None):
    """Create follow relationships. If target_user, ~50% of follows involve them."""
    print(f"Creating ~{count} follow relationships...")
    created = 0
    pairs = set()
    attempts = 0
    max_attempts = count * 3

    while created < count and attempts < max_attempts:
        attempts += 1
        if target_user and random.random() < 0.5:
            # Half the follows involve target user (as follower or following)
            other = random.choice([u for u in users if u.id != target_user.id])
            if random.random() < 0.5:
                u1, u2 = target_user, other  # target follows someone
            else:
                u1, u2 = other, target_user  # someone follows target
        else:
            u1, u2 = random.sample(users, 2)
        pair = (u1.id, u2.id)
        if pair in pairs:
            continue
        pairs.add(pair)
        session.add(Follow(
            id=generate_uuid(), follower_id=u1.id, following_id=u2.id,
            created_at=rdt(60),
        ))
        created += 1
        batch_commit(session, created)
        if created % 1000 == 0:
            progress(created, count)

    # Add a few blocks (never block target user)
    num_blocks = max(1, len(users) // 10)
    non_target = [u for u in users if not target_user or u.id != target_user.id]
    for _ in range(num_blocks):
        if len(non_target) >= 2:
            u1, u2 = random.sample(non_target, 2)
        else:
            u1, u2 = random.sample(users, 2)
        session.add(Block(
            id=generate_uuid(), blocker_id=u1.id, blocked_id=u2.id,
            created_at=rdt(30),
        ))

    session.commit()
    progress(created, count)
    print(f"   Done: {created} follows + {num_blocks} blocks")


def seed_posts(session: Session, users: list[User], count: int, target_user=None) -> list[Post]:
    """Create posts with media. If target_user, ~40% of posts are theirs."""
    print(f"Creating {count} posts...")
    posts = []

    for i in range(count):
        user = pick_user(users, target_user)
        created = rdt(90)
        post = Post(
            id=generate_uuid(),
            caption=random.choice(POST_CAPTIONS),
            posted_by=user.id,
            created_at=created,
            updated_at=created,
        )
        session.add(post)
        session.flush()

        # 1-3 media per post
        for j in range(random.randint(1, 3)):
            session.add(Media(
                id=generate_uuid(),
                file_path=random.choice(PLACEHOLDER_IMAGES),
                file_type=random.choice(["image", "image", "image", "video"]),
                post_id=post.id,
            ))

        posts.append(post)
        if (i + 1) % BATCH_SIZE == 0:
            session.commit()
            progress(i + 1, count)

    session.commit()
    progress(count, count)
    return posts


def seed_likes(session: Session, users: list[User], posts: list[Post], count: int, target_user=None):
    """Create post likes. If target_user, they like ~40% of posts and get likes on theirs."""
    print(f"Creating {count} likes...")
    created = 0
    pairs = set()

    while created < count:
        user = pick_user(users, target_user)
        post = random.choice(posts)
        pair = (user.id, post.id)
        if pair in pairs:
            continue
        pairs.add(pair)
        session.add(Like(
            id=generate_uuid(), user_id=user.id, post_id=post.id,
            liked=True, created_at=rdt(30),
        ))
        created += 1
        batch_commit(session, created)
        if created % 2000 == 0:
            progress(created, count)

    session.commit()
    progress(count, count)
    print(f"   Done: {created} likes")


def seed_comments(session: Session, users: list[User], posts: list[Post], count: int, target_user=None):
    """Create comments with replies. If target_user, they comment on ~40% of posts."""
    print(f"Creating {count} comments...")
    created = 0
    parent_comments = []

    for i in range(count):
        user = pick_user(users, target_user)
        post = random.choice(posts)

        # 20% are replies to existing comments on the same post
        reply_to = None
        if parent_comments and random.random() < 0.2:
            candidates = [c for c in parent_comments[-200:] if c[1] == post.id]
            if candidates:
                reply_to = random.choice(candidates)[0]

        comment = Comment(
            id=generate_uuid(),
            comment=random.choice(COMMENTS_TEXT),
            user_id=user.id,
            post_id=post.id,
            reply_to=reply_to,
            created_at=rdt(30),
        )
        session.add(comment)
        session.flush()
        parent_comments.append((comment.id, post.id))
        created += 1

        if created % BATCH_SIZE == 0:
            session.commit()
            progress(created, count)

    session.commit()
    progress(count, count)
    print(f"   Done: {created} comments")


def seed_bookmarks(session: Session, users: list[User], posts: list[Post], count: int, target_user=None):
    """Create bookmark folders and bookmarks."""
    print(f"Creating ~{count} bookmarks...")
    folder_names = ["Favorites", "Inspiration", "Read Later", "Funny", "Tech",
                    "Travel", "Food", "Fitness", "Music", "Work"]
    created = 0

    # Give ~half of users bookmark folders, always include target_user
    bookmark_users = random.sample(users, max(1, len(users) // 2))
    if target_user and target_user not in bookmark_users:
        bookmark_users.insert(0, target_user)

    folders_per_user = max(1, count // len(bookmark_users) // 5)
    bookmarks_per_user = max(1, count // len(bookmark_users))

    for user in bookmark_users:
        user_folders = []
        for j in range(random.randint(1, min(folders_per_user + 1, len(folder_names)))):
            folder = BookmarkFolder(
                id=generate_uuid(),
                name=folder_names[j % len(folder_names)],
                created_by=user.id,
                created_at=rdt(30),
            )
            session.add(folder)
            session.flush()
            user_folders.append(folder)

        if not user_folders:
            continue

        num_bm = random.randint(1, min(bookmarks_per_user, len(posts)))
        sample_posts = random.sample(posts, num_bm)
        for post in sample_posts:
            session.add(Bookmark(
                id=generate_uuid(),
                post_id=post.id,
                bookmark_folder_id=random.choice(user_folders).id,
                created_at=rdt(20),
            ))
            created += 1

        if created >= count:
            break

    session.commit()
    print(f"   Done: {created} bookmarks")


def seed_chats_and_messages(session: Session, users: list[User], num_chats: int, msgs_per_chat: int, target_user=None):
    """Create chats with messages and reactions. If target_user, ~50% of chats involve them."""
    total_msgs = num_chats * msgs_per_chat
    print(f"Creating {num_chats} chats with ~{total_msgs} messages...")
    chat_count = 0
    msg_count = 0

    # Generate unique chat pairs, biased toward target_user
    chat_pairs = set()
    attempts = 0
    while len(chat_pairs) < num_chats and attempts < num_chats * 3:
        attempts += 1
        if target_user and random.random() < 0.5:
            other = random.choice([u for u in users if u.id != target_user.id])
            pair = tuple(sorted([target_user.id, other.id]))
        else:
            u1, u2 = random.sample(users, 2)
            pair = tuple(sorted([u1.id, u2.id]))
        chat_pairs.add(pair)

    user_map = {u.id: u for u in users}

    for p1_id, p2_id in chat_pairs:
        chat_created = rdt(60)
        chat = Chat(
            id=generate_uuid(),
            participant_one_id=p1_id,
            participant_two_id=p2_id,
            created_at=chat_created,
            updated_at=chat_created,
        )
        session.add(chat)
        session.flush()
        chat_count += 1

        num_msgs = random.randint(max(1, msgs_per_chat // 2), msgs_per_chat * 2)
        last_msg_text = None
        last_msg_type = None
        last_msg_time = chat_created

        for j in range(num_msgs):
            is_p1 = random.choice([True, False])
            sender_id = p1_id if is_p1 else p2_id
            receiver_id = p2_id if is_p1 else p1_id

            msg_time = last_msg_time + timedelta(minutes=random.randint(1, 120))
            if msg_time > datetime.now():
                msg_time = datetime.now() - timedelta(minutes=random.randint(1, 60))

            msg_type = random.choices(
                [MessageType.TEXT, MessageType.IMAGE, MessageType.AUDIO, MessageType.VIDEO],
                weights=[0.75, 0.12, 0.08, 0.05],
            )[0]

            msg_text = random.choice(MESSAGES_TEXT) if msg_type == MessageType.TEXT else None

            msg = Message(
                id=generate_uuid(),
                message=msg_text,
                message_type=msg_type,
                media_url=random.choice(PLACEHOLDER_IMAGES) if msg_type != MessageType.TEXT else None,
                media_type=msg_type.value if msg_type != MessageType.TEXT else None,
                duration=random.randint(5, 180) if msg_type == MessageType.AUDIO else None,
                sender_id=sender_id,
                receiver_id=receiver_id,
                chat_id=chat.id,
                created_at=msg_time,
                updated_at=msg_time,
                status=MessageStatus.READ if j < num_msgs - 3 else MessageStatus.DELIVERED,
                is_read=j < num_msgs - 3,
            )
            session.add(msg)
            last_msg_text = msg_text or f"[{msg_type.value}]"
            last_msg_type = msg_type.value
            last_msg_time = msg_time
            msg_count += 1

            # 15% reactions
            if random.random() < 0.15:
                session.flush()
                session.add(Reaction(
                    id=generate_uuid(),
                    emoji=random.choice(EMOJIS),
                    user_id=receiver_id,
                    message_id=msg.id,
                    created_at=msg_time + timedelta(minutes=random.randint(1, 30)),
                ))

        chat.last_message = last_msg_text
        chat.last_message_type = last_msg_type
        chat.last_message_datetime = last_msg_time
        chat.updated_at = last_msg_time

        if chat_count % 50 == 0:
            session.commit()
            progress(chat_count, num_chats)

    session.commit()
    progress(num_chats, num_chats)
    print(f"   Done: {chat_count} chats, {msg_count} messages")


def seed_stories(session: Session, users: list[User], count: int, target_user=None):
    """Create stories with media and views. If target_user, ~40% of stories are theirs."""
    print(f"Creating {count} stories...")
    created = 0

    for i in range(count):
        user = pick_user(users, target_user)
        created_at = datetime.now() - timedelta(hours=random.randint(1, 22))
        story = Story(
            id=generate_uuid(),
            user_id=user.id,
            text=random.choice(STORY_TEXTS),
            created_at=created_at,
            updated_at=created_at,
            expires_on=created_at + timedelta(hours=24),
        )
        session.add(story)
        session.flush()

        session.add(StoryMedia(
            id=generate_uuid(),
            story_id=story.id,
            media_url=random.choice(PLACEHOLDER_IMAGES),
            media_type=random.choice(["image", "image", "video"]),
        ))

        # 3-10 views per story
        num_views = random.randint(3, min(10, len(users) - 1))
        viewers = random.sample([u for u in users if u.id != user.id], num_views)
        for viewer in viewers:
            session.add(StoryView(
                id=generate_uuid(),
                story_id=story.id,
                viewer_id=viewer.id,
                viewed_at=created_at + timedelta(minutes=random.randint(10, 600)),
            ))

        created += 1
        if created % BATCH_SIZE == 0:
            session.commit()
            progress(created, count)

    session.commit()
    progress(count, count)
    print(f"   Done: {created} stories")


def seed_calls(session: Session, users: list[User], count: int, target_user=None):
    """Create call history. If target_user, ~50% of calls involve them."""
    print(f"Creating {count} call records...")
    statuses = ["answered", "missed", "declined", "ended"]

    for i in range(count):
        if target_user and random.random() < 0.5:
            other = random.choice([u for u in users if u.id != target_user.id])
            if random.random() < 0.5:
                caller, callee = target_user, other
            else:
                caller, callee = other, target_user
        else:
            caller, callee = random.sample(users, 2)
        status = random.choice(statuses)
        is_video = random.choice([True, False])
        duration = random.randint(15, 3600) if status in ("answered", "ended") else None
        created = rdt(60)

        session.add(Call(
            id=generate_uuid(),
            call_from=caller.id, call_to=callee.id,
            call_status=status, is_video_call=is_video,
            duration_seconds=duration,
            created_at=created, updated_at=created,
        ))
        if (i + 1) % BATCH_SIZE == 0:
            session.commit()

    session.commit()
    print(f"   Done: {count} calls")


def seed_notifications(session: Session, users: list[User], posts: list[Post], count: int, target_user=None):
    """Create notifications. If target_user, ~50% of notifications are for them."""
    print(f"Creating {count} notifications...")

    for i in range(count):
        if target_user and random.random() < 0.5:
            recipient = target_user
        else:
            recipient = random.choice(users)
        sender = random.choice([u for u in users if u.id != recipient.id])
        notif_type, category, msg_template, redirect_type = random.choice(NOTIFICATION_TEMPLATES)

        redirect_id = None
        if redirect_type == "post" and posts:
            redirect_id = random.choice(posts).id
        elif redirect_type == "profile":
            redirect_id = sender.id

        session.add(Notification(
            id=generate_uuid(),
            notification_type=notif_type,
            notification_category=category,
            recipient_id=recipient.id,
            sender_id=sender.id,
            title=sender.name,
            message=msg_template,
            image_url=sender.profile_pic,
            redirect_to=redirect_type,
            redirect_type=redirect_type,
            redirect_id=redirect_id,
            is_read=random.choice([True, False]),
            read_at=rdt_recent(3) if random.random() > 0.5 else None,
            priority=random.choices([0, 1, 2], weights=[0.7, 0.2, 0.1])[0],
            created_at=rdt_recent(14),
        ))
        if (i + 1) % BATCH_SIZE == 0:
            session.commit()
            progress(i + 1, count)

    session.commit()
    progress(count, count)
    print(f"   Done: {count} notifications")


def seed_loops(session: Session, users: list[User], num_chats: int, msgs_per_chat: int, target_user=None):
    """Create loop profiles, friends, requests, chats, messages."""
    loop_users = [u for u in users if u.is_loop_enabled]
    # Ensure target_user is loop-enabled
    if target_user and target_user not in loop_users:
        target_user.is_loop_enabled = True
        session.add(target_user)
        session.commit()
        loop_users.append(target_user)
    if not loop_users:
        print("   No loop-enabled users, skipping loops.")
        return

    print(f"Creating loop data for {len(loop_users)} users...")
    profiles = []

    for i, user in enumerate(loop_users):
        existing_profile = session.exec(
            select(LoopProfile).where(LoopProfile.user_id == user.id)
        ).first()
        if existing_profile:
            profiles.append(existing_profile)
            continue

        profile = LoopProfile(
            id=generate_uuid(),
            user_id=user.id,
            displayname=user.name.split()[0],
            bio=LOOP_BIOS[i % len(LOOP_BIOS)],
            profile_pic=user.profile_pic,
            date_of_birth=user.date_of_birth,
            gender=user.gender,
            created_at=rdt(60),
        )
        session.add(profile)
        session.flush()
        profiles.append(profile)

        for j in range(random.randint(1, 3)):
            session.add(LoopProfilePhoto(
                id=generate_uuid(),
                loop_profile_id=profile.id,
                photo_url=random.choice(PLACEHOLDER_IMAGES),
                order=j, is_primary=(j == 0),
            ))

    session.commit()
    print(f"   Created {len(profiles)} loop profiles")

    # Friends
    friend_pairs = set()
    for profile in profiles:
        num_friends = random.randint(2, min(5, len(profiles) - 1))
        targets = random.sample([p for p in profiles if p.id != profile.id],
                                min(num_friends, len(profiles) - 1))
        for target in targets:
            pair = tuple(sorted([profile.id, target.id]))
            if pair not in friend_pairs:
                friend_pairs.add(pair)
                session.add(LoopRequest(
                    id=generate_uuid(),
                    requester_profile_id=profile.id,
                    receiver_profile_id=target.id,
                    status=LoopRequestStatus.accepted,
                    created_at=rdt(30),
                ))
                session.add(LoopFriend(
                    id=generate_uuid(),
                    loop_profile_id=profile.id,
                    friend_profile_id=target.id,
                ))
                session.add(LoopFriend(
                    id=generate_uuid(),
                    loop_profile_id=target.id,
                    friend_profile_id=profile.id,
                ))

    # Pending requests
    pending = min(8, len(profiles) * 2)
    for _ in range(pending):
        r, recv = random.sample(profiles, 2)
        session.add(LoopRequest(
            id=generate_uuid(),
            requester_profile_id=r.id,
            receiver_profile_id=recv.id,
            status=LoopRequestStatus.pending,
            created_at=rdt_recent(7),
        ))

    session.commit()
    print(f"   Created {len(friend_pairs)} friend pairs + {pending} pending requests")

    # Loop chats
    actual_chats = min(num_chats, len(friend_pairs))
    friend_list = list(friend_pairs)
    profile_map = {p.id: p for p in profiles}

    chat_count = 0
    msg_count = 0
    for pair in friend_list[:actual_chats]:
        p1_id, p2_id = pair
        chat = LoopChat(
            id=generate_uuid(),
            profile1_id=p1_id, profile2_id=p2_id,
            created_at=rdt(20),
        )
        session.add(chat)
        session.flush()
        chat_count += 1

        num_msgs = random.randint(max(1, msgs_per_chat // 2), msgs_per_chat * 2)
        last_content = None
        last_time = chat.created_at

        for j in range(num_msgs):
            sender_id = random.choice([p1_id, p2_id])
            msg_time = last_time + timedelta(minutes=random.randint(5, 120))
            if msg_time > datetime.now():
                msg_time = datetime.now() - timedelta(minutes=random.randint(1, 60))
            content = random.choice(MESSAGES_TEXT)

            msg = LoopMessage(
                id=generate_uuid(),
                chat_id=chat.id,
                sender_profile_id=sender_id,
                content=content,
                created_at=msg_time,
            )
            session.add(msg)
            session.flush()
            last_content = content
            last_time = msg_time
            msg_count += 1

            if random.random() < 0.15:
                reactor_id = p2_id if sender_id == p1_id else p1_id
                session.add(LoopReaction(
                    id=generate_uuid(),
                    message_id=msg.id,
                    profile_id=reactor_id,
                    emoji=random.choice(EMOJIS),
                ))

        chat.last_message_content = last_content
        chat.last_message_at = last_time

    session.commit()
    print(f"   Created {chat_count} loop chats, {msg_count} loop messages")


# ─── Menu & Config ──────────────────────────────────────────────────────────

SEED_OPTIONS = [
    # (key,           label,                                          default, depends_on)
    ("users",         "Users (sessions, preferences, settings)",       50,     []),
    ("follows",       "Follow relationships & blocks",                 300,    ["users"]),
    ("posts",         "Posts with media",                              100,    ["users"]),
    ("likes",         "Likes on posts",                                1000,   ["users", "posts"]),
    ("comments",      "Comments & replies on posts",                   500,    ["users", "posts"]),
    ("bookmarks",     "Bookmark folders & bookmarks",                  150,    ["users", "posts"]),
    ("messages",      "Chats with messages & reactions",               5000,   ["users"]),
    ("stories",       "Stories with media & views",                    60,     ["users"]),
    ("calls",         "Call history (audio & video)",                   100,    ["users"]),
    ("notifications", "Notifications (all types)",                     500,    ["users"]),
    ("loops",         "Loop profiles, friends, chats & messages",      2000,   ["users"]),
]

OPTION_KEYS = [o[0] for o in SEED_OPTIONS]


def ask_int(prompt: str, default: int) -> int:
    """Ask for an integer with a default."""
    val = input(f"  {prompt} [{default}]: ").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def add_dependencies(selected: list[str]) -> list[str]:
    """Auto-add required dependencies and return ordered list."""
    deps_map = {o[0]: o[3] for o in SEED_OPTIONS}
    result = []
    added = set()

    def add(key):
        if key in added:
            return
        for dep in deps_map.get(key, []):
            add(dep)
        added.add(key)
        result.append(key)

    for key in selected:
        add(key)
    return result


def interactive_menu() -> tuple[list[str], dict]:
    """Simple interactive menu: pick items, then set count for each."""
    print()
    print("What do you want to insert?")
    print("─" * 55)
    for i, (key, label, default, _) in enumerate(SEED_OPTIONS, 1):
        print(f"  {i:2}. {label}")
    print()
    print("  a = all    q = quit")
    print()

    choice = input("Pick items (e.g. 1,3,7) or 'a' for all: ").strip().lower()
    if choice == 'q':
        return [], {}
    if choice == 'a':
        selected = [o[0] for o in SEED_OPTIONS]
    else:
        try:
            indices = [int(x.strip()) - 1 for x in choice.split(",")]
            selected = [SEED_OPTIONS[i][0] for i in indices if 0 <= i < len(SEED_OPTIONS)]
        except (ValueError, IndexError):
            print("Invalid input.")
            return [], {}

    if not selected:
        return [], {}

    # Add dependencies automatically
    selected = add_dependencies(selected)
    defaults = {o[0]: o[2] for o in SEED_OPTIONS}

    print()
    print("How many? (press Enter for default)")
    print("─" * 55)

    counts = {}
    for key in selected:
        label_map = {
            "users":         "Users",
            "follows":       "Follows",
            "posts":         "Posts",
            "likes":         "Likes",
            "comments":      "Comments",
            "bookmarks":     "Bookmarks",
            "messages":      "Messages (total across all chats)",
            "stories":       "Stories",
            "calls":         "Calls",
            "notifications": "Notifications",
            "loops":         "Loop messages (total across all chats)",
        }
        label = label_map.get(key, key)
        counts[key] = ask_int(label, defaults[key])

    print()
    print("Summary:")
    print("─" * 55)
    for key in selected:
        print(f"  {key:<16} {counts[key]:>8,}")
    print()

    confirm = input("Start seeding? (y/n) [y]: ").strip().lower()
    if confirm and confirm != 'y':
        return [], {}

    return selected, counts


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Meeloop Seed Data Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/seed_data.py                              Interactive mode
  python scripts/seed_data.py --all                        Seed everything (defaults)
  python scripts/seed_data.py --all --users 200 --posts 500 --messages 50000
  python scripts/seed_data.py --for-user nanne --all       Seed data for existing user 'nanne'
  python scripts/seed_data.py --for-user nanne --all --posts 50 --messages 1000
  python scripts/seed_data.py --only posts --posts 1000    Just posts
  python scripts/seed_data.py --only messages --messages 100000
  python scripts/seed_data.py --clean                      Interactive: pick what to delete
  python scripts/seed_data.py --clean --all                Delete ALL seed data
  python scripts/seed_data.py --clean --only posts,stories Delete only posts & stories
  python scripts/seed_data.py --clean --all --all          Clean everything + re-seed

Login: testuser1..testuser{N} / Test@123
        """,
    )
    parser.add_argument("--clean", action="store_true", help="Delete all seed data first")
    parser.add_argument("--all", action="store_true", help="Seed everything (non-interactive)")
    parser.add_argument("--for-user", type=str, default=None,
                        help="Username or user ID of an existing user to seed data for")
    parser.add_argument("--only", type=str, default="",
                        help="Comma-separated categories: users,follows,posts,likes,comments,bookmarks,messages,stories,calls,notifications,loops")
    # Individual count overrides
    parser.add_argument("--users", type=int, default=None, help="Number of users")
    parser.add_argument("--follows", type=int, default=None, help="Number of follows")
    parser.add_argument("--posts", type=int, default=None, help="Number of posts")
    parser.add_argument("--likes", type=int, default=None, help="Number of likes")
    parser.add_argument("--comments", type=int, default=None, help="Number of comments")
    parser.add_argument("--bookmarks", type=int, default=None, help="Number of bookmarks")
    parser.add_argument("--messages", type=int, default=None, help="Total messages across all chats")
    parser.add_argument("--stories", type=int, default=None, help="Number of stories")
    parser.add_argument("--calls", type=int, default=None, help="Number of call records")
    parser.add_argument("--notifications", type=int, default=None, help="Number of notifications")
    parser.add_argument("--loops", type=int, default=None, help="Total loop messages")
    args = parser.parse_args()

    print("=" * 60)
    print("  Meeloop Seed Data Script")
    print("=" * 60)
    print(f"  Password: {PASSWORD}")

    target_user = None  # The --for-user target

    with Session(engine) as session:
        # Resolve --for-user
        if args.for_user:
            target_user = session.exec(
                select(User).where(
                    (User.username == args.for_user) | (User.id == args.for_user)
                )
            ).first()
            if not target_user:
                print(f"\nERROR: User '{args.for_user}' not found (tried username and ID).")
                return
            print(f"  Target user: {target_user.name} (@{target_user.username}, {target_user.id})")

        # Clean
        if args.clean:
            print()
            if args.all:
                # --clean --all → delete everything
                clean_seed_data(session)
            elif args.only:
                # --clean --only posts,stories → delete specific categories
                clean_cats = [s.strip() for s in args.only.split(",") if s.strip() in OPTION_KEYS]
                if clean_cats:
                    clean_seed_data(session, categories=clean_cats)
                else:
                    print("No valid categories specified.")
                    return
            else:
                # --clean alone → interactive: ask what to delete
                print("What do you want to delete?")
                print("─" * 55)
                for i, (key, label, _, _) in enumerate(SEED_OPTIONS, 1):
                    print(f"  {i:2}. {label}")
                print()
                print("  a = all    q = quit")
                print()
                choice = input("Pick items to delete (e.g. 1,3,7) or 'a' for all: ").strip().lower()
                if choice == 'q':
                    print("Cancelled.")
                    return
                if choice == 'a':
                    confirm = input("Delete ALL seed data? (y/n) [n]: ").strip().lower()
                    if confirm != 'y':
                        print("Cancelled.")
                        return
                    clean_seed_data(session)
                else:
                    try:
                        indices = [int(x.strip()) - 1 for x in choice.split(",")]
                        clean_cats = [SEED_OPTIONS[i][0] for i in indices if 0 <= i < len(SEED_OPTIONS)]
                    except (ValueError, IndexError):
                        print("Invalid input.")
                        return
                    if not clean_cats:
                        print("Nothing selected.")
                        return
                    confirm = input(f"Delete {', '.join(clean_cats)}? (y/n) [n]: ").strip().lower()
                    if confirm != 'y':
                        print("Cancelled.")
                        return
                    clean_seed_data(session, categories=clean_cats)

            # If only cleaning (no --all for re-seed), stop here
            if not args.all and not args.only:
                return

        # Check existing — skip this check when --for-user is specified
        # (existing test users become the pool for the target user's data)
        existing = session.exec(select(User).where(User.username == "testuser1")).first()
        if existing and not args.clean and not target_user:
            if not (args.all or args.only):
                # Interactive mode — user might want to add more data on top
                pass
            else:
                print("\nSeed data already exists! Use --clean to reset first.")
                print(f"   testuser1 ID: {existing.id}")
                return

        # Build defaults dict
        defaults = {o[0]: o[2] for o in SEED_OPTIONS}

        # Determine what to seed & counts
        if args.all or args.only:
            if args.all:
                selected = OPTION_KEYS[:]
            else:
                selected = [s.strip() for s in args.only.split(",") if s.strip() in OPTION_KEYS]

            selected = add_dependencies(selected)

            # Start from defaults, apply CLI overrides
            counts = {k: defaults[k] for k in selected}
            cli_overrides = {
                "users": args.users, "follows": args.follows, "posts": args.posts,
                "likes": args.likes, "comments": args.comments, "bookmarks": args.bookmarks,
                "messages": args.messages, "stories": args.stories, "calls": args.calls,
                "notifications": args.notifications, "loops": args.loops,
            }
            for k, v in cli_overrides.items():
                if v is not None and k in counts:
                    counts[k] = v

        else:
            # Interactive mode
            selected, counts = interactive_menu()
            if not selected:
                print("Nothing to seed. Bye!")
                return

        # ── Run ──
        start = time.time()
        users = []
        posts = []

        print()
        print("Seeding...")
        print("=" * 60)

        # Load existing users if not creating new ones
        if "users" in selected:
            # If --for-user and test users already exist, reuse them
            existing_test_users = session.exec(
                select(User).where(col(User.username).startswith("testuser"))
            ).all()
            if target_user and existing_test_users:
                users = existing_test_users
                print(f"Reusing {len(users)} existing test users")
            else:
                users = seed_users(session, counts["users"])
        else:
            users = session.exec(
                select(User).where(col(User.username).startswith("testuser"))
            ).all()
            if not users:
                print("ERROR: No users found. Include 'users' first.")
                return

        # Include target user in the users list so seed functions create data for them
        if target_user:
            # Refresh target_user in case session changed
            target_user = session.get(User, target_user.id)
            if target_user and target_user not in users:
                users.insert(0, target_user)
            print(f"\n  ⟶  Target user @{target_user.username} included in seed pool ({len(users)} total users)")

        if "follows" in selected:
            seed_follows(session, users, counts["follows"], target_user=target_user)

        if "posts" in selected:
            posts = seed_posts(session, users, counts["posts"], target_user=target_user)

        # Load existing posts if needed but not being created
        if not posts and any(k in selected for k in ["likes", "comments", "bookmarks", "notifications"]):
            posts = session.exec(
                select(Post).where(col(Post.posted_by).in_([u.id for u in users]))
            ).all()
            if not posts:
                print("WARNING: No posts found. Skipping likes/comments/bookmarks.")

        if "likes" in selected and posts:
            seed_likes(session, users, posts, counts["likes"], target_user=target_user)

        if "comments" in selected and posts:
            seed_comments(session, users, posts, counts["comments"], target_user=target_user)

        if "bookmarks" in selected and posts:
            seed_bookmarks(session, users, posts, counts["bookmarks"], target_user=target_user)

        if "messages" in selected:
            # Split total messages into chats. ~20 msgs per chat by default.
            total_msgs = counts["messages"]
            msgs_per_chat = 20
            num_chats = max(5, total_msgs // msgs_per_chat)
            # Cap chats at user_pairs possible
            max_chats = len(users) * (len(users) - 1) // 2
            num_chats = min(num_chats, max_chats)
            msgs_per_chat = max(5, total_msgs // num_chats) if num_chats > 0 else 20
            seed_chats_and_messages(session, users, num_chats, msgs_per_chat, target_user=target_user)

        if "stories" in selected:
            seed_stories(session, users, counts["stories"], target_user=target_user)

        if "calls" in selected:
            seed_calls(session, users, counts["calls"], target_user=target_user)

        if "notifications" in selected:
            seed_notifications(session, users, posts if posts else [], counts["notifications"], target_user=target_user)

        if "loops" in selected:
            total_loop_msgs = counts["loops"]
            loop_msgs_per_chat = 15
            num_loop_chats = max(3, total_loop_msgs // loop_msgs_per_chat)
            seed_loops(session, users, num_loop_chats, loop_msgs_per_chat, target_user=target_user)

        elapsed = time.time() - start
        num_users = counts.get("users", len(users))

        print()
        print("=" * 60)
        print(f"  Done in {elapsed:.1f}s")
        print("=" * 60)
        print()
        print(f"  Login:")
        print(f"    Username: testuser1 .. testuser{num_users}")
        print(f"    Password: {PASSWORD}")
        print(f"    Email:    testuser1@{EMAIL_DOMAIN} .. testuser{num_users}@{EMAIL_DOMAIN}")
        print()


if __name__ == "__main__":
    main()
