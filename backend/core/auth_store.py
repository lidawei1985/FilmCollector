# -*- coding: utf-8 -*-
"""
账号凭证自动记忆与自动填充。

职责：
- 把「平台 / 用户名 / 仓库名 / Access Token」安全地存在本机。
- 下次打开工具时自动回填表单（Token 仅显示脱敏后几位）。
- 一键部署时直接读取，无需用户再次输入。

安全策略（单机自用，兼顾小白与隐私）：
- Token 用 Fernet（AES-128 + HMAC）加密后再落盘，明文绝不写入文件。
- 加密主密钥优先存系统密钥环（Windows Credential Manager / macOS Keychain /
  Linux SecretService），不可用时退化为本机密钥文件（权限 600）。
- 用户名/仓库名不敏感，明文存于 config（便于自动填充）。
"""
import os
import base64

from . import store

try:
    from cryptography.fernet import Fernet
except Exception:  # 极端情况降级（不应发生，已打进 EXE）
    Fernet = None

_KEY_FILE = os.path.join(store.DATA_DIR, ".fc_deploy_key")
_CFG_KEY = "deploy"


def _master_key():
    """返回加密主密钥（bytes）。优先系统密钥环，退化为本地 key 文件。"""
    # 1) 系统密钥环
    try:
        import keyring
        k = keyring.get_password("FilmCollector", "deploy_master")
        if k:
            return k.encode()
    except Exception:
        pass
    # 2) 本地 key 文件
    if os.path.exists(_KEY_FILE):
        try:
            return open(_KEY_FILE, "rb").read()
        except Exception:
            pass
    # 3) 生成并持久化
    if Fernet is None:
        # 退化：用随机字节当 key（不持久主密钥，重启重新生成时旧密文失效——仅兜底）
        return os.urandom(32)
    key = Fernet.generate_key()
    try:
        os.makedirs(os.path.dirname(_KEY_FILE), exist_ok=True)
        with open(_KEY_FILE, "wb") as f:
            f.write(key)
        try:
            os.chmod(_KEY_FILE, 0o600)
        except Exception:
            pass
        try:
            import keyring
            keyring.set_password("FilmCollector", "deploy_master", key.decode())
        except Exception:
            pass
    except Exception:
        pass
    return key


def _fernet():
    if Fernet is None:
        raise RuntimeError("cryptography 未安装，无法加解密 Token")
    return Fernet(_master_key())


def save(platform, username, repo, token):
    """加密保存全部凭据。token 为空则不更新（保留旧 token）。"""
    cfg = store.load_config()
    d = cfg.get(_CFG_KEY, {})
    d["platform"] = platform or "github"
    if username is not None:
        d["username"] = username
    if repo:
        d["repo"] = repo
    if token:
        enc = _fernet().encrypt(token.encode()).decode()
        d["token_enc"] = enc
        d.pop("token_b64", None)  # 清除旧弱混淆
    cfg[_CFG_KEY] = d
    store.save_config(cfg)


def load():
    """返回解密后的凭据 dict（含明文 token）；无则返回 None。"""
    cfg = store.load_config()
    d = cfg.get(_CFG_KEY)
    if not d:
        return None
    enc = d.get("token_enc")
    # 兼容旧版 base64 弱混淆：自动迁移为密文
    if not enc and d.get("token_b64"):
        try:
            raw = base64.b64decode(d["token_b64"]).decode()
            enc = _fernet().encrypt(raw.encode()).decode()
            d["token_enc"] = enc
            d.pop("token_b64", None)
            cfg[_CFG_KEY] = d
            store.save_config(cfg)
        except Exception:
            return None
    if not enc:
        return None
    try:
        token = _fernet().decrypt(enc.encode()).decode()
    except Exception:
        return None
    return {
        "platform": d.get("platform", "github"),
        "username": d.get("username") or "",
        "repo": d.get("repo") or "FilmCollector",
        "token": token,
    }


def has():
    return load() is not None


def clear():
    cfg = store.load_config()
    cfg.pop(_CFG_KEY, None)
    store.save_config(cfg)


def mask_token(token):
    """脱敏展示：ghp_****abcd，只显示前后各 4 位。"""
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return token[:4] + "****" + token[-4:]
