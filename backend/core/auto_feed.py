# -*- coding: utf-8 -*-
"""
auto_feed.py —— 自动找片源（合法公共领域 / CC 合集）
====================================================
注意边界（不是偷懒，是法律与安全硬线）：
- 不做"全网乱爬"。只从一批**人工筛选**的合法公共领域 / CC 合集里自动挑新片。
- 这些合集来自 Internet Archive（feature_films / prelinger / animation_and_cartoons /
  open_movies / stock_footage），内容经确认多为公有领域或 CC 授权，可公开播放。
- 工具只采集"有权访问且授权"的页面，不内置任何绕过站点防护的能力。

对外暴露：
- CURATED_COLLECTIONS : 可自动采集的合集清单（小白可在设置里勾选）
- discover_candidates : 用 archive.org advancedsearch 按"最新上传"拉候选，排除已入库
- fetch_one           : 取单部影片详情（复用 scraper.parse_detail），补 source_id/海报
"""
import os
import json
import time
import re
import urllib.parse

import requests

from . import store, scraper, image_cache

# 人工筛选的合法公共领域 / CC 合集（archive.org）。仅这些，不全网乱爬。
CURATED_COLLECTIONS = [
    {"key": "feature_films",        "collection": "feature_films",        "label": "公共领域电影",   "license": "Public Domain"},
    {"key": "prelinger",            "collection": "prelinger",            "label": "Prelinger 资料片", "license": "Public Domain"},
    {"key": "animation_cartoons",   "collection": "animation_and_cartoons","label": "公共领域动画",   "license": "Public Domain"},
    {"key": "open_movies",          "collection": "open_movies",          "label": "开源/CC 短片",   "license": "CC"},
    {"key": "stock_footage",        "collection": "stock_footage",        "label": "公共领域素材",   "license": "Public Domain"},
]


def _http_json(url):
    try:
        r = requests.get(url, headers={"User-Agent": store.UA_POOL[0]}, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        store.log("warn", f"auto_feed 检索失败 {url}：{e}")
        return None


def discover_candidates(max_per=8, existing_ids=None, existing_titles=None, categories=None):
    """从筛选合集里按「最新上传」拉候选影片，排除已入库的。

    返回 list[{identifier,title,collection,collection_label,license}]
    """
    existing_ids = set(existing_ids or [])
    existing_titles = set(t for t in (existing_titles or []) if t)
    cats = [c for c in CURATED_COLLECTIONS if (not categories) or (c["key"] in categories)]
    if not cats:
        cats = CURATED_COLLECTIONS

    cands = []
    seen = set()
    for c in cats:
        q = f'collection:{c["collection"]} AND mediatype:movies'
        url = ("https://archive.org/advancedsearch.php?q=" + urllib.parse.quote(q) +
               "&fl[]=identifier&fl[]=title&fl[]=addeddate"
               "&sort[]=addeddate+desc&rows=" + str(int(max_per)) + "&output=json")
        data = _http_json(url)
        if not data:
            continue
        for doc in data.get("response", {}).get("docs", []):
            ident = doc.get("identifier")
            title = doc.get("title") or ident
            if not ident or ident in existing_ids or ident in seen:
                continue
            norm = (title or "").strip().lower()
            if norm and norm in existing_titles:
                continue
            seen.add(ident)
            cands.append({
                "identifier": ident,
                "title": title,
                "collection": c["collection"],
                "collection_label": c["label"],
                "license": c["license"],
            })
    store.log("info", f"auto_feed 发现候选 {len(cands)} 部（来自 {len(cats)} 个合集）")
    return cands


def fetch_one(identifier):
    """取单部影片详情，补 source_id / 海报兜底 / 播放地址。失败或无播放地址返回 None。"""
    url = f"https://archive.org/details/{identifier}"
    try:
        html = scraper._fetch_html(url)
        item = scraper.parse_detail(None, html, url)
    except Exception as e:
        store.log("warn", f"auto_feed 详情失败 {identifier}：{e}")
        return None
    if not item:
        return None
    item["source_id"] = identifier
    item["source_url"] = url
    item["license"] = "Public Domain / CC"
    # 清洗 archive.org 详情页标题自带的丑后缀（如 " : Free Download, Borrow, and Streaming : Internet Archive"）
    raw_title = (item.get("title") or "").strip()
    raw_title = re.sub(r"\s*:\s*Free Download,?\s*Borrow,?\s*and Streaming\s*:\s*Internet Archive\s*$", "", raw_title, flags=re.I)
    raw_title = re.sub(r"\s*:\s*Internet Archive\s*$", "", raw_title, flags=re.I)
    if raw_title:
        item["title"] = raw_title
    # 海报：优先用详情页抓到的真实海报；都没有则兜底 archive.org 缩略图服务
    poster = (item.get("poster") or f"https://archive.org/services/img/{identifier}").strip()
    # 关键：把远程海报下载进本地图库，poster 改写为本地相对路径。
    # 这样发布时会随订阅包一起上传到你的仓库，APK 永远从你自己的地址读图，
    # 不再依赖 archive.org（国内不稳）。下载失败则留空，避免远程白屏。
    local = image_cache.cache_image(poster, prefix="poster")
    item["poster"] = local or ""
    # 必须有可播放地址
    if not item.get("episodes"):
        return None
    item["status"] = "ok"
    return item


def collect_new(candidates, max_new=20, interval=None):
    """逐条采集候选详情并入库（调用方负责去重与保存）。返回采集到的 item 列表。"""
    if interval is None:
        interval = store.load_config().get("request_interval", 1.5)
    items = []
    for c in candidates[:int(max_new)]:
        it = fetch_one(c["identifier"])
        if it:
            items.append(it)
        if interval:
            time.sleep(interval)
    return items
