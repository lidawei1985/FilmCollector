# -*- coding: utf-8 -*-
"""
link_check.py —— 播放地址分级健康自检（抗"源失效"）
================================================
- 定期 HEAD/Range 检查每部片的播放地址。
- **关键修正（防误删）**：只有"确实死链"（404/410）才移除；
  429/5xx/超时/断网等"不确定"一律保留，但按连续失败次数升级健康状态
  （warning→degraded→disabled），APK 自动隐藏 disabled 资源，库保留可恢复。
- 支持每部片多条候选地址（urls）：只剔死的那条，留好地址做兜底，
  保证 APK 永远只拿到"能播"的片，库在涨但不会掺假。
- 分级健康与动态评分见 quality.py；检测成功自动恢复 active。
"""
from enum import IntEnum

from . import net, store, quality


class Health(IntEnum):
    ALIVE = 0      # 确认可播
    DEAD = 1       # 确认死链（404/410）→ 可安全移除
    UNKNOWN = 2    # 限流/超时/断网/403 → 不确定，保留但记一次失败（驱动降级）


def _check(url, timeout=10):
    """返回某地址的健康状态。url 非法直接判 DEAD。"""
    if not url or not str(url).startswith("http"):
        return Health.DEAD
    try:
        r = net.head(url, timeout=timeout)
    except Exception:
        return Health.UNKNOWN
    if r is None:
        # 网络层彻底失败（超时/断网/DNS）——不确定，保留
        return Health.UNKNOWN
    sc = r.status_code
    if sc in (404, 410):
        return Health.DEAD
    if sc < 400:
        return Health.ALIVE
    # 429/5xx/其它 4xx（限流/服务器临时错误/防盗链）→ 不确定，保留
    return Health.UNKNOWN


def _candidates(ep):
    """取一条 episode 的候选地址列表（保序、去空、去重）。"""
    urls = ep.get("urls") or []
    if isinstance(urls, str):
        urls = [urls]
    primary = ep.get("url")
    out = []
    if primary:
        out.append(primary)
    for u in urls:
        if u and u not in out:
            out.append(u)
    return out


def check_db_health(db, enabled=True, timeout=10):
    """分级健康自检。返回 (db, removed_titles, changed_titles)。

    规则（对标设计第 7 条分级健康）：
    - 一条 episode 检测每个候选地址：
        * 有 ALIVE（确认可播）→ 保留，标记成功恢复（active，失败清零）；剔掉其中 DEAD。
        * 全是 UNKNOWN（限流/超时/断网，无法确认）→ 不删，但记一次失败：
          warning(1) → degraded(≥3) → disabled(≥5)；库保留，APK 隐藏 disabled。
        * 全是 DEAD → 该 episode 移除。
    - 一部片完全没有可用 episode → 整部移除（removed）。
    - 仅当某片的健康状态/候选集合发生变化才算 changed（用于报告）。
    """
    if not enabled:
        return db, [], []
    items = db.get("items", [])
    kept, removed, changed = [], [], []
    for it in items:
        eps = it.get("episodes") or []
        if not eps:
            # 本就没有播放地址：标记为死片移除（避免空壳入库）
            removed.append(it.get("title") or it.get("source_id") or "?")
            continue
        new_eps = []
        ep_changed = False
        for ep in eps:
            cands = _candidates(ep)
            if not cands:
                ep_changed = True
                continue
            results = [(_check(u, timeout=timeout), u) for u in cands]
            alive = [u for st, u in results if st == Health.ALIVE]
            unknown = [u for st, u in results if st == Health.UNKNOWN]
            dead = [u for st, u in results if st == Health.DEAD]
            if alive:
                # 有确认可播地址 → 成功恢复；保留 ALIVE 在前、UNKNOWN 兜底；剔 DEAD
                prev_status = (ep.get("health") or {}).get("status")
                ordered = alive + unknown
                new_ep = dict(ep)
                new_ep["url"] = ordered[0]
                new_ep["urls"] = ordered
                quality.on_success(new_ep)
                if dead or (new_ep.get("health") or {}).get("status") != prev_status:
                    ep_changed = True
                new_eps.append(new_ep)
            elif unknown:
                # 全是"不确定"（限流/超时/断网）→ 不删，记一次失败驱动降级，保留地址
                new_ep = dict(ep)
                quality.on_failure(new_ep)
                new_eps.append(new_ep)
                ep_changed = True  # on_failure 已修改健康（失败计数+1）
            else:
                # 全部确认死链 → 该 episode 移除
                ep_changed = True
        if not new_eps:
            removed.append(it.get("title") or it.get("source_id") or "?")
        else:
            # 总是用最新（含健康状态/候选）覆盖，确保计数与状态持久化
            it["episodes"] = new_eps
            if ep_changed:
                changed.append(it.get("title") or "?")
            kept.append(it)
    db["items"] = kept
    return db, removed, changed
