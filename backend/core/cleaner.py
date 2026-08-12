# -*- coding: utf-8 -*-
"""
全自动资源巡检清洗：失效/超时/404 链接标记清除、广告域名过滤、图片校验缓存、
名称规范化、字幕地址登记。所有动作默认开启（开箱即用）。
"""
import re
import requests
from . import store, image_cache

AD_SUBSTR = ["/ad/", "/ads/", "advert", "click", "banner", "popwin", "pop-up", "promo", "tracker", "beacon"]


def is_ad_url(url, ad_domains):
    if not url:
        return False
    u = url.lower()
    host = re.sub(r"^https?://", "", u).split("/")[0]
    if any(d.lower() in host for d in ad_domains):
        return True
    if any(s in u for s in AD_SUBSTR):
        return True
    return False


def validate_url(url, cfg):
    """轻量校验：HEAD 优先，失败回退 GET 小片段。"""
    if not url or not url.startswith("http"):
        return False, 0
    headers = {"User-Agent": store.UA_POOL[0], "Range": "bytes=0-1024"}
    try:
        r = requests.head(url, headers=headers, timeout=cfg.get("timeout", 12), allow_redirects=True)
        if r.status_code in (200, 206):
            return True, r.status_code
        if r.status_code == 404:
            return False, 404
        # HEAD 不支持则 GET
        r = requests.get(url, headers=headers, timeout=cfg.get("timeout", 12), stream=True)
        ok = r.status_code == 200
        code = r.status_code
        r.close()
        return ok, code
    except Exception:
        return False, 0


def fix_title(title):
    """名称规范化：去控制符、合并空白、统一全角括号、剔除首尾噪声。"""
    if not title:
        return title
    t = title
    t = re.sub(r"[\x00-\x1f\x7f]", "", t)
    t = t.replace("（", "(").replace("）", ")")
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip(" -_·•·")
    return t


def clean_item(item, cfg, ad_domains):
    stats = {"ads": 0, "dead": 0, "cached": 0, "broken_img": 0, "name_fixed": False}
    item["title"] = fix_title(item.get("title", ""))
    if item.get("aliases"):
        item["aliases"] = fix_title(item["aliases"])

    # 分集：广告过滤 + 失效剔除
    kept = []
    for ep in item.get("episodes", []):
        url = ep.get("url", "")
        if is_ad_url(url, ad_domains):
            stats["ads"] += 1
            continue
        ok, _ = validate_url(url, cfg)
        if not ok:
            stats["dead"] += 1
            continue
        kept.append(ep)
    item["episodes"] = kept

    # 海报/封面：校验+缓存本地；缺失尝试兜底
    for key in ("poster", "cover"):
        url = item.get(key, "")
        if url and url.startswith("http"):
            if is_ad_url(url, ad_domains):
                item[key] = ""
            else:
                local = image_cache.cache_image(url, prefix=key)
                if local:
                    item[key] = local
                    stats["cached"] += 1
                else:
                    stats["broken_img"] += 1
                    item[key] = ""
    if not item.get("poster"):
        item["poster"] = ""  # 缺封面：本地占位（备用图库为可扩展点）

    item["status"] = "ok" if item["episodes"] else "dead"
    item["validated"] = True
    return item, stats


def clean_items(items):
    cfg = store.load_config()
    ad_domains = store.load_ad_domains() if cfg.get("ad_filter", True) else []
    out = []
    totals = {"ads": 0, "dead": 0, "cached": 0, "broken_img": 0, "name_fixed": 0}
    for it in items:
        cleaned, s = clean_item(it, cfg, ad_domains)
        out.append(cleaned)
        for k in totals:
            totals[k] += s[k]
    store.log("info", f"清洗完成：广告过滤 {totals['ads']} 条，失效剔除 {totals['dead']} 条，图片缓存 {totals['cached']} 张", totals["ads"] + totals["dead"])
    return out, totals
