"""Where analysis code records provenance: dataset records and provider request logs.

Analysis modules never write to a database directly. They call `current()`, which is
either the prototype app's database recorder (the default) or a recorder supplied by
the caller, e.g. the engine's in-memory recorder that returns everything in the result.
The recorder is held in a context variable, so concurrent engine calls in different
threads or tasks never mix their records.
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Recorder(Protocol):
    def dataset(self, record: dict[str, Any]) -> str: ...
    def request_log(self, record: dict[str, Any]) -> None: ...


class DatabaseRecorder:
    """Default: the prototype app's SQLite tables `datasets` and `provider_logs`."""

    def dataset(self, record: dict[str, Any]) -> str:
        from . import db

        db.insert("datasets", record)
        return record["id"]

    def request_log(self, record: dict[str, Any]) -> None:
        from . import db

        db.insert("provider_logs", record)


class MemoryRecorder:
    """Collects records in memory; used by the engine so a call needs no database."""

    def __init__(self) -> None:
        self.datasets: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []

    def dataset(self, record: dict[str, Any]) -> str:
        self.datasets[record["id"]] = record
        return record["id"]

    def request_log(self, record: dict[str, Any]) -> None:
        self.requests.append(record)


_current: contextvars.ContextVar[Recorder | None] = contextvars.ContextVar("urbanova_recorder", default=None)
_default = DatabaseRecorder()


def current() -> Recorder:
    return _current.get() or _default


@contextmanager
def use(recorder: Recorder) -> Iterator[Recorder]:
    token = _current.set(recorder)
    try:
        yield recorder
    finally:
        _current.reset(token)
