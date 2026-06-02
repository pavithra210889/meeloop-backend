"""
Migrate audio templates from Cloudflare R2 directly to production PostgreSQL.

Reads audio/ prefix from R2, deduplicates by slug, inserts into memetemplates.

Usage:
    source env/bin/activate
    export DATABASE_URL='postgresql://...'
    python3 migrate_audio_r2_postgres.py
"""
import json
import os
import re
import uuid
from datetime import datetime

import boto3
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL      = os.environ.get("DATABASE_URL")
R2_ACCOUNT_ID     = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID  = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET         = os.getenv("R2_SECRET_ACCESS_KEY")
BUCKET            = os.getenv("R2_BUCKET_NAME", "meme-templates")
PUBLIC_URL        = os.getenv("R2_PUBLIC_URL", "media.meeloop.com")

if not DATABASE_URL:
    raise SystemExit("ERROR: Set DATABASE_URL environment variable first.")

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET,
    region_name="auto",
)


def slug_to_title(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("-", " ").replace("_", " ").split())


def list_audio_files():
    paginator = s3.get_paginator("list_objects_v2")
    slug_map: dict[str, str] = {}
    total = 0
    for page in paginator.paginate(Bucket=BUCKET, Prefix="audio/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            fname = key.removeprefix("audio/")
            slug = re.sub(r"[-_][a-f0-9]{8}\.mp3$", "", fname)
            if not slug:
                continue
            total += 1
            if slug not in slug_map:
                slug_map[slug] = key
    print(f"R2 audio/ prefix: {total} files → {len(slug_map)} unique tracks")
    return slug_map


def get_default_user_id(pg):
    with pg.cursor() as cur:
        cur.execute('SELECT id FROM "user" LIMIT 1')
        row = cur.fetchone()
        if not row:
            raise SystemExit("ERROR: No users found in the live database.")
        return row[0]


def migrate():
    slug_map = list_audio_files()
    if not slug_map:
        print("No audio files found in R2. Check R2_BUCKET_NAME and credentials.")
        return

    pg = psycopg2.connect(DATABASE_URL)
    try:
        default_user_id = get_default_user_id(pg)
        print(f"Using default user ID: {default_user_id}")

        with pg.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM memetemplates WHERE template_type = 'AUDIO'")
            existing = cur.fetchone()[0]
        if existing > 0:
            answer = input(f"Target already has {existing} AUDIO rows. Continue? (y/n): ")
            if answer.lower() != "y":
                print("Aborted.")
                return

        now = datetime.utcnow().isoformat()
        batch = []
        batch_size = 500
        migrated = 0
        total = len(slug_map)

        print("Migrating…")
        for slug, key in sorted(slug_map.items()):
            public_url = f"https://{PUBLIC_URL}/{key}"
            title = slug_to_title(slug)
            tags = [w.lower() for w in slug.replace("-", " ").replace("_", " ").split() if len(w) > 2]

            batch.append((
                str(uuid.uuid4()),
                "AUDIO",
                title,
                json.dumps([public_url]),
                json.dumps(tags),
                json.dumps({}),
                now, now,
                default_user_id, default_user_id,
            ))

            if len(batch) >= batch_size:
                migrated += _flush(pg, batch)
                batch = []
                print(f"  {migrated}/{total}")

        if batch:
            migrated += _flush(pg, batch)

        pg.commit()
        print(f"\nDone. Migrated: {migrated} audio templates | Errors: {total - migrated}")

        with pg.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM memetemplates WHERE template_type = 'AUDIO'")
            print(f"Total AUDIO rows in memetemplates: {cur.fetchone()[0]}")

    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()


def _flush(pg, batch):
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


if __name__ == "__main__":
    print("Audio Templates R2 → PostgreSQL Migration")
    print("=" * 45)
    migrate()
