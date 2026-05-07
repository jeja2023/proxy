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


def parse_vmess(parsed: urllib.parse.ParseResult, query: dict, tag: str) -> dict:
    raw_str = parsed.netloc + parsed.path
    try:
        decoded_str = decode_base64_userinfo(raw_str.strip())
        data = json.loads(decoded_str)
    except Exception as e:
        raise ValueError(f"VMess 链接 Base64 解码或 JSON 解析失败: {e}")

    if not isinstance(data, dict):
        raise ValueError("VMess JSON 格式无效")

    server = data.get("add", "")
    if not server:
        raise ValueError("VMess 缺少服务器地址 (add)")

    try:
        port = int(data.get("port", 0))
    except (ValueError, TypeError):
        raise ValueError(f"VMess 端口无效: {data.get('port')}")

    uuid_str = data.get("id", "")
    if not uuid_str:
        raise ValueError("VMess 缺少用户 UUID (id)")

    security = data.get("scy", "auto") or "auto"
    alter_id = 0
    try:
        alter_id = int(data.get("aid", 0))
    except (ValueError, TypeError):
        pass

    node = {
        "type": "vmess",
        "tag": tag,
        "server": server,
        "server_port": port,
        "uuid": uuid_str,
        "security": security,
        "alter_id": alter_id,
    }

    tls_enabled = data.get("tls") == "tls"
    if tls_enabled:
        tls_sni = data.get("sni", "") or data.get("host", "") or server
        tls_fp = data.get("fp", "chrome") or "chrome"
        node["tls"] = {
            "enabled": True,
            "server_name": tls_sni,
            "utls": {
                "enabled": True,
                "fingerprint": tls_fp
            }
        }
        alpn_raw = data.get("alpn", "")
        if alpn_raw:
            alpn = [x.strip() for x in alpn_raw.split(",") if x.strip()]
            node["tls"]["alpn"] = alpn

    net = (data.get("net") or "").lower()
    host = data.get("host", "")
    path = data.get("path", "")

    if net in ("ws", "websocket"):
        tr = {
            "type": "ws",
            "path": path or "/",
        }
        if host:
            tr["headers"] = {"Host": host}
        node["transport"] = tr
    elif net in ("grpc",):
        node["transport"] = {
            "type": "grpc",
            "service_name": path or ""
        }
    elif net in ("http", "h2"):
        node["transport"] = {
            "type": "http",
            "path": path or "/",
            "host": [host] if host else []
        }

    return node


def parse_trojan(parsed: urllib.parse.ParseResult, query: dict, tag: str) -> dict:
    if not parsed.hostname:
        raise ValueError("Trojan 链接缺少服务器地址")
    
    port = parsed.port
    if not port:
        try:
            netloc_parts = parsed.netloc.split("@")[-1].split(":")
            if len(netloc_parts) > 1:
                port = int(netloc_parts[-1])
        except Exception:
            pass
    if not port:
        raise ValueError("Trojan 链接缺少服务器端口")

    tls: dict = {
        "enabled": True,
        "server_name": _first(query, "sni", _first(query, "peer", parsed.hostname or "")),
        "utls": {
            "enabled": True,
            "fingerprint": _first(query, "fp", "chrome")
        }
    }
    if _truthy_query(query, "insecure", "allowInsecure"):
        tls["insecure"] = True
    
    alpn_raw = _first(query, "alpn", "")
    if alpn_raw:
        alpn = [x.strip() for x in alpn_raw.split(",") if x.strip()]
        tls["alpn"] = alpn

    node: dict = {
        "type": "trojan",
        "tag": tag,
        "server": parsed.hostname,
        "server_port": port,
        "password": urllib.parse.unquote(parsed.username) if parsed.username else "",
        "tls": tls,
    }
    
    t = _first(query, "type", "").lower()
    if t == "ws":
        tr = {
            "type": "ws",
            "path": _first(query, "path", "/"),
        }
        host = _first(query, "host", "")
        if host:
            tr["headers"] = {"Host": host}
        node["transport"] = tr
    elif t == "grpc":
        node["transport"] = {
            "type": "grpc",
            "service_name": _first(query, "serviceName", "")
        }
    return node


def build_outbounds(urls: list[str]) -> tuple[list[dict], list[str]]:
    outbounds: list[dict] = []
    tags: list[str] = []
    tag_counts: dict[str, int] = {}

    for idx, u in enumerate(urls):
        line_no = idx + 1
        parsed = urllib.parse.urlparse(u)
        scheme = (parsed.scheme or "").lower()
        
        raw_tag = ""
        if scheme == "vmess":
            try:
                raw_str = parsed.netloc + parsed.path
                decoded_str = decode_base64_userinfo(raw_str.strip())
                data = json.loads(decoded_str)
                if isinstance(data, dict) and "ps" in data:
                    raw_tag = str(data["ps"]).strip()
            except Exception:
                pass

        if not raw_tag:
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
            elif scheme == "vmess":
                outbounds.append(parse_vmess(parsed, query, tag))
            elif scheme == "trojan":
                outbounds.append(parse_trojan(parsed, query, tag))
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


ROUTE_MODES = {
    "global": "全局代理",
    "rule": "规则分流 (内置拦截)",
    "bypass_cn": "绕过大陆 (推荐)",
    "direct": "全局直连",
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply_config_template(config: dict) -> dict:
    path_raw = os.environ.get("SINGBOX_TEMPLATE_PATH", "").strip()
    if not path_raw:
        return config
    path = Path(path_raw)
    try:
        override = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"SINGBOX_TEMPLATE_PATH 无法读取或不是合法 JSON: {e}") from e
    if not isinstance(override, dict):
        raise ValueError("SINGBOX_TEMPLATE_PATH 必须是 JSON object")
    return _deep_merge(config, override)


def build_singbox_config(urls: list[str], route_mode: str = "bypass_cn") -> dict:
    """根据 URL 列表和路由模式构建完整 sing-box 配置 dict。"""
    urls, _ = dedupe_urls(urls)
    if not urls:
        raise ValueError("节点列表为空")

    outbounds, tags = build_outbounds(urls)

    default_tag = os.environ.get("SINGBOX_SELECTOR_DEFAULT", "").strip() or tags[0]
    if default_tag not in tags:
        raise ValueError(
            f"SINGBOX_SELECTOR_DEFAULT={default_tag!r} 不在节点 tag 列表中。"
            f" 可用 tag: {', '.join(tags[:10])}{'…' if len(tags) > 10 else ''}"
        )

    # 基础节点组
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
    outbounds.append({"type": "dns", "tag": "dns-out"})
    outbounds.append({"type": "block", "tag": "block"})

    # 路由规则预设
    rules = []
    
    # 基础 DNS 规则
    rules.append({"protocol": "dns", "outbound": "dns-out"})
    
    if route_mode == "bypass_cn":
        # 绕过大陆模式
        rules.extend([
            {"clash_mode": "Direct", "outbound": "direct"},
            {"clash_mode": "Global", "outbound": _SELECTOR_TAG},
            {"domain_suffix": [".cn"], "outbound": "direct"},
            {"geoip": ["cn", "private"], "outbound": "direct"},
            {"geosite": ["cn"], "outbound": "direct"},
        ])
    elif route_mode == "rule":
        # 规则分流模式 (基础版)
        rules.extend([
            {"geosite": ["category-ads-all"], "outbound": "block"},
            {"geoip": ["private"], "outbound": "direct"},
        ])
    elif route_mode == "direct":
        # 全局直连模式
        rules.extend([
            {"outbound": "direct"}
        ])
    
    # 默认规则
    rules.append({"outbound": "direct" if route_mode == "direct" else _SELECTOR_TAG})

    config: dict = {
        "log": {
            "level": os.environ.get("SINGBOX_LOG_LEVEL", "info").strip() or "info",
            "timestamp": True,
        },
        "dns": {
            "servers": [
                {"tag": "dns-remote", "address": "https://8.8.8.8/dns-query", "detour": _SELECTOR_TAG},
                {"tag": "dns-local", "address": "223.5.5.5", "detour": "direct"},
                {"tag": "dns-block", "address": "rcode://success"}
            ],
            "rules": [
                {"outbound": "any", "server": "dns-local"},
                {"geosite": ["cn"], "server": "dns-local"},
                {"geosite": ["category-ads-all"], "server": "dns-block"}
            ],
            "final": "dns-remote"
        },
        "inbounds": [
            {
                "type": "http",
                "tag": "http-in",
                "listen": os.environ.get("SINGBOX_HTTP_LISTEN", "0.0.0.0").strip() or "0.0.0.0",
                "listen_port": int(os.environ.get("SINGBOX_HTTP_PORT", "2080")),
                "sniff": True,
                "sniff_override_destination": True
            }
        ],
        "outbounds": outbounds,
        "route": {
            "rules": rules,
            "final": "direct" if route_mode == "direct" else _SELECTOR_TAG,
            "auto_detect_interface": True
        },
    }

    clash = clash_api_block()
    if clash is not None:
        config["experimental"] = clash

    return _apply_config_template(config)


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
    import re
    import base64
    out: list[str] = []
    
    # 尝试检测整块文本是否为 Base64 密文（许多订阅源直接返回全段 base64）
    stripped = "".join(text.split()).strip()
    if len(stripped) > 20 and re.match(r"^[A-Za-z0-9+/=]+$", stripped):
        try:
            missing_padding = len(stripped) % 4
            if missing_padding:
                stripped += "=" * (4 - missing_padding)
            decoded_text = base64.b64decode(stripped.encode("utf-8")).decode("utf-8", errors="ignore")
            # 校验解码后是否含有主流代理链接标志，若含有则递归深度解析
            if any(p in decoded_text for p in ("vmess://", "vless://", "trojan://", "ss://", "shadowsocks://", "hysteria2://", "tuic://")):
                return parse_urls_text(decoded_text)
        except Exception:
            pass

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
            
        # 智能正则匹配：提取包含在任意行文本混排中的节点分享链接（隔离文本噪音）
        matches = re.findall(r"((?:vmess|vless|trojan|ss|shadowsocks|hysteria2|tuic)://\S+)", line)
        if matches:
            for m in matches:
                out.append(m)
        else:
            # 兼容普通 http/https 订阅链接或分流规则配置行
            if line.startswith("http://") or line.startswith("https://"):
                out.append(line)
    return out


def dedupe_urls(urls: list[str]) -> tuple[list[str], int]:
    """Preserve order while removing duplicate share links."""
    seen: set[str] = set()
    unique: list[str] = []
    dup_count = 0
    for raw in urls:
        u = (raw or "").strip()
        if not u:
            continue
        if u in seen:
            dup_count += 1
            continue
        seen.add(u)
        unique.append(u)
    return unique, dup_count


def load_urls_file(path: Path) -> list[str]:
    urls = parse_urls_text(path.read_text(encoding="utf-8"))
    unique, _ = dedupe_urls(urls)
    return unique


def outbound_to_vmess_url(outbound: dict) -> str:
    import base64
    tls = outbound.get("tls", {})
    tls_enabled = tls.get("enabled", False)
    utls = tls.get("utls", {})
    fp = utls.get("fingerprint", "chrome") if utls.get("enabled", False) else ""
    
    transport = outbound.get("transport", {})
    net = transport.get("type", "tcp")
    path = transport.get("path", "")
    host = ""
    if net == "ws":
        host = transport.get("headers", {}).get("Host", "")
    elif net == "http":
        hosts = transport.get("host", [])
        host = hosts[0] if hosts else ""
        path = transport.get("path", "")
    elif net == "grpc":
        path = transport.get("service_name", "")

    data = {
        "v": "2",
        "ps": outbound.get("tag", "VMess Node"),
        "add": outbound.get("server", ""),
        "port": outbound.get("server_port", 0),
        "id": outbound.get("uuid", ""),
        "aid": outbound.get("alter_id", 0),
        "scy": outbound.get("security", "auto"),
        "net": net,
        "type": "none",
        "host": host,
        "path": path,
        "tls": "tls" if tls_enabled else "none",
        "sni": tls.get("server_name", "") if tls_enabled else "",
        "fp": fp,
    }
    dumped = json.dumps(data, ensure_ascii=False)
    b64 = base64.b64encode(dumped.encode("utf-8")).decode("utf-8")
    return f"vmess://{b64}"


def outbound_to_vless_url(outbound: dict) -> str:
    uuid = outbound.get("uuid", "")
    server = outbound.get("server", "")
    port = outbound.get("server_port", 0)
    tag = outbound.get("tag", "VLESS Node")
    
    tls = outbound.get("tls", {})
    tls_enabled = tls.get("enabled", False)
    
    params = {}
    params["encryption"] = "none"
    if tls_enabled:
        params["security"] = "tls"
        if tls.get("server_name"):
            params["sni"] = tls.get("server_name")
        utls = tls.get("utls", {})
        if utls.get("enabled", False):
            params["fp"] = utls.get("fingerprint", "chrome")
        reality = tls.get("reality", {})
        if reality.get("enabled", False):
            params["security"] = "reality"
            if reality.get("public_key"):
                params["pbk"] = reality.get("public_key")
            if reality.get("short_id"):
                params["sid"] = reality.get("short_id")
    else:
        params["security"] = "none"

    transport = outbound.get("transport", {})
    t_type = transport.get("type", "tcp")
    params["type"] = t_type
    if t_type == "ws":
        params["path"] = transport.get("path", "/")
        host = transport.get("headers", {}).get("Host", "")
        if host:
            params["host"] = host
    elif t_type == "grpc":
        params["serviceName"] = transport.get("service_name", "")
    elif t_type in ("httpupgrade", "http"):
        params["path"] = transport.get("path", "/")
        if transport.get("host"):
            params["host"] = transport.get("host")

    query_str = urllib.parse.urlencode(params)
    return f"vless://{uuid}@{server}:{port}?{query_str}#{urllib.parse.quote(tag)}"


def outbound_to_trojan_url(outbound: dict) -> str:
    password = outbound.get("password", "")
    server = outbound.get("server", "")
    port = outbound.get("server_port", 0)
    tag = outbound.get("tag", "Trojan Node")
    
    tls = outbound.get("tls", {})
    tls_enabled = tls.get("enabled", False)
    
    params = {}
    if tls_enabled:
        params["security"] = "tls"
        if tls.get("server_name"):
            params["sni"] = tls.get("server_name")
        utls = tls.get("utls", {})
        if utls.get("enabled", False):
            params["fp"] = utls.get("fingerprint", "chrome")
        if tls.get("insecure"):
            params["allowInsecure"] = "1"
    else:
        params["security"] = "none"

    transport = outbound.get("transport", {})
    t_type = transport.get("type", "tcp")
    if t_type != "tcp":
        params["type"] = t_type
        if t_type == "ws":
            params["path"] = transport.get("path", "/")
            host = transport.get("headers", {}).get("Host", "")
            if host:
                params["host"] = host
        elif t_type == "grpc":
            params["serviceName"] = transport.get("service_name", "")

    query_str = urllib.parse.urlencode(params)
    return f"trojan://{urllib.parse.quote(password)}@{server}:{port}?{query_str}#{urllib.parse.quote(tag)}"


def outbound_to_ss_url(outbound: dict) -> str:
    import base64
    method = outbound.get("method", "")
    password = outbound.get("password", "")
    server = outbound.get("server", "")
    port = outbound.get("server_port", 0)
    tag = outbound.get("tag", "SS Node")
    
    user_info = f"{method}:{password}"
    user_info_b64 = base64.b64encode(user_info.encode("utf-8")).decode("utf-8")
    return f"ss://{user_info_b64}@{server}:{port}#{urllib.parse.quote(tag)}"


def outbound_to_hysteria2_url(outbound: dict) -> str:
    password = outbound.get("password", "")
    server = outbound.get("server", "")
    port = outbound.get("server_port", 0)
    tag = outbound.get("tag", "Hysteria2 Node")
    
    tls = outbound.get("tls", {})
    params = {}
    if tls.get("server_name"):
        params["sni"] = tls.get("server_name")
    if tls.get("insecure"):
        params["insecure"] = "1"
        
    obfs = outbound.get("obfs", {})
    if obfs and obfs.get("type"):
        params["obfs"] = obfs.get("type")
        if obfs.get("password"):
            params["obfs-password"] = obfs.get("password")
            
    query_str = urllib.parse.urlencode(params)
    return f"hysteria2://{urllib.parse.quote(password)}@{server}:{port}?{query_str}#{urllib.parse.quote(tag)}"


def outbound_to_tuic_url(outbound: dict) -> str:
    uuid = outbound.get("uuid", "")
    password = outbound.get("password", "")
    server = outbound.get("server", "")
    port = outbound.get("server_port", 0)
    tag = outbound.get("tag", "TUIC Node")
    
    tls = outbound.get("tls", {})
    params = {}
    if tls.get("server_name"):
        params["sni"] = tls.get("server_name")
    if tls.get("alpn"):
        params["alpn"] = ",".join(tls.get("alpn"))
    if tls.get("insecure"):
        params["insecure"] = "1"
    if outbound.get("congestion_control"):
        params["congestion_control"] = outbound.get("congestion_control")
        
    query_str = urllib.parse.urlencode(params)
    return f"tuic://{uuid}:{urllib.parse.quote(password)}@{server}:{port}?{query_str}#{urllib.parse.quote(tag)}"


def outbound_to_share_url(outbound: dict) -> str | None:
    t = outbound.get("type")
    try:
        if t == "vmess":
            return outbound_to_vmess_url(outbound)
        elif t == "vless":
            return outbound_to_vless_url(outbound)
        elif t == "trojan":
            return outbound_to_trojan_url(outbound)
        elif t == "shadowsocks":
            return outbound_to_ss_url(outbound)
        elif t == "hysteria2":
            return outbound_to_hysteria2_url(outbound)
        elif t == "tuic":
            return outbound_to_tuic_url(outbound)
    except Exception:
        pass
    return None


def outbound_to_clash_proxy(outbound: dict) -> dict | None:
    t = outbound.get("type")
    tag = outbound.get("tag", "Node")
    server = outbound.get("server", "")
    port = outbound.get("server_port", 0)
    if not server or not port:
        return None

    if t == "shadowsocks":
        return {
            "name": tag,
            "type": "ss",
            "server": server,
            "port": port,
            "cipher": outbound.get("method", ""),
            "password": outbound.get("password", "")
        }

    elif t == "vmess":
        tls = outbound.get("tls", {})
        tls_enabled = tls.get("enabled", False)
        transport = outbound.get("transport", {})
        net_type = transport.get("type", "tcp")
        
        proxy = {
            "name": tag,
            "type": "vmess",
            "server": server,
            "port": port,
            "uuid": outbound.get("uuid", ""),
            "alterId": outbound.get("alter_id", 0),
            "cipher": outbound.get("security", "auto"),
            "tls": tls_enabled
        }
        if tls_enabled and tls.get("server_name"):
            proxy["servername"] = tls.get("server_name")
            
        if net_type == "ws":
            proxy["network"] = "ws"
            proxy["ws-opts"] = {
                "path": transport.get("path", "/"),
                "headers": {"Host": transport.get("headers", {}).get("Host", "")} if transport.get("headers", {}).get("Host") else {}
            }
        elif net_type == "grpc":
            proxy["network"] = "grpc"
            proxy["grpc-opts"] = {
                "grpc-service-name": transport.get("service_name", "")
            }
        elif net_type in ("http", "h2"):
            proxy["network"] = "http"
            proxy["http-opts"] = {
                "path": transport.get("path", "/"),
                "headers": {"Host": transport.get("host", [""])[0]} if transport.get("host") else {}
            }
        return proxy

    elif t == "vless":
        tls = outbound.get("tls", {})
        tls_enabled = tls.get("enabled", False)
        transport = outbound.get("transport", {})
        net_type = transport.get("type", "tcp")
        
        proxy = {
            "name": tag,
            "type": "vless",
            "server": server,
            "port": port,
            "uuid": outbound.get("uuid", ""),
            "tls": tls_enabled
        }
        if tls_enabled:
            if tls.get("server_name"):
                proxy["servername"] = tls.get("server_name")
            reality = tls.get("reality", {})
            if reality.get("enabled", False):
                proxy["reality-opts"] = {
                    "public-key": reality.get("public_key", ""),
                    "short-id": reality.get("short_id", "")
                }
                
        if net_type == "ws":
            proxy["network"] = "ws"
            proxy["ws-opts"] = {
                "path": transport.get("path", "/"),
                "headers": {"Host": transport.get("headers", {}).get("Host", "")} if transport.get("headers", {}).get("Host") else {}
            }
        elif net_type == "grpc":
            proxy["network"] = "grpc"
            proxy["grpc-opts"] = {
                "grpc-service-name": transport.get("service_name", "")
            }
        return proxy

    elif t == "trojan":
        tls = outbound.get("tls", {})
        tls_enabled = tls.get("enabled", False)
        transport = outbound.get("transport", {})
        net_type = transport.get("type", "tcp")
        
        proxy = {
            "name": tag,
            "type": "trojan",
            "server": server,
            "port": port,
            "password": outbound.get("password", ""),
            "tls": tls_enabled
        }
        if tls_enabled and tls.get("server_name"):
            proxy["sni"] = tls.get("server_name")
            
        if net_type == "ws":
            proxy["network"] = "ws"
            proxy["ws-opts"] = {
                "path": transport.get("path", "/"),
                "headers": {"Host": transport.get("headers", {}).get("Host", "")} if transport.get("headers", {}).get("Host") else {}
            }
        elif net_type == "grpc":
            proxy["network"] = "grpc"
            proxy["grpc-opts"] = {
                "grpc-service-name": transport.get("service_name", "")
            }
        return proxy

    elif t == "hysteria2":
        tls = outbound.get("tls", {})
        obfs = outbound.get("obfs", {})
        
        proxy = {
            "name": tag,
            "type": "hysteria2",
            "server": server,
            "port": port,
            "password": outbound.get("password", "")
        }
        if tls.get("server_name"):
            proxy["sni"] = tls.get("server_name")
        if tls.get("insecure"):
            proxy["skip-cert-verify"] = True
        if obfs and obfs.get("type"):
            proxy["obfs"] = obfs.get("type")
            if obfs.get("password"):
                proxy["obfs-password"] = obfs.get("password")
        return proxy

    elif t == "tuic":
        tls = outbound.get("tls", {})
        
        proxy = {
            "name": tag,
            "type": "tuic",
            "server": server,
            "port": port,
            "uuid": outbound.get("uuid", ""),
            "password": outbound.get("password", "")
        }
        if tls.get("server_name"):
            proxy["sni"] = tls.get("server_name")
        if tls.get("alpn"):
            proxy["alpn"] = tls.get("alpn")
        if outbound.get("congestion_control"):
            proxy["congestion-controller"] = outbound.get("congestion_control")
        return proxy

    return None


def generate_clash_yaml(proxies: list[dict]) -> str:
    import yaml

    group_name = _SELECTOR_TAG
    config = {
        "port": 7890,
        "socks-port": 7891,
        "allow-lan": True,
        "mode": "rule",
        "log-level": "info",
        "external-controller": "127.0.0.1:9090",
        "proxies": proxies,
        "proxy-groups": [
            {
                "name": group_name,
                "type": "select",
                "proxies": [p.get("name", "") for p in proxies],
            }
        ],
        "rules": [
            "GEOIP,CN,DIRECT",
            f"MATCH,{group_name}",
        ],
    }
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
