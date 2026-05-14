"""Per-session conversation memory store with LRU eviction."""
from __future__ import annotations

import threading
from collections import OrderedDict

from langchain.memory import ConversationBufferWindowMemory


class SessionMemoryStore:
    def __init__(self, window: int = 5, max_sessions: int = 1000):
        self._memories: OrderedDict[str, ConversationBufferWindowMemory] = OrderedDict()
        self._window = window
        self._max_sessions = max_sessions
        self._lock = threading.Lock()

    def get(self, session_id: str) -> ConversationBufferWindowMemory:
        with self._lock:
            if session_id in self._memories:
                self._memories.move_to_end(session_id)
                return self._memories[session_id]

            mem = ConversationBufferWindowMemory(
                k=self._window,
                memory_key="chat_history",
                return_messages=True,
                output_key="answer",
                input_key="question",
            )
            self._memories[session_id] = mem
            if len(self._memories) > self._max_sessions:
                self._memories.popitem(last=False)
            return mem

    def clear(self, session_id: str) -> bool:
        with self._lock:
            return self._memories.pop(session_id, None) is not None

    def size(self) -> int:
        with self._lock:
            return len(self._memories)


_GLOBAL_STORE: SessionMemoryStore | None = None


def get_store() -> SessionMemoryStore:
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        _GLOBAL_STORE = SessionMemoryStore()
    return _GLOBAL_STORE
