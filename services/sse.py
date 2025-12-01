# services/sse.py
from __future__ import annotations
import json, time
from queue import Queue, Empty
from typing import Dict, Any, Iterator, List, Tuple, Iterable, Union

InitialItem = Tuple[str, Dict[str, Any]]  # (event_name, payload)
InitialArg = Union[
    None,
    Dict[str, Any],            # backwards-compat: treated as ("health", data)
    InitialItem,               # single typed event
    Iterable[InitialItem],     # multiple typed events
]

class SseHub:
    """Fan-out hub for SSE. Each client gets a Queue of messages."""
    def __init__(self, keepalive_s: float = 25.0, max_q: int = 32):
        self.keepalive_s = keepalive_s
        self.max_q = max_q
        self._clients: List[Queue] = []

    def register(self) -> Queue:
        q = Queue(maxsize=self.max_q)
        self._clients.append(q)
        return q

    def unregister(self, q: Queue):
        try:
            self._clients.remove(q)
        except ValueError:
            pass

    def publish(self, event: str, data: Dict[str, Any]):
        """Broadcast a typed SSE event to all clients."""
        payload = f"event: {event}\ndata: {json.dumps(data, separators=(',',':'))}\n\n"
        dead: List[Queue] = []
        for q in list(self._clients):
            try:
                q.put_nowait(payload)   # drop if full (last-wins)
            except Exception:
                dead.append(q)
        for q in dead:
            self.unregister(q)

    def _write_initial(self, initial: InitialArg) -> Iterator[str]:
        if initial is None:
            return
        # Back-compat: bare dict means a "health" event
        if isinstance(initial, dict):
            yield f"event: health\ndata: {json.dumps(initial, separators=(',',':'))}\n\n"
            return
        # Single ("event", {data})
        if isinstance(initial, tuple) and len(initial) == 2:
            ev, data = initial
            yield f"event: {ev}\ndata: {json.dumps(data, separators=(',',':'))}\n\n"
            return
        # Iterable of typed items
        try:
            for ev, data in initial:  # type: ignore
                yield f"event: {ev}\ndata: {json.dumps(data, separators=(',',':'))}\n\n"
        except Exception:
            # swallow bad initial input to avoid breaking the stream
            return

    def stream(self, initial: InitialArg = None) -> Iterator[str]:
        q = self.register()
        try:
            # Optional one-time seed events
            yield from self._write_initial(initial)

            # Keepalive comment every keepalive_s (avoids proxies closing idle streams)
            last = time.time()
            while True:
                try:
                    msg = q.get(timeout=self.keepalive_s)
                    yield msg
                except Empty:
                    yield f": keepalive {int(time.time())}\n\n"  # comment
                finally:
                    if time.time() - last >= self.keepalive_s:
                        last = time.time()
        finally:
            self.unregister(q)
