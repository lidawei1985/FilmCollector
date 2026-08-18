# -*- coding: utf-8 -*-
"""
auto_feed.py —— 自动找片源（合法公共领域 / CC 合集）
====================================================
注意边界（不是偷懒，是法律与安全硬线）：
- 不做"全网乱爬"。只从一批**人工筛选**的合法公共领域 / CC 合集里自动挑新片。
- 当前主源：Internet Archive（feature_films / prelinger / animation_and_cartoons /
  open_movies / stock_footage），内容经确认多为公有领域或 CC 授权，可公开播放。
- 架构上支持**多源（providers）**：任何"能稳定给出'直接可播直链'的合法来源"都可按
  PROVIDERS 协议接入。archive.org 是被验证唯一稳定、可直接播放的公共领域影片源；
  其它源（如 Openverse CC0）仅在能取到'直接可播直链'时才入库，取不到就跳过——
  绝不把"看似有、点开播不了"的片塞进订阅，保证'可播'这条底线（不将就）。

对外暴露：
- CURATED_COLLECTIONS : IA 可自动采集的合集清单（小白可在设置里勾选）
- PROVIDERS           : 已注册的内容源（"ia" 默认开启；"openverse" 需显式开启）
- discover_candidates : 跨启用的源按"最新上传"拉候选，排除已入库，带 provider 标记
- fetch_one           : 按 provider 取单部影片详情（含 episodes/海报）；无直链返回 None
- last_blocked        : 本轮是否因上游限流而中止（供流水线标记 degraded）
"""
import os
import json
import time
import re
import urllib.parse

from . import store, scraper, image_cache, net

# 人工筛选的合法公共领域 / CC 合集（Internet Archive）。仅这些，不全网乱爬。
CURATED_COLLECTIONS = [
    {"key": "feature_films",        "collection": "feature_films",        "label": "公共领域电影",   "license": "Public Domain"},
    {"key": "prelinger",            "collection": "prelinger",            "label": "Prelinger 资料片", "license": "Public Domain"},
    {"key": "animation_cartoons",   "collection": "animation_and_cartoons","label": "公共领域动画",   "license": "Public Domain"},
    {"key": "open_movies",          "collection": "open_movies",          "label": "开源/CC 短片",   "license": "CC"},
    {"key": "stock_footage",        "collection": "stock_footage",        "label": "公共领域素材",   "license": "Public Domain"},
]

# 上一轮发现是否因上游限流而被迫中止（供流水线判断健康度）
last_blocked = False


# ----------------------------- Internet Archive 源 -----------------------------
def _ia_http_json(url):
    try:
        return net.get_json(url, timeout=20, min_interval=0.8)
    except net.RateLimited:
        raise
    except Exception as e:
        store.log("warn", f"auto_feed 检索失败 {url}：{e}")
        return None


def _ia_discover(max_per, existing_ids, existing_titles, categories=None):
    cands = []
    cats = [c for c in CURATED_COLLECTIONS if (not categories) or (c["key"] in categories)]
    if not cats:
        cats = CURATED_COLLECTIONS
    for c in cats:
        q = f'collection:{c["collection"]} AND mediatype:movies'
        url = ("https://archive.org/advancedsearch.php?q=" + urllib.parse.quote(q) +
               "&fl[]=identifier&fl[]=title&fl[]=addeddate"
               "&sort[]=addeddate+desc&rows=" + str(int(max_per)) + "&output=json")
        try:
            data = _ia_http_json(url)
        except net.RateLimited:
            raise
        if not data:
            continue
        for doc in data.get("response", {}).get("docs", []):
            ident = doc.get("identifier")
            title = doc.get("title") or ident
            if not ident or ident in existing_ids:
                continue
            norm = (title or "").strip().lower()
            if norm and norm in existing_titles:
                continue
            cands.append({
                "identifier": ident,
                "title": title,
                "provider": "ia",
                "collection": c["collection"],
                "collection_label": c["label"],
                "license": c["license"],
                "source_url": f"https://archive.org/details/{ident}",
            })
    return cands


def _ia_fetch_one(identifier):
    meta_url = f"https://archive.org/metadata/{identifier}"
    try:
        data = net.get_json(meta_url, timeout=25, min_interval=0.8)
    except net.RateLimited:
        raise
    if not data:
        store.log("warn", f"auto_feed metadata 失败 {identifier}")
        return None
    meta = data.get("metadata") or {}
    files = data.get("files") or []
    if not meta and not files:
        return None

    # 标题清洗（去掉 archive.org 详情页丑后缀）
    raw_title = (meta.get("title") or identifier).strip()
    raw_title = re.sub(r"\s*:\s*Free Download,?\s*Borrow,?\s*and Streaming\s*:\s*Internet Archive\s*$", "", raw_title, flags=re.I)
    raw_title = re.sub(r"\s*:\s*Internet Archive\s*$", "", raw_title, flags=re.I)
    if not raw_title:
        raw_title = identifier

    # 播放地址：只取 mp4，排除 sample/trailer/缩略图等
    # 每部片同时记录多形态直链（download + serve）作为候选，发布时主链用"已验证可用"
    # 的那条，其余留作兜底（单点故障不影响观看）。
    cands = []
    for f in files:
        name = (f.get("name") or "")
        if not name.lower().endswith(".mp4"):
            continue
        if re.search(r"(sample|trailer|thumbnail|preview)", name, re.I):
            continue
        dl = f"https://archive.org/download/{identifier}/{name}"
        urls = [dl, f"https://archive.org/serve/{identifier}/{name}"]
        cands.append({"name": name, "url": dl, "urls": urls, "fmt": f.get("format") or ""})
    if not cands:
        return None

    episodes = [{"name": scraper._quality_label(c["url"]), "url": c["url"],
                 "urls": c["urls"], "line": "默认线路"}
                for c in cands]
    episodes = scraper._collapse_quality_variants(episodes)
    if not episodes:
        return None

    item = {
        "title": raw_title,
        "description": (meta.get("description") or "").strip(),
        "year": str(meta.get("year") or "").strip(),
        "type": "电影",
        "region": (meta.get("language") or "").strip(),
        "episodes": episodes,
        "source_id": identifier,
        "source_url": f"https://archive.org/details/{identifier}",
        "license": "Public Domain / CC",
        "status": "ok",
    }
    # 海报：优先 metadata.image，否则 archive.org 缩略图服务；下载进本地图库，发布后 APK 从你自己的地址读
    poster = (meta.get("image") or f"https://archive.org/services/img/{identifier}").strip()
    item["poster"] = image_cache.cache_image(poster, prefix="poster") or ""
    return item


# ----------------------------- Openverse (CC0) 源（可选） -----------------------------
# 仅当能取到"直接可播直链"时才入库；取不到直接返回 None（跳过），绝不污染订阅。
_VIDEO_EXT = (".mp4", ".webm", ".ogv", ".mkv", ".m3u8")


def _openverse_discover(max_per, existing_ids, existing_titles):
    """从 Openverse 拉 CC0 视频候选（仅作补充源）。返回带 provider=openverse 的候选。"""
    cands = []
    url = ("https://api.openverse.org/v1/videos/?license_type=public_domain"
           "&mature=false&page_size=" + str(int(max_per)))
    try:
        data = net.get_json(url, timeout=20, min_interval=1.0)
    except net.RateLimited:
        raise
    if not data:
        return cands
    for it in data.get("results", []):
        src = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        if not src or not title:
            continue
        norm = title.lower()
        if norm in existing_titles:
            continue
        # 用源页 URL 当 identifier（fetch 时会尝试解析出直接直链）
        cands.append({
            "identifier": src,
            "title": title,
            "provider": "openverse",
            "collection": "openverse_cc0",
            "collection_label": "Openverse CC0",
            "license": it.get("license") or "CC0",
            "source_url": src,
        })
    return cands


def _openverse_fetch_one(source_url):
    """尝试从 Openverse 条目解析出'直接可播直链'；取不到则返回 None（跳过该候选）。"""
    # Openverse 列表结果里常带 file_url（直接媒体），少数情况下仅给源页。
    # 这里只接受"直接以视频扩展名结尾"的直链，避免把源页当播放地址塞进订阅。
    try:
        data = net.get_json(source_url, timeout=20, min_interval=1.0)
    except net.RateLimited:
        raise
    except Exception:
        data = None
    candidates = []
    if isinstance(data, dict):
        for key in ("file_url", "url"):
            u = (data.get(key) or "").strip()
            if u.lower().endswith(_VIDEO_EXT):
                candidates.append(u)
    if not candidates:
        return None
    # 取首个直链；多清晰度合并为一条
    episodes = [{"name": "默认", "url": candidates[0], "urls": candidates, "line": "默认线路"}]
    return {
        "title": (data.get("title") or source_url).strip(),
        "description": (data.get("description") or "").strip(),
        "year": str(data.get("year") or "").strip(),
        "type": "电影",
        "region": "",
        "episodes": episodes,
        "source_id": source_url,
        "source_url": source_url,
        "license": "CC0",
        "status": "ok",
        "poster": image_cache.cache_image((data.get("thumbnail") or ""), prefix="poster") or "",
    }


# ----------------------------- 源注册表 -----------------------------
PROVIDERS = {
    "ia":        {"label": "Internet Archive", "discover": _ia_discover, "fetch": _ia_fetch_one, "default_on": True},
    "openverse": {"label": "Openverse CC0",   "discover": _openverse_discover, "fetch": _openverse_fetch_one, "default_on": False},
}


def _enabled_providers():
    cfg = store.load_config()
    chosen = cfg.get("auto_providers")
    if not chosen:
        chosen = [k for k, v in PROVIDERS.items() if v.get("default_on")]
    return [k for k in chosen if k in PROVIDERS]


def discover_candidates(max_per=8, existing_ids=None, existing_titles=None, categories=None, providers=None):
    """跨启用的源拉候选，排除已入库，返回带 provider 标记的 list。

    任一源被限流封禁（RateLimited）会标记 last_blocked=True 并中止该源发现，
    不影响其它源；调用方据此把整体健康度降为 degraded，但仍发布现有片库。
    """
    global last_blocked
    last_blocked = False
    existing_ids = set(existing_ids or [])
    existing_titles = set(t for t in (existing_titles or []) if t)
    provs = providers or _enabled_providers()

    cands = []
    seen = set()
    for pkey in provs:
        p = PROVIDERS[pkey]
        try:
            if pkey == "ia":
                got = p["discover"](max_per, existing_ids, existing_titles, categories)
            else:
                got = p["discover"](max_per, existing_ids, existing_titles)
        except net.RateLimited:
            last_blocked = True
            store.log("warn", f"源 {p['label']} 被限流，本轮跳过其发现")
            continue
        except Exception as e:
            store.log("warn", f"源 {p['label']} 发现异常：{e}")
            continue
        for c in got:
            ident = c.get("identifier")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            cands.append(c)
    store.log("info", f"auto_feed 发现候选 {len(cands)} 部（来自 {len(provs)} 个启用的源）")
    return cands


def fetch_one(identifier, provider="ia"):
    """按 provider 取单部影片详情。无直接可播直链返回 None。"""
    p = PROVIDERS.get(provider)
    if not p:
        return None
    try:
        return p["fetch"](identifier)
    except net.RateLimited:
        global last_blocked
        last_blocked = True
        store.log("warn", f"源 {p['label']} 被限流，放弃 {identifier}")
        return None
    except Exception as e:
        store.log("warn", f"fetch_one({provider}) 失败 {identifier}：{e}")
        return None


def collect_new(candidates, max_new=20, interval=None):
    """逐条采集候选详情并入库（调用方负责去重与保存）。返回采集到的 item 列表。"""
    if interval is None:
        interval = store.load_config().get("request_interval", 1.5)
    items = []
    for c in candidates[:int(max_new)]:
        it = fetch_one(c["identifier"], c.get("provider", "ia"))
        if it:
            items.append(it)
        if interval:
            time.sleep(interval)
    return items
