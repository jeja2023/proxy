#!/usr/bin/env python3
"""NetHub 服务器部署诊断脚本。

本脚本不打印敏感密钥信息。主要用于排查最常见的云服务器部署失败问题：
宿主机代理端口与 sing-box 容器内监听端口配置不一致。
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> dict[str, str]:
    # 加载环境变量配置文件
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


def find_http_inbound(config: dict) -> dict:
    # 查找配置中的 HTTP 入站
    for inbound in config.get("inbounds") or []:
        if inbound.get("type") == "http":
            return inbound
    return {}


def mask(value: str) -> str:
    # 掩码敏感信息
    return "<已设置>" if value else "<未设置>"


def main() -> int:
    env = load_env(ROOT / ".env")
    config_path = ROOT / "config.json"
    compose_path = ROOT / "docker-compose.yml"

    print("NetHub 服务器部署诊断")
    print("=" * 38)

    if not config_path.is_file():
        print("[失败] config.json 文件缺失。")
        return 1

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[失败] config.json 不是合法的 JSON 格式: {exc}")
        return 1

    http_inbound = find_http_inbound(config)
    if not http_inbound:
        print("[失败] 在 config.json 中未找到 HTTP 入站。")
        return 1

    public_port = env.get("SINGBOX_HTTP_PORT") or "2080"
    panel_port = env.get("PANEL_PORT") or "8080"
    listen = http_inbound.get("listen") or ""
    internal_port = str(http_inbound.get("listen_port") or "")
    has_auth = bool(http_inbound.get("users"))
    controller = ((config.get("experimental") or {}).get("clash_api") or {}).get("external_controller", "")

    print(f"[信息] .env 文件中的公网代理端口: {public_port}")
    print(f"[信息] sing-box HTTP 入站监听: {listen}:{internal_port}")
    print(f"[信息] config.json 中的代理身份验证: {'已启用' if has_auth else '已禁用'}")
    print(f"[信息] Clash API 控制器: {controller or '<缺失>'}")
    print(f"[信息] PANEL_ADMIN_PASSWORD: {mask(env.get('PANEL_ADMIN_PASSWORD', ''))}")
    print(f"[信息] CLASH_API_SECRET: {mask(env.get('CLASH_API_SECRET', ''))}")

    if listen in ("127.0.0.1", "localhost", "::1"):
        print("[警告] HTTP 代理仅监听本地回环地址，远程客户端将无法连接。")
    elif listen in ("0.0.0.0", "::", ""):
        print("[正常] HTTP 代理监听在可远程访问的地址。")
    else:
        print("[警告] HTTP 代理监听在特定地址，请确认其为服务器的公网/私网网卡。")

    compose = compose_path.read_text(encoding="utf-8", errors="replace") if compose_path.is_file() else ""
    old_mapping = "${SINGBOX_HTTP_PORT:-2080}:${SINGBOX_HTTP_PORT:-2080}"
    fixed_mapping = "${SINGBOX_HTTP_PORT:-2080}:2080"

    if old_mapping in compose:
        print("[失败] docker-compose.yml 将公网端口映射为了相同的容器端口。")
        print("       如果 .env 使用了自定义的 SINGBOX_HTTP_PORT，容器内部可能没有监听此端口。")
        return 1

    if fixed_mapping in compose:
        if internal_port == "2080":
            print("[正常] Docker 已成功将自定义公网端口映射到容器内 2080 端口。")
        else:
            print("[警告] Compose 映射到容器 2080 端口，但 config.json 监听的却是另一个端口。")
            print("       请重新为 2080 端口生成 config.json，或者调整 compose 的容器端口。")
    else:
        print("[警告] 无法在 docker-compose.yml 中识别代理端口映射。")

    print()
    print("建议执行的服务器检查命令:")
    print(f"  docker compose --profile panel up -d --force-recreate")
    print(f"  curl -x http://127.0.0.1:{public_port} http://example.com -I")
    print(f"  ss -lntp | grep -E '(:{public_port}|:{panel_port})'")
    print(f"  请在防火墙/安全组中放行以下 TCP 端口: {public_port}, {panel_port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
