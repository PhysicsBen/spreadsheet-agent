"""Integration tests for query API."""

from uuid import uuid4

from core.config import settings


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


async def test_single_turn_query_returns_complete_response(simple_xlsx, api_client):
    client, _ = api_client
    session = await _create_session(api_client, simple_xlsx)

    response = await client.post(
        f"/api/v1/sessions/{session['session_id']}/query",
        json={"question": "What is in this workbook?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == session["session_id"]
    assert payload["answer"].startswith("Mock answer turn 1:")
    assert payload["model"] == "mock-model"
    assert payload["tokens_used"] == 21
    assert payload["sources"] == [
        {
            "table_id": "Sheet1.table_0",
            "sheet": "Sheet1",
            "tool_used": "get_sheet_sample",
        }
    ]
    assert payload["needs_clarification"] is False


async def test_multi_turn_query_reuses_same_thread(
    simple_xlsx, api_client, random_thread_id
):
    client, _ = api_client
    session = await _create_session(api_client, simple_xlsx)

    first = await client.post(
        f"/api/v1/sessions/{session['session_id']}/query",
        json={"question": "First question", "thread_id": random_thread_id},
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/sessions/{session['session_id']}/query",
        json={"question": "Second question", "thread_id": random_thread_id},
    )
    assert second.status_code == 200

    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["thread_id"] == random_thread_id
    assert second_payload["thread_id"] == random_thread_id
    assert first_payload["answer"].startswith("Mock answer turn 1:")
    assert second_payload["answer"].startswith("Mock answer turn 2:")


async def test_query_nonexistent_session_returns_404(api_client):
    client, _ = api_client
    missing_session_id = str(uuid4())

    response = await client.post(
        f"/api/v1/sessions/{missing_session_id}/query",
        json={"question": "Will this fail?"},
    )

    assert response.status_code == 404


async def test_omitting_thread_id_creates_new_thread_each_time(simple_xlsx, api_client):
    """Each request without a thread_id must get a distinct thread."""
    client, _ = api_client
    session = await _create_session(api_client, simple_xlsx)

    first = await client.post(
        f"/api/v1/sessions/{session['session_id']}/query",
        json={"question": "Question A"},
    )
    second = await client.post(
        f"/api/v1/sessions/{session['session_id']}/query",
        json={"question": "Question B"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["thread_id"] != second.json()["thread_id"]


async def test_question_exceeding_max_chars_returns_422(api_client, simple_xlsx):
    client, _ = api_client
    session = await _create_session(api_client, simple_xlsx)

    long_question = "a" * (settings.max_question_chars + 1)
    response = await client.post(
        f"/api/v1/sessions/{session['session_id']}/query",
        json={"question": long_question},
    )
    assert response.status_code == 422


async def test_query_timeout_returns_504(simple_xlsx, api_client, monkeypatch):
    """A graph that never returns should produce a 504 response."""
    client, mock_graph = api_client

    def _slow_invoke(initial_state, config):
        import time

        time.sleep(2)
        return initial_state

    monkeypatch.setattr(mock_graph, "invoke", _slow_invoke)
    monkeypatch.setattr(settings, "query_timeout_secs", 0)

    session = await _create_session(api_client, simple_xlsx)
    response = await client.post(
        f"/api/v1/sessions/{session['session_id']}/query",
        json={"question": "Will this time out?"},
    )
    assert response.status_code == 504
