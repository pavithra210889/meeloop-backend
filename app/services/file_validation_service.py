import logging
from typing import Dict, List, Tuple, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class FileType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHER = "other"


class ContentType(str, Enum):
    STORY = "story"
    POST = "post"
    MESSAGE = "message"
    PROFILE_PICTURE = "profile_picture"


class FileValidationService:
    """Service for validating file types and sizes based on content type."""
    
    # File type mappings
    IMAGE_EXTENSIONS = {
        'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tiff', 'svg'
    }
    
    VIDEO_EXTENSIONS = {
        'mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv', 'm4v'
    }
    
    AUDIO_EXTENSIONS = {
        'mp3', 'wav', 'aac', 'flac', 'ogg', 'm4a', 'wma'
    }
    
    DOCUMENT_EXTENSIONS = {
        'pdf', 'doc', 'docx', 'txt', 'rtf', 'odt', 'xls', 'xlsx', 
        'ppt', 'pptx', 'csv', 'zip', 'rar', '7z', 'tar', 'gz'
    }
    
    # Content type restrictions
    CONTENT_TYPE_RESTRICTIONS = {
        ContentType.STORY: {
            'allowed_file_types': [FileType.IMAGE, FileType.VIDEO],
            'max_size_mb': 50,  # 50MB for stories
            'description': 'Stories can only contain images and videos'
        },
        ContentType.POST: {
            'allowed_file_types': [FileType.IMAGE, FileType.VIDEO],
            'max_size_mb': 100,  # 100MB for posts
            'description': 'Posts can only contain images and videos'
        },
        ContentType.MESSAGE: {
            'allowed_file_types': [FileType.IMAGE, FileType.VIDEO, FileType.AUDIO, FileType.DOCUMENT],
            'max_size_mb': 25,  # 25MB for messages
            'description': 'Messages can contain images, videos, audio, and documents'
        },
        ContentType.PROFILE_PICTURE: {
            'allowed_file_types': [FileType.IMAGE],
            'max_size_mb': 10,  # 10MB for profile pictures
            'description': 'Profile pictures must be images'
        }
    }
    
    def __init__(self):
        # Build reverse lookup for file extensions
        self.extension_to_file_type = {}
        for file_type, extensions in [
            (FileType.IMAGE, self.IMAGE_EXTENSIONS),
            (FileType.VIDEO, self.VIDEO_EXTENSIONS),
            (FileType.AUDIO, self.AUDIO_EXTENSIONS),
            (FileType.DOCUMENT, self.DOCUMENT_EXTENSIONS),
        ]:
            for ext in extensions:
                self.extension_to_file_type[ext.lower()] = file_type
    
    def get_file_type_from_extension(self, file_extension: str) -> FileType:
        """Determine file type from extension."""
        ext = file_extension.lower().lstrip('.')
        return self.extension_to_file_type.get(ext, FileType.OTHER)
    
    def get_file_type_from_mime_type(self, mime_type: str) -> FileType:
        """Determine file type from MIME type."""
        mime = mime_type.lower()
        
        if mime.startswith('image/'):
            return FileType.IMAGE
        elif mime.startswith('video/'):
            return FileType.VIDEO
        elif mime.startswith('audio/'):
            return FileType.AUDIO
        elif mime in [
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'text/plain',
            'application/rtf',
            'application/vnd.oasis.opendocument.text',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.ms-powerpoint',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'text/csv',
            'application/zip',
            'application/x-rar-compressed',
            'application/x-7z-compressed',
            'application/x-tar',
            'application/gzip'
        ]:
            return FileType.DOCUMENT
        else:
            return FileType.OTHER
    
    def validate_file_for_content_type(
        self, 
        file_extension: str, 
        mime_type: str, 
        file_size_bytes: Optional[int],
        content_type: ContentType
    ) -> Tuple[bool, str]:
        """
        Validate if a file can be uploaded for the given content type.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Get file type from both extension and MIME type
        file_type_from_ext = self.get_file_type_from_extension(file_extension)
        file_type_from_mime = self.get_file_type_from_mime_type(mime_type)
        
        # Ensure both methods agree on file type
        if file_type_from_ext != file_type_from_mime and file_type_from_ext != FileType.OTHER:
            logger.warning(
                f"File type mismatch: extension suggests {file_type_from_ext}, "
                f"MIME type suggests {file_type_from_mime}"
            )
            # Use MIME type as it's more reliable
            file_type = file_type_from_mime
        else:
            file_type = file_type_from_ext
        
        # Check if content type is supported
        if content_type not in self.CONTENT_TYPE_RESTRICTIONS:
            return False, f"Unsupported content type: {content_type}"
        
        restrictions = self.CONTENT_TYPE_RESTRICTIONS[content_type]
        
        # Check if file type is allowed
        if file_type not in restrictions['allowed_file_types']:
            allowed_types = ', '.join([ft.value for ft in restrictions['allowed_file_types']])
            return False, f"File type '{file_type.value}' not allowed for {content_type.value}. Allowed types: {allowed_types}"
        
        # Check file size if provided
        if file_size_bytes is not None:
            max_size_bytes = restrictions['max_size_mb'] * 1024 * 1024
            if file_size_bytes > max_size_bytes:
                return False, f"File size ({file_size_bytes / (1024*1024):.1f}MB) exceeds maximum allowed size ({restrictions['max_size_mb']}MB) for {content_type.value}"
        
        return True, ""
    
    def get_allowed_extensions_for_content_type(self, content_type: ContentType) -> List[str]:
        """Get list of allowed file extensions for a content type."""
        if content_type not in self.CONTENT_TYPE_RESTRICTIONS:
            return []
        
        allowed_file_types = self.CONTENT_TYPE_RESTRICTIONS[content_type]['allowed_file_types']
        extensions = []
        
        for file_type in allowed_file_types:
            if file_type == FileType.IMAGE:
                extensions.extend(self.IMAGE_EXTENSIONS)
            elif file_type == FileType.VIDEO:
                extensions.extend(self.VIDEO_EXTENSIONS)
            elif file_type == FileType.AUDIO:
                extensions.extend(self.AUDIO_EXTENSIONS)
            elif file_type == FileType.DOCUMENT:
                extensions.extend(self.DOCUMENT_EXTENSIONS)
        
        return sorted(extensions)
    
    def get_max_file_size_for_content_type(self, content_type: ContentType) -> int:
        """Get maximum file size in MB for a content type."""
        if content_type not in self.CONTENT_TYPE_RESTRICTIONS:
            return 0
        
        return self.CONTENT_TYPE_RESTRICTIONS[content_type]['max_size_mb']


# Global instance
file_validation_service = FileValidationService()
