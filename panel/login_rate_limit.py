"""登录接口滑动窗口限流（单机内存，防暴力破解）。"""

from __future__ import annotations

import os
import time
from threading import Lock

_fail_times: dict[str, list[float]] = {}
_lock = Lock()


def _window_and_max() -> tuple[float, int]:
    try:
        w = float(os.environ.get("PANEL_LOGIN_WINDOW_SEC", "900").strip())
    except ValueError:
        w = 900.0
    try:
        m = int(os.environ.get("PANEL_LOGIN_MAX_ATTEMPTS", "15").strip())
    except ValueError:
        m = 15
    w = max(60.0, min(w, 86400.0))
    m = max(3, min(m, 1000))
    return w, m


def login_failures_exceeded(client_ip: str) -> bool:
    """若 True，应拒绝登录尝试（HTTP 429）。"""
    now = time.monotonic()
    window, max_n = _window_and_max()
    with _lock:
        lst = _fail_times.get(client_ip, [])
        lst = [t for t in lst if now - t < window]
        _fail_times[client_ip] = lst
        return len(lst) >= max_n


def record_login_failure(client_ip: str) -> None:
    now = time.monotonic()
    window, _ = _window_and_max()
    with _lock:
        lst = _fail_times.get(client_ip, [])
        lst = [t for t in lst if now - t < window]
        lst.append(now)
        _fail_times[client_ip] = lst


def clear_login_failures(client_ip: str) -> None:
    with _lock:
        _fail_times.pop(client_ip, None)
