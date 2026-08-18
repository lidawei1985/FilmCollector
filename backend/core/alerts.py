# -*- coding: utf-8 -*-
"""
alerts.py —— 异常报警接口（无人值守可观测性）

设计目标：
- 自动运行 7 天无人看管时，关键异常必须「被记录、可查询、不丢失」。
- 与 store.log 区分：log 是流水账（进 db.json，限 500 条，混在业务数据里）；
  alerts 是「待办/故障工单」，去重、可标记已解决、可单独查询与展示。
- 典型触发：部署失败、片库异常骤减(保留上次有效订阅)、目录连续性恢复、运行崩溃、上游长期封禁。

存储：backend/data/alerts.json（与 db.json 分离，避免污染业务库；上限 200 条）。
"""
import os
import json
import threading
from datetime import datetime

from . import store

ALERTS_PATH = os.path.join(store.DATA_DIR, "alerts.json")
_CAP = 200
_lock = threading.RLock()

LEVEL_CRITICAL = "critical"   # 必须人工介入：部署失败、崩溃、片库异常
LEVEL_WARN = "warn"           # 需关注：上游封禁、连续性恢复
LEVEL_INFO = "info"           # 提示：已自动处理


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load():
    if not os.path.exists(ALERTS_PATH):
        return []
    try:
        with open(ALERTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(lst):
    os.makedirs(store.DATA_DIR, exist_ok=True)
    tmp = ALERTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lst, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ALERTS_PATH)


def raise_alert(key, level, msg, resolved=False):
    """按 key 去重上报一条报警；同一 key 重复只累加 count 并更新 last_at。

    key 用于合并同类报警（如每次部署失败都用 key="deploy_failed"），
    避免 7 天里被同一条报警刷屏。返回当前报警条目。
    """
    key = str(key)
    level = level if level in (LEVEL_CRITICAL, LEVEL_WARN, LEVEL_INFO) else LEVEL_WARN
    with _lock:
        lst = _load()
        now = _now()
        for a in lst:
            if a.get("key") == key:
                a["count"] = int(a.get("count", 0)) + 1
                a["last_at"] = now
                a["level"] = level
                a["msg"] = msg
                a["resolved"] = resolved
                _save(lst)
                return a
        a = {"key": key, "level": level, "msg": msg, "first_at": now,
             "last_at": now, "count": 1, "resolved": resolved}
        lst.insert(0, a)
        if len(lst) > _CAP:
            lst = lst[:_CAP]
        _save(lst)
        return a


def resolve_alert(key):
    with _lock:
        lst = _load()
        for a in lst:
            if a.get("key") == key:
                a["resolved"] = True
        _save(lst)


def clear_resolved():
    with _lock:
        lst = [a for a in _load() if not a.get("resolved")]
        _save(lst)


def list_alerts(include_resolved=False):
    with _lock:
        lst = _load()
    if not include_resolved:
        lst = [a for a in lst if not a.get("resolved")]
    return lst


def active_critical():
    """当前未解决且为 critical 的报警 key 列表（供状态页红灯）。"""
    return [a["key"] for a in list_alerts() if a.get("level") == LEVEL_CRITICAL]
