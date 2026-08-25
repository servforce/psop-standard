from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Iterator


def summarize(value: object, *, max_chars: int = 1000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


@contextmanager
def logged_call(
    session: object,
    *,
    interface_type: str,
    tool_or_endpoint: str,
    caller: str | None = None,
    request: object | None = None,
    video_id: str | None = None,
    standard_id: str | None = None,
) -> Iterator[str]:
    del session, interface_type, tool_or_endpoint, caller, request, video_id, standard_id
    call_id = uuid.uuid4().hex
    yield call_id


@contextmanager
def logged_call_with_session(
    *,
    interface_type: str,
    tool_or_endpoint: str,
    caller: str | None = None,
    request: object | None = None,
    video_id: str | None = None,
    standard_id: str | None = None,
) -> Iterator[tuple[object, str]]:
    del interface_type, tool_or_endpoint, caller, request, video_id, standard_id
    yield object(), uuid.uuid4().hex


def finish_call(session: object, call_id: str, response: object) -> None:
    del session, call_id, response
