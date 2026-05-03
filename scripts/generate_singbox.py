import argparse
import os
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_URLS = _REPO_ROOT / "urls.txt"
_DEFAULT_CONFIG = _REPO_ROOT / "config.json"


def main() -> None:
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from core.build_config import (
        NodeBuildError,
        build_singbox_config,
        load_urls_file,
        write_singbox_config,
    )
    from core.vault_store import encrypt_vault_file

    parser = argparse.ArgumentParser(description="将分享链接转换为 sing-box config.json")
    parser.add_argument(
        "--urls",
        type=Path,
        default=_DEFAULT_URLS,
        help=f"节点列表文件路径（默认: {_DEFAULT_URLS.name}）",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"输出 config 路径（默认: {_DEFAULT_CONFIG.name}）",
    )
    parser.add_argument(
        "--from-vault",
        action="store_true",
        help="从 data/vault.enc 解密读取节点（需环境变量 VAULT_PASSWORD），不再读取 urls 文件",
    )
    parser.add_argument(
        "--vault-path",
        type=Path,
        default=_REPO_ROOT / "data" / "vault.enc",
        help="与 --from-vault 合用：加密库路径",
    )
    parser.add_argument(
        "--save-vault",
        action="store_true",
        help="将本次从 urls 文件读取的节点同时加密写入 data/vault.enc（需 VAULT_PASSWORD）",
    )
    args = parser.parse_args()

    if args.from_vault:
        pw = os.environ.get("VAULT_PASSWORD", "").strip()
        if not pw:
            print("错误: --from-vault 需要环境变量 VAULT_PASSWORD", file=sys.stderr)
            sys.exit(1)
        vp = args.vault_path.resolve()
        if not vp.is_file():
            print(f"错误: 未找到节点库 {vp}", file=sys.stderr)
            sys.exit(1)
        from core.vault_store import decrypt_vault_file

        try:
            urls = decrypt_vault_file(vp, pw)
        except Exception as e:
            print(f"错误: 解密失败: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        path = args.urls.resolve()
        if not path.is_file():
            print(
                f"错误: 未找到节点列表文件: {path}\n"
                "请复制 urls.txt.example 为 urls.txt，或使用 --urls / --from-vault。",
                file=sys.stderr,
            )
            sys.exit(1)
        urls = load_urls_file(path)
        if not urls:
            print("错误: 节点文件中没有有效链接。", file=sys.stderr)
            sys.exit(1)

        if args.save_vault:
            pw = os.environ.get("VAULT_PASSWORD", "").strip()
            if not pw:
                print("错误: --save-vault 需要环境变量 VAULT_PASSWORD", file=sys.stderr)
                sys.exit(1)
            out_v = args.vault_path.resolve()
            encrypt_vault_file(urls, pw, out_v)
            print(f"已写入加密节点库: {out_v}")

    try:
        config = build_singbox_config(urls)
    except NodeBuildError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    out_path = args.output.resolve()
    write_singbox_config(config, out_path)
    sel = next((o for o in config.get("outbounds", []) if o.get("type") == "selector"), {})
    tags = sel.get("outbounds") or []
    default_tag = sel.get("default") or "?"
    print(f"已写入: {out_path}（共 {len(tags)} 个节点，默认: {default_tag}）")


if __name__ == "__main__":
    main()
