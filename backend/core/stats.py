# -*- coding: utf-8 -*-
"""
stats.py —— 运营数据统计（机器可读，随订阅包发布 stats.json）

为「长期无人运营」提供一眼可见的运营画像：
  - 总量、按类型/年份分布
  - 资源健康分布（active/warning/degraded/disabled）
  - 评分分布（按影片聚合分分段）
  - 热门内容 Top N（按 score_movie 降序，供首页/精选参考）
  - 最近更新（last_run_added / updated_at）
"""
from datetime import datetime

from . import quality


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def build_stats(db, top_n=20, last_run_added=0, blocked=False):
    items = db.get("items", [])
    alive = [it for it in items if it.get("status") != "dead"]

    # 分类 / 年份分布
    by_type, by_year = {}, {}
    for it in alive:
        t = it.get("type_name") or it.get("type") or "其他"
        by_type[t] = by_type.get(t, 0) + 1
        y = str(it.get("year") or "未知")
        by_year[y] = by_year.get(y, 0) + 1

    # 资源健康分布
    rh = {"active": 0, "warning": 0, "degraded": 0, "disabled": 0}
    for it in items:
        for ep in it.get("episodes") or []:
            st = (ep.get("health") or {}).get("status", quality.STATUS_ACTIVE)
            if st in rh:
                rh[st] += 1
            else:
                rh["active"] += 1

    # 评分分布（影片聚合分分段）
    score_bins = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    top = []
    for it in alive:
        s = quality.score_movie(it)
        if s <= 20:
            score_bins["0-20"] += 1
        elif s <= 40:
            score_bins["21-40"] += 1
        elif s <= 60:
            score_bins["41-60"] += 1
        elif s <= 80:
            score_bins["61-80"] += 1
        else:
            score_bins["81-100"] += 1
        top.append((it.get("title", "?"), s, it.get("type_name") or it.get("year")))
    top.sort(key=lambda x: x[1], reverse=True)
    top_list = [{"title": t, "score": s, "type": ty} for (t, s, ty) in top[:top_n]]

    return {
        "app": "FilmCollector",
        "generated_at": _now(),
        "total": len(alive),
        "total_including_dead": len(items),
        "by_type": by_type,
        "by_year": by_year,
        "resource_health": rh,
        "score_distribution": score_bins,
        "last_run_added": last_run_added,
        "blocked": blocked,
        "top": top_list,
    }
