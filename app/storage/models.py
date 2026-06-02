from pydantic import BaseModel
from enum import Enum


from app.services.file_validation_service import ContentType


class PresignedUrlRequest(BaseModel):
    file_extension: str
    content_type: str
    upload_for: ContentType  # What type of content this file is for


class PresignedUrlResponse(BaseModel):
    presigned_url: str
    file_key: str
    public_url: str
    expires_in: int


