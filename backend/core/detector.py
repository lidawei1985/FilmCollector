# -*- coding: utf-8 -*-
"""
站点兼容性自动检测：用户仅粘贴链接，自动识别加密等级 / 反爬策略 / 视频流格式。
返回三类结论：
  1 = 完全支持全自动整站批量抓取
  2 = 轻度加密，仅支持单集手动提取（启用轻量防封禁策略）
  3 = 高强度加密防护，无法解析视频资源（提供手动复制兜底）
"""
import re
import requests
from . import store

CF_MARKERS = ["cf-browser-verification", "challenge-platform", "Just a moment", "__cf_chl_", "cf_clearance"]
CAPTCHA_MARKERS = ["captcha", "verify you are human", "recaptcha", "hcaptcha", "security check", "人机验证", "验证码"]
PLAY_MARKERS = [".m3u8", ".mp4", "player", "play/", "/v/", "m3u8", "video"]


def _pick_ua(rotate):
    if rotate:
        import random
        return random.choice(store.UA_POOL)
    return store.UA_POOL[0]


def detect(url, rotate_ua=True):
    cfg = store.load_config()
    result = {
        "url": url,
        "reachable": False,
        "status": None,
        "level": 3,
        "level_text": "",
        "signals": [],
        "has_m3u8": False,
        "has_player": False,
        "anti_bot": [],
        "advice": "",
    }
    try:
        headers = {
            "User-Agent": _pick_ua(rotate_ua),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        r = requests.get(url, headers=headers, timeout=cfg.get("timeout", 12),
                         allow_redirects=True, stream=True)
        result["reachable"] = True
        result["status"] = r.status_code
        # 只读前 200KB 判定
        chunk = b""
        for c in r.iter_content(8192):
            chunk += c
            if len(chunk) >= 200 * 1024:
                break
        r.close()
        try:
            html = chunk.decode("utf-8", errors="ignore")
        except Exception:
            html = chunk.decode("gbk", errors="ignore")
        low = html.lower()

        # 反爬信号
        for m in CF_MARKERS:
            if m.lower() in low:
                result["anti_bot"].append("Cloudflare 质询")
                break
        for m in CAPTCHA_MARKERS:
            if m.lower() in low:
                result["anti_bot"].append("验证码/人机校验")
                break
        if "document.referrer" in low and ("location.href" in low or "window.location" in low):
            result["anti_bot"].append("JS 跳转重定向")
        if "x-frame-options" in str(r.headers).lower():
            result["anti_bot"].append("禁止嵌入(frame)")

        # 视频流格式
        result["has_m3u8"] = (".m3u8" in low) or ("m3u8" in low)
        result["has_player"] = any(k in low for k in ["player", "play/", "/v/", "video", "dplayer", "artplayer", "hls"])

        # 评分
        if result["anti_bot"]:
            # 有质询/验证码，但不一定完全不可解
            if "Cloudflare 质询" in result["anti_bot"] or "验证码/人机校验" in result["anti_bot"]:
                result["level"] = 2
                result["signals"].append("检测到访问质询，启用轻量防封禁策略（轮换UA/模拟浏览），仅支持单集手动提取")
            else:
                result["level"] = 2
        elif result["has_m3u8"] or result["has_player"]:
            result["level"] = 1
            result["signals"].append("页面含可直接解析的视频流，支持全自动整站批量抓取")
        else:
            # 无可识别视频流，但页面可达
            result["level"] = 2
            result["signals"].append("页面可达但未发现标准视频流特征，建议单集手动提取或手动复制播放地址")

        if result["level"] == 3:
            result["advice"] = "高强度加密站点：使用可视化面板手动复制单集播放地址作为兜底。"
        elif result["level"] == 2:
            result["advice"] = "轻度加密站点：已自动启用防封禁策略（轮换UA、自定义间隔、模拟真人浏览），可单集提取。"
        else:
            result["advice"] = "完全支持：可直接一键整站批量抓取。"
        result["level_text"] = {1: "① 完全支持全自动批量抓取", 2: "② 轻度加密·单集手动提取", 3: "③ 高强度加密·无法解析"}[result["level"]]
    except requests.exceptions.RequestException as e:
        result["signals"].append(f"请求失败：{e}")
        result["level"] = 3
        result["advice"] = "站点无法访问，请检查链接或网络。高强度加密站点可手动复制播放地址兜底。"
    return result
