"""Integration tests for sessions API: upload, get, delete, list."""

from uuid import UUID


async def _create_session(
    api_client, file_bytes: bytes, filename: str = "workbook.xlsx"
):
    client, _ = api_client
    response = await client.post(
        "/api/v1/sessions",
        files={
            "file": (
                filename,
                file_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 201
    return response.json()


async def test_upload_valid_file_returns_session(simple_xlsx, api_client):
    session = await _create_session(api_client, simple_xlsx)
    assert UUID(session["session_id"])
    assert session["filename"] == "workbook.xlsx"
    assert session["file_size_bytes"] > 0
    assert "sheets" in session["workbook_meta"]


async def test_upload_invalid_file_type_rejected(api_client):
    client, _ = api_client
    response = await client.post(
        "/api/v1/sessions",
        files={"file": ("not_excel.xlsx", b"plain text", "application/octet-stream")},
    )
    assert response.status_code == 400


async def test_get_session_returns_existing_record(simple_xlsx, api_client):
    client, _ = api_client
    created = await _create_session(api_client, simple_xlsx, "sales.xlsx")

    response = await client.get(f"/api/v1/sessions/{created['session_id']}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == created["session_id"]
    assert payload["filename"] == "sales.xlsx"
    assert payload["workbook_meta"]["filename"] == "sales.xlsx"


async def test_delete_session_removes_record(simple_xlsx, api_client):
    client, _ = api_client
    created = await _create_session(api_client, simple_xlsx)
    session_id = created["session_id"]

    delete_response = await client.delete(f"/api/v1/sessions/{session_id}")
    assert delete_response.status_code == 204

    get_response = await client.get(f"/api/v1/sessions/{session_id}")
    assert get_response.status_code == 404


async def test_upload_file_exceeding_size_limit_returns_413(api_client, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "max_file_size_mb", 0)

    client, _ = api_client
    response = await client.post(
        "/api/v1/sessions",
        files={
            "file": (
                "big.xlsx",
                b"PK\x03\x04" + b"x" * 10,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 413


async def test_list_sessions_includes_uploaded_session(simple_xlsx, api_client):
    client, _ = api_client
    created = await _create_session(api_client, simple_xlsx, "list_test.xlsx")

    response = await client.get("/api/v1/sessions")
    assert response.status_code == 200
    sessions = response.json()
    session_ids = [s["session_id"] for s in sessions]
    assert created["session_id"] in session_ids


async def test_get_nonexistent_session_returns_404(api_client):
    from uuid import uuid4

    client, _ = api_client
    response = await client.get(f"/api/v1/sessions/{uuid4()}")
    assert response.status_code == 404
