# -*- coding: utf-8 -*-
"""
智能去重：以「片名 + 年份」为唯一标识，重复影片自动合并多条备用播放线路，不产生冗余数据。
"""
import re


def _key(item):
    title = re.sub(r"[\s\u3000\W]+", "", (item.get("title") or "").lower())
    year = re.sub(r"\D", "", str(item.get("year") or ""))[:4]
    return (title, year)


def _ep_eq(a, b):
    return a.get("name") == b.get("name") and a.get("url") == b.get("url")


def dedup_items(items):
    groups = {}
    order = []
    for it in items:
        k = _key(it)
        if k not in groups:
            groups[k] = it
            order.append(k)
        else:
            main = groups[k]
            # 合并分集/线路
            existing = main.get("episodes", [])
            for ep in it.get("episodes", []):
                if not any(_ep_eq(ep, e) for e in existing):
                    existing.append(ep)
            main["episodes"] = existing
            # 合并别名/演员等补充信息
            for f in ("aliases", "actors", "director", "description"):
                if not main.get(f) and it.get(f):
                    main[f] = it[f]
            # 多线路标记
            lines = set(e.get("line") for e in existing)
            main["play_lines_count"] = len(lines)
    merged = [groups[k] for k in order]
    dup = len(items) - len(merged)
    if dup:
        store_log(dup)
    return merged


def store_log(dup):
    from . import store
    store.log("info", f"智能去重：合并重复影片 {dup} 条，线路已合并", dup)
