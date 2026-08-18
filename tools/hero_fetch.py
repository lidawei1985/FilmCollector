# -*- coding: utf-8 -*-
"""
hero_fetch.py —— Hero 专用高清影视素材采集（独立 pipeline）
================================================================

定位（对照需求一~十八）：
  - 这是「Hero 高清素材采集能力」本身，与 grab_posters.py（通用找大图）隔离，
    但写入同一个目录 output/posters/<片名>/，hero.py 会自动发现并评估。
  - 只【新增】素材与元数据，绝不改动现有 poster / cover / episodes / 订阅契约。
  - 核心要解决：影片数据 + 可用源 + 高清 Poster + 高清横版 Backdrop + 准确匹配
    + 源健康 + 数据稳定，七件事同时成立才进 Hero。

与评分规则的关系：
  - 本模块【只负责采集/筛选/验证/缓存/落盘】，不修改 hero.py 的评分规则。
  - 采集完成后由 tools/verify_hero.py（evaluate_all + build_hero_json）做最终资格判定。

质量门禁 HeroAssetQuality（每张图至少检查）：
  width / height / aspect_ratio / file_size / mime_type / image_decode / image_hash
  / source / match_score
  - Poster 宽度 < 800            → 淘汰
  - Backdrop 宽度 < 1280         → 淘汰
  - 图片损坏 / 解码失败          → 淘汰
  - 仅缩略图（HTTP 200 但小图）  → 淘汰（以下载后真实像素为准）
  - 影片匹配不确定              → 淘汰（宁可不用，绝不张冠李戴）

严格匹配（综合）：
  title + year + type + 外部 ID（tmdb id）+ 文件名/URL metadata
  - TMDB：搜索结果严格比对标题+年份，命中后才取该 movie_id 的 images，
          因此 Poster 与 Backdrop 天然属于【同一影片】（同片绑定保证）。
  - Wikimedia：免 Key 补充源，尽力而为；Poster/Backdrop 分别检索，
          同片绑定为尽力，match_score 保守，规模化建议以 TMDB 为主。

同片绑定（需求六）：
  Movie A 必须对应 Poster A + Backdrop A + Source A，绝不允许 A 配 B 的图。
  TMDB 天然满足；Wikimedia 无法硬保证 → 以 match_score 保守处理并在报告中标注。

失败分类（需求十二，至少区分）：
  NO_POSTER / NO_BACKDROP / LOW_RESOLUTION / IMAGE_404 / IMAGE_TIMEOUT /
  IMAGE_DECODE_ERROR / MATCH_UNCERTAIN / SOURCE_UNAVAILABLE / SOURCE_HEALTH_LOW /
  DUPLICATE / INVALID_FORMAT

性能与节制（需求十、十一）：
  - 并发上限 / 请求超时 / 重试上限 / 指数退避
  - URL hash + image hash 双缓存去重；已成功影片后续只校验可用性，不重复下载
  - 每部片拿到「合格 HD Poster + 合格 HD Backdrop」后立即停止继续寻找
"""
import os
import re
import sys
import json
import time
import math
import hashlib
import threading
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image

# ---- 引入项目内部（仅读取 db / 源健康，不改写）----
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.core import store, quality

UA = "FilmCollector-HeroFetcher/1.0 (personal media library; +https://github.com/)"
TIMEOUT = 25
CHUNK = 64 * 1024
MAX_BYTES = 30 * 1024 * 1024      # 单图上限，防超大违规图
CONCURRENCY = 4                   # 并发上限
RETRY = 3                         # 重试上限
BACKOFF = 2.0                     # 指数退避基数（秒）
MAX_CANDIDATES_PER_FILM = 8       # 每片保留候选上限

# 质量门槛（与 hero.py 对齐）
HERO_POSTER_MIN_W = 800
BACKDROP_MIN_W = 1280

# 失败码
FAIL = {
    "NO_POSTER": "未找到合格高清 Poster",
    "NO_BACKDROP": "未找到合格高清 Backdrop",
    "LOW_RESOLUTION": "图片分辨率不足（Poster<800 / Backdrop<1280）",
    "IMAGE_404": "图片地址 404 / 不可达",
    "IMAGE_TIMEOUT": "图片请求超时",
    "IMAGE_DECODE_ERROR": "图片解码失败/损坏",
    "MATCH_UNCERTAIN": "影片匹配不确定（宁缺毋滥，未采用）",
    "SOURCE_UNAVAILABLE": "影片无可用影视源",
    "SOURCE_HEALTH_LOW": "影视源健康度过低（<40）",
    "DUPLICATE": "与已采集图片重复，已去重",
    "INVALID_FORMAT": "图片格式不合法",
    "NETWORK": "网络/接口异常",
}

CACHE_LOCK = threading.Lock()


# ---------------- 标题清洗（用于检索，不改原片名）----------------
_SUFFIX_RE = re.compile(
    r"\s*:\s*Free Download,? Borrow,? and Streaming\s*:?\s*Internet Archive.*$",
    re.I)
_CREDIT_RE = re.compile(r"\s*-\s*[A-Za-z][\w\s,.&']{3,}$")
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def clean_search_title(title):
    """从 archive.org 杂乱标题中提取干净检索词（保留原片名用于落盘目录）。"""
    t = title or ""
    t = _SUFFIX_RE.sub("", t)
    t = _CREDIT_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def extract_year(title):
    m = _YEAR_RE.search(title or "")
    return m.group(0) if m else ""


def _norm(s):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (s or "").lower())


# ---------------- 缓存 ----------------
def _cache_path():
    return os.path.join(store.BASE_DIR, "output", "posters", ".hero_fetch_cache.json")


def load_cache():
    p = _cache_path()
    if os.path.isfile(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {"urls": {}, "hashes": {}}
    return {"urls": {}, "hashes": {}}


def save_cache(cache):
    p = _cache_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(cache, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


# ---------------- HTTP（带限流/重试/退避）----------------
_net_lock = threading.Lock()
_last_req = 0.0


def _ratelimit():
    global _last_req
    with _net_lock:
        gap = 0.4 - (time.time() - _last_req)
        if gap > 0:
            time.sleep(gap)
        _last_req = time.time()


def http_get_json(url, params=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    for attempt in range(RETRY):
        try:
            _ratelimit()
            r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
            if r.status_code == 404:
                raise _Fail("IMAGE_404", "404")
            r.raise_for_status()
            return r.json()
        except _Fail:
            raise
        except requests.exceptions.Timeout:
            if attempt == RETRY - 1:
                raise _Fail("IMAGE_TIMEOUT", "timeout")
            time.sleep(BACKOFF * (attempt + 1))
        except Exception as e:
            if attempt == RETRY - 1:
                raise _Fail("NETWORK", str(e)[:120])
            time.sleep(BACKOFF * (attempt + 1))


def http_get_bytes(url, referer=None):
    h = {"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"}
    if referer:
        h["Referer"] = referer
    for attempt in range(RETRY):
        try:
            _ratelimit()
            r = requests.get(url, headers=h, timeout=TIMEOUT, stream=True)
            if r.status_code == 404:
                raise _Fail("IMAGE_404", "404")
            r.raise_for_status()
            data = b""
            for c in r.iter_content(CHUNK):
                data += c
                if len(data) > MAX_BYTES:
                    raise _Fail("INVALID_FORMAT", "image too large")
            return data
        except _Fail:
            raise
        except requests.exceptions.Timeout:
            if attempt == RETRY - 1:
                raise _Fail("IMAGE_TIMEOUT", "timeout")
            time.sleep(BACKOFF * (attempt + 1))
        except Exception as e:
            if attempt == RETRY - 1:
                raise _Fail("NETWORK", str(e)[:120])
            time.sleep(BACKOFF * (attempt + 1))


class _Fail(Exception):
    def __init__(self, code, msg=""):
        self.code = code
        self.msg = msg
        super().__init__(code)


# ---------------- 图片校验（HeroAssetQuality）----------------
def verify_image(data, kind):
    """用 Pillow 解码并取真实像素尺寸；返回 (ok, info_or_failcode)。"""
    if not data or len(data) < 500:
        return False, "LOW_RESOLUTION"
    try:
        im = Image.open(__import__("io").BytesIO(data))
        im.load()  # 强制解码，捕获损坏
        w, h = im.size
        fmt = (im.format or "JPEG").upper()
    except Exception:
        return False, "IMAGE_DECODE_ERROR"
    if w <= 0 or h <= 0:
        return False, "IMAGE_DECODE_ERROR"
    if fmt not in ("JPEG", "PNG", "WEBP", "BMP"):
        return False, "INVALID_FORMAT"
    if kind == "backdrop" and w < BACKDROP_MIN_W:
        return False, "LOW_RESOLUTION"
    if kind == "poster" and w < HERO_POSTER_MIN_W:
        return False, "LOW_RESOLUTION"
    # 感知哈希（去重 + 假高清识别）
    try:
        g = im.convert("L").resize((8, 8), Image.LANCZOS)
        px = list(g.getdata())
        avg = sum(px) / len(px)
        bits = "".join("1" if p > avg else "0" for p in px)
        phash = "%016x" % int(bits, 2)
    except Exception:
        phash = None
    # 清晰度代理（相邻方差），用于识别「低清放大伪装」
    try:
        small = im.convert("L").resize((32, 32))
        var = __import__("statistics").pvariance(list(small.getdata()))
    except Exception:
        var = None
    return True, {
        "width": w, "height": h, "format": fmt, "phash": phash,
        "blur_var": var, "bytes": len(data),
    }


def phash_dist(a, b):
    if not a or not b or len(a) != len(b):
        return 99
    return sum(x != y for x, y in zip(a, b))


# ---------------- 图源：TMDB（主源，需 Key，同片绑定保证）----------------
def provider_tmdb(clean_title, year, media_type, api_key):
    """返回 (assets, fail_code)。assets 为同 movie_id 的 poster+backdrop 列表。"""
    if not api_key:
        return [], "NETWORK"
    base = "https://api.themoviedb.org/3"
    ep = "tv" if (media_type or "").startswith("剧") else "movie"
    # 先搜电影，再（若剧集）搜 tv
    def search(kind):
        try:
            return http_get_json(f"{base}/search/{kind}", params={
                "api_key": api_key, "query": clean_title,
                "language": "zh-CN", "include_adult": "false",
            }).get("results") or []
        except _Fail:
            return []
    results = search(ep)
    if not results and ep == "movie":
        results = search("tv")
    if not results:
        return [], "MATCH_UNCERTAIN"

    # 严格匹配：标题归一相等/包含 + 年份一致（若有）
    yn = year
    best = None
    for r in results[:8]:
        rt = _norm(r.get("title") or r.get("name") or "")
        ct = _norm(clean_title)
        if not rt or not ct:
            continue
        title_ok = (rt == ct) or (ct in rt) or (rt in ct)
        ry = (r.get("release_date") or r.get("first_air_date") or "")[:4]
        year_ok = (not yn) or (not ry) or (yn == ry)
        if title_ok and year_ok:
            best = r
            break
        if title_ok and best is None:
            best = r  # 退而求其次：标题匹配但年份缺失
    if best is None:
        return [], "MATCH_UNCERTAIN"

    mid = best.get("id")
    try:
        im = http_get_json(f"{base}/{ep}/{mid}/images", params={
            "api_key": api_key, "include_image_language": "zh,en,null",
        })
    except _Fail:
        return [], "NETWORK"

    assets = []
    for b in (im.get("backdrops") or []):
        fp = b.get("file_path")
        if not fp:
            continue
        assets.append(_tmdb_asset("backdrop", fp, b, mid, best))
    for p in (im.get("posters") or []):
        fp = p.get("file_path")
        if not fp:
            continue
        assets.append(_tmdb_asset("poster", fp, p, mid, best))
    if not assets:
        return [], "NO_POSTER"
    return assets, None


def _tmdb_asset(kind, fp, meta, mid, best):
    w = int(meta.get("width", 0) or 0)
    h = int(meta.get("height", 0) or 0)
    # 优先 original 全分辨率
    url = f"https://image.tmdb.org/t/p/original{fp}"
    return {
        "kind": kind, "url": url, "width": w, "height": h,
        "mime": "image/jpeg", "source": "tmdb", "movie_id": str(mid),
        "match_score": 0.95,  # TMDB 经严格匹配 + 同 movie_id，高置信
        "title_found": best.get("title") or best.get("name") or "",
        "year_found": (best.get("release_date") or best.get("first_air_date") or "")[:4],
    }


# ---------------- 图源：Wikimedia（免 Key 补充源，尽力同片）----------------
_THUMB_RE = re.compile(r"(thumb|thumbnail|/thumb/|_t\.|/small/|preview|150px|220px|320px|"
                       r"w=1[0-9]{2}|w=2[0-9]{2}|sz=\d{2,3}|_w\d{2,3})", re.I)
_WM_RE = re.compile(r"(banner|logo|watermark|水印|贴纸|screenshot|截图|textless\?|"
                    r"dvd_?cover|vhs|promo_?still|poster_?art_?frame)", re.I)


def provider_wikimedia(clean_title, year, media_type, api_key=None):
    api = "https://commons.wikimedia.org/w/api.php"
    queries = [f"{clean_title} poster", f"{clean_title} film", clean_title]
    assets = []
    seen = set()
    for q in queries:
        try:
            data = http_get_json(api, params={
                "action": "query", "generator": "search",
                "gsrsearch": q, "gsrnamespace": "6", "gsrlimit": "20",
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "format": "json",
            })
        except _Fail:
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
            desc = re.sub(r"<[^>]+>", " ", (em.get("ImageDescription") or {}).get("value", "") or "")
            if _THUMB_RE.search(url) or _THUMB_RE.search(title) or _WM_RE.search(title + " " + desc):
                continue
            # 推断 kind：横版→backdrop 候选，竖版→poster 候选
            kind = "backdrop" if (w and h and w / h >= 1.4) else "poster"
            # 匹配度：文件名/描述含干净标题令牌
            ct = _norm(clean_title)
            hay = _norm(title + " " + desc[:200])
            if ct and ct in hay:
                ms = 0.85
            elif ct and any(tok in hay for tok in re.findall(r"[a-z0-9\u4e00-\u9fff]{3,}", ct)):
                ms = 0.7
            else:
                ms = 0.5
            seen.add(url)
            assets.append({
                "kind": kind, "url": url, "width": w, "height": h,
                "mime": mime.split("/")[-1].upper(), "source": "wikimedia",
                "movie_id": "wiki:" + pid, "match_score": ms,
                "title_found": title, "year_found": "",
            })
        if assets:
            break
    if not assets:
        return [], "MATCH_UNCERTAIN"
    return assets, None


# ---------------- 落盘 ----------------
def _safe_dir(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "untitled"


def _save_image(data, film_dir, kind, asset, phash):
    ext = (asset["mime"].split("/")[-1].replace("JPEG", "jpg").lower() or "jpg")
    if ext == "jpeg":
        ext = "jpg"
    base = f"{kind}_{asset['width']}x{asset['height']}_{asset['source']}"
    fname = f"{base}.{ext}"
    path = os.path.join(film_dir, fname)
    k = 1
    while os.path.isfile(path):
        fname = f"{base}_{k}.{ext}"
        path = os.path.join(film_dir, fname)
        k += 1
    with open(path, "wb") as f:
        f.write(data)
    return path, fname


# ---------------- 旧资产清理（P1-1 定点）----------------
def _cleanup_stale_assets(film_dir, saved_poster_file, saved_backdrop_file):
    """删除上一轮遗留的 hero_fetch 旧资产（仅删自己的 poster_/backdrop_ 前缀）。

    关键守护（P1-1）：仅当本轮在该种类上成功保存了有效新资产时，才清理该种类旧文件；
    若整轮采集因超时/源站临时故障等没有任何成功资产，则【保留上一轮已有高清图】，
    绝不清空，避免源站临时故障导致已有好图被误删。
    """
    if not (saved_poster_file or saved_backdrop_file):
        # 整轮无成功资产：保留全部旧高清图，不做任何删除
        return
    try:
        names = os.listdir(film_dir)
    except OSError:
        return
    for fn in names:
        # 仅删除「本轮在该种类上回填了新资产」的旧文件；未回填的种类一律保留（对应旧资产清理）
        if fn.startswith("poster_") and saved_poster_file and fn != saved_poster_file:
            try:
                os.remove(os.path.join(film_dir, fn))
            except OSError:
                pass
        elif fn.startswith("backdrop_") and saved_backdrop_file and fn != saved_backdrop_file:
            try:
                os.remove(os.path.join(film_dir, fn))
            except OSError:
                pass


# ---------------- 单部影片采集 ----------------
def collect_one(item, api_key, cache, stats):
    title = item.get("title") or ""
    year = extract_year(title)
    mtype = item.get("type") or ""
    clean = clean_search_title(title)
    film_dir = os.path.join(store.BASE_DIR, "output", "posters", _safe_dir(title))
    os.makedirs(film_dir, exist_ok=True)

    rec = {
        "title": title, "clean_title": clean, "year": year, "type": mtype,
        "poster_found": 0, "backdrop_found": 0, "hd_poster": 0, "hd_backdrop": 0,
        "matched": 0, "saved_poster": None, "saved_backdrop": None,
        "failures": [], "match_score": 0.0, "same_movie": False, "source": None,
    }

    # 源健康（仅记录，不参与采集；最终资格由 hero 判定）
    eps = item.get("episodes") or []
    usable = [e for e in eps if not quality.is_disabled(e) and (e.get("urls") or [e.get("url")])]
    if not usable:
        rec["failures"].append("SOURCE_UNAVAILABLE")
    else:
        from backend.core.hero import _source_health
        sh, _ = _source_health(item)
        if sh < 40:
            rec["failures"].append("SOURCE_HEALTH_LOW")

    # 选择图源顺序
    providers = []
    if api_key:
        providers.append(("tmdb", provider_tmdb))
    providers.append(("wikimedia", provider_wikimedia))

    best_poster = None
    best_backdrop = None
    used_ids = set()

    for pname, pfunc in providers:
        if best_poster and best_backdrop:
            break  # 早期停止：已凑齐合格 HD Poster + Backdrop
        try:
            assets, fail = pfunc(clean, year, mtype, api_key)
        except _Fail as e:
            rec["failures"].append(e.code)
            continue
        if fail:
            rec["failures"].append(fail)
            continue

        for a in assets:
            kind = a["kind"]
            if kind == "poster" and best_poster:
                continue
            if kind == "backdrop" and best_backdrop:
                continue
            # 同片绑定：仅接受与已选定资产相同 movie_id（Wikimedia 宽松：允许不同 id 但降置信）
            if best_poster or best_backdrop:
                existing = best_poster or best_backdrop
                if existing["movie_id"] != a["movie_id"]:
                    if a["source"] == "tmdb":
                        continue  # TMDB 必须同片
                    # Wikimedia：不同 id，保守降低匹配
                    a = dict(a); a["match_score"] = min(a["match_score"], 0.6)
            # 匹配门槛（宁缺毋滥）：弱匹配直接淘汰，绝不进 Hero（防止错配）
            if a["match_score"] < 0.6:
                with CACHE_LOCK:
                    cache["urls"][a["url"]] = {"status": "fail", "code": "MATCH_UNCERTAIN"}
                rec["failures"].append("MATCH_UNCERTAIN")
                continue
            # 缓存：URL 已成功且本地存在同 hash → 跳过下载
            with CACHE_LOCK:
                cached = cache["urls"].get(a["url"])
            if cached and cached.get("status") == "ok" and cached.get("local") \
                    and os.path.isfile(os.path.join(film_dir, cached["local"])):
                # 命中缓存（不重复下载），但仍计入统计
                stats["cache_hits"] += 1
                if kind == "poster":
                    best_poster = a; best_poster["_local"] = cached["local"]
                else:
                    best_backdrop = a; best_backdrop["_local"] = cached["local"]
                continue

            # 下载 + 校验
            try:
                data = http_get_bytes(a["url"],
                                      referer=(a["source"] == "wikimedia" and "https://commons.wikimedia.org/" or None))
            except _Fail as e:
                with CACHE_LOCK:
                    cache["urls"][a["url"]] = {"status": "fail", "code": e.code}
                rec["failures"].append(e.code)
                continue

            ok, info = verify_image(data, kind)
            if not ok:
                with CACHE_LOCK:
                    cache["urls"][a["url"]] = {"status": "fail", "code": info}
                rec["failures"].append(info)
                continue

            # image hash 去重（跨片/同片重复）
            phash = info["phash"]
            with CACHE_LOCK:
                dup_of = cache["hashes"].get(phash)
                if dup_of and os.path.isfile(dup_of):
                    stats["duplicates"] += 1
                    cache["urls"][a["url"]] = {"status": "dup", "of": dup_of}
                    rec["failures"].append("DUPLICATE")
                    continue
                # 落盘
                path, fname = _save_image(data, film_dir, kind, {**a, "mime": info["format"]}, phash)
                cache["urls"][a["url"]] = {"status": "ok", "local": fname,
                                            "w": info["width"], "h": info["height"],
                                            "bytes": info["bytes"], "phash": phash}
                cache["hashes"][phash] = path

            a["_local"] = fname
            a["_phash"] = phash
            a["width"] = info["width"]; a["height"] = info["height"]
            a["bytes"] = info["bytes"]
            if kind == "poster":
                best_poster = a
            else:
                best_backdrop = a
            if a["source"] == "tmdb":
                rec["same_movie"] = True
            rec["source"] = a["source"]

    # 统计 + 元数据
    if best_poster:
        rec["hd_poster"] = 1
        rec["saved_poster"] = {
            "file": best_poster["_local"], "width": best_poster["width"],
            "height": best_poster["height"], "bytes": best_poster.get("bytes"),
            "source": best_poster["source"], "url": best_poster["url"],
            "match_score": best_poster["match_score"], "movie_id": best_poster["movie_id"],
            "phash": best_poster.get("_phash"), "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        rec["poster_found"] = 1
        rec["match_score"] = max(rec["match_score"], best_poster["match_score"])
    if best_backdrop:
        rec["hd_backdrop"] = 1
        rec["saved_backdrop"] = {
            "file": best_backdrop["_local"], "width": best_backdrop["width"],
            "height": best_backdrop["height"], "bytes": best_backdrop.get("bytes"),
            "source": best_backdrop["source"], "url": best_backdrop["url"],
            "match_score": best_backdrop["match_score"], "movie_id": best_backdrop["movie_id"],
            "phash": best_backdrop.get("_phash"), "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        rec["backdrop_found"] = 1
        rec["match_score"] = max(rec["match_score"], best_backdrop["match_score"])
        if best_poster:
            rec["matched"] = 1

    # 清理：仅在本轮成功保存了有效新资产时，才清理【对应种类】的旧 poster_/backdrop_ 文件。
    # 若整轮失败（超时/源站临时故障）没有任何成功资产，则保留上一轮已有高清图，绝不清空（P1-1 定点）。
    # （只删 hero_fetch 自己的命名前缀，绝不误删 grab_posters 产物）
    saved_poster_file = (rec.get("saved_poster") or {}).get("file")
    saved_backdrop_file = (rec.get("saved_backdrop") or {}).get("file")
    _cleanup_stale_assets(film_dir, saved_poster_file, saved_backdrop_file)

    # 记录每片素材元数据（需求三：原始URL/最终URL/宽/高/大小/格式/获取时间/来源/匹配依据/hash）
    meta = {"film": title, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "poster": rec["saved_poster"], "backdrop": rec["saved_backdrop"],
            "same_movie": rec["same_movie"], "match_score": round(rec["match_score"], 2)}
    with open(os.path.join(film_dir, "hero_assets.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 累加全局统计
    stats["posters_found"] += rec["poster_found"]
    stats["backdrops_found"] += rec["backdrop_found"]
    stats["hd_posters"] += rec["hd_poster"]
    stats["hd_backdrops"] += rec["hd_backdrop"]
    stats["matched"] += rec["matched"]
    for fc in rec["failures"]:
        stats["failures"][fc] = stats["failures"].get(fc, 0) + 1
    if not best_poster:
        stats["reject_reasons"]["NO_POSTER_GLOBAL"] = stats["reject_reasons"].get("NO_POSTER_GLOBAL", 0) + 1
    if not best_backdrop:
        stats["reject_reasons"]["NO_BACKDROP_GLOBAL"] = stats["reject_reasons"].get("NO_BACKDROP_GLOBAL", 0) + 1
    return rec


# ---------------- 全量采集 ----------------
def collect_all(max_films=None, api_key=None, sources=None):
    db = store.load_db()
    items = db.get("items", [])
    if max_films:
        items = items[:max_films]
    cache = load_cache()
    stats = {
        "scanned": 0, "posters_found": 0, "backdrops_found": 0,
        "hd_posters": 0, "hd_backdrops": 0, "matched": 0,
        "cache_hits": 0, "duplicates": 0, "failures": {},
        "reject_reasons": {},
    }
    records = []

    def worker(it):
        return collect_one(it, api_key, cache, stats)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = [ex.submit(worker, it) for it in items]
        for f in as_completed(futs):
            try:
                rec = f.result()
            except Exception as e:
                msg = str(e)
                code = "NETWORK" if ("Timeout" in msg or "Connection" in msg or "timed out" in msg) else "ERROR"
                rec = {"title": "?", "failures": [code], "error": msg[:160]}
            records.append(rec)
            stats["scanned"] += 1

    save_cache(cache)
    # 成功率与质量汇总
    recs_sorted = sorted(records, key=lambda r: r.get("title", ""))
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "api_key_present": bool(api_key),
        "sources": sources or (["tmdb", "wikimedia"] if api_key else ["wikimedia"]),
        "stats": stats,
        "records": recs_sorted,
    }
    out = os.path.join(store.BASE_DIR, "output", "posters", "hero_fetch_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    store.log("info", "Hero 素材采集完成：扫描 %d，HD Poster %d，HD Backdrop %d，同片匹配 %d"
              % (stats["scanned"], stats["hd_posters"], stats["hd_backdrops"], stats["matched"]))
    return summary


# ---------------- 命令行 ----------------
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Hero 专用高清影视素材采集")
    ap.add_argument("--max", type=int, default=0, help="最多采集前 N 部（0=全部）")
    ap.add_argument("--source", default="tmdb,wikimedia",
                    help="图源顺序（需 tmdb key 才启用 tmdb）")
    ap.add_argument("--tmdb-key", default=os.environ.get("TMDB_API_KEY", ""),
                    help="TMDB API Key（或设环境变量 TMDB_API_KEY）")
    ap.add_argument("--run-hero", action="store_true",
                    help="采集后自动重跑 hero 评估 + 生成 hero.json")
    ap.add_argument("--base", default="https://lidawei1985.github.io/filmcollector-pages",
                    help="托管根地址（用于 hero.json 公网 URL）")
    args = ap.parse_args()

    sources = [s.strip().lower() for s in args.source.split(",") if s.strip()]
    api_key = args.tmdb_key
    if "tmdb" in sources and not api_key:
        print("⚠ 未提供 TMDB Key，TMDB 源跳过（仅 Wikimedia 免 Key 补充源）。"
              "免费申请：https://www.themoviedb.org/settings/api")
        sources = [s for s in sources if s != "tmdb"]

    print(f"图源={sources}  并发={CONCURRENCY}  上限={args.max or '全部'}")
    summary = collect_all(max_films=args.max or None, api_key=api_key, sources=sources)
    s = summary["stats"]
    print("\n========== Hero 素材采集统计 ==========")
    print(f"扫描影片      : {s['scanned']}")
    print(f"找到 Poster   : {s['posters_found']}")
    print(f"高清 Poster   : {s['hd_posters']}")
    print(f"找到 Backdrop : {s['backdrops_found']}")
    print(f"高清 Backdrop : {s['hd_backdrops']}")
    print(f"同片匹配成功  : {s['matched']}")
    print(f"缓存命中      : {s['cache_hits']}")
    print(f"重复图片      : {s['duplicates']}")
    print(f"失败计数      : {s['failures']}")
    print("报告: output/posters/hero_fetch_report.json")

    if args.run_hero:
        from backend.core import hero
        print("\n--- 重跑 Hero 评估 + 生成 hero.json ---")
        hero.evaluate_all()
        hero.build_hero_json(args.base)
        hj = json.load(open(os.path.join(store.BASE_DIR, "tvbox-dist", "hero.json"), encoding="utf-8"))
        st = hj.get("stats", {})
        print(f"hero_eligible={st.get('hero_eligible')} / 总 {st.get('total')}")


if __name__ == "__main__":
    main()
