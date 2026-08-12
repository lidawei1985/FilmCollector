# -*- coding: utf-8 -*-
"""
自动生成双格式影视订阅源，并每日备份两套格式历史文件：
  ① TVBox 标准订阅 JSON（maccms 兼容，影视仓/LunaTV/ZYPlayer/OK影视/猫影视等可直接导入）
  ② 通用纯净影片 JSON（仅基础元数据，无私有嵌套字段，适配自研 APP / 网页系统 / 第三方播放器）
导出前进行格式校验；所有文件本地保存。
"""
import os
import json
import shutil
from datetime import datetime

from . import store
from . import validator

OUT_DIR = os.path.join(store.BASE_DIR, "output", "json")
TVBOX_DIR = os.path.join(OUT_DIR, "tvbox")
GENERIC_DIR = os.path.join(OUT_DIR, "generic")
BACKUP_DIR = os.path.join(store.BASE_DIR, "output", "backup")


# ---------------- TVBox (maccms 兼容) ----------------
def _to_tvbox(item):
    lines = {}
    for ep in item.get("episodes", []):
        lines.setdefault(ep.get("line", "默认线路"), []).append(ep)
    play_from = list(lines.keys())
    play_url_parts = []
    for ln in play_from:
        segs = [f"{ln}${ep.get('url', '')}" for ep in lines[ln]]
        play_url_parts.append("#".join(segs))
    play_url = "$$$".join(play_url_parts)
    return {
        "vod_id": item.get("id") or item.get("source_url") or item.get("title", ""),
        "vod_name": item.get("title", ""),
        "vod_pic": item.get("poster", ""),
        "vod_cover": item.get("cover", ""),
        "type_name": item.get("type", "电影"),
        "vod_year": str(item.get("year", "")),
        "vod_area": item.get("region", ""),
        "vod_director": item.get("director", ""),
        "vod_actor": item.get("actors", ""),
        "vod_content": item.get("description", ""),
        "vod_remarks": f"共{len(item.get('episodes', []))}集" if item.get("episodes") else "暂无资源",
        "vod_class": ",".join(item.get("genres", [])),
        "vod_sub": item.get("subtitle", ""),
        "vod_duration": item.get("duration", ""),
        "vod_douban": item.get("rating", ""),
        "vod_play_from": ",".join(play_from),
        "vod_play_url": play_url,
    }


# ---------------- 通用纯净影片（对齐用户「标准影片 JSON 样例」）----------------
# 样例字段：id/name/type/movie/year/area/lang/actor/desc/pic/tag/episode_count/
#          play_list[{name,url}]/score/director/sub_url
TYPE_EN = {
    '电影': 'movie', '连续剧': 'tv', '短剧': 'short', '动漫': 'anime',
    '综艺': 'variety', '纪录片': 'documentary', '少儿': 'kids',
}

def _to_generic(item):
    eps = item.get("episodes", []) or []
    play_list = [{"name": ep.get("name", ""), "url": ep.get("url", ""), "line": ep.get("line", "默认线路")}
                 for ep in eps if ep.get("url")]
    actors = item.get("actors", "")
    actor = actors if isinstance(actors, str) else ",".join(actors)
    genres = item.get("genres", []) or []
    tag = item.get("tag") or (",".join(genres) if genres else "")
    return {
        "id": item.get("id") or item.get("source_url") or "",
        "name": item.get("title", ""),
        "type": TYPE_EN.get(item.get("type", ""), item.get("type", "movie")),
        "year": str(item.get("year", "")),
        "area": item.get("region", ""),
        "lang": item.get("lang", ""),
        "actor": actor,
        "desc": item.get("description", ""),
        "pic": item.get("poster", ""),
        "tag": tag,
        "episode_count": len(play_list),
        "play_list": play_list,
        "score": item.get("rating", ""),
        "director": item.get("director", ""),
        "sub_url": item.get("sub_url") or item.get("subtitle", ""),
    }


def _envelope(vods, page=1, limit=0):
    total = len(vods)
    return {
        "code": 1, "msg": "ok",
        "page": page,
        "pagecount": 1 if limit == 0 else ((total + limit - 1) // limit or 1),
        "limit": limit or total, "total": total, "list": vods,
    }


def _write(dirpath, name, vods):
    os.makedirs(dirpath, exist_ok=True)
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_envelope(vods), f, ensure_ascii=False, indent=2)
    return path


def _split(vods, kind):
    if kind == "tvbox":
        movie = [v for v in vods if v["type_name"] in ("电影", "连续剧", "综艺", "纪录片", "少儿")]
        short = [v for v in vods if v["type_name"] == "短剧"]
        anime = [v for v in vods if v["type_name"] == "动漫"]
        live = [v for v in vods if "$" in v["vod_play_url"] and any(k in v["vod_name"].lower() for k in ("live", "直播", "电视"))]
        return {"all.json": vods, "movie.json": movie, "short.json": short, "anime.json": anime, "live.json": live}
    else:
        movie = [v for v in vods if v["type"] in ("movie", "tv", "variety", "documentary", "kids")]
        short = [v for v in vods if v["type"] == "short"]
        anime = [v for v in vods if v["type"] == "anime"]
        live = [v for v in vods if v["play_list"] and any(k in v["name"].lower() for k in ("live", "直播", "电视"))]
        return {"all.json": vods, "movie.json": movie, "short.json": short, "anime.json": anime, "live.json": live}


def _backup_json():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"json_{ts}")
    if os.path.isdir(TVBOX_DIR):
        shutil.copytree(TVBOX_DIR, os.path.join(dst, "tvbox"), dirs_exist_ok=True)
    if os.path.isdir(GENERIC_DIR):
        shutil.copytree(GENERIC_DIR, os.path.join(dst, "generic"), dirs_exist_ok=True)
    # 仅保留最近 30 份
    dirs = sorted([d for d in os.listdir(BACKUP_DIR) if d.startswith("json_")])
    for old in dirs[:-30]:
        try:
            shutil.rmtree(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    return dst


def generate(items=None):
    if items is None:
        items = store.load_db().get("items", [])
    alive = [it for it in items if it.get("status") != "dead"]

    tvbox_vods = [_to_tvbox(it) for it in alive]
    generic_vods = [_to_generic(it) for it in alive]

    tvbox_errors = validator.validate_tvbox(tvbox_vods)
    generic_errors = validator.validate_generic(generic_vods)

    tvbox_paths, generic_paths = {}, {}
    for name, vods in _split(tvbox_vods, "tvbox").items():
        tvbox_paths[name] = _write(TVBOX_DIR, name, vods)
    for name, vods in _split(generic_vods, "generic").items():
        generic_paths[name] = _write(GENERIC_DIR, name, vods)

    # 校验写出的文件语法
    for p in list(tvbox_paths.values()) + list(generic_paths.values()):
        ok, err = validator.check_file_syntax(p)
        if not ok:
            tvbox_errors.append(f"文件语法错误 {os.path.basename(p)}：{err}")

    _backup_json()
    store.backup_db()
    store.log("info", f"双格式订阅源生成：TVBox {len(tvbox_vods)} 条 / 通用 {len(generic_vods)} 条；TVBox校验{len(tvbox_errors)}处，通用校验{len(generic_errors)}处")
    return {
        "count": len(alive),
        "tvbox": tvbox_paths,
        "generic": generic_paths,
        "tvbox_errors": tvbox_errors,
        "generic_errors": generic_errors,
    }
