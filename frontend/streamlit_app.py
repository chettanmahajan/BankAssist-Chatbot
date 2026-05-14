"""Streamlit chat UI for the Banking Support Assistant."""
from __future__ import annotations

import json
import os
import uuid
from typing import Iterator

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL", "http://localhost:8000")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))


SAMPLE_QUESTIONS = [
    "What is the current home loan interest rate?",
    "What documents do I need for a personal loan?",
    "What is the daily UPI transaction limit?",
    "What is the difference between NEFT and RTGS?",
    "How do I report a lost credit card?",
    "Tell me about the PPF scheme.",
]


# ----- helpers --------------------------------------------------------------


def _api_chat_stream(query: str, session_id: str) -> Iterator[dict]:
    """Hit /chat/stream and yield parsed SSE events as dicts."""
    url = f"{API_URL}/chat/stream"
    try:
        with requests.post(
            url,
            json={"query": query, "session_id": session_id},
            stream=True,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status_code != 200:
                yield {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
                return
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                if raw_line.startswith("data:"):
                    payload = raw_line[len("data:"):].strip()
                    if not payload:
                        continue
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        continue
    except requests.exceptions.ConnectionError:
        yield {"error": f"Cannot reach API at {API_URL}. Is the backend running?"}
    except requests.exceptions.Timeout:
        yield {"error": "Request timed out."}
    except requests.exceptions.RequestException as exc:
        yield {"error": f"Request error: {exc}"}


def _api_clear_session(session_id: str) -> bool:
    try:
        resp = requests.delete(f"{API_URL}/session/{session_id}", timeout=10)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def _api_health() -> dict | None:
    try:
        resp = requests.get(f"{API_URL}/health", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except requests.exceptions.RequestException:
        return None
    return None


# ----- session state --------------------------------------------------------


def _init_state() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid.uuid4().hex
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "pending_query" not in st.session_state:
        st.session_state.pending_query = None


# ----- main UI --------------------------------------------------------------


st.set_page_config(
    page_title="Banking Support Assistant",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)
_init_state()


with st.sidebar:
    st.title("🏦 Banking Assistant")
    st.markdown(
        "Ask anything about loans, credit cards, accounts, UPI, NEFT/RTGS, "
        "fraud protection, NRI banking, investments, and more."
    )
    st.divider()

    health = _api_health()
    if health is None:
        st.error("API unreachable")
        st.caption(f"Expected at: `{API_URL}`")
    elif health.get("status") == "ok":
        st.success(f"API healthy · {health.get('docs_indexed', 0)} chunks indexed")
    else:
        st.warning(f"API degraded: {health}")

    st.divider()
    st.subheader("Try a sample question")
    for q in SAMPLE_QUESTIONS:
        if st.button(q, key=f"sample_{hash(q)}", use_container_width=True):
            st.session_state.pending_query = q
            st.rerun()

    st.divider()
    if st.button("🗑️ Clear conversation", use_container_width=True):
        cleared = _api_clear_session(st.session_state.session_id)
        st.session_state.messages = []
        st.session_state.session_id = uuid.uuid4().hex
        st.session_state.pending_query = None
        st.toast("Conversation cleared" if cleared else "Cleared locally (API didn't respond)")
        st.rerun()

    st.divider()
    st.caption(f"Session ID: `{st.session_state.session_id[:12]}...`")
    st.caption(f"API: `{API_URL}`")


# ----- main column ----------------------------------------------------------

st.title("Banking Support Assistant 🏦")
st.caption(
    "Powered by RAG (LangChain + ChromaDB + Groq Llama-3.3-70B). "
    "Answers grounded in curated Indian banking documents."
)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("sources"):
            with st.expander(f"📄 Sources ({len(msg['sources'])})"):
                for src in msg["sources"]:
                    st.markdown(f"**{src['filename']}** · _{src['category']}_")
                    st.caption(src["snippet"])
                    st.divider()


# Pick up query from sidebar click OR chat input
user_query: str | None = None
if st.session_state.pending_query:
    user_query = st.session_state.pending_query
    st.session_state.pending_query = None

typed = st.chat_input("Ask a banking question...")
if typed:
    user_query = typed


if user_query:
    user_query = user_query.strip()
    st.session_state.messages.append({"role": "user", "content": user_query})

    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        sources_placeholder = st.empty()

        with st.spinner("Thinking..."):
            collected = ""
            collected_sources: list[dict] = []
            error_msg: str | None = None

            for event in _api_chat_stream(user_query, st.session_state.session_id):
                if "error" in event:
                    error_msg = event["error"]
                    break
                if "token" in event:
                    collected += event["token"]
                    placeholder.markdown(collected + "▌")
                if event.get("done"):
                    collected_sources = event.get("sources", []) or []
                    break

        if error_msg:
            placeholder.error(error_msg)
            st.session_state.messages.append({"role": "assistant", "content": f"⚠️ {error_msg}", "sources": []})
        else:
            placeholder.markdown(collected.strip() or "_(no response)_")
            if collected_sources:
                with sources_placeholder.expander(f"📄 Sources ({len(collected_sources)})"):
                    for src in collected_sources:
                        st.markdown(f"**{src['filename']}** · _{src['category']}_")
                        st.caption(src["snippet"])
                        st.divider()
            st.session_state.messages.append(
                {"role": "assistant", "content": collected.strip(), "sources": collected_sources}
            )
