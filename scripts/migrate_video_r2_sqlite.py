"""
Migrate video templates from local folder → Cloudflare R2 + local SQLite.

For each .mp4 + .txt pair in SOURCE_DIR:
  - Reads .txt for hashtags and description text
  - Generates slug: {tag1}_{tag2}_{random8hex}
  - Uploads .mp4 to R2 at video/{slug}.mp4
  - Inserts into memetemplates with template_type='VIDEO'

Usage:
    source env/bin/activate
    python3 migrate_video_r2_sqlite.py

Flags:
    --dry-run     Preview what would be done, no uploads or DB writes
    --limit N     Process only the first N entries (useful for testing)
    --skip-upload Skip R2 upload (if files are already uploaded)
"""
import argparse
import json
import os
import re
import secrets
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv()

SOURCE_DIR = Path("/Users/nanne/Downloads/videoskiadda")
DB_PATH = Path(__file__).parent / "database.sqlite"

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET = os.getenv("R2_SECRET_ACCESS_KEY")
BUCKET = os.getenv("R2_BUCKET_NAME", "meme-templates")
PUBLIC_URL = os.getenv("R2_TEMPLATES_URL", "templates.meeloop.com").rstrip("/")
if not PUBLIC_URL.startswith("http"):
    PUBLIC_URL = "https://" + PUBLIC_URL

_VKA_RE = re.compile(r"_vka$", re.IGNORECASE)
_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


def parse_txt(path: Path) -> tuple[list[str], str]:
    tags: list[str] = []
    content_lines: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return [], ""

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            for raw in re.findall(r"#(\w+)", line):
                tag = _VKA_RE.sub("", raw).lower()
                tag = _SLUG_CLEAN.sub("_", tag).strip("_")
                if tag and len(tag) > 1:
                    tags.append(tag)
        else:
            content_lines.append(line)

    seen: set[str] = set()
    unique_tags: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)

    return unique_tags, " ".join(content_lines)


def make_slug(tags: list[str], stem: str) -> str:
    meaningful = [t for t in tags if len(t) > 2][:2]
    if meaningful:
        base = "_".join(t[:20] for t in meaningful)
    else:
        base = re.sub(r"[^a-z0-9]", "", stem.lower())[:16] or "video"
    return f"{base}_{secrets.token_hex(4)}"


def get_r2_client():
    if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET]):
        raise SystemExit(
            "ERROR: R2 credentials not set. "
            "Ensure R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY are in .env"
        )
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET,
        region_name="auto",
    )


def r2_key_exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def get_default_user_id(con: sqlite3.Connection) -> str:
    row = con.execute('SELECT id FROM "user" LIMIT 1').fetchone()
    if not row:
        raise SystemExit("ERROR: No users found in database.sqlite. Create at least one user first.")
    return row[0]


def migrate(dry_run: bool, limit: int | None, skip_upload: bool, offset: int = 0, yes: bool = False):
    mp4s = sorted(SOURCE_DIR.glob("*.mp4"))
    if offset:
        mp4s = mp4s[offset:]
    if limit:
        mp4s = mp4s[:limit]
    total = len(mp4s)
    print(f"Found {total} .mp4 files in {SOURCE_DIR}")

    s3 = None if (dry_run or skip_upload) else get_r2_client()

    con = sqlite3.connect(DB_PATH)
    existing = con.execute("SELECT COUNT(*) FROM memetemplates WHERE template_type = 'VIDEO'").fetchone()[0]
    if existing > 0 and not yes:
        answer = input(f"DB already has {existing} VIDEO rows. Continue and add more? (y/n): ")
        if answer.lower() != "y":
            con.close()
            print("Aborted.")
            return
    elif existing > 0:
        print(f"DB already has {existing} VIDEO rows. Continuing…")

    default_user_id = get_default_user_id(con)
    print(f"Using user ID: {default_user_id}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    batch: list = []
    batch_size = 100
    migrated = skipped = errors = 0

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Migrating {total} video templates…\n")

    for i, mp4_path in enumerate(mp4s, 1):
        stem = mp4_path.stem
        txt_path = mp4_path.with_suffix(".txt")

        tags, content = parse_txt(txt_path)
        slug = make_slug(tags, stem)
        r2_key = f"video/{slug}.mp4"
        public_url = f"{PUBLIC_URL}/{r2_key}"

        if dry_run:
            if i <= 5:
                print(f"  [{i}] {stem}")
                print(f"       tags    : {tags[:5]}")
                print(f"       content : {content[:80]!r}")
                print(f"       r2_key  : {r2_key}")
                print(f"       url     : {public_url}")
            elif i == 6:
                print("  … (showing first 5 only in dry-run)")
            migrated += 1
            continue

        if not skip_upload:
            try:
                if r2_key_exists(s3, r2_key):
                    skipped += 1
                else:
                    s3.upload_file(
                        str(mp4_path),
                        BUCKET,
                        r2_key,
                        ExtraArgs={"ContentType": "video/mp4"},
                    )
            except Exception as exc:
                print(f"  ERROR uploading {stem}: {exc}", file=sys.stderr)
                errors += 1
                continue

        batch.append((
            str(uuid.uuid4()),
            "VIDEO",
            content,
            json.dumps([public_url]),
            json.dumps(tags),
            json.dumps({}),
            now,
            now,
            default_user_id,
            default_user_id,
        ))

        if len(batch) >= batch_size:
            con.executemany(
                """INSERT OR IGNORE INTO memetemplates
                   (id, template_type, content, urls, hash_tags, metadata_info,
                    created_at, updated_at, created_by_id, updated_by_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                batch,
            )
            con.commit()
            migrated += len(batch)
            batch = []
            print(f"  {migrated + skipped}/{total}  (migrated={migrated}, skipped={skipped}, errors={errors})")

    if batch:
        con.executemany(
            """INSERT OR IGNORE INTO memetemplates
               (id, template_type, content, urls, hash_tags, metadata_info,
                created_at, updated_at, created_by_id, updated_by_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            batch,
        )
        con.commit()
        migrated += len(batch)

    total_in_db = con.execute("SELECT COUNT(*) FROM memetemplates WHERE template_type = 'VIDEO'").fetchone()[0]
    con.close()

    print(f"\nTotal VIDEO rows in memetemplates: {total_in_db}")
    print(f"Done. migrated={migrated} | skipped={skipped} | errors={errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate video templates to R2 + SQLite")
    parser.add_argument("--dry-run", action="store_true", help="Preview without uploading or writing to DB")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N entries")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N files (resume from offset)")
    parser.add_argument("--skip-upload", action="store_true", help="Skip R2 upload (files already uploaded)")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    args = parser.parse_args()

    print("Video Templates → R2 + SQLite Migration")
    print("=" * 45)
    migrate(dry_run=args.dry_run, limit=args.limit, skip_upload=args.skip_upload, offset=args.offset, yes=args.yes)
