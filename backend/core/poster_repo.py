# -*- coding: utf-8 -*-
"""
poster_repo.py —— 把 FilmCollector 本地图库桥接成 Lumflix APK 的「独立海报仓库」
================================================================================

为什么需要它
------------
Lumflix（包名 com.nettv.app，WebView 架构）的前端 assets/www/js/api.js 已内置
「独立海报仓库」机制：

    POSTER_CDN = '<你的仓库>/'
    posterRepoUrl(name) = POSTER_CDN + 'img/'   + md5(normalize(片名)) + '.jpg'
    getHeroBg(item)      = vod_pic_slide → POSTER_CDN+'img/'+md5(片名) → vod_pic
    getHeroPoster(item)  = POSTER_CDN+'img/'+md5(片名) → vod_pic

即 APK 对**任意**片名都按 `md5(normalize(片名))` 去你的仓库找图，找到就显示你自己的
高清图，找不到才回退别人源站。卡片图、主视觉竖版海报、详情页大图全都优先用你的图。

但 FilmCollector 现有 image_cache 落盘文件名是 `poster_<URL文件名>.jpg`，**不是 md5 命名**，
导致 APK 查到的全是 404，只能回退别人图——这就是「依赖别人 API 才白屏」的根因。

本模块补上这一座桥：
  1. export_repo()   把本地图库 + 高清抓取结果，按 APK 约定的 md5(片名) 命名，
                     导出到 tvbox-dist/repo/img/（竖版海报）与 repo/slide/（横版主视觉）。
  2. build_featured() 自动挑「既有可播片源 + 又有高清图」的影片，生成 featured.json
                     （今日精选），并按天数自动轮换批次（默认每 3 天换一批）。

normalize 必须与 APK 的 normalizeName() 完全一致（见 api.js），否则哈希对不上：
    1) trim
    2) 全角(FF01-FF5E) → 半角（charCode - 0xFEE0）
    3) 全角空格(U+3000) → ' '
    4) 转小写
    5) 去除所有空白（\\s+ → ''）
"""

import hashlib
import os
import shutil
import time
import struct

from . import store, json_gen

IMG_SRC_DIR = os.path.join(store.BASE_DIR, "output", "images")
POSTERS_DIR = os.path.join(store.BASE_DIR, "output", "posters")
REPO_DIR = os.path.join(store.BASE_DIR, "tvbox-dist", "repo")

# 默认轮换周期（天）与精选数量（与 Lumflix 的 heroBannerCount=5 对齐）
DEFAULT_ROTATE_DAYS = 3
DEFAULT_FEATURED_COUNT = 5


# ---------------- 与 APK 完全一致的 normalize + md5 ----------------
def normalize_name(name):
    """必须与 Lumflix assets/www/js/api.js 的 normalizeName() 输出逐字节一致。"""
    if not name:
        return ""
    s = str(name).strip()
    # 全角 → 半角
    s = "".join(
        chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c
        for c in s
    )
    # 全角空格
    s = s.replace("\u3000", " ")
    # 转小写
    s = s.lower()
    # 去所有空白
    s = "".join(s.split())
    return s


def poster_repo_url(name, kind="img", base=""):
    """返回某片名在你的海报仓库中的地址。kind: 'img'(竖版) / 'slide'(横版主视觉)。"""
    h = hashlib.md5(normalize_name(name).encode("utf-8")).hexdigest()
    base = (base or "").rstrip("/") + "/"
    return base + "repo/" + kind + "/" + h + ".jpg"


# ---------------- 图片尺寸解析（仅读文件头，零额外依赖） ----------------
def _img_dims(head):
    """从图片文件头解析 (宽, 高)；不支持的格式返回 (0, 0)。"""
    if len(head) < 24:
        return (0, 0)
    sig = head[:8]
    if sig == b"\x89PNG\r\n\x1a\n":
        try:
            w, h = struct.unpack(">II", head[16:24])
            return (w, h)
        except Exception:
            return (0, 0)
    if sig[:2] == b"\xff\xd8":  # JPEG：找 SOF 标记
        i = 2
        while i < len(head) - 9:
            if head[i] != 0xFF:
                i += 1
                continue
            m = head[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                     0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return (w, h)
            seg = struct.unpack(">H", head[i + 2:i + 4])[0]
            i += 2 + seg
        return (0, 0)
    if sig[:2] == b"BM":
        try:
            w, h = struct.unpack("<II", head[18:26])
            return (w, h)
        except Exception:
            return (0, 0)
    if sig[:6] in (b"GIF87a", b"GIF89a"):
        try:
            w, h = struct.unpack("<HH", head[6:10])
            return (w, h)
        except Exception:
            return (0, 0)
    if sig[:4] == b"RIFF" and head[8:12] == b"WEBP":
        # VP8 / VP8L / VP8X
        fmt = head[12:16]
        try:
            if fmt == b"VP8 ":
                w, h = struct.unpack("<HH", head[26:30])
                return (w, h)
            if fmt == b"VP8L":
                b0, b1, b2, b3 = head[21], head[22], head[23], head[24]
                bits = (b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)) >> 8
                w = (bits & 0x3FFF) + 1
                h = ((bits >> 14) & 0x3FFF) + 1
                return (w, h)
            if fmt == b"VP8X":
                w = (head[24] | (head[25] << 8) | (head[26] << 16)) + 1
                h = (head[27] | (head[28] << 8) | (head[29] << 16)) + 1
                return (w, h)
        except Exception:
            return (0, 0)
    return (0, 0)


def _best_image_in_dir(d):
    """返回目录下「像素面积最大」的图片路径（高清主视觉优先）。"""
    best = None
    best_area = -1
    for fn in os.listdir(d):
        if not fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")):
            continue
        p = os.path.join(d, fn)
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        if sz < 3000:  # 过小跳过
            continue
        try:
            with open(p, "rb") as f:
                head = f.read(64)
            w, h = _img_dims(head)
        except Exception:
            w = h = 0
        area = (w * h) if (w and h) else sz  # 尺寸未知时用文件大小兜底
        if area > best_area:
            best_area = area
            best = p
    return best


# ---------------- 仓库导出 ----------------
def _repo_root(out_dir=None):
    return os.path.join(out_dir, "repo") if out_dir else REPO_DIR


def export_repo(base="", out_dir=None):
    """把本地图库 + 高清抓取结果，按 md5(片名) 导出成 APK 可直接消费的仓库。

    out_dir: 发布输出目录（默认 tvbox-dist）。仓库写入 <out_dir>/repo/{img,slide}/。
    返回统计：{'img': n, 'slide': m, 'films': k}
    """
    root = _repo_root(out_dir)
    os.makedirs(os.path.join(root, "img"), exist_ok=True)
    os.makedirs(os.path.join(root, "slide"), exist_ok=True)

    # 片名 → 已导出的竖版/横版路径（避免重复导出）
    img_done = set()
    slide_done = set()
    film_count = 0

    # ① 来自 DB 条目里引用的本地海报（poster 字段指向 output/images/poster_*.jpg）
    items = store.load_db().get("items", [])
    for it in items:
        name = it.get("title") or it.get("vod_name")
        if not name:
            continue
        ref = (it.get("poster") or it.get("cover") or "").strip()
        src = None
        if ref.startswith("images/"):
            cand = os.path.join(store.BASE_DIR, "output", ref)
            if os.path.isfile(cand):
                src = cand
        elif ref.startswith("http"):
            # 远程海报暂不入库（保持「只依赖一次」原则由 image_cache 负责下载）
            src = None
        if not src:
            continue
        h = hashlib.md5(normalize_name(name).encode("utf-8")).hexdigest()
        # 竖版海报
        dst_img = os.path.join(root, "img", h + ".jpg")
        if h not in img_done:
            shutil.copy2(src, dst_img)
            img_done.add(h)
        film_count += 1

    # ② 来自高清抓取目录 output/posters/<片名>/（含竖版海报与横版主视觉）
    if os.path.isdir(POSTERS_DIR):
        for folder in os.listdir(POSTERS_DIR):
            d = os.path.join(POSTERS_DIR, folder)
            if not os.path.isdir(d) or folder.startswith("."):
                continue
            best = _best_image_in_dir(d)
            if not best:
                continue
            # 用文件夹名归一化（与 vod_name 归一化规则一致即可命中）
            h = hashlib.md5(normalize_name(folder).encode("utf-8")).hexdigest()
            # 横版主视觉（hero 背景）始终用最佳图
            if h not in slide_done:
                dst_slide = os.path.join(root, "slide", h + ".jpg")
                shutil.copy2(best, dst_slide)
                slide_done.add(h)
            # 竖版海报：若 DB 海报未覆盖，则用最佳图补一张（保证卡片也有图）
            if h not in img_done:
                dst_img = os.path.join(root, "img", h + ".jpg")
                shutil.copy2(best, dst_img)
                img_done.add(h)
            film_count += 1

    stats = {"img": len(img_done), "slide": len(slide_done), "films": film_count}
    store.log("info",
              f"海报仓库导出：{stats['films']} 部影片 → repo/img {stats['img']} 张, repo/slide {stats['slide']} 张")
    return stats


# ---------------- 今日精选（自动轮换） ----------------
def _candidate_films(out_dir=None):
    """候选：有可播片源 + 仓库里有图（img 或 slide）的影片。"""
    root = _repo_root(out_dir)
    items = store.load_db().get("items", [])
    cands = []
    for it in items:
        name = it.get("title") or it.get("vod_name")
        if not name:
            continue
        if not it.get("episodes"):  # 必须有可播放地址
            continue
        h = hashlib.md5(normalize_name(name).encode("utf-8")).hexdigest()
        has_img = os.path.isfile(os.path.join(root, "img", h + ".jpg"))
        has_slide = os.path.isfile(os.path.join(root, "slide", h + ".jpg"))
        if not (has_img or has_slide):
            continue
        cands.append((it, h, has_img, has_slide))
    return cands


def build_featured(base="", rotate_days=DEFAULT_ROTATE_DAYS, count=DEFAULT_FEATURED_COUNT, out_dir=None):
    """生成今日精选 featured.json（与 Lumflix 首页 hero 直接对齐）。

    轮换：以「天数 // rotate_days」为批次号，滑动候选窗口，实现每 rotate_days 天换一批。
    条目携带完整可播字段（vod_play_from / vod_play_url）+ 你自己的仓库图地址。
    """
    base = (base or "").rstrip("/") + "/"
    cands = _candidate_films(out_dir)
    if not cands:
        store.log("warn", "今日精选：没有「有片源且有高清图」的候选影片，跳过生成")
        return {"count": 0, "featured": []}

    # 稳定排序（按片名），保证轮换窗口可预测
    cands.sort(key=lambda c: normalize_name(c[0].get("title", "")))

    batch = int(time.time() // 86400) // rotate_days
    start = (batch * count) % len(cands)
    take = min(count, len(cands))  # 候选不足时不多重复同一部
    window = []
    i = 0
    while len(window) < take:
        c = cands[(start + i) % len(cands)]
        if c not in window:
            window.append(c)
        i += 1

    featured = []
    for it, h, has_img, has_slide in window:
        v = json_gen._to_tvbox(it)
        if has_slide:
            v["vod_pic_slide"] = base + "repo/slide/" + h + ".jpg"
            v["vod_pic"] = base + "repo/slide/" + h + ".jpg" if not has_img else base + "repo/img/" + h + ".jpg"
        elif has_img:
            v["vod_pic"] = base + "repo/img/" + h + ".jpg"
        # 备注里标注这是精选（APK 侧可忽略）
        v["_featured"] = True
        featured.append(v)

    out = {
        "code": 1, "msg": "ok",
        "rotate_days": rotate_days,
        "batch": batch,
        "count": len(featured),
        "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
        "list": featured,
    }
    root = _repo_root(out_dir)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "featured.json"), "w", encoding="utf-8") as f:
        import json as _json
        _json.dump(out, f, ensure_ascii=False, indent=2)
    # 同时输出 featured.js（全局变量，规避 WebView 跨域限制，供 APK 的 NetTVAPI.getFeatured 读取）
    with open(os.path.join(root, "featured.js"), "w", encoding="utf-8") as f:
        f.write("window.__NETTV_FEATURED__ = " + _json.dumps(out, ensure_ascii=False) + ";\n")

    store.log("info",
              f"今日精选生成：第 {batch} 批 / 共 {len(featured)} 部（每 {rotate_days} 天轮换）")
    return {"count": len(featured), "batch": batch, "featured": featured}


def refresh(base="", rotate_days=DEFAULT_ROTATE_DAYS, count=DEFAULT_FEATURED_COUNT, out_dir=None):
    """一站式：先导出仓库，再生成精选。供发布 / 定时任务调用。"""
    repo_stats = export_repo(base, out_dir)
    feat = build_featured(base, rotate_days, count, out_dir)
    return {"repo": repo_stats, "featured": feat}
