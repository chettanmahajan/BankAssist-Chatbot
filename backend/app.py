"""FastAPI backend for the Banking Support Assistant."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .cache import get_cache
from .memory import get_store
from .rag_pipeline import (
    FALLBACK_ANSWER,
    build_chain,
    count_documents,
    format_sources,
    load_vectorstore,
    low_score_fallback,
)


load_dotenv()


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("banking_chatbot")


# ----- models ---------------------------------------------------------------


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=1000)
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class SourceRef(BaseModel):
    filename: str
    category: str
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceRef]
    session_id: str
    cached: bool
    latency_ms: int


class HealthResponse(BaseModel):
    status: str
    version: str
    vectorstore_loaded: bool
    docs_indexed: int


# ----- lifespan: load vectorstore once --------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("loading vector store ...")
    try:
        vs = load_vectorstore()
        app.state.vectorstore = vs
        app.state.docs_indexed = count_documents(vs)
        app.state.vectorstore_loaded = True
        logger.info("vector store loaded; %d indexed chunks", app.state.docs_indexed)
    except Exception as exc:
        logger.exception("failed to load vector store: %s", exc)
        app.state.vectorstore = None
        app.state.docs_indexed = 0
        app.state.vectorstore_loaded = False

    app.state.memory_store = get_store()
    app.state.cache = get_cache()
    yield


app = FastAPI(
    title="Banking Support Assistant API",
    version="1.0.0",
    description="RAG-powered banking support chatbot.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- routes ---------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    state = request.app.state
    return HealthResponse(
        status="ok" if getattr(state, "vectorstore_loaded", False) else "degraded",
        version="1.0.0",
        vectorstore_loaded=getattr(state, "vectorstore_loaded", False),
        docs_indexed=getattr(state, "docs_indexed", 0),
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    state = request.app.state
    if not getattr(state, "vectorstore_loaded", False):
        raise HTTPException(status_code=503, detail="Vector store not loaded. Run build_index.py.")

    start = time.perf_counter()
    query = req.query.strip()

    cache = state.cache
    cache_key = cache.key_for(query, req.session_id)
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.info("cache hit session=%s latency_ms=%d", req.session_id[:8], latency_ms)
        return ChatResponse(
            answer=cached_payload["answer"],
            sources=[SourceRef(**s) for s in cached_payload["sources"]],
            session_id=req.session_id,
            cached=True,
            latency_ms=latency_ms,
        )

    vs = state.vectorstore
    if low_score_fallback(vs, query):
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.info("low-score fallback session=%s latency_ms=%d", req.session_id[:8], latency_ms)
        return ChatResponse(
            answer=FALLBACK_ANSWER,
            sources=[],
            session_id=req.session_id,
            cached=False,
            latency_ms=latency_ms,
        )

    memory = state.memory_store.get(req.session_id)
    try:
        chain = build_chain(vs, memory, streaming=False)
        result = await asyncio.to_thread(chain.invoke, {"question": query})
    except Exception as exc:
        logger.exception("chain invocation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal error while generating response.")

    answer = result.get("answer", "").strip() or FALLBACK_ANSWER
    sources = format_sources(result.get("source_documents", []))

    cache.set(cache_key, {"answer": answer, "sources": sources})

    latency_ms = int((time.perf_counter() - start) * 1000)
    logger.info("chat ok session=%s latency_ms=%d", req.session_id[:8], latency_ms)
    return ChatResponse(
        answer=answer,
        sources=[SourceRef(**s) for s in sources],
        session_id=req.session_id,
        cached=False,
        latency_ms=latency_ms,
    )


async def _stream_chain(state, query: str, session_id: str) -> AsyncIterator[str]:
    """Yield SSE-formatted events: token streams + final sources event."""
    vs = state.vectorstore
    memory = state.memory_store.get(session_id)

    if low_score_fallback(vs, query):
        yield f"data: {json.dumps({'token': FALLBACK_ANSWER})}\n\n"
        yield f"data: {json.dumps({'sources': [], 'done': True})}\n\n"
        return

    try:
        chain = build_chain(vs, memory, streaming=True)
        collected_tokens: list[str] = []
        collected_sources: list[dict] = []

        async for chunk in chain.astream({"question": query}):
            if "answer" in chunk:
                token = chunk["answer"]
                if token:
                    collected_tokens.append(token)
                    yield f"data: {json.dumps({'token': token})}\n\n"
            if "source_documents" in chunk:
                collected_sources = format_sources(chunk["source_documents"])

        full_answer = "".join(collected_tokens).strip() or FALLBACK_ANSWER

        cache = state.cache
        cache.set(cache.key_for(query, session_id), {"answer": full_answer, "sources": collected_sources})

        yield f"data: {json.dumps({'sources': collected_sources, 'done': True})}\n\n"
    except Exception as exc:
        logger.exception("streaming chain failed: %s", exc)
        yield f"data: {json.dumps({'error': 'streaming failed', 'done': True})}\n\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    state = request.app.state
    if not getattr(state, "vectorstore_loaded", False):
        raise HTTPException(status_code=503, detail="Vector store not loaded.")

    query = req.query.strip()
    logger.info("stream start session=%s", req.session_id[:8])

    return StreamingResponse(
        _stream_chain(state, query, req.session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
