"""Tests for the presigned upload URL endpoint."""

from unittest.mock import patch

_FAKE_PRESIGNED = {
    "presigned_url": "https://r2.example.com/upload/test.jpg?sig=abc",
    "file_key": "uploads/test-user/test.jpg",
    "public_url": "https://media.meeloop.com/uploads/test-user/test.jpg",
    "expires_in": 900,
}


class TestPresignedUploadUrl:
    def test_valid_image_returns_presigned_url(self, client, auth_headers):
        """Valid image extension and content type returns the presigned URL data."""
        with patch("app.storage.routers.file_validation_service.validate_file_for_content_type", return_value=(True, None)), \
             patch("app.storage.routers.r2_service.generate_presigned_upload_url", return_value=_FAKE_PRESIGNED):
            resp = client.post("/storage/presigned-url", json={
                "file_extension": "jpg",
                "content_type": "image/jpeg",
                "upload_for": "post",
            }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "presigned_url" in data
        assert "file_key" in data
        assert "public_url" in data
        assert "expires_in" in data

    def test_invalid_file_type_returns_400(self, client, auth_headers):
        """File type not allowed for the given content category returns 400."""
        with patch("app.storage.routers.file_validation_service.validate_file_for_content_type", return_value=(False, "File type not allowed for this content")):
            resp = client.post("/storage/presigned-url", json={
                "file_extension": "exe",
                "content_type": "application/octet-stream",
                "upload_for": "post",
            }, headers=auth_headers)
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"].lower()

    def test_r2_error_returns_500(self, client, auth_headers):
        """R2 service failure returns 500."""
        with patch("app.storage.routers.file_validation_service.validate_file_for_content_type", return_value=(True, None)), \
             patch("app.storage.routers.r2_service.generate_presigned_upload_url", side_effect=Exception("R2 unavailable")):
            resp = client.post("/storage/presigned-url", json={
                "file_extension": "jpg",
                "content_type": "image/jpeg",
                "upload_for": "post",
            }, headers=auth_headers)
        assert resp.status_code == 500

    def test_requires_auth(self, client):
        """Upload URL request without auth returns 401."""
        resp = client.post("/storage/presigned-url", json={
            "file_extension": "jpg",
            "content_type": "image/jpeg",
            "upload_for": "post",
        })
        assert resp.status_code == 401

    def test_profile_pic_upload_allowed(self, client, auth_headers):
        """Profile picture upload type is accepted."""
        with patch("app.storage.routers.file_validation_service.validate_file_for_content_type", return_value=(True, None)), \
             patch("app.storage.routers.r2_service.generate_presigned_upload_url", return_value=_FAKE_PRESIGNED):
            resp = client.post("/storage/presigned-url", json={
                "file_extension": "png",
                "content_type": "image/png",
                "upload_for": "profile_picture",
            }, headers=auth_headers)
        assert resp.status_code == 200
