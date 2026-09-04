# -*- coding: utf-8 -*-
"""
channel_router.py —— ChannelRouter 路由分发 + 5 channel feed 输出（P0-3）
============================================================================
- 走 ContentClassifier 把 aggregated.json 的 items 分类
- 输出 5 个独立验证 feed（P2-1/C4 起写入独立子目录，不再与 APK 契约 feed 同名冲突）：
    dist/channels/normal.json / child.json / adult.json / sports.json / unknown.json
    （router schema：dedup_id/origins/classify_evidence，仅供内部验证/人工复核）
- APK 发布契约 feed 仍由 apk_feed.write_apk_feeds() 输出到 dist/feed.<mode>.json
  （APK_CONTRACT schema：name/vodId/playUrl），两者互不覆盖。
- 灰度开关 FILMCOLLECTOR_USE_CHANNEL_ROUTER=0：不输出新 feed (默认)
                            =1: 输出 5 个 channel 验证 feed，旧链路不受影响
- 不删除任何旧文件，完全可回滚
"""
import json
import os
import shutil
from datetime import datetime, timezone

from . import store
from . import aggregator
from . import content_classifier

DIST_DIR = store.DIST_DIR if hasattr(store, "DIST_DIR") else os.path.join(store.BASE_DIR, "dist")
# P2-1/C4：router 验证产物独立子目录，与 APK 契约 feed（dist/feed.<mode>.json）彻底分离
CHANNELS_DIR = os.path.join(DIST_DIR, "channels")
SCHEMA_VERSION = "2.0"

CHANNELS = ("normal", "child", "adult", "sports", "unknown")
USE_CHANNEL_ROUTER = os.environ.get("FILMCOLLECTOR_USE_CHANNEL_ROUTER", "0") == "1"


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _abs_image_url(url, base="http://127.0.0.1:8800/images/"):
    """相对路径图 → 完整 URL；已是 http(s) 原样。"""
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("images/"):
        return base + url[len("images/"):]
    return url


def _load_aggregated_items():
    """拉 aggregated.json.items，按 list 形式返回。"""
    data = aggregator.load_aggregated()
    items_dict = data.get("items", {})
    items = []
    for did, it in items_dict.items():
        item = dict(it)
        item["dedup_id"] = did
        # source_id 默认从 origins[] 取第一个
        if not item.get("source_id"):
            origins = item.get("origins") or []
            if origins and isinstance(origins, list):
                item["source_id"] = origins[0].get("source_id") or ""
        # original_category 默认从 canonical_categories[0] 取
        if not item.get("original_category"):
            ccs = item.get("canonical_categories") or item.get("aggregated_categories") or []
            if ccs:
                item["original_category"] = str(ccs[0])
        items.append(item)
    return items


def _format_item_for_channel(it):
    """统一字段格式（与旧 feed 契约兼容）。"""
    year = it.get("year") or ""
    try:
        year_v = int(str(year)[:4]) if str(year).strip() else 0
    except Exception:
        year_v = 0
    return {
        "dedup_id": it.get("dedup_id"),
        "title": it.get("title", ""),
        "year": year_v,
        "director": it.get("director", ""),
        "actors": it.get("actors") or [""],
        "summary": it.get("summary", ""),
        "poster": _abs_image_url(it.get("poster", "")),
        "poster_hd": _abs_image_url(it.get("poster_hd", "") or it.get("poster", "")),
        "backdrop": _abs_image_url(it.get("backdrop", "")),
        "is_adult": it.get("is_adult", False),
        "channel": it.get("channel", "normal"),  # 新增字段：明确 channel
        "classify_confidence": it.get("classify_confidence", 0.0),
        "classify_evidence": it.get("classify_evidence", ""),
        "classify_matched": it.get("classify_matched", ""),
        "content_type": it.get("content_type", "movie"),
        "playable": bool(it.get("playable", False)),
        "aggregated_categories": it.get("canonical_categories") or it.get("aggregated_categories", []),
        "first_seen_at": it.get("first_seen_at", ""),
        "collected_at": it.get("collected_at", ""),
        "origins": it.get("origins", []),
    }


def build_channel_feed(items, channel):
    """单 channel feed 字典。"""
    bucket = [it for it in items if it.get("channel") == channel]
    formatted = [_format_item_for_channel(it) for it in bucket]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "mode": channel,            # V2 新增：每个 feed mode 独立
        "channel": channel,         # 同 mode，明确字段
        "router": "content_classifier",
        "router_version": content_classifier.SCHEMA_VERSION if hasattr(content_classifier, "SCHEMA_VERSION") else "2.0",
        "use_channel_router": USE_CHANNEL_ROUTER,
        "count": len(formatted),
        "items": formatted,
    }


def build_all_channel_feeds(out_dir=CHANNELS_DIR, enable=None):
    """一次性构建 5 个 channel 验证 feed；enable=None 时从环境变量读。

    默认输出 dist/channels/{channel}.json（P2-1/C4：与 APK 契约 feed 分离）。
    返回 {channel: feed_dict}
    """
    if enable is None:
        enable = USE_CHANNEL_ROUTER
    if not enable:
        store.log("warn", "[channel_router] USE_CHANNEL_ROUTER=0, 跳过 5 channel feed 生成")
        return {}

    items = _load_aggregated_items()
    store.log("info", "[channel_router] input aggregated items: %d" % len(items))

    # 分类 + 分仓
    buckets, counts = content_classifier.classify_and_route(items)
    store.log("info", "[channel_router] bucketed: %s" % counts)

    os.makedirs(out_dir, exist_ok=True)
    feeds = {}
    for ch in CHANNELS:
        feed = build_channel_feed(items, ch)
        path = os.path.join(out_dir, "%s.json" % ch)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(feed, f, ensure_ascii=False, indent=2)
        feeds[ch] = feed
        store.log("info", "[channel_router] wrote %s (%d items)" % (path, len(feed["items"])))

    # channel_router_summary.json（灰度报告用）
    summary = {
        "generated_at": _now_iso(),
        "use_channel_router": True,
        "router": "content_classifier",
        "total_aggregated_items": len(items),
        "channel_counts": counts,
        "data_loss_check": sum(counts.values()) - len(items),  # 应该 = 0
        "feed_files": {ch: "feed.%s.json" % ch for ch in CHANNELS},
    }
    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    store.log("info", "[channel_router] summary: %s" % summary_path)
    feeds["_summary"] = summary
    return feeds


def is_enabled():
    """对外暴露：当前是否启用新链路（避免重复判断）。"""
    return USE_CHANNEL_ROUTER


if __name__ == "__main__":
    import pprint
    # 默认不启用（必须 env=1 才跑）
    if is_enabled():
        print("FILMCOLLECTOR_USE_CHANNEL_ROUTER=1, 开始构建 5 channel feeds...")
        feeds = build_all_channel_feeds()
        print("完成: %s" % list(feeds.keys()))
        pprint.pprint(feeds.get("_summary", {}))
    else:
        print("FILMCOLLECTOR_USE_CHANNEL_ROUTER=0, 跳过（生产链路保持兼容）。")
        print("要启用请 export FILMCOLLECTOR_USE_CHANNEL_ROUTER=1")
