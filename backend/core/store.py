# -*- coding: utf-8 -*-
"""
本地数据仓储：所有抓取/清洗/编辑结果仅存于本机 JSON 文件，零外部依赖、零运维。
"""
import json
import os
import sys
import threading
import shutil
import time
from datetime import datetime, timezone

# ---- 打包/运行路径解析 ----
# 开发态：项目根目录。
# 打包态(PyInstaller 单文件 EXE)：前端/默认数据在 _MEIPASS(只读临时解压，随进程销毁)，
#   用户数据(数据库/配置/图库/导出)写在 EXE 同目录(便携可写，长期保留)。
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    _MEIPASS = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    ASSET_DIR = _MEIPASS
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ASSET_DIR = BASE_DIR

DATA_DIR = os.path.join(BASE_DIR, "backend", "data")
DB_PATH = os.path.join(DATA_DIR, "db.json")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
AD_PATH = os.path.join(DATA_DIR, "ad_domains.txt")
BACKUP_DIR = os.path.join(BASE_DIR, "output", "backup")

_lock = threading.RLock()

DEFAULT_CONFIG = {
    "api_host": "127.0.0.1",        # 仅本机：工具界面与本地观影客户端在本机访问；外部访问走「一键部署到公网」
    "api_port": 9911,
    "api_enabled": False,
    "request_interval": 1.5,          # 抓取间隔（秒），默认礼貌限速
    "rotate_ua": True,                # 轮换 UA（轻量反爬应对，非突破防护）
    "max_retry": 2,
    "timeout": 12,
    "schedule": {"enabled": False, "mode": "daily", "hour": 3},
    "auto_clean": True,               # 自动化流程默认全开
    "ad_filter": True,
    "image_cache": True,
    # ---- 全自动闭环（找片→采集→打包→上传公网→喂指定 APK）----
    "auto_mode": False,               # 是否开启全自动
    "auto_on_launch": True,           # 打开工具时自动跑一次
    "auto_max_new": 20,               # 每次最多新增几部新片
    "auto_upload": True,              # 自动上传公网（需先填一次 Token）
    "auto_categories": [],            # 空=全部合集；可勾选具体合集 key
    "auto_last_run": "",
    "auto_last_result": {},
}


def _now():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36 Edg/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _ensure():
    _ensure_dirs()
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump({"items": [], "logs": [], "templates": {}}, f, ensure_ascii=False, indent=2)
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    if not os.path.exists(AD_PATH):
        with open(AD_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(DEFAULT_AD_DOMAINS) + "\n")


DEFAULT_AD_DOMAINS = [
    "adservice.google.com", "googlesyndication.com", "doubleclick.net", "adnxs.com",
    "pubmatic.com", "criteo.com", "taboola.com", "outbrain.com", "scorecardresearch.com",
    "moatads.com", "adsrvr.org", "rubiconproject.com", "openx.net", "advertising.com",
    "adcolony.com", "innity.com", "yahoo.com", "adsystem.com", "spotx.tv", "vidazoo.com",
    "infolinks.com", "mgid.com", "revcontent.com", "zergnet.com", "sharethrough.com",
]


def load_db():
    _ensure()
    with _lock:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)


def save_db(db):
    _ensure()
    with _lock:
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)


def load_config():
    _ensure()
    with _lock:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    # 合并默认值，避免旧配置缺字段
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def get_lan_ip():
    """返回本机在局域网中的 IPv4 地址（用于手机/电视访问）。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def save_config(cfg):
    _ensure_dirs()
    with _lock:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_ad_domains():
    _ensure()
    with _lock:
        with open(AD_PATH, "r", encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]


def add_ad_domains(domains):
    cur = set(load_ad_domains())
    added = [d for d in domains if d not in cur]
    cur.update(added)
    with _lock:
        with open(AD_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(cur)) + "\n")
    return added


def log(level, msg, count=None):
    db = load_db()
    entry = {"time": _now(), "level": level, "msg": msg}
    if count is not None:
        entry["count"] = count
    db["logs"].insert(0, entry)
    db["logs"] = db["logs"][:500]
    save_db(db)


def backup_db():
    """每日自动备份历史版本，支持一键回滚。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"db_{ts}.json")
    shutil.copy(DB_PATH, dst)
    # 仅保留最近 30 份
    files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("db_")])
    for old in files[:-30]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    return dst


def list_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    return sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("db_")], reverse=True)


def restore_backup(name):
    src = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(src):
        return False
    shutil.copy(src, DB_PATH)
    return True
