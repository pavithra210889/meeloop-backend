"""
Migrate video templates from local folder → Cloudflare R2 + PostgreSQL.

For each .mp4 + .txt pair in SOURCE_DIR:
  - Reads .txt for hashtags and description text
  - Generates slug: {tag1}_{tag2}_{random8hex}  (same naming as image templates)
  - Uploads .mp4 to R2 at video/{slug}.mp4
  - Inserts into memetemplates with template_type='VIDEO'

Usage:
    source env/bin/activate
    export DATABASE_URL="postgresql://user:pass@host:5432/meeloop"
    python3 migrate_video_r2_postgres.py

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
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

SOURCE_DIR = Path("/Users/nanne/Downloads/videoskiadda")
DATABASE_URL = os.environ.get("DATABASE_URL")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET = os.getenv("R2_SECRET_ACCESS_KEY")
BUCKET = os.getenv("R2_BUCKET_NAME", "meme-templates")
PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "media.meeloop.com")

# Tags ending with _vka are account-specific — strip that suffix
_VKA_RE = re.compile(r"_vka$", re.IGNORECASE)
# Keep only alphanumeric + underscore in slug parts
_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


def parse_txt(path: Path) -> tuple[list[str], str]:
    """
    Returns (tags, content) from a .txt caption file.
    Lines starting with # are hashtags; all other non-empty lines are content.
    """
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
            # Extract all #tags from the line (a line may have multiple)
            for raw in re.findall(r"#(\w+)", line):
                tag = _VKA_RE.sub("", raw).lower()
                tag = _SLUG_CLEAN.sub("_", tag).strip("_")
                if tag and len(tag) > 1:
                    tags.append(tag)
        else:
            content_lines.append(line)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_tags: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)

    return unique_tags, " ".join(content_lines)


def make_slug(tags: list[str], stem: str) -> str:
    """
    Build a filename slug from the first two meaningful tags + 8 random hex chars.
    Falls back to the file stem if no tags are available.
    Example: pubgaddict_trendingvideos_a3f8c201
    """
    meaningful = [t for t in tags if len(t) > 2][:2]
    if meaningful:
        base = "_".join(t[:20] for t in meaningful)
    else:
        # Use the timestamp stem sanitised (e.g. 2019-04-27_00-54-03_UTC → 20190427)
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
    except s3.exceptions.ClientError:
        return False
    except Exception:
        return False


def get_default_user_id(pg) -> str:
    with pg.cursor() as cur:
        cur.execute('SELECT id FROM "user" LIMIT 1')
        row = cur.fetchone()
        if not row:
            raise SystemExit("ERROR: No users found in the live database. Create at least one user first.")
        return row[0]


def _flush(pg, batch: list) -> int:
    sql = """
        INSERT INTO memetemplates
            (id, template_type, content, urls, hash_tags, metadata_info,
             created_at, updated_at, created_by_id, updated_by_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """
    with pg.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, batch, page_size=200)
    pg.commit()
    return len(batch)


def generate_uuid() -> str:
    import uuid
    return str(uuid.uuid4())


def collect_entries(limit: int | None) -> list[Path]:
    """Return sorted list of .mp4 paths from SOURCE_DIR."""
    mp4s = sorted(SOURCE_DIR.glob("*.mp4"))
    if limit:
        mp4s = mp4s[:limit]
    return mp4s


def migrate(dry_run: bool, limit: int | None, skip_upload: bool):
    if not DATABASE_URL and not dry_run:
        raise SystemExit("ERROR: Set DATABASE_URL environment variable first.")

    entries = collect_entries(limit)
    total = len(entries)
    print(f"Found {total} .mp4 files in {SOURCE_DIR}")

    s3 = None if (dry_run or skip_upload) else get_r2_client()
    pg = None if dry_run else psycopg2.connect(DATABASE_URL)

    if pg:
        psycopg2.extras.register_uuid()
        with pg.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM memetemplates WHERE template_type = 'VIDEO'")
            existing = cur.fetchone()[0]
        if existing > 0:
            answer = input(f"Target already has {existing} VIDEO rows. Continue and add more? (y/n): ")
            if answer.lower() != "y":
                pg.close()
                print("Aborted.")
                return
        default_user_id = get_default_user_id(pg)
        print(f"Using default user ID: {default_user_id}")

    now = datetime.now(timezone.utc).isoformat()
    batch: list = []
    batch_size = 100
    migrated = skipped = errors = 0

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Migrating {total} video templates…\n")

    for i, mp4_path in enumerate(entries, 1):
        stem = mp4_path.stem  # e.g. "2019-04-27_00-54-03_UTC"
        txt_path = mp4_path.with_suffix(".txt")

        tags, content = parse_txt(txt_path)
        slug = make_slug(tags, stem)
        r2_key = f"video/{slug}.mp4"
        public_url = f"https://{PUBLIC_URL}/{r2_key}"

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

        # Upload to R2
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
            generate_uuid(),
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
            migrated += _flush(pg, batch)
            batch = []
            print(f"  {migrated + skipped}/{total}  (migrated={migrated}, skipped={skipped}, errors={errors})")

    if batch and not dry_run:
        migrated += _flush(pg, batch)

    if pg:
        pg.commit()
        with pg.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM memetemplates WHERE template_type = 'VIDEO'")
            total_in_db = cur.fetchone()[0]
        pg.close()
        print(f"\nTotal VIDEO rows in memetemplates: {total_in_db}")

    print(f"\nDone. migrated={migrated} | skipped={skipped} | errors={errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate video templates to R2 + PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="Preview without uploading or writing to DB")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N entries")
    parser.add_argument("--skip-upload", action="store_true", help="Skip R2 upload (files already uploaded)")
    args = parser.parse_args()

    print("Video Templates → R2 + PostgreSQL Migration")
    print("=" * 45)
    migrate(dry_run=args.dry_run, limit=args.limit, skip_upload=args.skip_upload)
