#!/usr/bin/env python3
"""启动 Proxy Bridge：默认先起 Web 服务（静态前端 + /api），再起 sing-box；浏览器只调面板 API，由服务端访问 Clash API。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
from getpass import getpass
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.env import load_repo_dotenv
VAULT_PATH = DATA_DIR / "vault.enc"
URLS = ROOT / "urls.txt"
DEV_SECRET_FILE = DATA_DIR / ".dev_clash_secret"
# 未设置 CLASH_API_SECRET 且 config 中也没有时，写入 data/.dev_clash_secret 供本地测试（勿用于公网）
_DEFAULT_DEV_CLASH_SECRET = "local-dev-test-secret-not-for-production"
GITHUB_LATEST = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"


def platform_release_hint() -> tuple[str, str]:
    """返回 (资源文件名需包含的子串, 说明)。"""
    import platform

    mach = platform.machine().lower()
    if sys.platform == "win32":
        return "windows-amd64", "zip"
    if sys.platform == "linux":
        if mach in ("aarch64", "arm64"):
            return "linux-arm64", "tar.gz"
        return "linux-amd64", "tar.gz"
    if sys.platform == "darwin":
        if mach in ("arm64", "aarch64"):
            return "darwin-arm64", "tar.gz"
        return "darwin-amd64", "tar.gz"
    return "", ""


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "proxy-bridge-run/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pick_asset_url(assets: list, hint: str, kind: str) -> tuple[str, str] | None:
    candidates: list[tuple[str, str]] = []
    for a in assets:
        name = a.get("name") or ""
        if hint not in name:
            continue
        url = a.get("browser_download_url") or ""
        if not url:
            continue
        if kind == "zip" and name.endswith(".zip"):
            candidates.append((name, url))
        if kind == "tar.gz" and name.endswith(".tar.gz"):
            candidates.append((name, url))
    if not candidates:
        return None
    for name, url in candidates:
        if "legacy" not in name.lower():
            return name, url
    return candidates[0]


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "proxy-bridge-run_local/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())


def _extract_singbox(archive: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    exe_name = "sing-box.exe" if sys.platform == "win32" else "sing-box"

    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                if Path(name).name != exe_name:
                    continue
                target = dest_dir / exe_name
                target.write_bytes(zf.read(name))
                if exe_name == "sing-box":
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                return target

    if str(archive).endswith(".tar.gz") or archive.suffix == ".gz":
        import tarfile

        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                if Path(member.name).name != exe_name:
                    continue
                f = tf.extractfile(member)
                if not f:
                    continue
                target = dest_dir / exe_name
                target.write_bytes(f.read())
                if exe_name == "sing-box":
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                return target

    raise RuntimeError(f"无法在压缩包中找到 {exe_name}: {archive}")


def cmd_install_binary(force: bool) -> int:
    hint, kind = platform_release_hint()
    if not hint:
        print(f"错误: 不支持的平台 {sys.platform!r}，请手动从 GitHub Releases 下载。", file=sys.stderr)
        return 1

    bin_dir = ROOT / "bin"
    exe_name = "sing-box.exe" if sys.platform == "win32" else "sing-box"
    target = bin_dir / exe_name
    if target.is_file() and not force:
        print(f"已存在 {target}，跳过下载。需要覆盖请加 --force-install")
        return 0

    print("正在查询 sing-box 最新版本…")
    try:
        data = _http_get_json(GITHUB_LATEST)
    except Exception as e:
        print(f"错误: 无法访问 GitHub API: {e}", file=sys.stderr)
        return 1

    tag = data.get("tag_name", "?")
    assets = data.get("assets") or []
    picked = _pick_asset_url(assets, hint, kind)
    if not picked:
        print(
            f"错误: 在 {tag} 中未找到匹配 {hint!r} 的发布资源，请手动下载: "
            "https://github.com/SagerNet/sing-box/releases",
            file=sys.stderr,
        )
        return 1

    asset_name, url = picked
    print(f"将下载: {asset_name}")

    try:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            arc = td_path / asset_name
            print("正在下载…")
            _download(url, arc)
            print("正在解压到 bin/ …")
            out = _extract_singbox(arc, bin_dir)
    except Exception as e:
        print(f"错误: 安装失败: {e}", file=sys.stderr)
        return 1

    print(f"已安装: {out}")
    print("接下来执行: python run.py（默认启动 sing-box + 面板）或加 --proxy-only 仅代理")
    return 0


def http_listen_port(cfg: Path) -> int:
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        for ib in data.get("inbounds") or []:
            if ib.get("type") == "http":
                return int(ib.get("listen_port") or 2080)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return 2080


def load_clash_api_from_config(cfg: Path) -> tuple[str | None, int]:
    """从 config.json 读取 Clash API secret 与监听端口（用于本机连接）。"""
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"无法读取配置: {e}") from e
    exp = data.get("experimental") or {}
    ca = exp.get("clash_api") or {}
    secret = (ca.get("secret") or "").strip() or None
    ctrl = (ca.get("external_controller") or "127.0.0.1:9020").strip()
    _host, _, port_s = ctrl.rpartition(":")
    if not port_s:
        port_s = "9020"
    try:
        port = int(port_s)
    except ValueError:
        port = 9020
    return secret, port


def _sanitize_embedded_clash_web(cfg: Path) -> None:
    """写入磁盘上的 config 若仍含 sing-box 内置网页字段则去掉（本仓库仅用自建面板）。"""
    from core.build_config import sanitize_config_file_if_needed

    if sanitize_config_file_if_needed(cfg):
        print(
            "已从 config.json 移除 experimental.clash_api.external_ui（仅使用自建面板）。",
            flush=True,
        )


def wait_tcp_port(host: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def prepare_vault_mode_config(cfg: Path) -> int:
    """--vault：有 vault 则解密生成 config；无 vault 则写占位 config 供面板导入。"""
    from core.build_config import (
        NodeBuildError,
        bootstrap_placeholder_config,
        build_singbox_config,
        write_singbox_config,
    )
    from core.vault_store import decrypt_vault_file

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if VAULT_PATH.is_file():
        strict = os.environ.get("VAULT_STRICT", "").strip().lower() in ("1", "true", "yes", "on")
        prompt = os.environ.get("VAULT_PROMPT", "").strip().lower() in ("1", "true", "yes", "on")

        def _ensure_placeholder_or_keep_existing(reason: str) -> int:
            """尽量不中断启动：优先保留现有 config；否则写占位 config。"""
            if cfg.is_file():
                try:
                    sec, _ = load_clash_api_from_config(cfg)
                except ValueError:
                    sec = None
                if sec:
                    print(f"提示: {reason}；将直接使用现有 {cfg} 继续启动。", file=sys.stderr)
                    return 0
            try:
                boot = bootstrap_placeholder_config()
            except ValueError as e:
                print(f"错误: {reason}；且无法生成占位配置: {e}", file=sys.stderr)
                print(
                    "请先设置环境变量，例如（PowerShell）:\n"
                    "  $env:SINGBOX_WITH_PANEL='1'\n"
                    "  $env:CLASH_API_SECRET='你的长随机串'",
                    file=sys.stderr,
                )
                return 1
            write_singbox_config(boot, cfg)
            print(
                f"提示: {reason}；已写入占位 {cfg} 并继续启动。\n"
                "请打开面板「导入节点」重新导入/订阅导入以恢复节点。",
                flush=True,
            )
            return 0

        pw = os.environ.get("VAULT_PASSWORD", "").strip()
        if not pw and prompt:
            pw = getpass("节点库密码: ").strip()
        if not pw:
            if strict:
                print("错误: 需要环境变量 VAULT_PASSWORD（或设置 VAULT_PROMPT=1 以交互输入）。", file=sys.stderr)
                return 1
            return _ensure_placeholder_or_keep_existing("未提供节点库密码（vault 将不会在启动时解密）")
        try:
            urls = decrypt_vault_file(VAULT_PATH, pw)
        except Exception as e:
            if strict:
                print(f"错误: 解密 data/vault.enc 失败: {e}", file=sys.stderr)
                return 1
            return _ensure_placeholder_or_keep_existing(f"解密 data/vault.enc 失败（原因: {e}）")
        try:
            blob = build_singbox_config(urls)
        except (NodeBuildError, ValueError) as e:
            if strict:
                print(f"错误: {e}", file=sys.stderr)
                return 1
            return _ensure_placeholder_or_keep_existing(f"无法根据 vault 生成配置（原因: {e}）")
        write_singbox_config(blob, cfg)
        print(f"已从加密节点库生成 {cfg}（{len(urls)} 条节点）。")
        return 0

    if cfg.is_file():
        try:
            sec, _ = load_clash_api_from_config(cfg)
        except ValueError:
            sec = None
        if sec:
            print("未找到 data/vault.enc，保留现有 config.json（已检测到 Clash API）。")
            return 0

    try:
        boot = bootstrap_placeholder_config()
    except ValueError as e:
        print(f"错误: 无法生成占位配置: {e}", file=sys.stderr)
        print(
            "请先设置环境变量，例如（PowerShell）:\n"
            "  $env:SINGBOX_WITH_PANEL='1'\n"
            "  $env:CLASH_API_SECRET='你的长随机串'",
            file=sys.stderr,
        )
        return 1
    write_singbox_config(boot, cfg)
    print(
        "尚无 data/vault.enc，已写入占位 config.json。\n"
        "请打开面板「导入节点」粘贴分享链接；Clash 密钥若与当前环境不一致，请在表单中填写 clash_secret。",
        flush=True,
    )
    return 0


def run_singbox_foreground(binary: Path, cfg: Path) -> int:
    """前台运行 sing-box；Ctrl+C 时结束子进程并干净退出（避免长 KeyboardInterrupt 堆栈）。"""
    proc = subprocess.Popen(
        [str(binary), "run", "-c", str(cfg)],
        cwd=ROOT,
    )
    try:
        return int(proc.wait())
    except KeyboardInterrupt:
        print("\n正在停止 sing-box…", file=sys.stderr)
        _terminate(proc)
        return 130


def cmd_dev_with_panel(
    binary: Path,
    cfg: Path,
    panel_host: str,
    panel_port: int,
    *,
    watch_config: bool = False,
) -> int:
    _sanitize_embedded_clash_web(cfg)
    try:
        secret, clash_port = load_clash_api_from_config(cfg)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    if not secret:
        print(
            "错误: config.json 中未启用 Clash API 或缺少 secret。\n"
            "使用 --vault 首次启动前请设置环境变量，例如（PowerShell）:\n"
            "  $env:SINGBOX_WITH_PANEL='1'\n"
            "  $env:CLASH_API_SECRET='你的长随机串'\n"
            "再执行: python run.py（默认已含面板与库准备）\n"
            "或加 --proxy-only --with-panel；或在面板「导入节点」中填写 clash_secret。\n"
            "并确保已安装: pip install -r panel/requirements.txt",
            file=sys.stderr,
        )
        return 1

    connect_host = "127.0.0.1"
    clash_url = f"http://{connect_host}:{clash_port}"
    env = os.environ.copy()
    env["CLASH_API_URL"] = clash_url
    env["CLASH_API_SECRET"] = secret
    env["PYTHONPATH"] = str(ROOT)
    panel_dir = ROOT / "panel"

    print(f"网枢 NetHub 正在启动（{panel_host}:{panel_port}）…")
    u = os.environ.get("PANEL_ADMIN_USER", "").strip()
    pw = os.environ.get("PANEL_ADMIN_PASSWORD", "").strip()
    if u and pw:
        print("面板: 已读取 PANEL_ADMIN_USER / PANEL_ADMIN_PASSWORD，访问前需登录。")
    else:
        print(
            "面板: 未同时设置 PANEL_ADMIN_USER 与 PANEL_ADMIN_PASSWORD，"
            "浏览器将只显示配置说明页，直到在 .env 中填写并重启。",
        )
    panel = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            panel_host,
            "--port",
            str(panel_port),
            "--log-level",
            "warning",
        ],
        cwd=str(panel_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    print(f"内核 sing-box 启动中…")
    sb = subprocess.Popen(
        [str(binary), "run", "-c", str(cfg)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sb_list: list[subprocess.Popen | None] = [sb]
    stop_watch = threading.Event()

    if not wait_tcp_port(connect_host, clash_port, timeout=45.0):
        print(f"错误: 在超时内未能连上 Clash API 端口 {connect_host}:{clash_port}。", file=sys.stderr)
        _terminate(panel)
        _terminate(sb)
        return 1

    def _clash_port_live() -> int:
        try:
            _, p = load_clash_api_from_config(cfg)
            return p
        except (ValueError, OSError, json.JSONDecodeError):
            return clash_port

    def _restart_singbox() -> None:
        _terminate(sb_list[0])
        sb_list[0] = subprocess.Popen(
            [str(binary), "run", "-c", str(cfg)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cp = _clash_port_live()
        if not wait_tcp_port(connect_host, cp, timeout=45.0):
            print("警告: 重启 sing-box 后 Clash API 暂未就绪。", file=sys.stderr)

    def _watcher() -> None:
        try:
            last = cfg.stat().st_mtime_ns if cfg.is_file() else 0
        except OSError:
            last = 0
        while not stop_watch.is_set():
            time.sleep(1.2)
            try:
                cur = cfg.stat().st_mtime_ns
            except OSError:
                continue
            if cur != last:
                last = cur
                print("\n[网枢] 检测到配置已更新，正在重启内核…", flush=True)
                _restart_singbox()

    watcher_thread: threading.Thread | None = None
    if watch_config:
        watcher_thread = threading.Thread(target=_watcher, daemon=True)
        watcher_thread.start()

    http_port = http_listen_port(cfg)
    print()
    print("------------------------------------------------")
    print(f"前端 + API: http://{panel_host}:{panel_port}  （浏览器请只打开此地址）")
    print(f"代理 HTTP:  http://127.0.0.1:{http_port}  （由 sing-box 提供，与上项独立）")
    if watch_config:
        print("已启用 config 热更新：在面板导入节点后会自动重启 sing-box。")
    print("按 Ctrl+C 可同时停止 Web 服务与 sing-box。")
    print("------------------------------------------------")
    print()

    rc = 0
    try:
        while True:
            if sb_list[0] and sb_list[0].poll() is not None:
                rc = sb_list[0].returncode or 0
                print("sing-box 已退出。", file=sys.stderr)
                break
            if panel.poll() is not None:
                rc = panel.returncode or 0
                print("面板进程已退出。", file=sys.stderr)
                break
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n正在停止…")
        rc = 130
    finally:
        stop_watch.set()
        if watcher_thread is not None:
            watcher_thread.join(timeout=2.5)
        _terminate(panel)
        _terminate(sb_list[0])

    return rc


def resolve_singbox() -> Path | None:
    env = os.environ.get("SINGBOX_BIN", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_file():
            return p
    name = "sing-box.exe" if sys.platform == "win32" else "sing-box"
    bundled = ROOT / "bin" / name
    if bundled.is_file():
        return bundled
    w = shutil.which("sing-box")
    if w:
        return Path(w).resolve()
    return None


def _pip_install_full_app() -> int:
    name = "requirements.txt"
    path = ROOT / name
    if not path.is_file():
        return 0
    print(f"[网枢] pip install -r {name} …")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(path)],
        cwd=ROOT,
    )
    if r.returncode != 0:
        print(
            f"[网枢] 错误: pip 安装失败。可加 --no-pip 并手动安装依赖。",
            file=sys.stderr,
        )
        return 1
    return 0


def _ensure_clash_for_generation(cfg: Path) -> None:
    if os.environ.get("CLASH_API_SECRET", "").strip():
        os.environ.setdefault("SINGBOX_WITH_PANEL", "1")
        return
    if cfg.is_file():
        try:
            sec, _ = load_clash_api_from_config(cfg)
            if sec:
                os.environ["CLASH_API_SECRET"] = sec
                os.environ.setdefault("SINGBOX_WITH_PANEL", "1")
                return
        except (ValueError, OSError):
            pass
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DEV_SECRET_FILE.is_file():
        s = DEV_SECRET_FILE.read_text(encoding="utf-8").strip()
    else:
        s = (os.environ.get("DEV_CLASH_TEST_SECRET") or _DEFAULT_DEV_CLASH_SECRET).strip()
        if not s:
            s = _DEFAULT_DEV_CLASH_SECRET
        DEV_SECRET_FILE.write_text(s, encoding="utf-8")
        print(
            f"[网枢] 已写入开发用 Clash 密钥文件 {DEV_SECRET_FILE.relative_to(ROOT)}（测试默认值，可用环境变量 DEV_CLASH_TEST_SECRET 覆盖）",
            flush=True,
        )
        print(f"[网枢] 当前 CLASH_API_SECRET（请勿提交到 Git）: {s}", flush=True)
    os.environ["CLASH_API_SECRET"] = s
    os.environ.setdefault("SINGBOX_WITH_PANEL", "1")


def _maybe_generate_from_urls(cfg: Path) -> None:
    vault = DATA_DIR / "vault.enc"
    if cfg.is_file() or vault.is_file() or not URLS.is_file():
        return
    from core.build_config import NodeBuildError, build_singbox_config, load_urls_file, write_singbox_config

    try:
        urls = load_urls_file(URLS)
    except OSError:
        return
    if not urls:
        return
    _ensure_clash_for_generation(cfg)
    try:
        write_singbox_config(build_singbox_config(urls), cfg)
        print(f"[网枢] 已从 {URLS.name} 生成 {cfg.name}")
    except NodeBuildError as e:
        print(f"[网枢] urls.txt 无法生成配置，将尝试占位/库流程: {e}")


def ensure_docker_engine() -> tuple[bool, str]:
    docker = shutil.which("docker")
    if not docker:
        return False, ""
    r = subprocess.run(
        [docker, "info"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr or r.stdout or "").strip()


def docker_engine_hint(stderr: str) -> str:
    s = stderr.lower().replace("\\", "/")
    if (
        "dockerdesktoplinuxengine" in s
        or "dockerdesktop" in s
        or "docker_desktop" in s
        or "npipe://" in s
    ):
        return (
            "请先打开 Docker Desktop，等托盘图标就绪后再运行。\n"
            "若已打开仍失败：在 Docker Desktop 中执行 Troubleshoot → Restart Docker Desktop，"
            "并确认设置里已启用「Use the WSL 2 based engine」（如适用）。"
        )
    if "cannot connect" in s or "connection refused" in s or "pipe" in s:
        return (
            "无法连接 Docker 引擎：Windows 请打开 Docker Desktop；"
            "Linux 请检查 docker 服务是否运行（例如 systemctl status docker）。"
        )
    return "无法连接 Docker 引擎，请查看下方 docker 输出或系统日志。"


def pick_compose_cmd() -> list[str] | None:
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    docker = shutil.which("docker")
    if not docker:
        return None
    r = subprocess.run(
        [docker, "compose", "version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        return [docker, "compose"]
    return None


def cmd_docker_up() -> int:
    print("正在通过 Docker Compose 启动 Proxy Bridge…")
    if not shutil.which("docker"):
        print("错误: 未检测到 docker。", file=sys.stderr)
        print("提示: 本机开发可执行: python run.py", file=sys.stderr)
        return 1
    ok, docker_err = ensure_docker_engine()
    if not ok:
        print("错误: Docker 引擎当前不可用。", file=sys.stderr)
        print(docker_engine_hint(docker_err), file=sys.stderr)
        if docker_err:
            print("--- docker info ---", file=sys.stderr)
            print(docker_err[:2000], file=sys.stderr)
        print("提示: 本机开发可执行: python run.py", file=sys.stderr)
        return 1
    cfg = ROOT / "config.json"
    if not cfg.is_file():
        print(f"错误: 未找到配置文件 {cfg}", file=sys.stderr)
        return 1
    cmd_prefix = pick_compose_cmd()
    if not cmd_prefix:
        print("错误: 未找到 docker-compose 或可用的 docker compose 插件。", file=sys.stderr)
        return 1
    print("正在拉起容器…")
    proc = subprocess.run([*cmd_prefix, "up", "-d"], cwd=ROOT)
    if proc.returncode != 0:
        print("启动失败，请检查 Docker 日志。", file=sys.stderr)
        return proc.returncode
    print()
    print("------------------------------------------------")
    print("Docker 栈已启动。")
    print("HTTP 代理: 2080")
    print("测试: curl -x http://127.0.0.1:2080 https://www.google.com -I")
    print("可选 Web 面板: 见 README 中 docker compose --profile panel")
    print("------------------------------------------------")
    return 0


def cmd_app_stack(
    cfg: Path,
    panel_host: str,
    panel_port: int,
    *,
    no_pip: bool,
    no_binary_download: bool,
) -> int:
    os.chdir(ROOT)
    if not no_pip:
        if _pip_install_full_app() != 0:
            return 1
    _maybe_generate_from_urls(cfg)
    # 占位 config 依赖环境里的 SINGBOX_WITH_PANEL + CLASH_API_SECRET 才会写入 Clash API；
    # 若磁盘上已有无 Clash 的旧 config，也必须先补环境，否则 prepare 会写出「无 secret」的占位。
    _ensure_clash_for_generation(cfg)
    pr = prepare_vault_mode_config(cfg)
    if pr != 0:
        return pr
    binary = resolve_singbox()
    if binary is None and not no_binary_download:
        print("[网枢] 未找到 sing-box，正在下载到 bin/ …")
        if cmd_install_binary(False) != 0:
            return 1
        binary = resolve_singbox()
    if binary is None:
        print(
            "错误: 未找到 sing-box。请执行: python run.py --install-binary\n"
            "或设置 SINGBOX_BIN / 放入 bin/ 后再运行 python run.py",
            file=sys.stderr,
        )
        return 1
    print("[网枢] 启动 Web（前端+API）与 sing-box；config 更新后将热重启 sing-box。")
    return cmd_dev_with_panel(
        binary,
        cfg,
        panel_host,
        panel_port,
        watch_config=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="启动 Proxy Bridge：默认本机 sing-box + Web 面板；--proxy-only 仅代理；--docker 使用 Compose。"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=ROOT / "config.json",
        help="sing-box 配置文件路径（默认项目根目录 config.json）",
    )
    parser.add_argument(
        "--bin",
        type=Path,
        default=None,
        help="sing-box 可执行文件路径（默认先试环境变量 SINGBOX_BIN，再试 ./bin/，最后 PATH）",
    )
    parser.add_argument(
        "--install-binary",
        action="store_true",
        help="从 GitHub 下载当前平台的 sing-box 到 bin/ 后退出（网络需能访问 github.com）",
    )
    parser.add_argument(
        "--force-install",
        action="store_true",
        help="与 --install-binary 合用：覆盖已存在的 bin/ 下可执行文件",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="使用 Docker Compose 启动（docker compose up -d），不运行本机 sing-box",
    )
    parser.add_argument(
        "--proxy-only",
        action="store_true",
        help="仅前台运行 sing-box，不安装依赖、不启动面板（可与 --with-panel / --vault 组合）",
    )
    parser.add_argument(
        "--no-pip",
        action="store_true",
        help="默认全栈启动时跳过 pip 安装（依赖已就绪时使用）",
    )
    parser.add_argument(
        "--no-binary-download",
        action="store_true",
        help="默认全栈启动时若未找到 sing-box 则报错、不自动从 GitHub 下载",
    )
    parser.add_argument(
        "--with-panel",
        action="store_true",
        help="与 --proxy-only 合用：仅本机进程时同时启动面板；默认全栈已含面板，无需再加",
    )
    parser.add_argument(
        "--panel-host",
        default="127.0.0.1",
        help="面板监听地址（默认 127.0.0.1）",
    )
    parser.add_argument(
        "--panel-port",
        type=int,
        default=8080,
        help="面板监听端口（默认 8080）",
    )
    parser.add_argument(
        "--vault",
        action="store_true",
        help="与 --proxy-only 合用时：先按加密库/占位准备 config，且面板模式下热更新 config；"
        "默认全栈启动已等价于始终准备库与热更新，可不写本参数",
    )
    args = parser.parse_args()

    load_repo_dotenv(ROOT)

    if args.install_binary:
        return cmd_install_binary(args.force_install)

    if args.docker:
        return cmd_docker_up()

    cfg = args.config.expanduser().resolve()

    if args.proxy_only:
        if args.vault:
            args.with_panel = True
            pr = prepare_vault_mode_config(cfg)
            if pr != 0:
                return pr
        elif not cfg.is_file():
            print(f"错误: 未找到配置文件 {cfg}，请先执行: python scripts/generate_singbox.py", file=sys.stderr)
            return 1

        if args.bin is not None:
            p = args.bin.expanduser().resolve()
            if not p.is_file():
                print(f"错误: --bin 指定的文件不存在: {p}", file=sys.stderr)
                return 1
            binary: Path | None = p
        else:
            binary = resolve_singbox()
        if not binary:
            print(
                "错误: 未找到 sing-box 可执行文件。\n"
                "  一键安装（需能访问 GitHub）: python run.py --install-binary\n"
                "  或手动: 将 sing-box 放到 bin/、加入 PATH，或设置 SINGBOX_BIN / --bin",
                file=sys.stderr,
            )
            return 1

        if args.with_panel:
            return cmd_dev_with_panel(
                binary,
                cfg,
                args.panel_host,
                args.panel_port,
                watch_config=bool(args.vault),
            )

        _sanitize_embedded_clash_web(cfg)
        print(f"使用: {binary}")
        print(f"配置: {cfg}")
        print("前台运行中，按 Ctrl+C 停止。\n")
        return run_singbox_foreground(binary, cfg)

    return cmd_app_stack(
        cfg,
        args.panel_host,
        args.panel_port,
        no_pip=args.no_pip,
        no_binary_download=args.no_binary_download,
    )


if __name__ == "__main__":
    raise SystemExit(main())
