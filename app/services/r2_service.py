import uuid
import logging
from typing import Dict, Optional, Tuple
from io import BytesIO

import boto3
import httpx
from botocore.exceptions import ClientError

from ..config import settings


logger = logging.getLogger(__name__)


class R2Service:
    def __init__(self) -> None:
        if not all([
            settings.R2_ACCOUNT_ID,
            settings.R2_ACCESS_KEY_ID,
            settings.R2_SECRET_ACCESS_KEY,
            settings.R2_BUCKET_NAME,
            settings.R2_PUBLIC_URL,
        ]):
            logger.warning("R2 configuration is incomplete. Presigned URL generation will fail.")

        self.s3_client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name=settings.R2_REGION,
        )
        self.bucket_name = settings.R2_BUCKET_NAME
        # Ensure public URL has https:// protocol
        public_url = settings.R2_PUBLIC_URL.rstrip("/")
        if not public_url.startswith(("http://", "https://")):
            public_url = f"https://{public_url}"
        self.public_url = public_url

    def generate_presigned_upload_url(
        self,
        *,
        file_extension: str,
        content_type: str,
        user_id: str,
        folder: str | None = None,
        expires_in: int = 3600,
    ) -> Dict[str, str]:
        """Generate a presigned URL for uploading a single file to R2.

        Files are stored under: {folder}/{user_id}/{uuid}.{ext}
        """
        unique_id = str(uuid.uuid4())
        safe_ext = (file_extension or "").lstrip(".")
        prefix = f"{user_id}" if not folder else f"{folder}/{user_id}"
        file_key = f"{prefix}/{unique_id}.{safe_ext}"

        try:
            presigned_url = self.s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket_name,
                    "Key": file_key,
                },
                ExpiresIn=expires_in,
            )
        except ClientError as e:
            logger.error("Failed to generate presigned URL: %s", e)
            raise

        return {
            "presigned_url": presigned_url,
            "file_key": file_key,
            "public_url": f"{self.public_url}/{file_key}",
            "expires_in": expires_in,
        }

    async def upload_from_url(
        self,
        *,
        url: str,
        user_id: str,
        folder: str = "profile_pics",
    ) -> Optional[str]:
        """Download an image from an external URL and upload it to R2.

        Returns the public R2 URL on success, or None on failure.
        """
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning("Failed to download image from %s: HTTP %s", url, resp.status_code)
                    return None

            content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            ext_map = {
                "image/jpeg": "jpg",
                "image/png": "png",
                "image/webp": "webp",
                "image/gif": "gif",
            }
            ext = ext_map.get(content_type, "jpg")

            unique_id = str(uuid.uuid4())
            file_key = f"{folder}/{user_id}/{unique_id}.{ext}"

            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=file_key,
                Body=BytesIO(resp.content),
                ContentType=content_type,
            )

            public_url = f"{self.public_url}/{file_key}"
            logger.info("Uploaded profile pic for user %s: %s", user_id, public_url)
            return public_url
        except Exception as e:
            logger.error("Failed to upload profile pic from URL %s: %s", url, e)
            return None

    def extract_file_key_from_url(self, url: str) -> Optional[str]:
        """Extract an object key from a public R2 URL if it matches our bucket public base URL."""
        base = f"{self.public_url}/"
        if url.startswith(base):
            return url[len(base) :]
        return None

    def get_object_head(self, file_key: str) -> Optional[Dict]:
        """Return object HEAD metadata (ContentType, ContentLength, etc.) or None on error."""
        try:
            return self.s3_client.head_object(Bucket=self.bucket_name, Key=file_key)
        except ClientError as e:
            logger.warning("head_object failed for key %s: %s", file_key, e)
            return None

    def get_content_type_and_length(self, file_key: str) -> Tuple[Optional[str], Optional[int]]:
        head = self.get_object_head(file_key)
        if not head:
            return None, None
        return head.get("ContentType"), head.get("ContentLength")


r2_service = R2Service()


