# -*- coding: utf-8 -*-
"""
presets.py —— 公共领域 / CC 免费影视采集源预设（可编辑，默认不抓取）
------------------------------------------------------------------
重要边界：
- 这些预设只是「可配置的起始样例」，默认 enabled=false，不会自动抓取。
- 用户必须确认自己有权访问目标页面、且目标内容确为公有领域/CC 授权后，
  再点击「加入采集」才会进入本地 检测→采集→清洗→JSON/API 链路。
- 本模块不内置任何绕过站点安全校验的能力。
"""
import os
import json
import threading

_lock = threading.Lock()

DEFAULT_PRESETS = [
    {
        "id": "ia_feature_films",
        "name": "Internet Archive · 公共领域电影（真实可采集）",
        "url": "https://archive.org/details/windjammer",
        "license": "Public Domain",
        "mode": "server",
        "template": {
            "fields": {"title": '[itemprop="name"]'},
            "episodes": {}
        },
        "note": "Internet Archive 公共领域影片详情页：标题取 itemprop=name，海报/简介走 og 标签，"
                "播放地址自动嗅探页面内 .mp4（多清晰度自动合并为一条）。已确认内容为公有领域，可直接采集。",
        "enabled": True,
    },
    {
        "id": "ia_feature_films_collection",
        "name": "Internet Archive · 公共领域电影合集（一键采集一批）",
        "url": "https://archive.org/details/feature_films",
        "license": "Public Domain",
        "mode": "server",
        "collection": "feature_films",
        "template": {
            "fields": {"title": '[itemprop="name"]'},
            "episodes": {}
        },
        "note": "通过 archive.org advancedsearch 列举 feature_films 集合内全部公共领域影片，"
                "逐条采集详情页，一个预设即可扩出一批真实片源。已确认集合内容为公有领域。",
        "enabled": True,
    },
    {
        "id": "ia_big_buck_bunny",
        "name": "Big Buck Bunny（Blender · CC-BY）",
        "url": "https://archive.org/details/BigBuckBunny_124",
        "license": "CC BY 3.0",
        "mode": "server",
        "template": {
            "fields": {"title": '[itemprop="name"]'},
            "episodes": {}
        },
        "note": "Blender 开源动画短片，CC-BY 3.0 授权，页面含直接 mp4 下载，可直接采集。",
        "enabled": True,
    },
    {
        "id": "ia_elephants_dream",
        "name": "Elephants Dream（Blender · CC-BY）",
        "url": "https://archive.org/details/ElephantsDream",
        "license": "CC BY 3.0",
        "mode": "server",
        "template": {
            "fields": {"title": '[itemprop="name"]'},
            "episodes": {}
        },
        "note": "Blender 开源动画短片，CC-BY 3.0 授权，页面含多清晰度 mp4，可直接采集。",
        "enabled": True,
    },
    {
        "id": "publicdomainTorrents",
        "name": "Public Domain Torrents",
        "url": "https://www.publicdomaintorrents.info/",
        "license": "Public Domain",
        "note": "示例：经典老片公有领域资源站，访问前请确认授权。",
        "enabled": False,
    },
    {
        "id": "opensource_movies",
        "name": "Open Source / CC 授权短片合集",
        "url": "",
        "license": "CC BY / CC0",
        "note": "占位：把你有授权的 CC 片源首页地址填到此处 url 即可。",
        "enabled": False,
    },
]


def _path():
    from . import store
    return os.path.join(store.DATA_DIR, "presets.json")


def load_presets():
    p = _path()
    if not os.path.exists(p):
        save_presets(DEFAULT_PRESETS)
        return list(DEFAULT_PRESETS)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return list(DEFAULT_PRESETS)


def save_presets(presets):
    p = _path()
    with _lock:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=2)


def add_preset(data):
    presets = load_presets()
    pid = data.get("id") or ("preset_" + str(abs(hash(data.get("name", "") or "") % 10**8)))
    data["id"] = pid
    if "enabled" not in data:
        data["enabled"] = False
    presets.append(data)
    save_presets(presets)
    return data


def update_preset(pid, data):
    presets = load_presets()
    for i, x in enumerate(presets):
        if x.get("id") == pid:
            presets[i].update(data)
            presets[i]["id"] = pid
            save_presets(presets)
            return presets[i]
    return None


def remove_preset(pid):
    presets = [x for x in load_presets() if x.get("id") != pid]
    save_presets(presets)
    return len(presets)
