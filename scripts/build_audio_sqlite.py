"""
Build audio_templates.sqlite from audio files already in R2.
- Lists all keys under audio/ prefix
- Deduplicates by slug (keeps one URL per unique track)
- Writes memetemplates table with template_type='audio'
"""
import boto3
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

R2_ACCOUNT_ID    = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET        = os.getenv("R2_SECRET_ACCESS_KEY")
BUCKET           = os.getenv("R2_BUCKET_NAME", "meme-templates")
PUBLIC_URL       = os.getenv("R2_PUBLIC_URL", "media.meeloop.com")
OUT_DB           = "audio_templates.sqlite"

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
    """Return dict: slug -> first matching R2 key."""
    paginator = s3.get_paginator("list_objects_v2")
    slug_map: dict[str, str] = {}
    total = 0
    for page in paginator.paginate(Bucket=BUCKET, Prefix="audio/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            fname = key.removeprefix("audio/")
            # extract slug by stripping trailing -xxxxxxxx or _xxxxxxxx hash before .mp3
            slug = re.sub(r"[-_][a-f0-9]{8}\.mp3$", "", fname)
            if not slug:
                continue
            total += 1
            # keep only the first occurrence per slug
            if slug not in slug_map:
                slug_map[slug] = key
    print(f"R2 audio/ prefix: {total} files → {len(slug_map)} unique tracks")
    return slug_map


def build_sqlite(slug_map: dict[str, str]):
    if os.path.exists(OUT_DB):
        os.remove(OUT_DB)

    conn = sqlite3.connect(OUT_DB)
    conn.execute("""
        CREATE TABLE memetemplates (
            id           TEXT PRIMARY KEY,
            template_type TEXT NOT NULL DEFAULT 'audio',
            content      TEXT NOT NULL,
            urls         TEXT NOT NULL DEFAULT '[]',
            hash_tags    TEXT NOT NULL DEFAULT '[]',
            metadata_info TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            created_by_id TEXT NOT NULL DEFAULT '',
            updated_by_id TEXT NOT NULL DEFAULT ''
        )
    """)

    now = datetime.utcnow().isoformat()
    rows = []
    for slug, key in sorted(slug_map.items()):
        public_url = f"https://{PUBLIC_URL}/{key}"
        title = slug_to_title(slug)
        # derive simple tags from slug words
        tags = [w.lower() for w in slug.replace("-", " ").replace("_", " ").split() if len(w) > 2]
        rows.append((
            str(uuid.uuid4()),
            "audio",
            title,
            json.dumps([public_url]),
            json.dumps(tags),
            json.dumps({}),
            now,
            now,
            "",
            "",
        ))

    conn.executemany(
        "INSERT INTO memetemplates VALUES (?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM memetemplates").fetchone()[0]
    print(f"Written {count} rows to {OUT_DB}")

    # Quick sanity check
    sample = conn.execute(
        "SELECT content, urls FROM memetemplates LIMIT 3"
    ).fetchall()
    for title, urls in sample:
        print(f"  {title}: {urls}")

    conn.close()


if __name__ == "__main__":
    slug_map = list_audio_files()
    build_sqlite(slug_map)
    print("Done.")
