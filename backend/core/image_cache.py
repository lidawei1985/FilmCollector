# -*- coding: utf-8 -*-
"""
本地素材图库：把抓取到的海报/封面下载到本机统一缓存，校验破损/404/超大图，杜绝客户端 404 空白。
"""
import os
import time
import requests
import urllib3
from urllib.parse import urlparse
from . import store

# 公共图源（如 archive.org）在部分受限环境会因证书链问题触发 SSL 校验失败，
# 而图片本身是公开静态资源、且我们下载后会校验 Content-Type 与体积，故关闭校验以保稳定。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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


# 海报压缩上限（与 poster-warehouse 同一思路：本地优化后再随包上传，省流量、快加载）
THUMB_WIDTH = 500
THUMB_QUALITY = 82


def _optimize(data):
    """若环境有 Pillow，则解码→缩放到 THUMB_WIDTH 宽→压成 JPEG；否则原样返回。"""
    try:
        from io import BytesIO
        from PIL import Image
    except Exception:
        return data  # 无 Pillow（极少数环境）：退化为原图直存，不影响功能
    try:
        im = Image.open(BytesIO(data))
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        else:
            im = im.convert("RGB")
        if im.width > THUMB_WIDTH:
            h = int(im.height * THUMB_WIDTH / im.width)
            im = im.resize((THUMB_WIDTH, h), Image.LANCZOS)
        out = BytesIO()
        im.save(out, "JPEG", quality=THUMB_QUALITY, optimize=True)
        return out.getvalue()
    except Exception:
        return data


def cache_image(url, prefix="poster"):
    """下载并校验图片，成功返回本地相对路径（相对 output/），失败返回 None。

    下载后会用 Pillow 压缩优化（最大宽度 500px、JPEG q82），随订阅包上传后
    APK 从我们自己的 Pages 读图，又快又稳，不依赖第三方图床。
    自带重试（应对 archive.org 偶发限流/超时）。
    """
    if not url or not url.startswith("http"):
        return None
    cfg = store.load_config()
    if not cfg.get("image_cache", True):
        return url
    os.makedirs(IMG_DIR, exist_ok=True)
    # 压缩优化（无 Pillow 时退化为原图）
    data = None
    for attempt in range(3):
        try:
            headers = {"User-Agent": store.UA_POOL[0], "Referer": url}
            r = requests.get(url, headers=headers, timeout=cfg.get("timeout", 12), stream=True, verify=False)
            if r.status_code != 200:
                if attempt < 2:
                    time.sleep(1.5)
                    continue
                return None
            ct = r.headers.get("Content-Type", "")
            if not ct.startswith("image/"):
                return None
            chunk = b""
            for c in r.iter_content(8192):
                chunk += c
                if len(chunk) > MAX_SIZE:
                    return None  # 超大图跳过
            if len(chunk) < 500:
                return None  # 破损/空白图
            data = _optimize(chunk)
            if len(data) < 500:
                return None
            break
        except Exception:
            if attempt < 2:
                time.sleep(1.5)
                continue
            return None
    if data is None:
        return None
    base = os.path.splitext(_safe_name(url))[0]  # 去掉 URL 自带扩展名，避免双后缀
    fname = f"{prefix}_{base}.jpg"  # 统一优化为 jpg
    # 防重名
    path = os.path.join(IMG_DIR, fname)
    if os.path.exists(path):
        return os.path.relpath(path, os.path.join(store.BASE_DIR, "output")).replace("\\", "/")
    with open(path, "wb") as f:
        f.write(data)
    return os.path.relpath(path, os.path.join(store.BASE_DIR, "output")).replace("\\", "/")


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


def _source_id_of(it):
    """优先用 source_id；没有则从 source_url 反推 archive.org 标识符。"""
    sid = (it.get("source_id") or "").strip()
    if sid:
        return sid
    url = (it.get("source_url") or "").strip()
    if "archive.org" in url:
        # 形如 https://archive.org/details/<id> 或 .../download/<id>/...
        import re
        m = re.search(r"archive\.org/(?:details|download)/([^/?#]+)", url)
        if m:
            return m.group(1)
    return ""


def backfill_missing(items):
    """批量把库里缺失 / 仍是远程链接 / 本地引用但文件已丢失的海报下载到本地图库，原地改写 items 的 poster/cover。

    这是「APK 始终有图」的关键补漏：
      - 远程链接 / 空引用：用 archive.org 官方缩略图兜底下载；
      - 本地引用（images/xxx.jpg）但磁盘文件实际丢失：自愈重下（应对云端 checkout 缺失、误删、同步失败）；
    每次定时巡检都补一次，保证库里每部影片尽量都有本地海报；没有图源的如实跳过。
    返回统计 dict：fixed 补齐张数 / skipped 无图源 / failed 下载失败。
    """
    fixed = skipped = failed = 0
    for it in items:
        for key in ("poster", "cover"):
            ref = (it.get(key) or "").strip()
            if ref.startswith("images/"):
                # 已在本地图库引用：但要确认文件真实存在，丢了就自愈重下
                fname = os.path.basename(ref[len("images/"):])
                local_path = os.path.join(IMG_DIR, fname)
                if os.path.isfile(local_path):
                    continue  # 文件健在，跳过
                # 文件缺失 → 重新推导图源再补
                sid = _source_id_of(it)
                if sid:
                    ref = f"https://archive.org/services/img/{sid}"
                else:
                    it[key] = ""  # 既丢了文件又无兜底图源，置空避免电视端拿失效相对路径白屏
                    skipped += 1
                    continue
            elif not ref:
                sid = _source_id_of(it)
                if sid:
                    ref = f"https://archive.org/services/img/{sid}"  # 公共领域片用 archive.org 兜底
                else:
                    skipped += 1
                    continue
            # 此时 ref 必为 http(s) 链接
            local = cache_image(ref, prefix=key)
            if local:
                it[key] = local
                fixed += 1
            else:
                failed += 1
    return {"fixed": fixed, "skipped": skipped, "failed": failed}
