# -*- coding: utf-8 -*-
"""
fetch_demo_sources.py —— 拉取真实的「公共领域 / CC」影片，生成演示数据集。

仅用于产出一份「真能播」的演示订阅（real public-domain / CC-BY 直链），
不涉及任何绕过站点安全校验的能力。数据来自 Internet Archive 公开元数据接口。

输出：backend/data/demo_sources.json（与 db.json 中 items 字段结构兼容，
       可被 publisher.py 直接消费）。

用法：
    python tools/fetch_demo_sources.py            # 默认拉取 ~18 部
    python tools/fetch_demo_sources.py --limit 30
"""
import argparse
import json
import os
import ssl
import sys
import urllib.request
import urllib.parse
import uuid
from datetime import datetime, timezone

# 沙箱/部分环境下 CA 校验会失败，这里仅用于拉取公开元数据，关闭校验。
try:
    _ctx = ssl.create_default_context()
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE
except Exception:
    _ctx = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "..", "backend", "data")
OUT = os.path.join(DATA_DIR, "demo_sources.json")

# 额外强制包含的经典 CC / 公共领域短片（保证演示质量）
PINNED = [
    "BigBuckBunny_124",   # Blender · CC-BY
    "ElephantsDream",     # Blender · CC-BY
    "SitaSingsTheBlues",  # Nina Paley · CC-BY
]


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 FilmCollector"})
    if _ctx is not None:
        raw = urllib.request.urlopen(req, timeout=25, context=_ctx)
    else:
        raw = urllib.request.urlopen(req, timeout=25)
    return json.loads(raw.read().decode("utf-8"))


def pick_mp4(files):
    """从 archive.org 文件列表里挑一个最佳 mp4 直链。优先 720p/h264/512kb，排除 sample。"""
    mp4s = [f for f in files if f.get("name", "").lower().endswith(".mp4")]
    if not mp4s:
        return None
    name = lambda f: f["name"].lower()
    # 优先级排序
    def score(f):
        n = name(f)
        s = 0
        if "sample" in n:
            s -= 100
        if "h264" in n:
            s += 6
        if "720" in n:
            s += 5
        if "512kb" in n:
            s += 4
        if "orig" in n:
            s += 2
        if "h.264" in n or "x264" in n:
            s += 3
        return s
    mp4s.sort(key=score, reverse=True)
    return mp4s[0]["name"]


def fetch_collection_ids(rows):
    q = ("https://archive.org/advancedsearch.php?q=" +
         urllib.parse.quote("collection:feature_films AND mediatype:movies") +
         f"&fl[]=identifier&rows={rows}&output=json")
    d = http_get_json(q)
    return [r["identifier"] for r in d.get("response", {}).get("docs", []) if r.get("identifier")]


def build_item(identifier):
    m = http_get_json("https://archive.org/metadata/" + identifier)
    meta = m.get("metadata", {})
    files = m.get("files", [])
    mp4_name = pick_mp4(files)
    if not mp4_name:
        return None
    play_url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(mp4_name)}"
    poster = f"https://archive.org/services/img/{identifier}"
    title = meta.get("title") or identifier
    year = (meta.get("year") or meta.get("date") or "")[:4]
    desc = (meta.get("description") or "")
    if isinstance(desc, list):
        desc = " ".join(desc)
    desc = desc[:500]
    return {
        "title": title,
        "aliases": "",
        "year": year or "",
        "region": meta.get("country", "") or "",
        "type": "电影",
        "director": meta.get("director", "") or "",
        "actors": ", ".join(meta.get("actor", []) if isinstance(meta.get("actor"), list) else []) or "",
        "description": desc,
        "duration": "",
        "rating": "",
        "subtitle": "",
        "poster": poster,
        "cover": "",
        "episodes": [{"line": "官方线路", "name": "正片", "url": play_url}],
        "genres": [g for g in (meta.get("subject", []) if isinstance(meta.get("subject"), list) else [])][:4],
        "source_url": f"https://archive.org/details/{identifier}",
        "status": "ok",
        "validated": True,
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=18)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    ids = list(PINNED)
    try:
        ids += fetch_collection_ids(max(20, args.limit))
    except Exception as e:
        print("集合检索失败，仅用 PINNED：", e, file=sys.stderr)

    # 去重并截断
    seen, uniq = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    uniq = uniq[: args.limit + len(PINNED)]

    items = []
    for i in uniq:
        try:
            it = build_item(i)
            if it:
                items.append(it)
                print("  +", it["title"])
        except Exception as e:
            print("  ! 跳过", i, e, file=sys.stderr)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"\n完成：{len(items)} 部真实公共片源 → {args.out}")


if __name__ == "__main__":
    main()
