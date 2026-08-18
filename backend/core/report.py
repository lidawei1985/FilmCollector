# -*- coding: utf-8 -*-
"""
report.py —— 长期运行报告（运营能力，机器+人类可读）

汇总最近 N 次自动运行（run_status.json 的 history）、当前报警（alerts.json）、
片库统计（db.json），生成一份运营报告。每次自动运行结束后由 auto_pipeline 调用一次
（轻量），把报告写入 output/report_daily.json，并向 output/report.log 追加一行摘要，
便于长期回溯 7 天/30 天的健康趋势。
"""
import os
import json
from datetime import datetime

from . import store, alerts, stats as stats_mod

REPORT_PATH = os.path.join(store.BASE_DIR, "output", "report_daily.json")
REPORT_LOG = os.path.join(store.BASE_DIR, "output", "report.log")


def _load_run_status():
    p = os.path.join(store.DATA_DIR, "run_status.json")
    if not os.path.exists(p):
        return {"last_run": "", "health": "unknown", "history": []}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {"last_run": "", "health": "unknown", "history": []}


def build(days=7):
    rs = _load_run_status()
    history = rs.get("history", [])[:days]
    db = store.load_db()
    st = stats_mod.build_stats(db, top_n=10)
    al = alerts.list_alerts(include_resolved=True)
    active = alerts.list_alerts()
    critical = [a for a in active if a.get("level") == "critical"]

    added_total = sum((h.get("added", 0) or 0) for h in history)
    errored = [h for h in history if h.get("errors")]

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_days": days,
        "library": {
            "total": st["total"],
            "by_type": st["by_type"],
            "resource_health": st["resource_health"],
        },
        "recent_runs": {
            "count": len(history),
            "added_total": added_total,
            "last_run": rs.get("last_run", ""),
            "last_health": rs.get("health", "unknown"),
            "runs_with_errors": len(errored),
        },
        "alerts": {
            "active": len(active),
            "critical": len(critical),
            "critical_keys": [a["key"] for a in critical],
            "total_logged": len(al),
        },
        "top_content": st["top"][:10],
        "verdict": "OK" if (not critical and rs.get("health") in ("ok", "degraded", None))
                   else "ATTENTION",
    }
    return report, st


def write_report(days=7):
    report, _ = build(days=days)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    line = (f"[{report['generated_at']}] verdict={report['verdict']} "
            f"total={report['library']['total']} "
            f"added{days}={report['recent_runs']['added_total']} "
            f"health={report['recent_runs']['last_health']} "
            f"alerts={report['alerts']['active']}(crit={report['alerts']['critical']})\n")
    with open(REPORT_LOG, "a", encoding="utf-8") as f:
        f.write(line)
    return report
