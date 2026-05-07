"""轻量管理面板：服务端转发 sing-box Clash API，浏览器不直接接触 9020 与 Clash secret。"""

from __future__ import annotations

import asyncio
import base64
import hmac
import io
import ipaddress
import json
import logging
import os
import secrets
import socket
import sys
import time
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from urllib.parse import quote
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from login_rate_limit import clear_login_failures, login_failures_exceeded, record_login_failure
from health_store import NodeHealthStore
from middleware import RequestIdMiddleware, SecurityHeadersMiddleware

STATIC_DIR = Path(__file__).resolve().parent / "static"
REPO_ROOT = Path(os.environ.get("PROXY_REPO_ROOT", str(Path(__file__).resolve().parent.parent))).resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.env import load_repo_dotenv as _load_dotenv_fn
from core.build_config import NodeBuildError, build_singbox_config, dedupe_urls, write_singbox_config, parse_urls_text, ROUTE_MODES
from core.vault_store import decrypt_vault_file, encrypt_vault_file

_load_dotenv_fn(REPO_ROOT)

DATA_DIR = Path(os.environ.get("PROXY_DATA_DIR", str(REPO_ROOT / "data"))).resolve()
_LEGACY_VAULT_FILE = DATA_DIR / "vault.enc"
VAULTS_DIR = DATA_DIR / "vaults"
VAULTS_INDEX = VAULTS_DIR / "index.json"
CONFIG_FILE = Path(os.environ.get("PROXY_CONFIG_PATH", str(REPO_ROOT / "config.json"))).resolve()

SELECTOR_TAG = os.environ.get("PANEL_SELECTOR_TAG", "代理选择").strip() or "代理选择"
DEFAULT_DELAY_TEST_URL = os.environ.get(
    "PANEL_DELAY_TEST_URL",
    "https://www.gstatic.com/generate_204",
).strip()
_CSRF_SESSION_KEY = "csrf_token"
_STARTED_AT = time.time()
_refresh_state: dict[str, object] = {
    "enabled": False,
    "interval_minutes": 0,
    "last_run_at": "",
    "last_refreshed": 0,
    "last_failed": [],
}

CLASH_BASE = os.environ.get("CLASH_API_URL", "http://127.0.0.1:9020").rstrip("/")
CLASH_SECRET = os.environ.get("CLASH_API_SECRET", "").strip()
PANEL_USER = os.environ.get("PANEL_ADMIN_USER", "").strip()
PANEL_PASSWORD = os.environ.get("PANEL_ADMIN_PASSWORD", "").strip()
PANEL_AUTH_CONFIGURED = bool(PANEL_USER and PANEL_PASSWORD)
_raw_session_secret = os.environ.get("PANEL_SESSION_SECRET", "").strip()
_secret_file = DATA_DIR / ".session_secret"

if not _raw_session_secret:
    if _secret_file.is_file():
        try:
            SESSION_SECRET = _secret_file.read_text(encoding="utf-8").strip()
        except OSError:
            import secrets as _secrets_mod
            SESSION_SECRET = _secrets_mod.token_urlsafe(32)
    else:
        import secrets as _secrets_mod
        SESSION_SECRET = _secrets_mod.token_urlsafe(32)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _secret_file.write_text(SESSION_SECRET, encoding="utf-8")
        except OSError:
            pass
    if not os.environ.get("PANEL_SESSION_SECRET"):
        print("[网枢] 注意: PANEL_SESSION_SECRET 未配置，已从文件加载或自动生成持久化密钥", flush=True)
else:
    SESSION_SECRET = _raw_session_secret
SESSION_SECURE = os.environ.get("PANEL_SESSION_SECURE", "").strip().lower() in ("1", "true", "yes")
PANEL_DEBUG = os.environ.get("PANEL_DEBUG", "").strip().lower() in ("1", "true", "yes")
try:
    _audit_max_raw = int(os.environ.get("PANEL_AUDIT_LOG_MAX", "500").strip())
except ValueError:
    _audit_max_raw = 500
PANEL_AUDIT_LOG_MAX = max(50, min(5000, _audit_max_raw))

AUDIT_LOG_FILE = Path(
    os.environ.get("PANEL_AUDIT_LOG_PATH", str(DATA_DIR / "panel_audit.jsonl"))
).resolve()
HEALTH_STORE = NodeHealthStore(DATA_DIR / "node_health.json")
try:
    _audit_read_chunk = max(65536, int(os.environ.get("PANEL_AUDIT_READ_CHUNK", "2097152").strip()))
except ValueError:
    _audit_read_chunk = 2097152

_audit_lines: deque[dict[str, str]] = deque(maxlen=PANEL_AUDIT_LOG_MAX)
_audit_lock = Lock()

logger = logging.getLogger("panel")

_SUBSCRIPTION_MAX_BYTES = max(
    256 * 1024,
    min(20 * 1024 * 1024, int(os.environ.get("PANEL_SUBSCRIPTION_MAX_BYTES", "2097152").strip() or "2097152")),
)
_SUBSCRIPTION_TIMEOUT = float(os.environ.get("PANEL_SUBSCRIPTION_TIMEOUT", "15").strip() or "15")

_VAULT_NAME_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _vault_name_norm(name: str) -> str:
    n = (name or "").strip()
    if not n:
        raise HTTPException(status_code=400, detail="节点库名称不能为空")
    if len(n) > 64:
        raise HTTPException(status_code=400, detail="节点库名称过长（限制 64 字符）")
    # 允许中文、字母、数字、空格、-、_ 等，但排除系统非法文件名字符
    invalid_chars = set('<>:"/\\|?*')
    if any(ch in invalid_chars or ord(ch) < 32 for ch in n):
        raise HTTPException(status_code=400, detail="节点库名称包含非法字符（不支持 <>:\"/\\|?* 等）")
    return n


def _vault_path(name: str) -> Path:
    n = _vault_name_norm(name)
    return (VAULTS_DIR / f"{n}.enc").resolve()


def _vaults_bootstrap_and_migrate() -> None:
    """初始化 vaults 目录；若存在旧版 data/vault.enc 且新结构为空，则迁移为 default.enc。"""
    try:
        VAULTS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    # 迁移 legacy
    try:
        legacy = _LEGACY_VAULT_FILE
        default_path = _vault_path("default")
        if legacy.is_file() and not default_path.is_file():
            try:
                default_path.write_bytes(legacy.read_bytes())
                legacy.unlink()
            except OSError:
                # 保底：不删除 legacy，避免数据丢失
                pass
    except HTTPException:
        pass


def _read_vault_index() -> dict:
    _vaults_bootstrap_and_migrate()
    if VAULTS_INDEX.is_file():
        try:
            data = json.loads(VAULTS_INDEX.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("vaults"), list):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"version": 1, "vaults": [], "current": ""}


def _write_vault_index(data: dict) -> None:
    _vaults_bootstrap_and_migrate()
    VAULTS_INDEX.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _list_vaults() -> list[dict]:
    idx = _read_vault_index()
    out: list[dict] = []
    for v in idx.get("vaults") or []:
        if not isinstance(v, dict):
            continue
        name = v.get("name")
        if not isinstance(name, str):
            continue
        enabled = bool(v.get("enabled", True))
        node_count = int(v.get("node_count", 0))
        path = _vault_path(name)
        out.append({
            "name": name,
            "enabled": enabled,
            "exists": path.is_file(),
            "node_count": node_count,
            "unique_count": int(v.get("unique_count", node_count)),
            "duplicate_count": int(v.get("duplicate_count", 0)),
            "source_url": (v.get("source_url") or "").strip(),
            "source_kind": (v.get("source_kind") or "").strip(),
            "last_import_at": (v.get("last_import_at") or "").strip(),
        })
    return out


def _enabled_vault_names() -> list[str]:
    return [v["name"] for v in _list_vaults() if v.get("enabled")]


def _update_vault_record(name: str, **changes) -> None:
    idx = _read_vault_index()
    vaults = idx.get("vaults") or []
    changed = False
    for v in vaults:
        if isinstance(v, dict) and v.get("name") == name:
            v.update(changes)
            changed = True
    if changed:
        idx["vaults"] = vaults
        _write_vault_index(idx)


def _update_vault_node_count(name: str, count: int) -> None:
    _update_vault_record(name, node_count=count)


def _annotate_url_with_vault(url: str, vault_name: str) -> str:
    """在节点名 fragment 前加 vault 前缀，便于区分来源库。"""
    u = (url or "").strip()
    if not u:
        return u
    try:
        p = urlparse(u)
    except Exception:
        return u
    frag = p.fragment or ""
    base = frag.strip() or "node"
    new_frag = f"{vault_name} · {base}"
    return p._replace(fragment=new_frag).geturl()


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _store_vault_import_metadata(
    vault_name: str,
    *,
    source_url: str = "",
    source_kind: str = "",
    unique_count: int,
    duplicate_count: int,
) -> None:
    changes = {
        "node_count": unique_count,
        "unique_count": unique_count,
        "duplicate_count": duplicate_count,
        "last_import_at": _now_iso(),
    }
    if source_url:
        changes["source_url"] = source_url
    else:
        changes["source_url"] = ""
    if source_kind:
        changes["source_kind"] = source_kind
    else:
        changes["source_kind"] = ""
    _update_vault_record(vault_name, **changes)


def _import_vault_urls(
    vault_name: str,
    vault_password: str,
    urls: list[str],
    *,
    source_url: str = "",
    source_kind: str = "",
    clash_secret: str | None = None,
) -> tuple[int, int, int]:
    unique_urls, duplicate_count = dedupe_urls(urls)
    if not unique_urls:
        raise HTTPException(status_code=400, detail="未解析到任何有效节点")
    vault_path = _vault_path(vault_name)
    try:
        encrypt_vault_file(unique_urls, vault_password, vault_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入加密库失败: {e}") from e
    total = _rebuild_config_from_vaults(vault_password, clash_secret)
    _store_vault_import_metadata(
        vault_name,
        source_url=source_url,
        source_kind=source_kind,
        unique_count=len(unique_urls),
        duplicate_count=duplicate_count,
    )
    return len(unique_urls), duplicate_count, total


def _rebuild_config_from_vaults(vault_password: str, clash_secret: str | None = None, route_mode: str = "bypass_cn") -> int:
    """解密所有启用 vault，合并生成 config.json；返回合并后的节点数。"""

    names = _enabled_vault_names()
    all_urls: list[str] = []
    skipped: list[str] = []
    for name in names:
        path = _vault_path(name)
        if not path.is_file():
            continue
        try:
            urls = decrypt_vault_file(path, vault_password)
        except Exception:
            skipped.append(name)
            continue
        all_urls.extend([_annotate_url_with_vault(u, name) for u in urls])
    if skipped:
        logger.warning("以下节点库因密码不匹配已跳过: %s", ", ".join(skipped))
    if not all_urls:
        raise HTTPException(status_code=400, detail="未找到任何可用节点（所有库为空或未启用）")

    old_secret = os.environ.get("CLASH_API_SECRET")
    if clash_secret and clash_secret.strip():
        os.environ["CLASH_API_SECRET"] = clash_secret.strip()
    try:
        cfg = build_singbox_config(all_urls, route_mode=route_mode)
    except (NodeBuildError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        if clash_secret and clash_secret.strip():
            if old_secret is not None:
                os.environ["CLASH_API_SECRET"] = old_secret
            else:
                os.environ.pop("CLASH_API_SECRET", None)

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        write_singbox_config(cfg, CONFIG_FILE)
        marker = DATA_DIR / ".config_revision"
        marker.write_text(str(CONFIG_FILE.stat().st_mtime_ns), encoding="utf-8")
        
        # 尝试通过 Clash API 通知内核热重载
        import httpx
        url = f"{CLASH_BASE}/configs"
        headers = {"Authorization": f"Bearer {os.environ.get('CLASH_API_SECRET', '').strip()}"}
        # {"force": True} 触发重载
        with httpx.Client() as client:
            try:
                client.put(url, headers=headers, json={"force": True}, timeout=3.0)
            except Exception:
                pass
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"写入 config.json 失败: {e}") from e
    return len(all_urls)


def _read_audit_jsonl_tail(path: Path, max_lines: int) -> list[dict[str, str]]:
    """读取 JSONL 末尾若干行（大文件只读尾部一段字节）。"""
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    out: list[dict[str, str]] = []
    try:
        with path.open("rb") as f:
            if size <= _audit_read_chunk:
                f.seek(0)
                blob = f.read().decode("utf-8", errors="replace")
            else:
                f.seek(size - _audit_read_chunk)
                f.readline()
                blob = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = [ln.strip() for ln in blob.splitlines() if ln.strip()]
    for ln in lines[-max_lines:]:
        try:
            row = json.loads(ln)
            if not isinstance(row, dict) or "t" not in row:
                continue
            # 兼容旧版：{"t": "...", "msg": "..."}
            # 新版：{"t": "...", "ip": "...", "user": "...", "op": "...", "msg": "..."}
            norm = {"t": str(row.get("t", ""))}
            for k in ("ip", "user", "op", "msg"):
                if k in row and row.get(k) is not None:
                    norm[k] = str(row.get(k))
            if "msg" not in norm and "msg" in row:
                norm["msg"] = str(row.get("msg") or "")
            if "msg" not in norm and "detail" in row:
                norm["msg"] = str(row.get("detail") or "")
            if "msg" not in norm:
                # 至少保证有 msg，避免前端空白
                norm["msg"] = ""
            out.append(norm)
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
    return out


def _client_ip(request: Request | None) -> str:
    if not request:
        return "-"
    # 优先从 Cloudflare 特有头或通用转发头获取真实 IP
    for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        val = request.headers.get(header)
        if val:
            # x-forwarded-for 可能包含多个 IP，取第一个最真实的
            return val.split(",")[0].strip()
    if not request.client:
        return "-"
    return request.client.host or "-"


def _session_user(request: Request | None) -> str:
    if not request:
        return "-"
    u = request.session.get("panel_user") if hasattr(request, "session") else None
    return str(u).strip() if u else "-"


def panel_audit(message: str, *, request: Request | None = None, op: str | None = None) -> None:
    """审计日志：追加到 JSONL 文件（持久化）；写盘失败时仅保留在内存队列。不记录密码或节点 URL。"""
    line: dict[str, str] = {
        "t": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "msg": message,
        "ip": _client_ip(request),
        "user": _session_user(request),
    }
    if op:
        line["op"] = op
    
    _audit_lines.append(line)
    while len(_audit_lines) > PANEL_AUDIT_LOG_MAX:
        try:
            _audit_lines.popleft()
        except IndexError:
            break
            
    payload = json.dumps(line, ensure_ascii=False) + "\n"
    
    def _do_write():
        with _audit_lock:
            try:
                AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                with AUDIT_LOG_FILE.open("a", encoding="utf-8") as af:
                    af.write(payload)
                    af.flush()
            except OSError:
                pass

    # 在后台线程中执行写盘，避免阻塞主循环
    asyncio.create_task(asyncio.to_thread(_do_write))


def _is_private_or_loopback_ip(ip: ipaddress._BaseAddress) -> bool:  # type: ignore[attr-defined]
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_public_http_url(url: str, *, label: str) -> str:
    u = (url or "").strip()
    if not u:
        raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if len(u) > 2048:
        raise HTTPException(status_code=400, detail=f"{label}过长")
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail=f"仅支持 http / https {label}")
    host = (parsed.hostname or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail=f"{label}缺少 host")
    if host.lower() in ("localhost",):
        raise HTTPException(status_code=400, detail=f"不允许使用 localhost {label}")
    # SSRF 基础防护：若能解析到内网/回环 IP，则拒绝
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        addrs = {info[4][0] for info in infos if info and info[4]}
        for a in addrs:
            try:
                ip = ipaddress.ip_address(a)
            except ValueError:
                continue
            if _is_private_or_loopback_ip(ip):
                raise HTTPException(status_code=400, detail=f"{label}不允许指向内网地址")
    except HTTPException:
        raise
    except Exception:
        # DNS 解析异常：交由 httpx 处理，避免误伤；但仍会有超时限制
        pass
    return u


def _validate_subscription_url(url: str) -> str:
    return _validate_public_http_url(url, label="订阅链接")


def _validate_delay_test_url(url: str) -> str:
    return _validate_public_http_url(url, label="测速地址")


def _decode_subscription_payload(text: str) -> str:
    """兼容常见 Base64 订阅：若解码后包含 '://' 则认为有效，否则回退原文。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    # 纯文本订阅一般已是多行 "scheme://"
    if "://" in raw:
        return raw
    # Base64（可能包含换行）
    b64 = "".join(raw.split())
    try:
        pad = "=" * ((4 - len(b64) % 4) % 4)
        decoded = base64.b64decode(b64 + pad, validate=False).decode("utf-8", errors="replace")
        if "://" in decoded:
            return decoded
    except Exception:
        pass
    return raw


async def _fetch_subscription_content(url: str) -> bytes:
    """Fetch a subscription while validating every redirect target."""
    current = _validate_subscription_url(url)
    headers = {"User-Agent": "ProxyBridgePanel/1.0"}
    async with httpx.AsyncClient(timeout=_SUBSCRIPTION_TIMEOUT, follow_redirects=False) as client:
        for _ in range(6):
            try:
                r = await client.get(current, headers=headers)
            except httpx.RequestError as e:
                raise HTTPException(status_code=502, detail=f"拉取订阅失败: {e}") from e
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location", "").strip()
                if not loc:
                    raise HTTPException(status_code=502, detail="订阅服务器返回重定向但缺少 Location")
                current = _validate_subscription_url(str(r.url.join(loc)))
                continue
            if not r.is_success:
                raise HTTPException(status_code=502, detail=f"订阅服务器返回 HTTP {r.status_code}")
            content = r.content or b""
            if len(content) > _SUBSCRIPTION_MAX_BYTES:
                raise HTTPException(status_code=413, detail="订阅内容过大，请拆分或调小订阅范围")
            return content
    raise HTTPException(status_code=502, detail="订阅重定向次数过多")


async def _refresh_subscription_vaults_once(vault_password: str, clash_secret: str | None = None) -> dict:
    refreshed = 0
    failed: list[str] = []
    for v in _list_vaults():
        if v.get("source_kind") != "subscription" or not v.get("source_url"):
            continue
        name = str(v["name"])
        try:
            content = await _fetch_subscription_content(str(v["source_url"]))
            decoded_text = _decode_subscription_payload(content.decode("utf-8", errors="replace"))
            urls = parse_urls_text(decoded_text)
            _import_vault_urls(
                name,
                vault_password,
                urls,
                source_url=str(v["source_url"]),
                source_kind="subscription",
                clash_secret=clash_secret,
            )
            refreshed += 1
        except Exception as e:
            logger.warning("刷新订阅节点库失败 %s: %s", name, e)
            failed.append(name)
    return {"refreshed": refreshed, "failed": failed}


async def _subscription_refresh_loop() -> None:
    try:
        interval_min = float(os.environ.get("PANEL_SUB_REFRESH_INTERVAL_MIN", "0").strip() or "0")
    except ValueError:
        interval_min = 0
    password = os.environ.get("VAULT_PASSWORD", "").strip()
    if interval_min <= 0 or not password:
        _refresh_state.update({"enabled": False, "interval_minutes": 0})
        return
    interval = max(300.0, interval_min * 60)
    _refresh_state.update({"enabled": True, "interval_minutes": int(interval // 60)})
    panel_audit(f"订阅后台刷新已启用，间隔 {int(interval // 60)} 分钟")
    while True:
        await asyncio.sleep(interval)
        result = await _refresh_subscription_vaults_once(password, os.environ.get("CLASH_API_SECRET", "").strip() or None)
        _refresh_state.update({
            "last_run_at": _now_iso(),
            "last_refreshed": int(result["refreshed"]),
            "last_failed": list(result["failed"]),
        })
        panel_audit(f"订阅后台刷新完成：成功 {result['refreshed']} 个，失败 {len(result['failed'])} 个", op="后台刷新")


@asynccontextmanager
async def _panel_lifespan(application: FastAPI):
    level = logging.DEBUG if PANEL_DEBUG else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _clash_timeout = float(os.environ.get("PANEL_CLASH_TIMEOUT", "15"))
    application.state.http_client = httpx.AsyncClient(timeout=_clash_timeout)
    application.state.sub_refresh_task = asyncio.create_task(_subscription_refresh_loop())
    panel_audit("面板服务已启动")
    try:
        yield
    finally:
        task = getattr(application.state, "sub_refresh_task", None)
        if task:
            task.cancel()
        await application.state.http_client.aclose()


app = FastAPI(title="Proxy Bridge Panel", docs_url=None, redoc_url=None, lifespan=_panel_lifespan)
# 中间件执行顺序（Starlette 按注册逆序执行）：
# 1. RequestIdMiddleware → 2. SecurityHeadersMiddleware → 3. SessionMiddleware
# CORS 如启用，在最后注册，最先执行
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=86400 * 30,
    same_site="lax",
    https_only=SESSION_SECURE,
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)

_cors = os.environ.get("PANEL_CORS_ORIGINS", "").strip()
if _cors:
    from fastapi.middleware.cors import CORSMiddleware

    _origins = [o.strip() for o in _cors.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )


@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def silence_chrome_devtools():
    """静默处理 Chrome DevTools 的特定请求，减少日志中的 404 噪音。"""
    return JSONResponse(content={})


@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return await http_exception_handler(request, exc)
    if isinstance(exc, RequestValidationError):
        return await request_validation_exception_handler(request, exc)
    rid = getattr(request.state, "request_id", None)
    logger.exception("未处理异常 request_id=%s path=%s", rid, request.url.path)
    if PANEL_DEBUG:
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "request_id": rid},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "内部服务器错误", "request_id": rid},
    )


def clash_headers() -> dict[str, str]:
    if not CLASH_SECRET:
        return {}
    return {"Authorization": f"Bearer {CLASH_SECRET}"}


def _safe_str_eq(a: str, b: str) -> bool:
    """长度不同时返回 False；否则常量时间比较 UTF-8 字节。"""
    x = a.encode("utf-8")
    y = b.encode("utf-8")
    if len(x) != len(y):
        return False
    return hmac.compare_digest(x, y)


async def clash_request(method: str, path: str, **kwargs) -> httpx.Response:
    url = f"{CLASH_BASE}{path}"
    return await app.state.http_client.request(method, url, headers=clash_headers(), **kwargs)


def login_required(request: Request) -> None:
    if not PANEL_AUTH_CONFIGURED:
        raise HTTPException(
            status_code=503,
            detail="面板未配置登录：请在环境变量中同时设置 PANEL_ADMIN_USER 与 PANEL_ADMIN_PASSWORD",
        )
    if not request.session.get("panel_ok"):
        raise HTTPException(status_code=401, detail="未登录")


def get_csrf_token(request: Request) -> str:
    token = request.session.get(_CSRF_SESSION_KEY)
    if not isinstance(token, str) or len(token) < 24:
        token = secrets.token_urlsafe(32)
        request.session[_CSRF_SESSION_KEY] = token
    return token


def verify_csrf(request: Request) -> None:
    expected = request.session.get(_CSRF_SESSION_KEY)
    provided = request.headers.get("X-CSRF-Token", "")
    if not isinstance(expected, str) or not _safe_str_eq(provided, expected):
        raise HTTPException(status_code=403, detail="CSRF 校验失败，请刷新页面后重试")


import hashlib

def get_subscription_token() -> str:
    """基于管理员密码与会话安全密钥派生出高度安全的订阅 Token（前 16 位），单向不可逆。"""
    src = f"{PANEL_PASSWORD}_{SESSION_SECRET}"
    return hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]


def verify_export_access(request: Request, token: str | None = None) -> None:
    """双重安全验证：1. 优先允许已登录的网页 Session。2. 否则校验 query 参数中的加密订阅凭证。"""
    if hasattr(request, "session") and request.session.get("panel_ok"):
        return
    expected = get_subscription_token()
    if not expected:
        raise HTTPException(status_code=503, detail="系统未初始化安全密钥")
    if not token or not _safe_str_eq(token, expected):
        raise HTTPException(status_code=401, detail="订阅密钥错误或凭证已过期")


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=512)


class RebuildBody(BaseModel):
    vault_password: str = Field(..., min_length=1, max_length=512)
    route_mode: str = Field("bypass_cn", pattern="^(global|rule|bypass_cn|direct)$")
    clash_secret: str | None = None


@app.get("/api/live")
async def api_live():
    """存活探针：无需认证，供负载均衡 / 编排健康检查。"""
    return {"status": "ok", "service": "proxy-bridge-panel"}


@app.post("/api/login")
async def api_login(body: LoginBody, request: Request):
    if not PANEL_AUTH_CONFIGURED:
        raise HTTPException(
            status_code=503,
            detail="未配置登录账号：请同时设置 PANEL_ADMIN_USER 与 PANEL_ADMIN_PASSWORD 后重启面板",
        )
    ip = request.client.host if request.client else "-"
    if login_failures_exceeded(ip):
        panel_audit("登录限流触发", request=request, op="登录限流")
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    u = (body.username or "").strip()
    p = body.password or ""
    if not _safe_str_eq(u, PANEL_USER) or not _safe_str_eq(p, PANEL_PASSWORD):
        record_login_failure(ip)
        panel_audit("登录失败（用户名或密码错误）", request=request, op="登录失败")
        raise HTTPException(status_code=403, detail="用户名或密码错误")
    clear_login_failures(ip)
    request.session["panel_ok"] = True
    request.session["panel_user"] = u
    panel_audit("登录成功", request=request, op="登录成功")
    return {"ok": True}


@app.post("/api/logout")
async def api_logout(request: Request):
    verify_csrf(request)
    request.session.clear()
    panel_audit("已退出登录", request=request, op="退出登录")
    return {"ok": True}


@app.get("/api/health")
async def api_health(request: Request):
    login_required(request)
    try:
        r = await clash_request("GET", "/version")
        if r.status_code == 404:
            r = await clash_request("GET", "/")
        return {"clash_http_status": r.status_code, "clash_ok": r.is_success}
    except httpx.ConnectError as e:
        return JSONResponse(
            status_code=503,
            content={
                "clash_ok": False,
                "error": f"无法连接 Clash API: {CLASH_BASE}",
                "detail": str(e),
            },
        )


@app.get("/api/gateway-summary")
async def api_gateway_summary(request: Request):
    login_required(request)
    vaults = _list_vaults()
    health = HEALTH_STORE.snapshot()
    scored = [h for h in health.values() if isinstance(h.get("score"), int)]
    avg_score = int(sum(int(h["score"]) for h in scored) / len(scored)) if scored else None
    degraded = sum(1 for h in scored if int(h["score"]) < 70)
    config_mtime = ""
    if CONFIG_FILE.is_file():
        try:
            config_mtime = datetime.fromtimestamp(CONFIG_FILE.stat().st_mtime, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except OSError:
            config_mtime = ""
    return {
        "ok": True,
        "uptime_seconds": int(time.time() - _STARTED_AT),
        "config_exists": CONFIG_FILE.is_file(),
        "config_updated_at": config_mtime,
        "vault_count": len(vaults),
        "enabled_vault_count": sum(1 for v in vaults if v.get("enabled")),
        "subscription_vault_count": sum(1 for v in vaults if v.get("source_kind") == "subscription" and v.get("source_url")),
        "node_count": sum(int(v.get("node_count") or 0) for v in vaults),
        "health_avg_score": avg_score,
        "health_degraded_count": degraded,
        "background_refresh": dict(_refresh_state),
    }


@app.get("/api/meta")
async def api_meta(request: Request):
    return {
        "login_required": True,
        "auth_configured": PANEL_AUTH_CONFIGURED,
        "selector_tag": SELECTOR_TAG,
        "audit_log_max": PANEL_AUDIT_LOG_MAX,
        "audit_log_path": str(AUDIT_LOG_FILE),
        "route_modes": ROUTE_MODES,
        "csrf_token": get_csrf_token(request),
    }


@app.get("/api/panel-logs")
async def api_panel_logs(request: Request):
    """面板审计日志：从持久化 JSONL 读取最近若干条；需登录。"""
    login_required(request)
    panel_audit("查看系统日志", request=request, op="查看日志")
    entries = _read_audit_jsonl_tail(AUDIT_LOG_FILE, PANEL_AUDIT_LOG_MAX)
    if not entries:
        with _audit_lock:
            entries = list(_audit_lines)
    return {
        "entries": entries,
        "max": PANEL_AUDIT_LOG_MAX,
        "path": str(AUDIT_LOG_FILE),
    }


@app.get("/api/proxies")
async def api_proxies(request: Request):
    login_required(request)
    panel_audit("读取 Clash proxies", request=request, op="查看代理信息")
    r = await clash_request("GET", "/proxies")
    if r.status_code == 404:
        r = await clash_request("GET", "/v1/proxies")
    if not r.is_success:
        raise HTTPException(
            status_code=502,
            detail=f"Clash API 返回 {r.status_code}: {r.text[:500]}",
        )
    return r.json()


@app.get("/api/selector-summary")
async def api_selector_summary(request: Request):
    login_required(request)
    panel_audit(f"查看节点列表（分组 {SELECTOR_TAG}）", request=request, op="查看节点")
    try:
        r = await clash_request("GET", "/proxies")
        if r.status_code == 404:
            r = await clash_request("GET", "/v1/proxies")
        if not r.is_success:
            raise HTTPException(
                status_code=502,
                detail=f"Clash API 返回 {r.status_code}: {r.text[:500]}",
            )
        data = r.json()
    except (httpx.RequestError, json.JSONDecodeError) as e:
        logger.error("连接内核 API 失败: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"无法从内核获取数据，请检查内核是否正常运行。错误: {e}",
        )

    proxies = data.get("proxies") or {}
    sel = proxies.get(SELECTOR_TAG)
    if not isinstance(sel, dict):
        sample = ", ".join(list(proxies.keys())[:15])
        raise HTTPException(
            status_code=404,
            detail=f"未找到有效分组 {SELECTOR_TAG}。已有 keys: {sample or '（空）'}",
        )

    all_nodes = sel.get("all")
    if not isinstance(all_nodes, list):
        all_nodes = []

    enabled_vaults = set(_enabled_vault_names())
    filtered_nodes = []
    node_types = {}
    for node in all_nodes:
        if not isinstance(node, str):
            continue
        if " · " in node:
            v_name = node.split(" · ")[0]
            if v_name in enabled_vaults:
                filtered_nodes.append(node)
        else:
            filtered_nodes.append(node)
            
    for node in filtered_nodes:
        proxy_info = proxies.get(node)
        if isinstance(proxy_info, dict):
            node_types[node] = proxy_info.get("type", "unknown")

    return {
        "tag": SELECTOR_TAG,
        "now": str(sel.get("now") or ""),
        "all": filtered_nodes,
        "types": node_types,
        "health": HEALTH_STORE.snapshot(),
    }


class SelectBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=512)


class ProxyDelaysBody(BaseModel):
    """对若干出站名称测 Clash 兼容 delay（sing-box experimental.clash_api）。"""

    names: list[str]
    timeout_ms: int | None = 10000
    test_url: str | None = None


class ProxyDelayBody(BaseModel):
    """对单个出站名称测 Clash 兼容 delay。"""

    name: str = Field(..., min_length=1, max_length=512)
    timeout_ms: int | None = 10000
    test_url: str | None = None


def _latency_tier_ms(delay_ms: int | None) -> str:
    if delay_ms is None:
        return "—"
    if delay_ms < 0:
        return "失败"
    if delay_ms < 150:
        return "极快 (>30M/s)"
    if delay_ms < 350:
        return "快 (10-30M/s)"
    if delay_ms < 800:
        return "一般 (2-10M/s)"
    return "较慢 (<2M/s)"


async def _clash_proxy_delay_ms(proxy_name: str, timeout_ms: int, test_url: str) -> tuple[int | None, str | None]:
    """调用 Clash API 测延迟；返回 (delay_ms, error_message)。"""
    enc = quote(proxy_name, safe="")
    to = max(3000, min(int(timeout_ms), 60000))
    q = f"?timeout={to}&url={quote(test_url, safe='')}"
    last_err: str | None = None
    for path in (f"/proxies/{enc}/delay{q}", f"/v1/proxies/{enc}/delay{q}"):
        try:
            r = await clash_request("GET", path)
        except httpx.RequestError as e:
            last_err = str(e)
            continue
        if r.status_code == 404:
            continue
        if not r.is_success:
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            continue
        try:
            data = r.json()
        except Exception:
            last_err = "响应非 JSON"
            continue
        if isinstance(data, dict) and "delay" in data:
            d = data.get("delay")
            if d is None:
                return None, "无 delay 字段"
            try:
                return int(d), None
            except (TypeError, ValueError):
                return None, "delay 格式异常"
        if isinstance(data, dict) and "message" in data:
            return None, str(data.get("message") or "错误")
        last_err = "未知响应"
    return None, last_err or "不支持测延迟"


@app.post("/api/proxy-delays")
async def api_proxy_delays(body: ProxyDelaysBody, request: Request):
    """批量测延迟；浏览器可一次请求测多个节点，避免暴露 Clash secret。"""
    login_required(request)
    verify_csrf(request)
    names = [str(n).strip() for n in body.names if str(n).strip()]
    if not names:
        raise HTTPException(status_code=400, detail="names 不能为空")
    if len(names) > 64:
        raise HTTPException(status_code=400, detail="单次最多测 64 个节点")
    panel_audit(f"测速节点 {len(names)} 个", request=request, op="测速")
    url = _validate_delay_test_url((body.test_url or "").strip() or DEFAULT_DELAY_TEST_URL)
    timeout_ms = body.timeout_ms if body.timeout_ms is not None else 10000
    sem = asyncio.Semaphore(5)

    async def one(name: str) -> tuple[str, int | None, str | None, str]:
        async with sem:
            delay_ms, err = await _clash_proxy_delay_ms(name, timeout_ms, url)
        tier = _latency_tier_ms(delay_ms)
        return name, delay_ms, err, tier

    pairs = await asyncio.gather(*[one(n) for n in names])
    results: dict = {}
    for name, delay_ms, err, tier in pairs:
        health = HEALTH_STORE.record(name, delay_ms, err)
        results[name] = {
            "delay_ms": delay_ms,
            "tier": tier,
            "error": err,
            "health": health,
        }
    return {"ok": True, "test_url": url, "results": results}


@app.post("/api/proxy-delay")
async def api_proxy_delay(body: ProxyDelayBody, request: Request):
    """单节点测速：供前端逐个请求，先完成先更新 UI。"""
    login_required(request)
    verify_csrf(request)
    name = str(body.name).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name 不能为空")
    url = _validate_delay_test_url((body.test_url or "").strip() or DEFAULT_DELAY_TEST_URL)
    timeout_ms = body.timeout_ms if body.timeout_ms is not None else 10000
    delay_ms, err = await _clash_proxy_delay_ms(name, timeout_ms, url)
    health = HEALTH_STORE.record(name, delay_ms, err)
    return {
        "ok": True,
        "name": name,
        "delay_ms": delay_ms,
        "tier": _latency_tier_ms(delay_ms),
        "error": err,
        "health": health,
    }


@app.get("/api/node-health")
async def api_node_health(request: Request):
    login_required(request)
    return {"ok": True, "health": HEALTH_STORE.snapshot()}


class VaultImportBody(BaseModel):
    """在面板中粘贴节点并写入加密库 + 生成 config.json。"""

    vault_password: str = Field(..., min_length=1, max_length=512)
    urls_text: str = Field(..., min_length=1, max_length=1_000_000)
    clash_secret: str | None = Field(None, max_length=512)
    vault_name: str | None = Field(None, max_length=64)


class VaultPreviewBody(BaseModel):
    vault_password: str = Field(..., min_length=1, max_length=512)
    urls_text: str = Field(..., min_length=1, max_length=1_000_000)
    vault_name: str | None = Field(None, max_length=64)


class VaultSubscriptionBody(BaseModel):
    vault_password: str = Field(..., min_length=1, max_length=512)
    subscription_url: str = Field(..., min_length=1, max_length=2048)
    clash_secret: str | None = Field(None, max_length=512)
    vault_name: str | None = Field(None, max_length=64)


class VaultRefreshBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=512)
    clash_secret: str | None = Field(None, max_length=512)


class VaultCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=512)


class VaultToggleBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    enabled: bool = True


class VaultRenameBody(BaseModel):
    old_name: str = Field(..., min_length=1, max_length=64)
    new_name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=512)


class VaultDeleteBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=512)


class VaultResetBody(BaseModel):
    admin_password: str = Field(..., min_length=1, max_length=512)


class VaultExportBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=512)


def _restore_env(key: str, old: str | None) -> None:
    if old is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = old


@app.get("/api/vault/status")
async def api_vault_status(request: Request):
    """登录后用于判断是否已存在加密节点库。"""
    login_required(request)
    vaults = _list_vaults()
    enabled = [v for v in vaults if v.get("enabled")]
    panel_audit("查看节点库状态", request=request, op="查看节点库")
    return {
        "has_vault": any(v.get("exists") for v in vaults),
        "vault_count": len(vaults),
        "enabled_count": len(enabled),
        "vaults": vaults,
        "config_exists": CONFIG_FILE.is_file(),
        "data_dir": str(DATA_DIR),
        "sub_token": get_subscription_token(),
    }


@app.get("/api/vaults")
async def api_vaults(request: Request):
    login_required(request)
    return {"vaults": _list_vaults()}


@app.post("/api/vaults/create")
async def api_vaults_create(body: VaultCreateBody, request: Request):
    login_required(request)
    verify_csrf(request)
    name = _vault_name_norm(body.name)
    idx = _read_vault_index()
    vaults = idx.get("vaults") or []
    if any(isinstance(v, dict) and v.get("name") == name for v in vaults):
        raise HTTPException(status_code=400, detail="节点库已存在")
    
    # 强制创建一个空的加密库文件，以锁定初始密码
    path = _vault_path(name)
    try:
        encrypt_vault_file([], body.password.strip(), path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"初始化加密库文件失败: {e}")

    vaults.append({"name": name, "enabled": True, "node_count": 0})
    idx["vaults"] = vaults
    _write_vault_index(idx)
    panel_audit(f"创建节点库：{name}（已初始化加密锁）", request=request, op="创建节点库")
    return {"ok": True, "vaults": _list_vaults()}


@app.post("/api/vaults/toggle")
async def api_vaults_toggle(body: VaultToggleBody, request: Request):
    login_required(request)
    verify_csrf(request)
    name = _vault_name_norm(body.name)
    idx = _read_vault_index()
    vaults = idx.get("vaults") or []
    changed = False
    for v in vaults:
        if isinstance(v, dict) and v.get("name") == name:
            v["enabled"] = bool(body.enabled)
            changed = True
    if not changed:
        raise HTTPException(status_code=404, detail="节点库不存在")
    idx["vaults"] = vaults
    _write_vault_index(idx)
    panel_audit(f"{'启用' if body.enabled else '停用'}节点库：{name}", request=request, op="配置节点库")
    return {"ok": True, "vaults": _list_vaults()}


@app.post("/api/vaults/rename")
async def api_vaults_rename(body: VaultRenameBody, request: Request):
    login_required(request)
    verify_csrf(request)
    old_name = _vault_name_norm(body.old_name)
    new_name = _vault_name_norm(body.new_name)
    if old_name == new_name:
        return {"ok": True, "vaults": _list_vaults()}
    idx = _read_vault_index()
    # 验证密码（库创建时即存在文件）
    path = _vault_path(old_name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="节点库文件丢失，无法验证密码")
    
    try:
        decrypt_vault_file(path, body.password.strip())
    except Exception:
        return {"ok": False, "detail": "密码错误，无法重命名该节点库"}
    vaults = idx.get("vaults") or []
    if any(isinstance(v, dict) and v.get("name") == new_name for v in vaults):
        raise HTTPException(status_code=400, detail="新名称已存在")
    found = False
    for v in vaults:
        if isinstance(v, dict) and v.get("name") == old_name:
            v["name"] = new_name
            found = True
    if not found:
        raise HTTPException(status_code=404, detail="节点库不存在")
    idx["vaults"] = vaults
    _write_vault_index(idx)
    # 移动文件（若存在）
    src = _vault_path(old_name)
    dst = _vault_path(new_name)
    try:
        if src.is_file() and not dst.exists():
            dst.write_bytes(src.read_bytes())
            src.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"重命名节点库文件失败: {e}") from e
    panel_audit(f"重命名节点库：{old_name} → {new_name}", request=request, op="配置节点库")
    return {"ok": True, "vaults": _list_vaults()}


@app.post("/api/vaults/delete")
async def api_vaults_delete(body: VaultDeleteBody, request: Request):
    login_required(request)
    verify_csrf(request)
    name = _vault_name_norm(body.name)
    idx = _read_vault_index()
    # 验证密码
    path = _vault_path(name)
    if not path.is_file():
        # 如果文件确实不存在，说明是异常数据，允许删除 index 记录
        pass
    else:
        try:
            decrypt_vault_file(path, body.password.strip())
        except Exception:
            return {"ok": False, "detail": "密码错误，无法删除该节点库"}
    vaults = [v for v in (idx.get("vaults") or []) if not (isinstance(v, dict) and v.get("name") == name)]
    if len(vaults) == len(idx.get("vaults") or []):
        raise HTTPException(status_code=404, detail="节点库不存在")
    idx["vaults"] = vaults
    _write_vault_index(idx)
    try:
        p = _vault_path(name)
        if p.is_file():
            p.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"删除节点库文件失败: {e}") from e
    panel_audit(f"删除节点库：{name}", request=request, op="配置节点库")
    return {"ok": True, "vaults": _list_vaults()}


@app.post("/api/vault/import")
async def api_vault_import(body: VaultImportBody, request: Request):
    login_required(request)
    verify_csrf(request)
    if not body.vault_password.strip():
        raise HTTPException(status_code=400, detail="vault_password 不能为空")

    urls = parse_urls_text(body.urls_text)
    vault_name = _vault_name_norm(body.vault_name or "default")
    unique_count, duplicate_count, total = _import_vault_urls(
        vault_name,
        body.vault_password.strip(),
        urls,
        source_kind="manual",
        clash_secret=body.clash_secret,
    )
    panel_audit(
        f"导入到节点库 {vault_name}：{unique_count} 条（去重 {duplicate_count} 条）；已合并生成配置（合计 {total} 条）",
        request=request,
        op="导入节点库",
    )
    return {
        "ok": True,
        "node_count": unique_count,
        "duplicate_count": duplicate_count,
        "total_count": total,
        "vault": str(_vault_path(vault_name)),
        "config": str(CONFIG_FILE),
    }


@app.post("/api/vault/preview")
async def api_vault_preview(body: VaultPreviewBody, request: Request):
    """Preview how a manual import would change the target vault."""
    login_required(request)
    verify_csrf(request)
    vault_name = _vault_name_norm(body.vault_name or "default")
    new_urls, duplicate_count = dedupe_urls(parse_urls_text(body.urls_text))
    old_urls: list[str] = []
    path = _vault_path(vault_name)
    if path.is_file():
        try:
            old_urls = decrypt_vault_file(path, body.vault_password.strip())
        except Exception:
            return {"ok": False, "detail": "密码错误，无法预览该节点库"}
    old_set = set(old_urls)
    new_set = set(new_urls)
    added = [u for u in new_urls if u not in old_set]
    removed = [u for u in old_urls if u not in new_set]
    unchanged = [u for u in new_urls if u in old_set]
    return {
        "ok": True,
        "new_count": len(new_urls),
        "old_count": len(old_urls),
        "added_count": len(added),
        "removed_count": len(removed),
        "unchanged_count": len(unchanged),
        "duplicate_count": duplicate_count,
        "added_preview": added[:5],
        "removed_preview": removed[:5],
    }


@app.post("/api/vault/reset")
async def api_vault_reset(body: VaultResetBody, request: Request):
    """重建节点库：删除所有加密文件（需要管理员密码）。"""
    login_required(request)
    verify_csrf(request)
    if not _safe_str_eq(body.admin_password.strip(), PANEL_PASSWORD):
        return {"ok": False, "detail": "管理员密码错误"}
    try:
        _vaults_bootstrap_and_migrate()
        # 删除所有 vaults/*.enc
        if VAULTS_DIR.is_dir():
            for p in VAULTS_DIR.glob("*.enc"):
                try:
                    p.unlink()
                except OSError:
                    pass
        # 兼容 legacy
        if _LEGACY_VAULT_FILE.is_file():
            try:
                _LEGACY_VAULT_FILE.unlink()
            except OSError:
                pass
        # 清空 index 中的节点数
        idx = _read_vault_index()
        for v in (idx.get("vaults") or []):
            if isinstance(v, dict):
                v["node_count"] = 0
        _write_vault_index(idx)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"删除节点库失败: {e}") from e
    panel_audit("重建节点库：已清空所有节点库文件", request=request, op="重建节点库")
    return {"ok": True, "has_vault": any(v.get("exists") for v in _list_vaults())}


@app.post("/api/vault/import-subscription")
async def api_vault_import_subscription(body: VaultSubscriptionBody, request: Request):
    """通过订阅链接拉取并导入节点。"""
    login_required(request)
    verify_csrf(request)
    subscription_url = _validate_subscription_url(body.subscription_url)
    if not body.vault_password.strip():
        raise HTTPException(status_code=400, detail="vault_password 不能为空")

    content = await _fetch_subscription_content(subscription_url)
    text = content.decode("utf-8", errors="replace")
    decoded_text = _decode_subscription_payload(text)

    urls = parse_urls_text(decoded_text)
    vault_name = _vault_name_norm(body.vault_name or "default")
    unique_count, duplicate_count, total = _import_vault_urls(
        vault_name,
        body.vault_password.strip(),
        urls,
        source_url=subscription_url,
        source_kind="subscription",
        clash_secret=body.clash_secret,
    )
    panel_audit(
        f"订阅导入到节点库 {vault_name}：{unique_count} 条（去重 {duplicate_count} 条）；已合并生成配置（合计 {total} 条）",
        request=request,
        op="订阅导入",
    )
    return {"ok": True, "node_count": unique_count, "duplicate_count": duplicate_count, "total_count": total}


@app.post("/api/vault/refresh")
async def api_vault_refresh(body: VaultRefreshBody, request: Request):
    """Using the stored subscription source, refresh a vault in place."""
    login_required(request)
    verify_csrf(request)
    vault_name = _vault_name_norm(body.name)
    idx = _read_vault_index()
    record = next((v for v in (idx.get("vaults") or []) if isinstance(v, dict) and v.get("name") == vault_name), None)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="节点库不存在")
    source_url = (record.get("source_url") or "").strip()
    source_kind = (record.get("source_kind") or "").strip()
    if not source_url or source_kind != "subscription":
        raise HTTPException(status_code=400, detail="该节点库没有可刷新订阅来源")
    if not body.password.strip():
        raise HTTPException(status_code=400, detail="password 不能为空")

    content = await _fetch_subscription_content(source_url)
    decoded_text = _decode_subscription_payload(content.decode("utf-8", errors="replace"))
    urls = parse_urls_text(decoded_text)
    unique_count, duplicate_count, total = _import_vault_urls(
        vault_name,
        body.password.strip(),
        urls,
        source_url=source_url,
        source_kind="subscription",
        clash_secret=body.clash_secret,
    )
    panel_audit(
        f"刷新节点库 {vault_name}：{unique_count} 条（去重 {duplicate_count} 条）；已重新生成配置（合计 {total} 条）",
        request=request,
        op="刷新订阅",
    )
    return {"ok": True, "node_count": unique_count, "duplicate_count": duplicate_count, "total_count": total, "source_url": source_url}


@app.post("/api/vault/verify")
async def api_vault_verify(body: VaultExportBody, request: Request):
    """验证库密码是否正确。如果库文件不存在，则视为新库，验证通过。"""
    login_required(request)
    verify_csrf(request)
    path = _vault_path(body.name)
    if not path.is_file():
        # 库文件不存在，说明是首次导入，任何密码都暂时接受（作为初始密码）
        return {"ok": True, "exists": False}
    try:
        decrypt_vault_file(path, body.password.strip())
        return {"ok": True, "exists": True}
    except Exception:
        return {"ok": False, "detail": "密码错误，无法解密该节点库"}


@app.post("/api/vault/export")
async def api_vault_export(body: VaultExportBody, request: Request):
    """解密并导出节点库内容：用于“查看”或“编辑”。"""
    login_required(request)
    verify_csrf(request)
    path = _vault_path(body.name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="节点库文件不存在")
    try:
        urls = decrypt_vault_file(path, body.password.strip())
        panel_audit(f"导出节点库内容：{body.name}", request=request, op="查看节点库")
        return {"ok": True, "urls": urls}
    except Exception as e:
        return {"ok": False, "detail": f"解析失败（密码可能错误）: {e}"}


@app.post("/api/backup/export")
async def api_backup_export(request: Request):
    """Export encrypted gateway state for off-host backup."""
    login_required(request)
    verify_csrf(request)
    buf = io.BytesIO()
    manifest = {
        "created_at": _now_iso(),
        "format": "nethub-backup-v1",
        "contains": [],
    }
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if VAULTS_INDEX.is_file():
            zf.write(VAULTS_INDEX, "vaults/index.json")
            manifest["contains"].append("vaults/index.json")
        if VAULTS_DIR.is_dir():
            for p in VAULTS_DIR.glob("*.enc"):
                zf.write(p, f"vaults/{p.name}")
                manifest["contains"].append(f"vaults/{p.name}")
        if CONFIG_FILE.is_file():
            zf.write(CONFIG_FILE, "config.json")
            manifest["contains"].append("config.json")
        health_path = DATA_DIR / "node_health.json"
        if health_path.is_file():
            zf.write(health_path, "node_health.json")
            manifest["contains"].append("node_health.json")
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    buf.seek(0)
    panel_audit("导出网关备份", request=request, op="导出备份")
    from fastapi.responses import StreamingResponse
    filename = f"nethub-backup-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.put("/api/selector/{group}")
async def api_select_group(group: str, body: SelectBody, request: Request):
    login_required(request)
    verify_csrf(request)
    if group != SELECTOR_TAG:
        raise HTTPException(status_code=400, detail="仅允许切换配置的 selector 分组")
    payload = {"name": body.name}
    enc = quote(group, safe="")
    for path in (f"/proxies/{enc}", f"/v1/proxies/{enc}"):
        r = await clash_request("PUT", path, json=payload)
        if r.status_code != 404:
            if not r.is_success:
                raise HTTPException(
                    status_code=502,
                    detail=f"切换失败 HTTP {r.status_code}: {r.text[:500]}",
                )
            panel_audit(
                f"切换代理节点：分组 {group} → {body.name}",
                request=request,
                op="切换节点",
            )
            return {"ok": True, "group": group, "name": body.name}
    raise HTTPException(status_code=502, detail="Clash API 不支持 PUT /proxies")


@app.post("/api/rebuild")
async def api_rebuild(body: RebuildBody, request: Request):
    login_required(request)
    verify_csrf(request)
    panel_audit(f"手动重构配置 (模式: {body.route_mode})", request=request, op="重构配置")
    total = _rebuild_config_from_vaults(
        body.vault_password.strip(),
        clash_secret=body.clash_secret,
        route_mode=body.route_mode
    )
    return {"ok": True, "total_count": total, "route_mode": body.route_mode}


@app.get("/api/traffic")
async def api_traffic(request: Request):
    """通过 SSE 代理内核流量数据。"""
    login_required(request)

    async def event_generator():
        url = f"{CLASH_BASE}/traffic"
        headers = clash_headers()
        try:
            async with app.state.http_client.stream("GET", url, headers=headers, timeout=None) as r:
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    yield f"data: {line}\n\n"
        except Exception as e:
            logger.error("流量代理异常: %s", e)
            yield f"data: {json.dumps({'up': 0, 'down': 0})}\n\n"

    from fastapi.responses import StreamingResponse
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/connections")
async def api_connections(request: Request):
    """获取内核当前的所有活跃代理连接，用于实时网络追踪。"""
    login_required(request)
    try:
        res = await clash_request("GET", "/connections")
        return res.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取连接失败: {e}")


@app.delete("/api/connections")
async def api_connections_close_all(request: Request):
    """强行中断内核中所有的活动代理连接。"""
    login_required(request)
    verify_csrf(request)
    try:
        res = await clash_request("DELETE", "/connections")
        return {"ok": True, "status": res.status_code}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"中断所有连接失败: {e}")


@app.delete("/api/connections/{conn_id}")
async def api_connection_close(conn_id: str, request: Request):
    """中断特定的活动代理连接。"""
    login_required(request)
    verify_csrf(request)
    try:
        res = await clash_request("DELETE", f"/connections/{conn_id}")
        return {"ok": True, "status": res.status_code}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"关闭该代理连接失败: {e}")


@app.get("/api/export/clash")
async def api_export_clash(request: Request, token: str | None = None):
    """导出当前运行的节点为 Clash YAML 订阅格式"""
    verify_export_access(request, token)
    if not CONFIG_FILE.is_file():
        raise HTTPException(status_code=404, detail="配置文件不存在")
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析配置文件失败: {e}")
        
    outbounds = data.get("outbounds", [])
    clash_proxies = []
    from core.build_config import outbound_to_clash_proxy, generate_clash_yaml
    for o in outbounds:
        if o.get("type") in ("vmess", "vless", "trojan", "shadowsocks", "hysteria2", "tuic"):
            p = outbound_to_clash_proxy(o)
            if p:
                clash_proxies.append(p)
                
    yaml_content = generate_clash_yaml(clash_proxies)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        yaml_content,
        headers={
            "Content-Disposition": "attachment; filename=nethub_clash.yaml"
        }
    )


@app.get("/api/export/v2ray")
async def api_export_v2ray(request: Request, token: str | None = None):
    """导出当前运行的节点为 V2ray 通用 Base64 订阅"""
    verify_export_access(request, token)
    if not CONFIG_FILE.is_file():
        raise HTTPException(status_code=404, detail="配置文件不存在")
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析配置文件失败: {e}")
        
    outbounds = data.get("outbounds", [])
    urls = []
    from core.build_config import outbound_to_share_url
    for o in outbounds:
        if o.get("type") in ("vmess", "vless", "trojan", "shadowsocks", "hysteria2", "tuic"):
            url = outbound_to_share_url(o)
            if url:
                urls.append(url)
                
    payload = "\n".join(urls)
    b64_payload = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        b64_payload,
        headers={
            "Content-Disposition": "attachment; filename=nethub_v2ray.txt"
        }
    )


@app.get("/api/export/singbox")
async def api_export_singbox(request: Request, token: str | None = None):
    """导出当前运行的 sing-box 配置 JSON"""
    verify_export_access(request, token)
    if not CONFIG_FILE.is_file():
        raise HTTPException(status_code=404, detail="配置文件不存在")
    return FileResponse(
        CONFIG_FILE,
        media_type="application/json",
        filename="nethub_config.json"
    )


@app.get("/login", include_in_schema=False)
async def login_page(request: Request):
    if not PANEL_AUTH_CONFIGURED:
        return FileResponse(STATIC_DIR / "setup_required.html")
    if request.session.get("panel_ok"):
        return RedirectResponse("/", status_code=302)
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/")
async def index(request: Request):
    if not PANEL_AUTH_CONFIGURED:
        return FileResponse(STATIC_DIR / "setup_required.html")
    if not request.session.get("panel_ok"):
        return FileResponse(STATIC_DIR / "login.html")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """避免浏览器默认请求 /favicon.ico 产生 404；正文为 SVG。"""
    return FileResponse(
        STATIC_DIR / "favicon.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
