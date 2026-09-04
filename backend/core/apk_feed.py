# -*- coding: utf-8 -*-
"""
apk_feed.py —— 面向三影视 APK 的统一契约 Feed（FilmCollector -> 星幕/心屋/夜航）
========================================================================
三 APK 的 DataManager.fetchFeed() 固定解析契约（见各 APK DataManager）：
  GET /feed.<mode>.json  -> { items:[ {name, vodId, sourceId, sourceName,
        originalCategoryName, cover, playUrl, aggregateCategoryId,
        aggregateCategoryName, appMode, poster} ] }
  GET /version.json      -> { "<mode>": {"version": <iso>, "count": N}, ... }

本模块：
  - 从聚合结果(aggregator)映射出上述契约字段（血缘 sourceId/sourceName、
    原始分类 originalCategoryName、播放地址 playUrl 全部可追溯）；
  - 海报 cover/poster 补全为绝对 URL；
  - 播放地址 playUrl 经 source_quality 筛查（adult 模式严格剔除，normal/child 仅剥离坏线路）；
  - mode 隔离：normal / adult / child 各自独立，互不串（child = normal 中儿童向子集）。
字段为「加法」扩展，不影响既有自定义 schema 消费方。
"""
import os
import re
import json
import hashlib
from datetime import datetime, timezone

from . import aggregator
from . import source_quality
from . import store

DIST_DIR = os.path.join(store.BASE_DIR, "dist")

# 儿童向分类（child 模式子集判定）
KID_CATS = {"少儿", "动画", "儿童", "亲子", "儿歌", "早教", "幼教", "动漫", "少儿动画", "儿童剧"}


def _slug(s):
    if not s:
        return ""
    # 允许 Unicode 文字（含中文分类名），仅把空白/标点替换为下划线，保证非空且稳定
    return re.sub(r"[^\w]+", "_", str(s)).strip("_")[:48]


def _vid(dedup_id):
    """由 dedup_id 生成稳定正整数 vodId（APK 用 sourceId#vodId 去重键）。"""
    if not dedup_id:
        return 0
    return int(hashlib.sha1(str(dedup_id).encode("utf-8")).hexdigest()[:12], 16) % (2 ** 31)


def _load_items():
    data = aggregator.load_aggregated()
    return data.get("items", {})


_ROUTER_CHANNEL_MAP = None


def _use_router():
    """灰度开关：=1 时 mode 分仓判定改走 ContentClassifier 频道（与 channel_router 同一判定路径）。
    缺省=0，行为与旧链路完全一致。"""
    return os.environ.get("FILMCOLLECTOR_USE_CHANNEL_ROUTER", "0") == "1"


def _router_channel_map():
    """env=1：对全量聚合条目跑一次分类器，返回 {dedup_id: channel}（进程内缓存一次）。
    分类输入映射与 channel_router._load_aggregated_items 完全一致，保证两条链路判定相同。"""
    global _ROUTER_CHANNEL_MAP
    if _ROUTER_CHANNEL_MAP is not None:
        return _ROUTER_CHANNEL_MAP
    from . import content_classifier
    items = []
    for did, it in _load_items().items():
        item = dict(it)
        item["dedup_id"] = item.get("dedup_id") or did
        origins = item.get("origins") or []
        if not item.get("source_id") and origins and isinstance(origins, list):
            item["source_id"] = origins[0].get("source_id") or ""
        if not item.get("original_category"):
            ccs = item.get("canonical_categories") or item.get("aggregated_categories") or []
            if ccs:
                item["original_category"] = str(ccs[0])
        items.append(item)
    content_classifier.classify_items(items)
    _ROUTER_CHANNEL_MAP = {it["dedup_id"]: (it.get("channel") or "unknown") for it in items}
    return _ROUTER_CHANNEL_MAP


def _mode_match(it, mode):
    if _use_router():
        # 新链路：分仓判定完全交给 ContentClassifier（规则+源白名单+血缘，P1 已验证）
        ch = _router_channel_map().get(it.get("dedup_id"), "unknown")
        if mode == "all":
            return ch != "unknown"   # all 不含人工复核仓
        if mode == "normal":
            return ch == "normal"    # sports/unknown 不进 normal
        if mode == "adult":
            return ch == "adult"
        if mode == "child":
            return ch == "child"
        return False
    # ---- 旧链路（env=0 默认）：is_adult 布尔 + KID_CATS 关键词子集，保持原样 ----
    is_adult = bool(it.get("is_adult"))
    if mode == "all":
        return True
    if mode == "normal":
        return not is_adult
    if mode == "adult":
        return is_adult
    if mode == "child":
        if is_adult:
            return False
        cats = set(it.get("aggregated_categories") or [])
        return bool(cats & KID_CATS)
    return False


def _apk_item(it, mode, deep_scan=False):
    origins = it.get("origins") or []
    first = origins[0] if origins else {}
    poster = it.get("poster") or ""
    cats = it.get("aggregated_categories") or []

    # 播放地址筛查（跨 origin 收集）
    scr = source_quality.screen_item(it, mode=mode, deep_scan=deep_scan)
    if scr["excluded"]:
        return None  # adult 模式且无任何安全播放地址 -> 隔离，不进入夜航

    play_urls = scr["safe_urls"]
    play_url = "|".join(play_urls)

    return {
        "name": it.get("title", ""),
        "vodId": _vid(it.get("dedup_id")),
        "sourceId": first.get("source_id", ""),
        "sourceName": first.get("source_name", ""),
        "originalCategoryName": first.get("original_category", ""),
        "cover": poster,
        "poster": poster,
        "playUrl": play_url,
        "aggregateCategoryId": _slug(cats[0]) if cats else "",
        "aggregateCategoryName": cats[0] if cats else "",
        "appMode": mode,
        # 附加（向后兼容，不影响 APK 解析）
        "year": it.get("year", ""),
        "summary": it.get("summary", ""),
        "quality_score": it.get("quality_score", 0.0),
        "playable": bool(play_url),
        "screened_removed": scr["removed"],
    }


def build_apk_feed(mode, deep_scan=False):
    """返回 APK 契约的 items 列表（已 mode 隔离 + 源质量筛查）。"""
    items = _load_items()
    out = []
    for it in items.values():
        if not _mode_match(it, mode):
            continue
        m = _apk_item(it, mode, deep_scan=deep_scan)
        if m is None:
            continue
        if not m["name"]:
            continue
        out.append(m)
    return out


def build_version_map():
    """生成 version.json：{ mode: {version, count} }。version 取生成时刻 ISO，
    任意重生成都会变化，APK 据此决定是否重新全量同步。"""
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    counts = {}
    for mode in ("all", "normal", "adult", "child"):
        counts[mode] = len(build_apk_feed(mode))
    return {m: {"version": now, "count": counts[m]} for m in counts}


def write_apk_feeds(out_dir=DIST_DIR, deep_scan=False):
    """落盘 feed.<mode>.json 与 version.json 到 dist/。"""
    os.makedirs(out_dir, exist_ok=True)
    files = {}
    for mode in ("normal", "adult", "child", "all"):
        items = build_apk_feed(mode, deep_scan=deep_scan)
        path = os.path.join(out_dir, "feed.%s.json" % mode)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"items": items, "mode": mode, "count": len(items)}, f, ensure_ascii=False, indent=2)
        files[mode] = path
    vpath = os.path.join(out_dir, "version.json")
    with open(vpath, "w", encoding="utf-8") as f:
        json.dump(build_version_map(), f, ensure_ascii=False, indent=2)
    files["version"] = vpath
    return files


if __name__ == "__main__":
    import pprint
    pprint.pprint(write_apk_feeds())
