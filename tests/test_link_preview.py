"""Tests for the link preview endpoint."""

from unittest.mock import AsyncMock, patch, MagicMock
import httpx


def test_link_preview_requires_auth(client):
    """GET /link-preview/ should 401 without auth headers."""
    resp = client.get("/link-preview/", params={"url": "https://example.com"})
    assert resp.status_code == 401 or resp.status_code == 403


def test_link_preview_missing_url(client, auth_headers):
    """GET /link-preview/ should 422 when url param is missing."""
    resp = client.get("/link-preview/", headers=auth_headers)
    assert resp.status_code == 422


def test_link_preview_success(client, auth_headers):
    """GET /link-preview/ should return parsed OG metadata."""
    fake_html = """
    <html>
    <head>
        <meta property="og:title" content="Example Title">
        <meta property="og:description" content="Example description here.">
        <meta property="og:image" content="https://example.com/image.jpg">
        <meta property="og:site_name" content="Example Site">
        <title>Fallback Title</title>
    </head>
    <body></body>
    </html>
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}
    mock_response.text = fake_html
    mock_response.raise_for_status = MagicMock()

    with patch("app.link_preview.routers.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = client.get(
            "/link-preview/",
            params={"url": "https://example.com"},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Example Title"
    assert data["description"] == "Example description here."
    assert data["image"] == "https://example.com/image.jpg"
    assert data["site_name"] == "Example Site"
    assert data["url"] == "https://example.com"


def test_link_preview_fallback_title(client, auth_headers):
    """Should fall back to <title> tag when og:title is missing."""
    fake_html = """
    <html>
    <head>
        <title>Fallback Title</title>
        <meta property="og:description" content="A description">
    </head>
    </html>
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = fake_html
    mock_response.raise_for_status = MagicMock()

    with patch("app.link_preview.routers.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = client.get(
            "/link-preview/",
            params={"url": "https://example.com"},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Fallback Title"
    assert data["description"] == "A description"


def test_link_preview_reversed_meta_attributes(client, auth_headers):
    """Should handle meta tags with content before property."""
    fake_html = """
    <html><head>
        <meta content="Reversed Title" property="og:title">
        <meta content="https://example.com/img.png" property="og:image">
    </head></html>
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = fake_html
    mock_response.raise_for_status = MagicMock()

    with patch("app.link_preview.routers.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = client.get(
            "/link-preview/",
            params={"url": "https://example.com"},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Reversed Title"
    assert data["image"] == "https://example.com/img.png"


def test_link_preview_relative_image_resolved(client, auth_headers):
    """Relative og:image URLs should be resolved against the base URL."""
    fake_html = '<html><head><meta property="og:title" content="T"><meta property="og:image" content="/img/photo.jpg"></head></html>'
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = fake_html
    mock_response.raise_for_status = MagicMock()

    with patch("app.link_preview.routers.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = client.get(
            "/link-preview/",
            params={"url": "https://example.com/page"},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["image"] == "https://example.com/img/photo.jpg"


def test_link_preview_non_html_rejected(client, auth_headers):
    """Should return 400 for non-HTML URLs."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.text = '{"key": "value"}'
    mock_response.raise_for_status = MagicMock()

    with patch("app.link_preview.routers.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = client.get(
            "/link-preview/",
            params={"url": "https://example.com/api/data"},
            headers=auth_headers,
        )

    assert resp.status_code == 400


def test_link_preview_http_error(client, auth_headers):
    """Should return 400 when the target URL returns an HTTP error."""
    with patch("app.link_preview.routers.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Not Found",
                request=MagicMock(),
                response=MagicMock(status_code=404),
            )
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = client.get(
            "/link-preview/",
            params={"url": "https://example.com/notfound"},
            headers=auth_headers,
        )

    assert resp.status_code == 400


def test_link_preview_prepends_https(client, auth_headers):
    """URLs without scheme should get https:// prepended."""
    fake_html = '<html><head><meta property="og:title" content="Hello"></head></html>'
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = fake_html
    mock_response.raise_for_status = MagicMock()

    with patch("app.link_preview.routers.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        resp = client.get(
            "/link-preview/",
            params={"url": "example.com"},
            headers=auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["url"] == "https://example.com"
