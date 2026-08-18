# -*- coding: utf-8 -*-
"""
quality.py —— 资源动态评分 + 分级健康状态机
==========================================
对应架构设计第 6 条（资源评分系统）+ 第 7 条（自动健康检查·分级 + 自动恢复）。

每条播放资源（episode）的健康状态机：
  active   -- 正常
  warning  -- 连续检测失败 1 次（软告警，仍可用）
  degraded -- 连续失败 ≥3 次（降权，APK 优先选别的资源）
  disabled -- 连续失败 ≥5 次（APK 自动隐藏，但库保留、可恢复）

恢复：只要有一次检测成功 → 回到 active，失败计数清零。

动态评分 Resource Score（约 0~100），APK 永远首选最高分资源：
  = 清晰度基础分 + 健康分 + 速度分
  - 清晰度：4K/2160p=40, 1080p=35, 720p=25, 480p/SD=15, 未知=20
  - 健康：active=+30, warning=+10, degraded=0, disabled=-1000（不参与发布）
  - 速度：延迟<1s=+10, <3s=+5, 无数据=+5, 否则 0
"""
import re
from datetime import datetime

# 健康分级阈值（连续失败次数）
WARN_FAILS = 1
DEGRADED_FAILS = 3
DISABLED_FAILS = 5

STATUS_ACTIVE = "active"
STATUS_WARNING = "warning"
STATUS_DEGRADED = "degraded"
STATUS_DISABLED = "disabled"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _quality_points(url):
    """从播放地址文件名/清晰度标记估清晰度基础分。"""
    u = (url or "").lower()
    if re.search(r"(2160p|4k|uhd)", u):
        return 40
    if re.search(r"1080p", u):
        return 35
    if re.search(r"720p", u):
        return 25
    if re.search(r"(480p|360p|sd)", u):
        return 15
    return 20  # 未知清晰度给中值


def _health_points(status):
    return {
        STATUS_ACTIVE: 30,
        STATUS_WARNING: 10,
        STATUS_DEGRADED: 0,
        STATUS_DISABLED: -1000,
    }.get(status, 0)


def _speed_points(latency):
    if latency is None:
        return 5
    if latency < 1.0:
        return 10
    if latency < 3.0:
        return 5
    return 0


def score_resource(ep):
    """计算一条播放资源的动态评分（0~100）。disabled 返回极低分（不会被选为首选）。"""
    health = ep.get("health") or {}
    status = health.get("status", STATUS_ACTIVE)
    url = ep.get("url") or (ep.get("urls") or [None])[0]
    base = _quality_points(url)
    hp = _health_points(status)
    sp = _speed_points(health.get("latency"))
    return max(0, min(100, base + hp + sp))


def is_disabled(ep):
    """该资源是否被禁用（APK 应隐藏）。"""
    health = ep.get("health") or {}
    return health.get("status") == STATUS_DISABLED


def on_success(ep, latency=None):
    """一次播放地址检测成功：回到 active，失败清零，记录延迟。"""
    h = dict(ep.get("health") or {})
    h["status"] = STATUS_ACTIVE
    h["fails"] = 0
    h["last_success"] = _now()
    h["last_check"] = _now()
    if latency is not None:
        h["latency"] = round(latency, 2)
    ep["health"] = h
    return ep


def on_failure(ep):
    """一次播放地址检测失败（非确认死链）：按连续失败次数升级健康状态。"""
    h = dict(ep.get("health") or {})
    h["fails"] = int(h.get("fails", 0)) + 1
    h["last_check"] = _now()
    f = h["fails"]
    if f >= DISABLED_FAILS:
        h["status"] = STATUS_DISABLED
    elif f >= DEGRADED_FAILS:
        h["status"] = STATUS_DEGRADED
    else:
        h["status"] = STATUS_WARNING
    ep["health"] = h
    return ep
