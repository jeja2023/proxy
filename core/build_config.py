"""由分享链接列表构建 sing-box JSON 配置（供 CLI / 面板 / vault 导出共用）。"""

from __future__ import annotations

import json
import os
import urllib.parse
from base64 import urlsafe_b64decode
from pathlib import Path

_SELECTOR_TAG = os.environ.get("PANEL_SELECTOR_TAG", "代理选择").strip() or "代理选择"


class NodeBuildError(ValueError):
    """单条节点解析失败。"""

    def __init__(self, line_no: int, message: str) -> None:
        self.line_no = line_no
        super().__init__(f"第 {line_no} 行: {message}")


def _truthy_query(query: dict, *keys: str) -> bool:
    for k in keys:
        if k not in query:
            continue
        v = (query[k][0] or "").strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
    return False


def _first(query: dict, key: str, default: str = "") -> str:
    vals = query.get(key)
    if not vals or not str(vals[0]).strip():
        return default
    return str(vals[0]).strip()


def decode_base64_userinfo(s: str) -> str:
    s = s.strip()
    s += "=" * ((4 - len(s) % 4) % 4)
    return urlsafe_b64decode(s).decode("utf-8")


def uniquify_tag(base: str, used: dict[str, int]) -> str:
    name = base.strip() or "node"
    if name not in used:
        used[name] = 1
        return name
    used[name] += 1
    return f"{name}-{used[name]}"


def build_vless_transport(query: dict) -> dict:
    t = _first(query, "type", "grpc").lower()
    if t == "grpc":
        return {"type": "grpc", "service_name": _first(query, "serviceName", "")}
    if t == "ws":
        tr: dict = {"type": "ws", "path": _first(query, "path", "/")}
        host = _first(query, "host", "")
        if host:
            tr["headers"] = {"Host": host}
        return tr
    if t == "tcp":
        return {"type": "tcp"}
    if t in ("httpupgrade", "http"):
        return {
            "type": "httpupgrade",
            "path": _first(query, "path", "/"),
            "host": _first(query, "host", ""),
        }
    return {"type": t, "service_name": _first(query, "serviceName", "")}


def parse_hysteria2(parsed: urllib.parse.ParseResult, query: dict, tag: str) -> dict:
    tls: dict = {
        "enabled": True,
        "server_name": _first(query, "sni", parsed.hostname or ""),
    }
    if _truthy_query(query, "insecure", "allowInsecure"):
        tls["insecure"] = True
    node: dict = {
        "type": "hysteria2",
        "tag": tag,
        "server": parsed.hostname,
        "server_port": parsed.port,
        "password": urllib.parse.unquote(parsed.username) if parsed.username else "",
        "tls": tls,
    }
    if "obfs" in query:
        node["obfs"] = {
            "type": query["obfs"][0],
            "password": _first(query, "obfs-password", ""),
        }
    return node


def parse_vless(parsed: urllib.parse.ParseResult, query: dict, tag: str) -> dict:
    return {
        "type": "vless",
        "tag": tag,
        "server": parsed.hostname,
        "server_port": parsed.port,
        "uuid": urllib.parse.unquote(parsed.username) if parsed.username else "",
        "tls": {
            "enabled": True,
            "server_name": _first(query, "sni", parsed.hostname or ""),
            "reality": {
                "enabled": bool(_first(query, "pbk", "")),
                "public_key": _first(query, "pbk", ""),
                "short_id": _first(query, "sid", ""),
            },
            "utls": {
                "enabled": True,
                "fingerprint": _first(query, "fp", "chrome"),
            },
        },
        "transport": build_vless_transport(query),
    }


def parse_tuic(parsed: urllib.parse.ParseResult, query: dict, tag: str) -> dict:
    password = parsed.password if parsed.password else urllib.parse.unquote(parsed.username)
    uuid_str = urllib.parse.unquote(parsed.username)
    user_host = urllib.parse.unquote(parsed.netloc.split("@")[0])
    if ":" in user_host:
        parts = user_host.split(":", 1)
        uuid_str = parts[0]
        password = parts[1]

    alpn_raw = _first(query, "alpn", "h3")
    alpn = [x.strip() for x in alpn_raw.split(",") if x.strip()] or ["h3"]

    tls: dict = {
        "enabled": True,
        "server_name": _first(query, "sni", parsed.hostname or ""),
        "alpn": alpn,
    }
    if _truthy_query(query, "insecure", "allowInsecure"):
        tls["insecure"] = True

    node: dict = {
        "type": "tuic",
        "tag": tag,
        "server": parsed.hostname,
        "server_port": parsed.port,
        "uuid": uuid_str,
        "password": password,
        "tls": tls,
    }
    if "congestion_control" in query:
        node["congestion_control"] = query["congestion_control"][0]
    return node


def parse_shadowsocks(parsed: urllib.parse.ParseResult, query: dict, tag: str) -> dict:
    pd = parsed.netloc.split("@")
    if len(pd) < 2:
        raise ValueError("Shadowsocks 链接缺少 @ 后的主机部分")

    user_info = urllib.parse.unquote(pd[0])
    decoded = user_info
    try:
        decoded = decode_base64_userinfo(user_info)
    except Exception:
        pass

    parts = decoded.split(":")
    if len(parts) < 2:
        raise ValueError(
            f"Shadowsocks 解码后格式无效（需 method:password 或 method:key1:key2）: {decoded!r}"
        )

    method = parts[0]
    if len(parts) >= 3:
        password = f"{parts[1]}:{parts[2]}"
    else:
        password = parts[1]

    return {
        "type": "shadowsocks",
        "tag": tag,
        "server": parsed.hostname,
        "server_port": parsed.port,
        "method": method,
        "password": password,
    }


def build_outbounds(urls: list[str]) -> tuple[list[dict], list[str]]:
    outbounds: list[dict] = []
    tags: list[str] = []
    tag_counts: dict[str, int] = {}

    for idx, u in enumerate(urls):
        line_no = idx + 1
        parsed = urllib.parse.urlparse(u)
        scheme = (parsed.scheme or "").lower()
        raw_tag = urllib.parse.unquote(parsed.fragment) or f"node-{line_no}"
        tag = uniquify_tag(raw_tag, tag_counts)
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if scheme == "hysteria2":
                outbounds.append(parse_hysteria2(parsed, query, tag))
            elif scheme == "vless":
                outbounds.append(parse_vless(parsed, query, tag))
            elif scheme == "tuic":
                outbounds.append(parse_tuic(parsed, query, tag))
            elif scheme in ("ss", "shadowsocks"):
                outbounds.append(parse_shadowsocks(parsed, query, tag))
            else:
                raise NodeBuildError(line_no, f"不支持的协议 {scheme!r}")
        except ValueError as e:
            raise NodeBuildError(line_no, str(e)) from e

        tags.append(tag)

    return outbounds, tags


def clash_api_block() -> dict | None:
    panel_mode = os.environ.get("SINGBOX_WITH_PANEL", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    flag = os.environ.get("SINGBOX_CLASH_API", "").strip().lower() in ("1", "true", "yes", "on")
    if not panel_mode and not flag:
        return None
    secret = os.environ.get("CLASH_API_SECRET", "").strip()
    if not secret:
        raise ValueError(
            "已启用 Clash API（SINGBOX_WITH_PANEL / SINGBOX_CLASH_API），但未设置 CLASH_API_SECRET"
        )
    default_listen = "0.0.0.0:9020" if panel_mode else "127.0.0.1:9020"
    listen = os.environ.get("CLASH_API_LISTEN", default_listen).strip()
    return {
        "clash_api": {
            "external_controller": listen,
            "secret": secret,
            "default_mode": os.environ.get("CLASH_API_DEFAULT_MODE", "rule").strip() or "rule",
        }
    }


def build_singbox_config(urls: list[str]) -> dict:
    """根据 URL 列表构建完整 sing-box 配置 dict。"""
    if not urls:
        raise ValueError("节点列表为空")

    outbounds, tags = build_outbounds(urls)

    default_tag = os.environ.get("SINGBOX_SELECTOR_DEFAULT", "").strip() or tags[0]
    if default_tag not in tags:
        raise ValueError(
            f"SINGBOX_SELECTOR_DEFAULT={default_tag!r} 不在节点 tag 列表中。"
            f" 可用 tag: {', '.join(tags[:10])}{'…' if len(tags) > 10 else ''}"
        )

    outbounds.insert(
        0,
        {
            "type": "selector",
            "tag": _SELECTOR_TAG,
            "outbounds": tags,
            "default": default_tag,
        },
    )
    outbounds.append({"type": "direct", "tag": "direct"})

    config: dict = {
        "log": {
            "level": os.environ.get("SINGBOX_LOG_LEVEL", "info").strip() or "info",
            "timestamp": True,
        },
        "inbounds": [
            {
                "type": "http",
                "tag": "http-in",
                "listen": os.environ.get("SINGBOX_HTTP_LISTEN", "0.0.0.0").strip() or "0.0.0.0",
                "listen_port": int(os.environ.get("SINGBOX_HTTP_PORT", "2080")),
            }
        ],
        "outbounds": outbounds,
        "route": {
            "rules": [
                {
                    "inbound": ["http-in"],
                    "outbound": _SELECTOR_TAG,
                }
            ],
            "final": _SELECTOR_TAG,
        },
    }

    clash = clash_api_block()
    if clash is not None:
        config["experimental"] = clash

    return config


def strip_clash_embedded_web_ui(config: dict) -> bool:
    """从配置 dict 中移除 experimental.clash_api.external_ui（本仓库仅用自建面板，不使用 sing-box 内置网页）。"""
    exp = config.get("experimental")
    if not isinstance(exp, dict):
        return False
    ca = exp.get("clash_api")
    if not isinstance(ca, dict) or "external_ui" not in ca:
        return False
    del ca["external_ui"]
    return True


def write_singbox_config(config: dict, path: Path) -> None:
    strip_clash_embedded_web_ui(config)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sanitize_config_file_if_needed(path: Path) -> bool:
    """若磁盘上的 JSON 仍含 external_ui，则去掉并写回。返回是否已写回。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not strip_clash_embedded_web_ui(data):
        return False
    write_singbox_config(data, path)
    return True


def bootstrap_placeholder_config() -> dict:
    """无节点时占位：仅 HTTP 入站 + direct，便于先起 sing-box 与面板再导入。"""
    cfg: dict = {
        "log": {"level": "info", "timestamp": True},
        "inbounds": [
            {
                "type": "http",
                "tag": "http-in",
                "listen": os.environ.get("SINGBOX_HTTP_LISTEN", "0.0.0.0").strip() or "0.0.0.0",
                "listen_port": int(os.environ.get("SINGBOX_HTTP_PORT", "2080")),
            }
        ],
        "outbounds": [
            {
                "type": "selector",
                "tag": _SELECTOR_TAG,
                "outbounds": ["direct"],
                "default": "direct",
            },
            {"type": "direct", "tag": "direct"},
        ],
        "route": {
            "rules": [{"inbound": ["http-in"], "outbound": _SELECTOR_TAG}],
            "final": _SELECTOR_TAG,
        },
    }
    clash = clash_api_block()
    if clash is not None:
        cfg["experimental"] = clash
    return cfg


def parse_urls_text(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def load_urls_file(path: Path) -> list[str]:
    return parse_urls_text(path.read_text(encoding="utf-8"))
