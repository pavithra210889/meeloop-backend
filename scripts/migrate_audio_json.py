import json
import os
import uuid
from datetime import datetime
import asyncio
from dotenv import load_dotenv
import boto3
from sqlmodel import Session, create_engine, select
import sys

# Add the app directory to the path so we can import our models
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.meme_templates.models import MemeTemplates, TemplateType
# Load the entire app to initialize all models and relationships
from main import app
from app.meme_templates.models import MemeTemplates, TemplateType
from app.users.models import User
from app.database import engine

# Load environment variables
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./database.sqlite")
engine = create_engine(DATABASE_URL)

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL")

# Initialize S3 client for Cloudflare R2
s3_client = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name="auto", 
)

LOCAL_FILES_DIR = "/Users/nanne/Downloads/memesounds/files"
JSON_FILE_PATH = "/Users/nanne/Downloads/memesounds/audio.json"

def find_local_file(audio_url: str) -> str | None:
    """Extracts the filename from the URL, ignoring the timestamp blob."""
    # Example URL: https://telugumemesounds.blob.core.windows.net/sounds/padhe-padhe-20260101150410.mp3
    filename = audio_url.split("/")[-1]
    
    # Let's try to remove the "-timestamp" suffix if it exists.
    # We will just search the LOCAL_FILES_DIR for anything matching the base prefix.
    # More simply, we might just look for the title/slug if the URL matching fails.
    
    # However, many files in `@files` exactly match the slug + .mp3.
    # Let's just return the directory path and let the caller search by slug.
    pass

def process_audio_file(session: Session, item: dict, default_user_id: int):
    slug = item.get("slug")
    if not slug:
        print(f"Skipping item with no slug: {item.get('title')}")
        return False

    expected_filename = f"{slug}.mp3"
    local_path = os.path.join(LOCAL_FILES_DIR, expected_filename)
    
    if not os.path.exists(local_path):
        print(f"File not found locally: {local_path} (ID: {item.get('id')})")
        return False

    # Generate the random URL name: slug-randomhash.mp3
    random_hash = uuid.uuid4().hex[:8]
    new_filename = f"{slug}-{random_hash}.mp3"
    r2_key = f"audio/{new_filename}"

    # Upload to Cloudflare R2
    try:
        s3_client.upload_file(
            local_path, 
            R2_BUCKET_NAME, 
            r2_key,
            ExtraArgs={"ContentType": "audio/mpeg"} 
        )
    except Exception as e:
        print(f"Failed to upload {local_path} to R2: {e}")
        return False

    # Construct public URL
    public_url = f"https://{R2_PUBLIC_URL}/{r2_key}"

    # Metadata transformation
    metadata_info = {
        "description": item.get("description", ""),
        "categories": item.get("categories", []),
        "playCount": item.get("playCount", 0),
        "downloadCount": item.get("downloadCount", 0),
        "likeCount": item.get("likeCount", 0),
        "originalUser": item.get("user", {}),
    }

    # Extract tags
    tags = [tag.get("name") for tag in item.get("tags", []) if tag.get("name")]

    # Parse dates
    created_at_str = item.get("createdAt")
    created_at = datetime.now()
    if created_at_str:
        try:
            # Handle standard ISO 8601 with Z
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            # Make naive for sqlite
            created_at = created_at.replace(tzinfo=None)
        except ValueError:
            pass

    # Create the template record
    template = MemeTemplates(
        template_type=TemplateType.AUDIO,
        content=item.get("title", ""),
        urls=json.dumps([public_url]),
        hash_tags=tags,
        metadata_info=metadata_info,
        created_at=created_at,
        updated_at=created_at,
        created_by_id=default_user_id,
        updated_by_id=default_user_id
    )

    try:
        session.add(template)
        session.commit()
        print(f"Successfully migrated: {item.get('title')} -> {public_url}")
        return True
    except Exception as e:
        session.rollback()
        print(f"Database error for {item.get('title')}: {e}")
        return False


def main():
    if not os.path.exists(JSON_FILE_PATH):
        print(f"JSON file not found at {JSON_FILE_PATH}")
        return
        
    with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Some APIs wrap the array in {"data": [...]}
    if isinstance(data, dict) and "data" in data:
        items = data["data"]
    elif isinstance(data, list):
        items = data
    else:
        print("Expected JSON data to be a list of objects or a dict with a 'data' array.")
        return

    print(f"Loaded {len(items)} items from JSON.")

    with Session(engine) as session:
        # We need a default user to attribute these templates to.
        # We'll pick the first user.
        default_user = session.exec(select(User).limit(1)).first()
        if not default_user:
            print("No users found in the database. Please create a user first.")
            return
        
        default_user_id = default_user.id
        print(f"Using default user ID: {default_user_id} for migrated templates.")

        success_count = 0
        for item in items:
            if process_audio_file(session, item, default_user_id):
                success_count += 1
                
        print(f"\nMigration completed! Successfully processed {success_count}/{len(items)} items.")

if __name__ == "__main__":
    main()
