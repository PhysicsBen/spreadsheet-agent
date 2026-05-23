"""Streamlit chat UI for the Spreadsheet Agent API.

This is a dev/test tool only. It calls the FastAPI backend and requires it to be
running. Start the backend with:
    docker compose up
or:
    uv run fastapi dev src/api/main.py

Then run this UI with:
    streamlit run ui/app.py
"""

import os

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="Spreadsheet Agent", page_icon="📊", layout="wide")


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _truncate_uuid(value: str | None, chars: int = 8) -> str:
    """Return the first *chars* characters of a UUID string for display."""
    if not value:
        return "—"
    return str(value)[:chars] + "…"


def _post_session(file_bytes: bytes, filename: str) -> dict:
    """Upload a file and create a new session. Returns the response JSON."""
    resp = requests.post(
        f"{API_BASE_URL}/api/v1/sessions",
        files={"file": (filename, file_bytes)},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _post_query(session_id: str, question: str, thread_id: str | None) -> dict:
    """Submit a question and return the response JSON."""
    payload: dict = {"question": question}
    if thread_id:
        payload["thread_id"] = thread_id
    resp = requests.post(
        f"{API_BASE_URL}/api/v1/sessions/{session_id}/query",
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Sidebar — Session Management
# ---------------------------------------------------------------------------


def render_sidebar() -> None:
    with st.sidebar:
        st.title("📊 Spreadsheet Agent")
        st.divider()

        # ── File uploader ──────────────────────────────────────────────────
        uploaded_file = st.file_uploader(
            "Upload an Excel file",
            type=["xlsx", "xlsm", "xls", "xlsb"],
            help="Accepted formats: .xlsx, .xlsm, .xls, .xlsb",
        )

        if uploaded_file is not None and (
            "session_id" not in st.session_state
            or st.session_state.get("_last_uploaded_name") != uploaded_file.name
        ):
            with st.spinner("Inspecting workbook…"):
                try:
                    data = _post_session(uploaded_file.read(), uploaded_file.name)
                    st.session_state["session_id"] = str(data["session_id"])
                    st.session_state["workbook_meta"] = data.get("workbook_meta", {})
                    st.session_state["thread_id"] = None
                    st.session_state["messages"] = []
                    st.session_state["_last_uploaded_name"] = uploaded_file.name
                    st.success("File uploaded successfully.")
                except requests.HTTPError as exc:
                    st.error(
                        f"Upload failed — HTTP {exc.response.status_code}: "
                        f"{exc.response.text}"
                    )
                except requests.RequestException as exc:
                    st.error(f"Could not reach the API: {exc}")

        # ── Workbook metadata ──────────────────────────────────────────────
        if "workbook_meta" in st.session_state:
            meta = st.session_state["workbook_meta"]
            sheets = meta.get("sheets", [])
            with st.expander("📁 Workbook metadata", expanded=False):
                filename = meta.get("filename", "—")
                st.markdown(f"**File:** {filename}")
                st.markdown(
                    f"**Formula values available:** "
                    f"{'✅ Yes' if meta.get('formula_values_available') else '❌ No'}"
                )
                for sheet in sheets:
                    dims = sheet.get("dimensions", {})
                    rows = dims.get("rows", "?")
                    cols = dims.get("cols", "?")
                    state_label = sheet.get("state", "visible")
                    hidden = " *(hidden)*" if state_label != "visible" else ""
                    st.markdown(
                        f"**Sheet:** {sheet['name']}{hidden} — "
                        f"{rows} rows × {cols} cols"
                    )
                    tables = sheet.get("tables", [])
                    if tables:
                        for tbl in tables:
                            tbl_cols = len(tbl.get("columns", []))
                            # row count = range rows minus header
                            tbl_range = tbl.get("range", "")
                            st.markdown(
                                f"  - 📋 **{tbl.get('name', tbl.get('id', '?'))}** "
                                f"({tbl_cols} columns"
                                + (f", range {tbl_range}" if tbl_range else "")
                                + ")"
                            )
                    else:
                        st.markdown("  *No named tables detected.*")

        # ── Conversation controls ──────────────────────────────────────────
        if "session_id" in st.session_state:
            st.divider()
            col1, col2 = st.columns(2)
            with col1:
                if st.button("💬 New Conversation", use_container_width=True):
                    st.session_state["thread_id"] = None
                    st.session_state["messages"] = []
                    st.rerun()
            with col2:
                if st.button("📂 Change File", use_container_width=True):
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    st.rerun()

            st.divider()
            st.caption("**Debug info**")
            st.caption(
                f"Session: `{_truncate_uuid(st.session_state.get('session_id'))}`"
            )
            st.caption(
                f"Thread:  `{_truncate_uuid(st.session_state.get('thread_id'))}`"
            )


# ---------------------------------------------------------------------------
# Main area — Chat Interface
# ---------------------------------------------------------------------------


def render_chat() -> None:
    st.header("Spreadsheet Agent")

    if "session_id" not in st.session_state:
        st.info(
            "📂 **Upload an Excel file in the sidebar to get started.**\n\n"
            "The agent can answer natural-language questions about your spreadsheet — "
            "totals, filters, comparisons, and more."
        )
        return

    # ── Render conversation history ────────────────────────────────────────
    for msg in st.session_state.get("messages", []):
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            with st.chat_message("user"):
                st.write(content)
        else:
            # assistant message
            needs_clarification = msg.get("needs_clarification", False)
            with st.chat_message("assistant"):
                if needs_clarification:
                    st.info(content)
                else:
                    st.write(content)

                # Sources & usage expander
                sources = msg.get("sources", [])
                tokens = msg.get("tokens_used", 0)
                model = msg.get("model", "—")
                with st.expander("Sources & Usage", expanded=False):
                    if sources:
                        st.markdown("**Sources**")
                        for src in sources:
                            st.markdown(
                                f"- **{src.get('table_id', '?')}** "
                                f"(sheet: `{src.get('sheet', '?')}`, "
                                f"tool: `{src.get('tool_used', '?')}`)"
                            )
                    else:
                        st.markdown("*No sources recorded.*")
                    st.markdown(f"**Tokens used:** {tokens}")
                    st.markdown(f"**Model:** {model}")

    # ── Chat input ─────────────────────────────────────────────────────────
    question = st.chat_input("Ask a question about your spreadsheet…")
    if not question:
        return

    # Show user message immediately
    st.session_state.setdefault("messages", []).append(
        {"role": "user", "content": question}
    )
    with st.chat_message("user"):
        st.write(question)

    # Call the API
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                result = _post_query(
                    st.session_state["session_id"],
                    question,
                    st.session_state.get("thread_id"),
                )
            except requests.HTTPError as exc:
                st.error(
                    f"API error — HTTP {exc.response.status_code}: {exc.response.text}"
                )
                # Remove the user message we just added so the history stays clean
                st.session_state["messages"].pop()
                return
            except requests.RequestException as exc:
                st.error(f"Could not reach the API: {exc}")
                st.session_state["messages"].pop()
                return

        # Persist thread_id for follow-up questions
        st.session_state["thread_id"] = str(result.get("thread_id", ""))

        answer = result.get("answer", "")
        needs_clarification = result.get("needs_clarification", False)
        sources = result.get("sources", [])
        tokens_used = result.get("tokens_used", 0)
        model = result.get("model", "—")

        if needs_clarification:
            st.info(answer)
        else:
            st.write(answer)

        with st.expander("Sources & Usage", expanded=False):
            if sources:
                st.markdown("**Sources**")
                for src in sources:
                    st.markdown(
                        f"- **{src.get('table_id', '?')}** "
                        f"(sheet: `{src.get('sheet', '?')}`, "
                        f"tool: `{src.get('tool_used', '?')}`)"
                    )
            else:
                st.markdown("*No sources recorded.*")
            st.markdown(f"**Tokens used:** {tokens_used}")
            st.markdown(f"**Model:** {model}")

    # Persist the assistant message to session state for future renders
    st.session_state["messages"].append(
        {
            "role": "assistant",
            "content": answer,
            "needs_clarification": needs_clarification,
            "sources": sources,
            "tokens_used": tokens_used,
            "model": model,
        }
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

render_sidebar()
render_chat()
