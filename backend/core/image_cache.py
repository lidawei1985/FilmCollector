# -*- coding: utf-8 -*-
"""
本地素材图库：把抓取到的海报/封面下载到本机统一缓存，校验破损/404/超大图，杜绝客户端 404 空白。
"""
import os
import requests
from urllib.parse import urlparse
from . import store

IMG_DIR = os.path.join(store.BASE_DIR, "output", "images")
MAX_SIZE = 5 * 1024 * 1024  # 5MB 上限，过滤超大违规图


def _safe_name(url):
    p = urlparse(url)
    base = os.path.basename(p.path) or "img"
    base = re_sub(base)
    return base


def re_sub(s):
    import re
    return re.sub(r"[^\w\.\-]", "_", s)[:80]


def cache_image(url, prefix="poster"):
    """下载并校验图片，成功返回本地相对路径（相对 output/），失败返回 None。"""
    if not url or not url.startswith("http"):
        return None
    cfg = store.load_config()
    if not cfg.get("image_cache", True):
        return url
    os.makedirs(IMG_DIR, exist_ok=True)
    try:
        headers = {"User-Agent": store.UA_POOL[0], "Referer": url}
        r = requests.get(url, headers=headers, timeout=cfg.get("timeout", 12), stream=True)
        if r.status_code != 200:
            return None
        ct = r.headers.get("Content-Type", "")
        if not ct.startswith("image/"):
            return None
        data = b""
        for c in r.iter_content(8192):
            data += c
            if len(data) > MAX_SIZE:
                return None  # 超大图跳过
        if len(data) < 500:
            return None  # 破损/空白图
        ext = (ct.split("/")[-1].split(";")[0] or "jpg").replace("jpeg", "jpg")
        base = os.path.splitext(_safe_name(url))[0]  # 去掉 URL 自带扩展名，避免双后缀
        fname = f"{prefix}_{base}.{ext}"
        # 防重名
        path = os.path.join(IMG_DIR, fname)
        if os.path.exists(path):
            return os.path.relpath(path, os.path.join(store.BASE_DIR, "output")).replace("\\", "/")
        with open(path, "wb") as f:
            f.write(data)
        return os.path.relpath(path, os.path.join(store.BASE_DIR, "output")).replace("\\", "/")
    except Exception:
        return None


def scan_broken():
    """扫描图库，返回疑似损坏（无法打开）的文件列表。"""
    broken = []
    if not os.path.isdir(IMG_DIR):
        return broken
    for f in os.listdir(IMG_DIR):
        p = os.path.join(IMG_DIR, f)
        if os.path.getsize(p) < 500:
            broken.append(p)
    return broken


def list_images():
    if not os.path.isdir(IMG_DIR):
        return []
    return sorted(os.listdir(IMG_DIR))


def backfill_missing(items):
    """批量把库里缺失 / 仍是远程链接的海报下载到本地图库，原地改写 items 的 poster/cover。

    这是「APK 始终有图」的关键补漏：每次定时巡检都补一次，保证库里每部影片尽量都有
    本地海报；没有图源的（无 source_id 也无远程 poster）如实跳过。
    返回统计 dict：fixed 补齐张数 / skipped 无图源 / failed 下载失败。
    """
    fixed = skipped = failed = 0
    for it in items:
        for key in ("poster", "cover"):
            ref = (it.get(key) or "").strip()
            if ref.startswith("images/"):
                continue  # 已在本地图库，跳过
            if not ref:
                sid = it.get("source_id")
                if sid:
                    ref = f"https://archive.org/services/img/{sid}"  # 公共领域片用 archive.org 兜底
                else:
                    skipped += 1
                    continue
            local = cache_image(ref, prefix=key)
            if local:
                it[key] = local
                fixed += 1
            else:
                failed += 1
    return {"fixed": fixed, "skipped": skipped, "failed": failed}
