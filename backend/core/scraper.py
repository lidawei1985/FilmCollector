# -*- coding: utf-8 -*-
"""
零代码可视化采集核心：读取用户通过"点击元素"得到的 CSS 选择器模板，从页面抽取影视字段。
支持两种数据来源：
  - 服务端渲染页：requests 拉取 HTML 后解析（默认，沙箱/无头环境可用）
  - 内置浏览器 DOM：桌面端用 WebView2 读取 document.documentElement.outerHTML 传回，再复用同一解析逻辑
自动探测：未配置选择器时，按常见结构（og 标签 / m3u8 链接）尝试生成单条记录。
"""
import re
import os
import json
import time
import urllib.parse
import urllib.request
import requests
from bs4 import BeautifulSoup
from . import store

FIELD_KEYS = ["title", "aliases", "year", "region", "type", "director",
              "actors", "description", "duration", "rating", "subtitle", "poster", "cover"]


def _fetch_html(url, base_headers=None):
    cfg = store.load_config()
    ua = store.UA_POOL[0]
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": url,
    }
    if base_headers:
        headers.update(base_headers)
    r = requests.get(url, headers=headers, timeout=cfg.get("timeout", 12), allow_redirects=True)
    raw = r.content
    # 正确解码：优先 UTF-8，兼容 GBK/GB2312/BIG5 中文站点，避免乱码
    for enc in ("utf-8", "gbk", "gb2312", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _abs(url, base):
    from urllib.parse import urljoin
    if not url:
        return url
    return urljoin(base, url.strip())


def _text(soup, sel):
    if not sel:
        return ""
    try:
        el = soup.select_one(sel)
        return el.get_text(strip=True) if el else ""
    except Exception:
        return ""


def _attr(soup, sel, attr):
    if not sel:
        return ""
    try:
        el = soup.select_one(sel)
        return el.get(attr, "") if el else ""
    except Exception:
        return ""


def _find_play_urls(soup, base):
    """页面内直接扫描 m3u8 / mp4 播放地址（视频流智能嗅探）。"""
    urls = []
    for tag in soup.find_all(["a", "source", "iframe", "video"]):
        cand = tag.get("href") or tag.get("src") or tag.get("data-src") or ""
        if re.search(r"\.m3u8(\?|$)", cand) or re.search(r"\.mp4(\?|$)", cand):
            urls.append(_abs(cand, base))
    # script 里也可能内联
    for s in soup.find_all("script"):
        txt = s.string or ""
        for m in re.findall(r'["\'](https?://[^\s"\']+\.(?:m3u8|mp4)[^\s"\']*)["\']', txt):
            urls.append(m)
    # 去重保序
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _meta(soup, prop):
    el = soup.select_one(f'meta[property="og:{prop}"]') or soup.select_one(f'meta[name="{prop}"]')
    return el.get("content", "").strip() if el else ""


# 常见清晰度/编码后缀：同一影片多个版本仅这些不同，应合并为一条
_QUALITY_RE = re.compile(
    r"[._-]?(?:h\.?264|h\.?265|x264|x265|ia|\d{3,4}(?:kb|p|k)|(?:240|360|480|720|1080)p)$",
    re.I,
)


def _norm_key(url):
    fn = url.rsplit("/", 1)[-1].split("?", 1)[0]
    base, _ = os.path.splitext(fn)
    return _QUALITY_RE.sub("", base).lower()


def _quality_label(url):
    fn = url.rsplit("/", 1)[-1].lower()
    m = re.search(r"(\d{3,4})p", fn)
    if m:
        return f"{m.group(1)}P"
    if "512kb" in fn:
        return "512K"
    if "h264" in fn or "x264" in fn:
        return "H264"
    if "h265" in fn or "x265" in fn:
        return "H265"
    if _QUALITY_RE.search(fn):
        return "高清"
    return "播放"


def _collapse_quality_variants(episodes):
    """同一影片的多个清晰度版本（仅后缀/编码不同）合并为一条，避免被误判为连续剧。"""
    if len(episodes) <= 1:
        return episodes
    groups = {}
    for ep in episodes:
        groups.setdefault(_norm_key(ep.get("url", "")), []).append(ep)
    out = []
    for eps in groups.values():
        if len(eps) == 1:
            out.append(eps[0])
            continue
        # 多清晰度：保留一条（优先「原画/无后缀」，否则取分辨率最高）
        def _score(e):
            fn = e.get("url", "").rsplit("/", 1)[-1].lower()
            has_q = 1 if _QUALITY_RE.search(fn) else 0
            m = re.search(r"(\d{3,4})p", fn)
            res = int(m.group(1)) if m else (512 if "kb" in fn else 0)
            return (has_q, res)
        best = dict(sorted(eps, key=_score)[0])
        best["name"] = _quality_label(best.get("url", ""))
        out.append(best)
    return out


def parse_detail(template, html, base_url):
    """对一个详情页 HTML 解析出一条记录（字段 + 分集 + 播放线路）。"""
    soup = BeautifulSoup(html, "lxml")
    fields = template.get("fields", {}) if template else {}
    item = {k: "" for k in FIELD_KEYS}
    item["title"] = _text(soup, fields.get("title")) or _meta(soup, "title") or soup.title.get_text(strip=True) if soup.title else ""
    item["aliases"] = _text(soup, fields.get("aliases"))
    item["year"] = _text(soup, fields.get("year"))
    item["region"] = _text(soup, fields.get("region"))
    item["type"] = _text(soup, fields.get("type"))
    item["director"] = _text(soup, fields.get("director"))
    item["actors"] = _text(soup, fields.get("actors"))
    item["description"] = _text(soup, fields.get("description")) or _meta(soup, "description")
    item["duration"] = _text(soup, fields.get("duration"))
    item["rating"] = _text(soup, fields.get("rating"))
    item["subtitle"] = _text(soup, fields.get("subtitle"))
    poster = _attr(soup, fields.get("poster"), "src") or _attr(soup, fields.get("poster"), "data-src") or _meta(soup, "image")
    item["poster"] = _abs(poster, base_url)
    cover = _attr(soup, fields.get("cover"), "src") or _attr(soup, fields.get("cover"), "data-src")
    item["cover"] = _abs(cover, base_url)

    episodes = []
    line_name = template.get("episodes", {}).get("line", "默认线路") if template else "默认线路"
    ep = template.get("episodes", {}) if template else {}
    if ep.get("container"):
        for node in soup.select(ep["container"]):
            name = node.get_text(strip=True) if not ep.get("name") else (node.select_one(ep["name"]).get_text(strip=True) if node.select_one(ep["name"]) else "")
            url = ""
            a = node.select_one(ep.get("url", "a")) if ep.get("url") else None
            if a:
                url = a.get("href") or a.get("data-src") or a.get("src") or ""
            if url:
                episodes.append({"name": name or f"第{len(episodes)+1}集", "url": _abs(url, base_url), "line": line_name})
    if not episodes:
        # 自动嗅探整页播放地址
        for u in _find_play_urls(soup, base_url):
            episodes.append({"name": f"线路片段{len(episodes)+1}", "url": u, "line": line_name})

    item["episodes"] = _collapse_quality_variants(episodes)
    item["source_url"] = base_url
    return item


def auto_template(html, base_url):
    """零配置自动探测：用 og 标签 + 嗅探播放地址凑出一条记录。"""
    soup = BeautifulSoup(html, "lxml")
    title = _meta(soup, "title") or (soup.title.get_text(strip=True) if soup.title else "")
    poster = _meta(soup, "image")
    desc = _meta(soup, "description")
    urls = _find_play_urls(soup, base_url)
    item = {k: "" for k in FIELD_KEYS}
    item["title"] = title
    item["poster"] = _abs(poster, base_url)
    item["description"] = desc
    item["episodes"] = _collapse_quality_variants(
        [{"name": f"自动嗅探{idx+1}", "url": u, "line": "自动线路"} for idx, u in enumerate(urls)]
    )
    item["source_url"] = base_url
    return item


def scrape(template, url, mode="server"):
    """采集入口。mode=server 直接拉取；browser 模式由桌面端传入已抓取的 HTML（见 parse_html）。"""
    if mode == "browser":
        return []  # 桌面端走 parse_html
    html = _fetch_html(url)
    return [parse_detail(template, html, url)] if template else [auto_template(html, url)]


def parse_html(template, html, base_url):
    """桌面端内置浏览器抓回的 DOM 在这里解析（支持 JS 渲染页面）。"""
    if template:
        return [parse_detail(template, html, base_url)]
    return [auto_template(html, base_url)]


def crawl_list(template, start_url, max_pages=5):
    """整站批量抓取：列表页 -> 详情页。自动翻页直到 max_pages 或无下一页。"""
    cfg = store.load_config()
    items = []
    url = start_url
    tpl = template or {}
    list_cfg = tpl.get("list", {})
    detail_tpl = tpl.get("detail_template", tpl)
    pages = 0
    import time
    while url and pages < max_pages:
        html = _fetch_html(url)
        soup = BeautifulSoup(html, "lxml")
        detail_links = []
        if list_cfg.get("item"):
            for node in soup.select(list_cfg["item"]):
                link = node.select_one(list_cfg.get("link", "a")) if list_cfg.get("link") else node
                href = (link.get("href") if link else "") or (node.get("href") if node.name == "a" else "")
                if href:
                    detail_links.append(_abs(href, url))
        else:
            # 没有列表模板，则把当前页当详情页
            items.append(parse_detail(detail_tpl, html, url))
            break
        for d in detail_links:
            dhtml = _fetch_html(d)
            items.append(parse_detail(detail_tpl, dhtml, d))
            if cfg.get("request_interval"):
                time.sleep(cfg["request_interval"])
        nxt = soup.select_one(list_cfg.get("next", "")) if list_cfg.get("next") else None
        url = _abs(nxt.get("href", ""), url) if nxt and nxt.get("href") else None
        pages += 1
        if cfg.get("request_interval"):
            time.sleep(cfg["request_interval"])
    return items


def crawl_collection(collection_id, detail_template=None, max_items=20):
    """archive.org 集合：用 advancedsearch API 列出集合内影片 identifier，逐条采集详情页。

    对 JS 渲染的集合页（服务端 HTML 抓不到条目）尤其有效，一个预设即可扩出一批真实片源。
    """
    cfg = store.load_config()
    ids = []
    q = f"collection:{collection_id}"
    api = ("https://archive.org/advancedsearch.php?q=" + urllib.parse.quote(q) +
           "&fl[]=identifier&rows=" + str(int(max_items)) + "&output=json")
    try:
        # 用 requests（自带 certifi）而非 urllib，避免冻结 EXE 内 HTTPS 证书异常
        r = requests.get(api, headers={"User-Agent": store.UA_POOL[0]}, timeout=20)
        r.raise_for_status()
        data = r.json()
        for doc in data.get("response", {}).get("docs", []):
            ident = doc.get("identifier")
            if ident:
                ids.append(ident)
    except Exception as e:
        store.log("warn", f"集合列举失败 {collection_id}：{e}")
    items = []
    for ident in ids:
        url = f"https://archive.org/details/{ident}"
        try:
            html = _fetch_html(url)
            items.append(parse_detail(detail_template, html, url))
        except Exception as e:
            store.log("warn", f"采集详情失败 {url}：{e}")
        if cfg.get("request_interval"):
            time.sleep(cfg["request_interval"])
    store.log("info", f"集合采集「{collection_id}」共 {len(items)} 条")
    return items
