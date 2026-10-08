"""Event bus thread-safe: il motore emette, le UI consumano e ricostruiscono."""
from __future__ import annotations

import threading
from typing import Any, Callable, Iterable

from .clock import SystemClock
from .council_events import CouncilEvent, EventType
from .schemas import MessageType
from .security import redact, redact_obj


class EventBus:
    def __init__(self, run_id: str, clock: Any = None) -> None:
        self.run_id = run_id
        self._clock = clock or SystemClock()
        self._events: list[CouncilEvent] = []
        self._subs: list[Callable[[CouncilEvent], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Callable[[CouncilEvent], None]) -> None:
        self._subs.append(callback)

    def emit(
        self,
        event_type: EventType,
        *,
        agent: str | None = None,
        target_agent: str | None = None,
        message: str = "",
        message_type: MessageType | None = None,
        evidence_ids: Iterable[str] = (),
        ui_action: str | None = None,
        source: str = "system",
        data: dict[str, Any] | None = None,
    ) -> CouncilEvent:
        with self._lock:
            ev = CouncilEvent(
                event_id=f"EVT-{len(self._events) + 1:03d}",
                timestamp=self._clock.now().isoformat(timespec="milliseconds"),
                run_id=self.run_id,
                event_type=event_type,
                agent=agent,
                target_agent=target_agent,
                message=redact(message),
                message_type=message_type,
                evidence_ids=list(evidence_ids),
                ui_action=ui_action,
                source=source,
                data=redact_obj(data or {}),
            )
            self._events.append(ev)
        for cb in list(self._subs):
            cb(ev)
        return ev

    @property
    def events(self) -> list[CouncilEvent]:
        with self._lock:
            return list(self._events)
