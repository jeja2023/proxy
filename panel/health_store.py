from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class NodeHealthStore:
    """Small JSON-backed health history for outbound nodes."""

    def __init__(self, path: Path, *, max_history: int = 20) -> None:
        self.path = path
        self.max_history = max(5, max_history)
        self._lock = Lock()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            data = self._read()
            return {
                name: self._summarize(events)
                for name, events in data.items()
                if isinstance(name, str) and isinstance(events, list)
            }

    def record(self, name: str, delay_ms: int | None, error: str | None = None) -> dict[str, Any]:
        node = (name or "").strip()
        if not node:
            return self._summarize([])
        event = {
            "ts": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "delay_ms": delay_ms,
            "ok": error is None and delay_ms is not None,
            "error": error or "",
        }
        with self._lock:
            data = self._read()
            events = data.get(node)
            if not isinstance(events, list):
                events = []
            events.append(event)
            data[node] = events[-self.max_history :]
            self._write(data)
            return self._summarize(data[node])

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _write(self, data: dict[str, list[dict[str, Any]]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            return

    def _summarize(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        recent = [e for e in events if isinstance(e, dict)][-self.max_history :]
        if not recent:
            return {
                "score": None,
                "tier": "未知",
                "success_rate": None,
                "avg_delay_ms": None,
                "last_error": "",
                "last_seen": "",
            }
        ok_events = [e for e in recent if e.get("ok") and isinstance(e.get("delay_ms"), int)]
        success_rate = len(ok_events) / len(recent)
        avg_delay = int(sum(int(e["delay_ms"]) for e in ok_events) / len(ok_events)) if ok_events else None
        delay_penalty = min(45, (avg_delay or 2000) / 40)
        score = int(max(0, min(100, 100 - delay_penalty - (1 - success_rate) * 55)))
        tier = "优秀" if score >= 85 else "良好" if score >= 70 else "波动" if score >= 45 else "较差"
        last = recent[-1]
        return {
            "score": score,
            "tier": tier,
            "success_rate": round(success_rate, 2),
            "avg_delay_ms": avg_delay,
            "last_error": str(last.get("error") or ""),
            "last_seen": str(last.get("ts") or ""),
        }
