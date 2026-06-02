from fastapi import APIRouter, Depends, HTTPException
from typing import Annotated

from ..users.routers import get_current_active_user
from ..users.models import User
from ..services.r2_service import r2_service
from ..services.file_validation_service import file_validation_service, ContentType
from .models import PresignedUrlRequest, PresignedUrlResponse


router = APIRouter(tags=["storage"])


@router.post("/storage/presigned-url", response_model=PresignedUrlResponse)
def get_presigned_upload_url(
    payload: PresignedUrlRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    try:
        # Validate file type and size restrictions
        is_valid, error_message = file_validation_service.validate_file_for_content_type(
            file_extension=payload.file_extension,
            mime_type=payload.content_type,
            file_size_bytes=None,  # Size validation happens on client side
            content_type=payload.upload_for
        )
        
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_message)
        
        data = r2_service.generate_presigned_upload_url(
            file_extension=payload.file_extension,
            content_type=payload.content_type,
            user_id=current_user.id,
        )
        return PresignedUrlResponse(**data)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")
