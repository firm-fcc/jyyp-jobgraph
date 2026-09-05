"""Timing only: delegates unchanged arguments/results; never logs request bodies."""
from __future__ import annotations

import functools
import hashlib
import json
import os
from pathlib import Path
import threading
import time


class RuntimeTrace:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.started = time.perf_counter()
        self.sequence = 0
        self.lock = threading.Lock()

    def emit(self, stage, event, **fields):
        if self.path is None:
            return
        # Callers supply only timings, hashes, numeric usage, and exception type.
        allowed = {'call_id', 'elapsed_seconds', 'error_type', 'usage',
                   'request_fingerprint', 'status', 'return_code', 'count'}
        if set(fields) - allowed:
            raise ValueError('unsupported runtime diagnostic field')
        row = {'stage': stage, 'event': event, 'pid': os.getpid(),
               'offset_seconds': round(time.perf_counter() - self.started, 6), **fields}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + '\n')

    def wrap(self, stage, function, *, on_result=None, llm=False, unbound=False):
        @functools.wraps(function)
        def measured(*args, **kwargs):
            with self.lock:
                self.sequence += 1
                cid = self.sequence
            extra = {}
            if llm:
                offset = 1 if unbound else 0
                # A one-way fingerprint identifies identical retries, not their text.
                pair = [args[offset:offset + 2], kwargs]
                extra['request_fingerprint'] = hashlib.sha256(
                    json.dumps(pair, sort_keys=True, ensure_ascii=False).encode('utf-8')
                ).hexdigest()
            self.emit(stage, 'start', call_id=cid, **extra)
            began = time.perf_counter()
            try:
                result = function(*args, **kwargs)
                if on_result is not None:
                    on_result(result)
            except BaseException as exc:
                self.emit(stage, 'end', call_id=cid,
                          elapsed_seconds=round(time.perf_counter() - began, 6),
                          error_type=type(exc).__name__, status='FAIL')
                raise
            numeric_usage = {}
            if llm:
                numeric_usage = {k: v for k, v in (getattr(result, 'usage', None) or {}).items()
                                 if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'}
                                 and type(v) in (int, float)}
            self.emit(stage, 'end', call_id=cid,
                      elapsed_seconds=round(time.perf_counter() - began, 6),
                      status='PASS', usage=numeric_usage)
            return result
        return measured


def read_events(path):
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # Process termination may interrupt the final telemetry write.
            continue
    return rows
