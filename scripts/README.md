# Backend Scripts

Utility scripts for development and testing. All scripts must be run from the `backend/` directory with the virtual environment activated.

```bash
cd backend
source env/bin/activate
```

---

## seed_data.py — Populate Database with Test Data

Inserts realistic test data (users, posts, messages, stories, calls, etc.) for testing pagination, features, and UI at scale.

**Only creates `testuser*` accounts — your real data is never touched.**

### Login Pattern

| Field    | Pattern                              |
|----------|--------------------------------------|
| Username | `testuser1` .. `testuser{N}`         |
| Password | `Test@123`                           |
| Email    | `testuser1@meeloop.com` .. `testuser{N}@meeloop.com` |

### Insert Data

```bash
# Interactive mode — pick what to insert and how many
python scripts/seed_data.py

# Insert everything with default counts
python scripts/seed_data.py --all

# Custom counts
python scripts/seed_data.py --all --users 200 --posts 500 --messages 50000

# Insert specific categories only
python scripts/seed_data.py --only posts,comments --posts 1000 --comments 500
```

### Delete Data

Cleanup only removes test data (`testuser*` and their related records).

```bash
# Interactive — pick what to delete
python scripts/seed_data.py --clean

# Delete ALL seed data
python scripts/seed_data.py --clean --all

# Delete only specific categories
python scripts/seed_data.py --clean --only posts,stories

# Clean + re-seed in one command
python scripts/seed_data.py --clean --only posts --posts 1000
```

### Categories

| # | Category        | Default | Description                                  |
|---|-----------------|---------|----------------------------------------------|
| 1 | `users`         | 50      | Users, sessions, preferences, settings       |
| 2 | `follows`       | 300     | Follow relationships and blocks              |
| 3 | `posts`         | 100     | Posts with media attachments                 |
| 4 | `likes`         | 1,000   | Likes on posts                               |
| 5 | `comments`      | 500     | Comments and replies on posts                |
| 6 | `bookmarks`     | 150     | Bookmark folders and bookmarks               |
| 7 | `messages`      | 5,000   | Chats with messages and reactions            |
| 8 | `stories`       | 60      | Stories with media and views                 |
| 9 | `calls`         | 100     | Call history (audio and video)               |
| 10| `notifications` | 500     | Notifications (all types)                    |
| 11| `loops`         | 2,000   | Loop profiles, friends, chats and messages   |

Dependencies are auto-resolved (e.g. `likes` automatically includes `users` + `posts`).

---

## verify_seed.py — Verify Seed Data

Queries the database and shows record counts for all seeded data.

```bash
# Summary counts
python scripts/verify_seed.py

# With sample records
python scripts/verify_seed.py --detail
```
