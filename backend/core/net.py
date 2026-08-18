# -*- coding: utf-8 -*-
"""
net.py —— 礼貌型 HTTP 客户端（抗反爬 / 抗限流 / 防自封）
================================================
- 统一 UA、超时
- 失败自动重试：指数退避（5xx/网络异常）
- 同主机最小请求间隔（rate limit），避免被封 IP
- **限流自保护**：archive.org 对 CDX/metadata 有 60 请求/分 硬限，连续 429 会触发
  1 小时 IP 封禁且逐次翻倍。一旦检测到连续 429，主动抛 RateLimited 让上层**尽早收手**，
  而不是越撞越狠把封禁锁死（参考 commoncrawl/cdx_toolkit 的生产级退避设计）。
- 专为「自动找片」这类会被站点限流的场景设计
"""
import time
import threading
from urllib.parse import urlparse

import requests

try:
    from . import store
except Exception:
    store = None

_lock = threading.Lock()
_last_req = {}        # host -> 上次请求时间戳
_consec_429 = {}       # host -> 连续 429 计数（用于自封保护）


class RateLimited(Exception):
    """连续触发限流，应让调用方尽快中止本轮抓取，避免 IP 被封禁更久。"""


def _ua():
    if store is not None:
        try:
            return store.UA_POOL[0]
        except Exception:
            pass
    return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _rate_limit(host, min_interval):
    if min_interval <= 0:
        return
    with _lock:
        last = _last_req.get(host, 0)
        wait = min_interval - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _last_req[host] = time.time()


def _note_429(host):
    """记录一次 429；连续达到阈值则抛 RateLimited 主动收手。"""
    with _lock:
        _consec_429[host] = _consec_429.get(host, 0) + 1
        n = _consec_429[host]
    # 退避 15s（archive.org 对持续违规会封 1 小时，先大幅降速示好）
    time.sleep(15)
    if n >= 3:
        raise RateLimited(
            f"{host} 连续限流 {n} 次，已主动中止本轮抓取以避免 IP 被封禁。"
            f"通常 1 小时后自动恢复；请调高请求间隔或减少并发。")


def _reset_429(host):
    with _lock:
        if _consec_429.get(host):
            _consec_429[host] = 0


def get(url, timeout=20, min_interval=1.0, headers=None, allow_redirects=True,
        stream=False, method="GET"):
    """发起请求，带重试与限速。失败返回 None。遇到连续 429 抛 RateLimited。"""
    host = urlparse(url).netloc
    _rate_limit(host, min_interval)
    hdr = {"User-Agent": _ua(), "Accept": "*/*"}
    if headers:
        hdr.update(headers)
    backoff = 1.0
    for _ in range(4):
        try:
            r = requests.request(method, url, headers=hdr, timeout=timeout,
                                 allow_redirects=allow_redirects, stream=stream)
            if r.status_code == 429:
                _note_429(host)   # 可能抛 RateLimited
                continue
            if r.status_code in (500, 502, 503, 504):
                # 503 多为"slow down"，退避稍长
                _reset_429(host)
                time.sleep(backoff * (2 if r.status_code == 503 else 1))
                backoff = min(backoff * 2, 8)
                continue
            _reset_429(host)
            return r
        except RateLimited:
            raise
        except Exception:
            _reset_429(host)
            time.sleep(backoff)
            backoff = min(backoff * 2, 8)
    # 最后一次尽力请求
    try:
        r = requests.request(method, url, headers=hdr, timeout=timeout,
                             allow_redirects=allow_redirects, stream=stream)
        if r.status_code == 429:
            _note_429(host)
        _reset_429(host)
        return r
    except RateLimited:
        raise
    except Exception:
        return None


def get_json(url, timeout=20, min_interval=1.0, headers=None):
    r = get(url, timeout=timeout, min_interval=min_interval, headers=headers)
    if r is None:
        return None
    try:
        return r.json()
    except Exception:
        return None


def get_text(url, timeout=20, min_interval=1.0, headers=None):
    r = get(url, timeout=timeout, min_interval=min_interval, headers=headers)
    if r is None:
        return None
    return r.text


def head(url, timeout=10, min_interval=0.5, headers=None):
    """HEAD 请求；部分 CDN 不支持 HEAD 时回退为 Range GET（仅取首字节）。"""
    try:
        r = get(url, timeout=timeout, min_interval=min_interval, headers=headers, method="HEAD")
    except RateLimited:
        raise
    if r is not None and r.status_code < 400:
        return r
    hdr = {"Range": "bytes=0-0"}
    if headers:
        hdr.update(headers)
    try:
        return get(url, timeout=timeout, min_interval=min_interval, headers=hdr, method="GET")
    except RateLimited:
        raise
