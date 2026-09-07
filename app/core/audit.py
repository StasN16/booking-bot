"""
Audit trail.

Every inbound message opens a trace. Each operation inside it records one
event holding its inputs, outputs, duration and status, in order. A trace is
therefore a complete account of one turn: what arrived, what each step was
given, what it returned, what it cost, and where it failed.

Events are written as JSON Lines so they can be read back and checked
mechanically. app/services/audit_analysis.py does that, and the tests run
those checks over traces produced by the real code paths.

Two rules the rest of the system depends on:

- Auditing never breaks the request. Every write is guarded; a failure to
  record is logged and swallowed.
- Nothing sensitive is written. Phone numbers are truncated and anything
  that looks like a credential is replaced before it reaches disk.
"""
import contextvars
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.core.timeutils import now as clinic_now

logger = logging.getLogger(__name__)

# Values under these keys are never written out in full.
SENSITIVE_KEYS = re.compile(
    r"token|secret|password|api_key|apikey|authorization|credential",
    re.IGNORECASE,
)

# Long free text is summarized rather than stored whole, so one oversized
# conversation history cannot bury the rest of the trace.
MAX_TEXT = 300
MAX_ITEMS = 20

_current_trace: contextvars.ContextVar = contextvars.ContextVar(
    "audit_trace", default=None
)


def redact(value: Any, key: str = "") -> Any:
    """Copy a value, removing anything that must not be written to disk."""
    if key and SENSITIVE_KEYS.search(key):
        return "***redacted***"

    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in list(value.items())[:MAX_ITEMS]}

    if isinstance(value, (list, tuple)):
        trimmed = [redact(v) for v in value[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            trimmed.append(f"...{len(value) - MAX_ITEMS} more")
        return trimmed

    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            return value[:MAX_TEXT] + f"...({len(value)} chars)"
        return value

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    if isinstance(value, datetime):
        return value.isoformat()

    return str(value)[:MAX_TEXT]


def mask_phone(phone: str) -> str:
    """Keep enough of a number to correlate traces, not enough to identify."""
    if not phone:
        return ""
    digits = str(phone)
    return f"***{digits[-4:]}" if len(digits) > 4 else "***"


@dataclass
class AuditEvent:
    trace_id: str
    seq: int
    operation: str
    started_at: str
    duration_ms: float
    status: str  # "ok" or "error"
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


class Trace:
    """One inbound message and everything done in response to it."""

    def __init__(self, name: str, context: dict = None):
        self.trace_id = uuid.uuid4().hex[:12]
        self.name = name
        self.context = context or {}
        self.started_at = clinic_now()
        self.events: list[AuditEvent] = []
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def add(self, event: AuditEvent):
        self.events.append(event)
        _sink.write(event)

    def summary(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "name": self.name,
            "context": self.context,
            "started_at": self.started_at.isoformat(),
            "events": len(self.events),
            "errors": sum(1 for e in self.events if e.status == "error"),
            "duration_ms": round(
                sum(e.duration_ms for e in self.events), 2
            ),
        }


class JsonlSink:
    """Appends events to a file, one JSON object per line."""

    def __init__(self, path: str | None = None):
        self.path = Path(path) if path else None

    def write(self, event: AuditEvent):
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(event.to_json() + "\n")
        except Exception as e:
            # An audit failure must never take the request down with it.
            logger.warning(f"Could not write audit event: {e}")


class MemorySink:
    """Keeps events in memory. Used by tests and by the analysis endpoint."""

    def __init__(self):
        self.events: list[AuditEvent] = []

    def write(self, event: AuditEvent):
        self.events.append(event)

    def clear(self):
        self.events.clear()


_sink: Any = JsonlSink(settings.AUDIT_LOG_PATH if settings.AUDIT_ENABLED else None)


def set_sink(sink):
    """Swap where events go. Returns the previous sink so it can be restored."""
    global _sink
    previous = _sink
    _sink = sink
    return previous


def current_trace() -> Trace | None:
    return _current_trace.get()


class trace:
    """
    Open a trace for one unit of work.

        with trace("message", phone=mask_phone(number)) as t:
            ...

    Nested operations recorded inside find this trace automatically, so they
    are stitched into one ordered story without being passed a handle.
    """

    def __init__(self, name: str, **context):
        self.name = name
        self.context = context
        self.trace: Trace | None = None
        self._token = None

    def __enter__(self) -> Trace:
        self.trace = Trace(self.name, self.context)
        self._token = _current_trace.set(self.trace)
        return self.trace

    def __exit__(self, exc_type, exc, tb):
        if exc is not None and self.trace is not None:
            # Record what escaped, so a trace never ends mid-sentence.
            record(
                "trace.unhandled_exception",
                inputs=self.trace.context,
                error=f"{exc_type.__name__}: {exc}",
                duration_ms=0.0,
            )
        if self._token is not None:
            _current_trace.reset(self._token)
        return False


def record(operation: str, inputs: dict = None, outputs: dict = None,
           error: str = None, duration_ms: float = 0.0):
    """Record one event against the current trace, if there is one."""
    active = _current_trace.get()
    if active is None:
        return
    try:
        event = AuditEvent(
            trace_id=active.trace_id,
            seq=active.next_seq(),
            operation=operation,
            started_at=clinic_now().isoformat(),
            duration_ms=round(duration_ms, 2),
            status="error" if error else "ok",
            inputs=redact(inputs or {}),
            outputs=redact(outputs or {}),
            error=error[:MAX_TEXT] if error else None,
        )
        active.add(event)
    except Exception as e:
        logger.warning(f"Could not record audit event {operation}: {e}")


class step:
    """
    Time one operation and record it, including whatever went wrong.

        with step("booking.create", inputs={...}) as s:
            result = ...
            s.outputs = {"success": result["success"]}

    An exception is recorded and re-raised, so the audit shows the failure
    without changing how the code behaves.
    """

    def __init__(self, operation: str, inputs: dict = None):
        self.operation = operation
        self.inputs = inputs or {}
        self.outputs: dict = {}
        self._started = 0.0

    def __enter__(self) -> "step":
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = (time.perf_counter() - self._started) * 1000
        record(
            self.operation,
            inputs=self.inputs,
            outputs=self.outputs,
            error=f"{exc_type.__name__}: {exc}" if exc else None,
            duration_ms=elapsed,
        )
        return False  # never swallow


def audited(operation: str,
            capture_in: Callable[..., dict] = None,
            capture_out: Callable[[Any], dict] = None):
    """
    Record an async function as one step.

    The extractors decide what is worth keeping. Without them nothing is
    captured beyond timing and status, because whole arguments are usually
    both too large and too sensitive to store.
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            inputs = {}
            if capture_in:
                try:
                    inputs = capture_in(*args, **kwargs)
                except Exception:
                    inputs = {"_capture_failed": True}

            started = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
            except Exception as e:
                record(
                    operation,
                    inputs=inputs,
                    error=f"{type(e).__name__}: {e}",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
                raise

            outputs = {}
            if capture_out:
                try:
                    outputs = capture_out(result)
                except Exception:
                    outputs = {"_capture_failed": True}

            record(
                operation,
                inputs=inputs,
                outputs=outputs,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            return result

        wrapper.__name__ = getattr(func, "__name__", operation)
        wrapper.__doc__ = getattr(func, "__doc__", None)
        wrapper.__wrapped__ = func
        return wrapper
    return decorator


def read_events(path: str | Path) -> list[AuditEvent]:
    """Load events back from a JSONL file, skipping unreadable lines."""
    path = Path(path)
    if not path.exists():
        return []

    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(AuditEvent(**json.loads(line)))
        except Exception:
            logger.warning("Skipping unreadable audit line")
    return events


def group_traces(events: list[AuditEvent]) -> dict[str, list[AuditEvent]]:
    """Gather events into traces, each ordered by sequence number."""
    traces: dict[str, list[AuditEvent]] = {}
    for event in events:
        traces.setdefault(event.trace_id, []).append(event)
    for trace_events in traces.values():
        trace_events.sort(key=lambda e: e.seq)
    return traces
