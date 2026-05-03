"""节点分享链接的加密存储（磁盘上不落明文 urls.txt）。"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_VAULT_VERSION = 1


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    raw = kdf.derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def encrypt_vault_file(urls: list[str], password: str, dest: Path) -> None:
    """将节点 URL 列表加密写入 dest（JSON 包装 salt + ciphertext）。"""
    salt = os.urandom(16)
    key = _derive_key(password, salt)
    fernet = Fernet(key)
    payload = json.dumps({"v": _VAULT_VERSION, "urls": urls}, ensure_ascii=False).encode("utf-8")
    token = fernet.encrypt(payload)
    blob = {
        "v": 1,
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "cipher_b64": base64.b64encode(token).decode("ascii"),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(blob, indent=0), encoding="utf-8")


def decrypt_vault_file(src: Path, password: str) -> list[str]:
    """从加密文件解密出 URL 列表。"""
    blob = json.loads(src.read_text(encoding="utf-8"))
    salt = base64.b64decode(blob["salt_b64"])
    token = base64.b64decode(blob["cipher_b64"])
    key = _derive_key(password, salt)
    fernet = Fernet(key)
    raw = fernet.decrypt(token)
    data = json.loads(raw.decode("utf-8"))
    urls = data.get("urls")
    if not isinstance(urls, list) or not all(isinstance(x, str) for x in urls):
        raise ValueError("vault 内容格式无效")
    return urls
