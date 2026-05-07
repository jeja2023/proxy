#!/usr/bin/env python3
"""网枢 NetHub 服务器部署诊断工具。

本脚本用于在不打印任何敏感配置明文的前提下，检测云端部署常见误区：
端口映射错误、仅本地监听、HTTPS 代理下 TLS 证书或密钥配置缺失等。
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def http_inbounds(config: dict) -> list[dict]:
    return [ib for ib in config.get("inbounds") or [] if ib.get("type") == "http"]


def inbound_by_tag(config: dict, tag: str) -> dict:
    for inbound in http_inbounds(config):
        if inbound.get("tag") == tag:
            return inbound
    return {}


def mask(value: str) -> str:
    return "<已设置>" if value else "<未设置>"


def enabled(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def check_listener(name: str, inbound: dict) -> None:
    listen = inbound.get("listen") or ""
    port = inbound.get("listen_port") or "<缺失>"
    print(f"[信息] {name} 入站监听: {listen}:{port}")
    if listen in ("127.0.0.1", "localhost", "::1"):
        print(f"[警告] {name} 仅监听本地回环地址，远程客户端将无法连接。")
    elif listen in ("0.0.0.0", "::", ""):
        print(f"[正常] {name} 监听在可远程访问的地址。")
    else:
        print(f"[警告] {name} 监听在特定地址，请确认其为服务器的公网/私网网卡。")


def main() -> int:
    env = load_env(ROOT / ".env")
    config_path = ROOT / "config.json"
    compose_path = ROOT / "docker-compose.yml"

    print("网枢 NetHub 服务器部署诊断")
    print("=" * 38)

    if not config_path.is_file():
        print("[失败] config.json 文件缺失。")
        return 1

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[失败] config.json 不是合法的 JSON 格式: {exc}")
        return 1

    http_inbound = inbound_by_tag(config, "http-in") or (http_inbounds(config)[0] if http_inbounds(config) else {})
    if not http_inbound:
        print("[失败] 在 config.json 中未找到 HTTP 代理入站。")
        return 1

    public_http_port = env.get("SINGBOX_HTTP_PORT") or "2080"
    panel_port = env.get("PANEL_PORT") or "8080"
    controller = ((config.get("experimental") or {}).get("clash_api") or {}).get("external_controller", "")

    print(f"[信息] .env 文件中的公网 HTTP 代理端口: {public_http_port}")
    print(f"[信息] .env 文件中的面板公网访问端口: {panel_port}")
    print(f"[信息] config.json 中的代理身份验证: {'已启用' if http_inbound.get('users') else '已禁用'}")
    print(f"[信息] Clash API 控制器: {controller or '<缺失>'}")
    print(f"[信息] PANEL_ADMIN_PASSWORD: {mask(env.get('PANEL_ADMIN_PASSWORD', ''))}")
    print(f"[信息] CLASH_API_SECRET: {mask(env.get('CLASH_API_SECRET', ''))}")
    check_listener("HTTP 代理", http_inbound)

    compose = compose_path.read_text(encoding="utf-8", errors="replace") if compose_path.is_file() else ""
    if "${SINGBOX_HTTP_PORT:-2080}:${SINGBOX_HTTP_PORT:-2080}" in compose:
        print("[失败] docker-compose.yml 将公网端口映射为了相同的容器端口。")
        print("       如果 .env 使用了自定义的宿主机公网端口，容器内部可能没有监听此端口。")
        return 1
    if "${SINGBOX_HTTP_PORT:-2080}:2080" in compose and str(http_inbound.get("listen_port")) == "2080":
        print("[正常] Docker 已成功将自定义公网端口映射到容器内 2080 端口。")
    else:
        print("[警告] 无法在 docker-compose.yml 中确认 HTTP 代理端口映射。")

    https_enabled = enabled(env.get("SINGBOX_HTTPS_PROXY_ENABLED", ""))
    https_public_port = env.get("SINGBOX_HTTPS_PROXY_PUBLIC_PORT") or "2443"
    https_inbound = inbound_by_tag(config, env.get("SINGBOX_HTTPS_PROXY_TAG") or "https-in")
    print(f"[信息] .env 文件中是否启用了 HTTPS 代理: {'是' if https_enabled else '否'}")

    if https_enabled:
        if not https_inbound:
            print("[失败] .env 中启用了 HTTPS 代理，但在 config.json 中未找到对应入站。")
            print("       请在面板或 Vault 重新生成并应用配置。")
            return 1
        check_listener("HTTPS 代理", https_inbound)
        tls = https_inbound.get("tls") or {}
        cert_path = tls.get("certificate_path") or env.get("SINGBOX_TLS_CERT_PATH", "")
        key_path = tls.get("key_path") or env.get("SINGBOX_TLS_KEY_PATH", "")
        print(f"[信息] HTTPS 代理公网宿主机端口: {https_public_port}")
        print(f"[信息] TLS 证书路径: {cert_path or '<缺失>'}")
        print(f"[信息] TLS 密钥路径: {key_path or '<缺失>'}")
        if not cert_path or not key_path:
            print("[失败] HTTPS 代理需要同时配置证书和私钥路径。")
            return 1
        if "${SINGBOX_HTTPS_PROXY_PUBLIC_PORT:-2443}:2443" in compose:
            print("[正常] Docker 已成功映射并放行 HTTPS 代理端口。")
        else:
            print("[警告] 无法在 docker-compose.yml 中确认 HTTPS 代理端口映射。")

    firewall_ports = [public_http_port, panel_port]
    if https_enabled:
        firewall_ports.append(https_public_port)

    print()
    print("建议执行的服务器检查命令:")
    print("  docker compose --profile panel up -d --force-recreate")
    print(f"  curl -x http://127.0.0.1:{public_http_port} http://example.com -I")
    if https_enabled:
        print(f"  curl -x https://127.0.0.1:{https_public_port} http://example.com -I")
    print(f"  ss -lntp | grep -E '(:{'|:'.join(firewall_ports)})'")
    print(f"  请在防火墙/安全组中放行以下 TCP 端口: {', '.join(firewall_ports)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
