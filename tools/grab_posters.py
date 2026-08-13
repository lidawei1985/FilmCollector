# -*- coding: utf-8 -*-
"""
grab_posters.py —— 按影片名从公开图源采集「高清主视觉大图」
=============================================================

使用场景：
  你手头只有某部影片的小图，拉伸后糊成一片，没法当主视觉大图。
  这个脚本按影片名（如《杀破狼》）去公开图源搜图，自动过滤掉低清缩略图和水印图，
  只下载「原图 / 高清」并归类存放，再生成一个画廊页面方便你一眼挑出当天的主推主视觉。

图源（免费、带分辨率元数据，不需要翻墙 Key 也能用）：
  1) Wikimedia Commons（默认，零 Key）—— 大量高清海报/剧照/扫描件，原始分辨率
  2) TMDB（可选，免费注册拿 Key）—— 商业片最实用的高清海报 + 横版 backdrop 主视觉

分辨率门槛（--mode）：
  1080p : 宽>=1920 且 高>=1080，或长边>=2000（含 2K 竖版海报）
  2k    : 宽>=2560 且 高>=1440，或长边>=2560
  4k    : 宽>=3840 且 高>=2160，或长边>=3840
  低于 1000px 长边的直接判为缩略图丢弃。

规避策略：
  - 缩略图：只取「原图」地址（不取 thumb 链接）；文件名/URL 含 thumb/150px/220px 等跳过；
            下载后按真实像素尺寸再卡一遍。
  - 水印：文件名/描述含 banner/logo/watermark/screenshot/水印/贴纸 等关键词跳过。
            注意：机器无法 100% 识别水印，最终请在你挑图时人工确认最干净的一张。

用法：
  python tools/grab_posters.py "杀破狼"
  python tools/grab_posters.py "杀破狼" "流浪地球" --mode 2k --max 40
  python tools/grab_posters.py "杀破狼" --source tmdb --tmdb-key YOUR_KEY
  python tools/grab_posters.py "杀破狼" --source wikimedia,tmdb --tmdb-key $TMDB_API_KEY

说明（版权）：脚本只从「免费、带授权元数据」的公开图源取图，用于搭建你个人的海报素材库。
  商业影片海报本身受版权保护；若你的 APK 是公开发布的订阅源，请确认你对该图的用途合规
  （个人片库整理通常问题不大，对外大规模分发建议选用 CC/公有领域素材或取得授权）。

依赖：仅标准库 + requests（项目 venv 已自带）。
"""

import os
import re
import sys
import json
import time
import struct
import argparse
import urllib.parse

import requests

UA = "FilmCollector-PosterGrabber/1.0 (https://github.com/; personal media library tool)"
TIMEOUT = 20
CHUNK = 64 * 1024

# ---------- 分辨率解析（不依赖 Pillow，直接读文件头） ----------
def image_dims(data):
    """返回 (宽, 高)；解析失败返回 (0, 0)。支持 JPEG/PNG/GIF/BMP/WebP。"""
    if not data or len(data) < 24:
        return (0, 0)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return (w, h)
    if data[:2] == b"BM":
        w, h = struct.unpack("<II", data[18:26])
        return (w, h)
    if data[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", data[6:10])
        return (w, h)
    if data[:2] == b"\xff\xd8":  # JPEG
        return _jpeg_dims(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_dims(data)
    return (0, 0)


def _jpeg_dims(data):
    i = 2
    n = len(data)
    while i + 9 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return (w, h)
        if m in (0xD9, 0xDA):
            break
        seg = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seg
    return (0, 0)


def _webp_dims(data):
    fmt = data[12:16]
    if fmt == b"VP8 " and len(data) >= 30:
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return (w, h)
    if fmt == b"VP8L" and len(data) >= 25:
        b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
        w = ((b0 | (b1 << 8)) & 0x3FFF) + 1
        h = (((b1 >> 6) | (b2 << 2) | (b3 << 10)) & 0x3FFF) + 1
        return (w, h)
    if fmt == b"VP8X" and len(data) >= 30:
        w = struct.unpack("<I", data[24:27] + b"\x00")[0] + 1
        h = struct.unpack("<I", data[27:30] + b"\x00")[0] + 1
        return (w, h)
    return (0, 0)


# ---------- HTTP ----------
def _get_json(url, params=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _get_bytes(url, referer=None):
    h = {"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"}
    if referer:
        h["Referer"] = referer
    r = requests.get(url, headers=h, timeout=TIMEOUT, stream=True)
    if r.status_code != 200:
        return None
    ct = r.headers.get("Content-Type", "")
    data = b""
    for c in r.iter_content(CHUNK):
        data += c
        if len(data) > 30 * 1024 * 1024:  # 30MB 上限，防超大违规图
            return None
    return data


# ---------- 关键词过滤 ----------
_THUMB_RE = re.compile(r"(thumb|thumbnail|/thumb/|_t\.|/small/|preview|150px|220px|320px|"
                       r"w=1[0-9]{2}|w=2[0-9]{2}|sz=\d{2,3}|_w\d{2,3})", re.I)
_WM_RE = re.compile(r"(banner|logo|watermark|水印|贴纸|screenshot|截图|textless\?|"
                    r"dvd_?cover|vhs|promo_?still|poster_?art_?frame)", re.I)


def looks_thumb(url, title, width, height):
    if max(width, height) < 1000:
        return "分辨率过低(<1000px)，疑似缩略图"
    if _THUMB_RE.search(url) or _THUMB_RE.search(title or ""):
        return "URL/文件名含缩略图特征"
    return None


def looks_watermark(title, desc):
    text = f"{title or ''} {desc or ''}"
    if _WM_RE.search(text):
        return "文件名/描述含水印/logo/banner 等特征"
    return None


# ---------- 分辨率门槛 ----------
def make_threshold(mode):
    if mode == "2k":
        return 2560, 1440, 2560
    if mode == "4k":
        return 3840, 2160, 3840
    return 1920, 1080, 2000  # 1080p 默认


def qualifies(width, height, thr):
    min_w, min_h, long_edge = thr
    if width <= 0 or height <= 0:
        return False
    if (width >= min_w and height >= min_h) or max(width, height) >= long_edge:
        return True
    return False


# ---------- 图源：Wikimedia Commons ----------
def source_wikimedia(film, max_n, thr):
    out = []
    seen = set()
    api = "https://commons.wikimedia.org/w/api.php"
    queries = [film, f"{film} poster", f"{film} film"]
    for q in queries:
        if len(out) >= max_n:
            break
        try:
            data = _get_json(api, params={
                "action": "query", "generator": "search",
                "gsrsearch": q, "gsrnamespace": "6", "gsrlimit": str(min(max_n, 30)),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "format": "json",
            })
        except Exception as e:
            print(f"  [wikimedia] 检索「{q}」失败：{e}")
            continue
        pages = (data.get("query") or {}).get("pages") or {}
        for pid, page in pages.items():
            ii = (page.get("imageinfo") or [{}])[0]
            url = ii.get("url")
            if not url or url in seen:
                continue
            mime = ii.get("mime", "")
            if not mime.startswith("image/"):
                continue
            w = int(ii.get("width", 0) or 0)
            h = int(ii.get("height", 0) or 0)
            title = page.get("title", "")
            em = ii.get("extmetadata") or {}
            desc = (em.get("ImageDescription") or {}).get("value", "")
            desc = re.sub(r"<[^>]+>", " ", desc or "")
            reason = looks_thumb(url, title, w, h) or looks_watermark(title, desc)
            if reason:
                continue
            if not qualifies(w, h, thr):
                continue
            seen.add(url)
            out.append({
                "url": url, "width": w, "height": h, "mime": mime,
                "title": title, "desc": desc[:120], "source": "wikimedia",
            })
            if len(out) >= max_n:
                break
        time.sleep(0.3)
    return out


# ---------- 图源：TMDB ----------
def source_tmdb(film, api_key, max_n, thr):
    if not api_key:
        return []
    out = []
    seen = set()
    base = "https://api.themoviedb.org/3"
    try:
        sd = _get_json(f"{base}/search/movie", params={
            "api_key": api_key, "query": film, "language": "zh-CN", "include_adult": "false",
        })
    except Exception as e:
        print(f"  [tmdb] 搜索「{film}」失败：{e}")
        return out
    results = sd.get("results") or []
    if not results:
        return out
    movie_id = results[0].get("id")
    try:
        im = _get_json(f"{base}/movie/{movie_id}/images", params={
            "api_key": api_key, "include_image_language": "zh,en,null",
        })
    except Exception as e:
        print(f"  [tmdb] 取图片失败：{e}")
        return out
    # 横版 backdrop 优先（最适合主视觉大图），其次竖版 poster
    cands = []
    for b in (im.get("backdrops") or []):
        cands.append(("backdrop", b))
    for p in (im.get("posters") or []):
        cands.append(("poster", p))
    for kind, imgt in cands:
        fp = imgt.get("file_path")
        if not fp:
            continue
        w = int(imgt.get("width", 0) or 0)
        h = int(imgt.get("height", 0) or 0)
        title = f"tmdb_{kind}_{os.path.basename(fp)}"
        reason = looks_thumb("", title, w, h) or looks_watermark(title, "")
        if reason:
            continue
        if not qualifies(w, h, thr):
            continue
        url = f"https://image.tmdb.org/t/p/original{fp}"
        if url in seen:
            continue
        seen.add(url)
        out.append({
            "url": url, "width": w, "height": h, "mime": "image/jpeg",
            "title": title, "desc": f"TMDB {kind}", "source": "tmdb",
        })
        if len(out) >= max_n:
            break
    return out


# ---------- 下载 + 落盘 ----------
def _safe_dir(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "untitled"


def download_and_save(item, film_dir):
    data = _get_bytes(item["url"], referer=item.get("source") == "wikimedia" and "https://commons.wikimedia.org/" or None)
    if not data:
        return None
    # 二次校验真实像素尺寸（防止原图地址返回的是缩略图）
    w, h = image_dims(data)
    if w and h and max(w, h) < 1000:
        return {"ok": False, "reason": f"实际像素仅 {w}x{h}，缩略图"}
    ext = (item["mime"].split("/")[-1].replace("jpeg", "jpg") or "jpg")
    if ext == "webp" and not w:
        return {"ok": False, "reason": "WebP 尺寸无法校验，已跳过"}
    base = f"{item['width']}x{item['height']}_{item['source']}"
    fname = f"{base}.{ext}"
    # 防重名
    path = os.path.join(film_dir, fname)
    k = 1
    while os.path.exists(path):
        fname = f"{base}_{k}.{ext}"
        path = os.path.join(film_dir, fname)
        k += 1
    with open(path, "wb") as f:
        f.write(data)
    return {"ok": True, "path": path, "width": w or item["width"], "height": h or item["height"],
            "bytes": len(data), "fname": fname}


# ---------- 画廊 ----------
def render_gallery(film_dir, film, items):
    rows = []
    for it in items:
        rel = it["fname"]
        rows.append(
            f'<div class="card"><img src="{rel}" loading="lazy">'
            f'<div class="meta"><b>{it["width"]}×{it["height"]}</b> · {it["source"]}<br>'
            f'<span class="url">{it["url"]}</span></div></div>'
        )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{film} · 高清主视觉候选</title>
<style>
 body{{font-family:system-ui,'Microsoft YaHei',sans-serif;margin:0;background:#0f1420;color:#e8edf5}}
 header{{padding:18px 22px;background:#161d2e;border-bottom:1px solid #2a3550}}
 h1{{margin:0;font-size:20px}} .sub{{color:#8aa0c0;font-size:13px;margin-top:4px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;padding:20px}}
 .card{{background:#1a2236;border:1px solid #2a3550;border-radius:10px;overflow:hidden}}
 img{{width:100%;height:340px;object-fit:contain;background:#0b0f18;display:block}}
 .meta{{padding:8px 10px;font-size:12px;color:#bcd0ee}} .url{{color:#6f86ad;word-break:break-all;font-size:11px}}
</style></head>
<body><header><h1>🎬 {film} · 高清主视觉候选（{len(items)} 张）</h1>
<div class="sub">挑一张最干净、无文字水印的作为当日主推主视觉；机器无法 100% 识别水印，请人工确认。</div></header>
<div class="grid">{"".join(rows)}</div></body></html>"""
    with open(os.path.join(film_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ---------- 主流程 ----------
def grab_one(film, sources, tmdb_key, max_n, thr, out_base):
    print(f"\n=== 采集《{film}》===")
    cands = []
    if "wikimedia" in sources:
        print("  · 搜 Wikimedia Commons ...")
        cands += source_wikimedia(film, max_n, thr)
    if "tmdb" in sources:
        print("  · 搜 TMDB ...")
        cands += source_tmdb(film, tmdb_key, max_n, thr)
    if not cands:
        print("  ✗ 未找到符合分辨率门槛的图（试试 --mode 1080p / 换图源 / 检查网络）。")
        return None

    film_dir = os.path.join(out_base, _safe_dir(film))
    os.makedirs(film_dir, exist_ok=True)

    saved = []
    skipped = 0
    for c in cands:
        res = download_and_save(c, film_dir)
        if res and res.get("ok"):
            saved.append({"fname": res["fname"], "width": res["width"], "height": res["height"],
                          "bytes": res["bytes"], "source": c["source"], "url": c["url"]})
            print(f"  ✔ {res['fname']}  ({res['width']}×{res['height']}, {res['bytes']//1024}KB)")
        else:
            skipped += 1
            print(f"  ✗ 跳过：{res.get('reason') if res else '下载失败'}  <- {c['url'][:80]}")

    if not saved:
        print("  ✗ 候选图全部因尺寸/水印被过滤，未落盘。")
        return None

    manifest = {
        "film": film, "mode": thr, "count": len(saved),
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "images": saved,
    }
    with open(os.path.join(film_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    render_gallery(film_dir, film, saved)
    print(f"  ✓ 已保存 {len(saved)} 张（跳过 {skipped} 张）→ {film_dir}")
    print(f"  ✓ 画廊：{os.path.join(film_dir, 'index.html')}")
    return film_dir


def main():
    p = argparse.ArgumentParser(
        description="按影片名采集高清海报/主视觉大图（规避缩略图与水印）")
    p.add_argument("films", nargs="+", help="影片名，可多个，如 杀破狼")
    p.add_argument("--mode", default="1080p", choices=["1080p", "2k", "4k"],
                   help="分辨率门槛（默认 1080p）")
    p.add_argument("--max", type=int, default=30, help="每部最多采集张数（默认 30）")
    p.add_argument("--source", default="wikimedia,tmdb",
                   help="图源，逗号分隔：wikimedia,tmdb（默认两者都试）")
    p.add_argument("--tmdb-key", default=os.environ.get("TMDB_API_KEY", ""),
                   help="TMDB API Key（也可设环境变量 TMDB_API_KEY）")
    p.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "output", "posters"),
                   help="输出根目录（默认 output/posters）")
    args = p.parse_args()

    sources = [s.strip().lower() for s in args.source.split(",") if s.strip()]
    sources = [s for s in sources if s in ("wikimedia", "tmdb")]
    if not sources:
        print("✗ 没有可用图源，请至少指定 wikimedia 或 tmdb。")
        sys.exit(1)
    if "tmdb" in sources and not args.tmdb_key:
        print("⚠ 未提供 TMDB Key，TMDB 图源将跳过（只跑 Wikimedia）。"
              "免费申请：https://www.themoviedb.org/settings/api")
        sources = [s for s in sources if s != "tmdb"]

    thr = make_threshold(args.mode)
    out_base = os.path.abspath(args.out)
    os.makedirs(out_base, exist_ok=True)

    print(f"图源={sources}  门槛={args.mode}({thr[0]}x{thr[1]},长边≥{thr[2]})  每部上限={args.max}")
    dirs = []
    for film in args.films:
        d = grab_one(film, sources, args.tmdb_key, args.max, thr, out_base)
        if d:
            dirs.append(d)

    print("\n========== 完成 ==========")
    if dirs:
        print("产出目录：")
        for d in dirs:
            print(f"  {d}")
        print("下一步：打开对应 index.html 挑一张最干净的高清图，"
              "复制到 backend 的 output/images/ 即可随订阅源发布到你的仓库、喂给 APK。")
    else:
        print("未采集到任何图，请检查影片名 / 图源 / 网络。")


if __name__ == "__main__":
    main()
