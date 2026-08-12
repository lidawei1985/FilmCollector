# -*- coding: utf-8 -*-
"""
后端服务：同时承载
  1) 零代码前端静态资源（桌面 WebView2 窗体内运行）
  2) 应用管理 API（/api/app/*）
  3) 本地影视订阅 API（/api.php/provide/vod，maccms 兼容，影视客户端可直接添加）
整套本地运行，数据仅存本机。
"""
import os
import json
import uuid
import shutil
import threading
import time
from datetime import datetime, timezone
import hashlib

from flask import Flask, request, jsonify, send_from_directory, Response

from .core import store
from .core import detector, scraper, cleaner, dedup, classifier, json_gen, image_cache
from .core import presets as presets_mod
from .core import publisher as publisher_mod
from .core import deployer as deployer_mod
from .core import auth_store as auth_store_mod
from .core import auto_feed as auto_feed_mod
from .core import auto_pipeline as auto_pipeline_mod
from .core import cloud_init as cloud_init_mod

FRONTEND_DIR = os.path.join(store.ASSET_DIR, "frontend")
CLIENT_DIR = os.path.join(store.ASSET_DIR, "frontend", "client")
app = Flask(__name__, static_folder=None)

# ----------------------- 本地订阅 API 开关 -----------------------
_api_enabled = {"on": False}
_sched_thread = None


def _now():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _items_to_vods(items, status_ok=True):
    from .core import json_gen
    src = [it for it in items if (not status_ok or it.get("status") != "dead")]
    return [json_gen._to_tvbox(it) for it in src]


def _items_to_generic(items, status_ok=True):
    from .core import json_gen
    src = [it for it in items if (not status_ok or it.get("status") != "dead")]
    return [json_gen._to_generic(it) for it in src]


def _filter_vods(vods, t, wd):
    if t:
        vods = [v for v in vods if v.get("type_name") == t]
    if wd:
        vods = [v for v in vods if wd in (v.get("vod_name", "") + v.get("vod_actor", "") + v.get("vod_content", "")).lower()]
    return vods


def _generic_filter(vods, t, wd):
    if t:
        vods = [v for v in vods if v.get("type") == t]
    if wd:
        vods = [v for v in vods if wd in (v.get("name", "") + v.get("desc", "")).lower()]
    return vods


# ====================== 应用管理 API ======================
@app.route("/api/app/detect", methods=["POST"])
def api_detect():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"ok": False, "msg": "链接为空"}), 400
    res = detector.detect(url, rotate_ua=store.load_config().get("rotate_ua", True))
    store.log("info", f"站点检测：{url} -> {res['level_text']}")
    return jsonify({"ok": True, **res})


@app.route("/api/app/scrape", methods=["POST"])
def api_scrape():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()
    template = data.get("template") or None
    mode = data.get("mode", "server")
    max_pages = int(data.get("max_pages", 1) or 1)
    collection = data.get("collection") or (template or {}).get("collection")
    list_mode = bool(template and template.get("list"))
    if not url and not collection and not list_mode:
        return jsonify({"ok": False, "msg": "链接为空"}), 400

    t0 = time.time()
    if collection:
        # 集合一键采集：archive.org advancedsearch 列片 -> 逐条详情
        max_items = int(data.get("max_items", 12) or 12)
        raw = scraper.crawl_collection(collection, template, max_items=max_items)
    elif max_pages > 1 or (template and template.get("list")):
        raw = scraper.crawl_list(template, url, max_pages=max_pages)
    elif mode == "browser":
        # 桌面端已把 DOM 传回
        html = data.get("html", "")
        raw = scraper.parse_html(template, html, url) if html else []
    else:
        raw = scraper.scrape(template, url, mode=mode)
    store.log("info", f"采集到原始记录 {len(raw)} 条：{url}")

    cleaned, cstats = cleaner.clean_items(raw)
    merged = dedup.dedup_items(cleaned)
    classified = classifier.classify(merged)

    db = store.load_db()
    added = 0
    for it in classified:
        it["id"] = str(uuid.uuid4())
        it["created_at"] = _now()
        it["updated_at"] = _now()
        db["items"].append(it)
        added += 1
    store.save_db(db)
    cost = round(time.time() - t0, 2)
    store.log("info", f"入库 {added} 条（耗时 {cost}s）")
    return jsonify({"ok": True, "added": added, "clean": cstats, "cost": cost})


@app.route("/api/app/items", methods=["GET"])
def api_items():
    db = store.load_db()
    items = db.get("items", [])
    t = request.args.get("type", "")
    q = request.args.get("q", "").strip().lower()
    page = int(request.args.get("page", 1) or 1)
    limit = int(request.args.get("limit", 50) or 50)
    if t:
        items = [i for i in items if i.get("type") == t]
    if q:
        items = [i for i in items if q in (i.get("title", "") + i.get("aliases", "") + i.get("actors", "")).lower()]
    total = len(items)
    start = (page - 1) * limit
    chunk = items[start:start + limit]
    return jsonify({"ok": True, "total": total, "page": page, "limit": limit, "items": chunk})


@app.route("/api/app/item/<iid>", methods=["PUT", "DELETE"])
def api_item(iid):
    db = store.load_db()
    items = db.get("items", [])
    idx = next((k for k, it in enumerate(items) if it.get("id") == iid), None)
    if idx is None:
        return jsonify({"ok": False, "msg": "未找到"}), 404
    if request.method == "DELETE":
        items.pop(idx)
        store.save_db(db)
        store.log("info", "手动删除影片")
        return jsonify({"ok": True})
    data = request.get_json(force=True, silent=True) or {}
    items[idx].update(data)
    items[idx]["updated_at"] = _now()
    store.save_db(db)
    return jsonify({"ok": True, "item": items[idx]})


@app.route("/api/app/manual_add", methods=["POST"])
def api_manual_add():
    """高强度加密站点兜底：可视化面板手动粘贴单集播放地址。"""
    data = request.get_json(force=True, silent=True) or {}
    item = {k: data.get(k, "") for k in
            ["title", "aliases", "year", "region", "type", "director", "actors",
             "description", "duration", "rating", "subtitle", "poster", "cover"]}
    urls = [u.strip() for u in data.get("play_urls", "").splitlines() if u.strip()]
    line = data.get("line", "手动线路")
    item["episodes"] = [{"name": f"第{i+1}集", "url": u, "line": line} for i, u in enumerate(urls)]
    item["id"] = str(uuid.uuid4())
    item["source_url"] = data.get("source_url", "")
    item["status"] = "ok" if item["episodes"] else "dead"
    item["created_at"] = _now()
    item["updated_at"] = _now()
    db = store.load_db()
    db["items"].append(item)
    store.save_db(db)
    store.log("info", f"手动添加影片：{item['title']}（{len(urls)} 条播放地址）")
    return jsonify({"ok": True, "item": item})


@app.route("/api/app/import_urls", methods=["POST"])
def api_import_urls():
    data = request.get_json(force=True, silent=True) or {}
    urls = [u.strip() for u in data.get("urls", "").splitlines() if u.strip()]
    # 批量：逐条自动探测+采集（无模板走自动嗅探）
    added = 0
    for u in urls:
        try:
            raw = scraper.scrape(None, u, mode="server")
            cleaned, _ = cleaner.clean_items(raw)
            merged = dedup.dedup_items(cleaned)
            classified = classifier.classify(merged)
            db = store.load_db()
            for it in classified:
                it["id"] = str(uuid.uuid4())
                it["created_at"] = _now()
                it["updated_at"] = _now()
                db["items"].append(it)
                added += 1
            store.save_db(db)
        except Exception as e:
            store.log("warn", f"批量采集失败 {u}：{e}")
    store.log("info", f"批量导入采集完成，新增 {added} 条")
    return jsonify({"ok": True, "added": added})


@app.route("/api/app/generate", methods=["POST"])
def api_generate():
    res = json_gen.generate()
    return jsonify({"ok": True, **res})


@app.route("/api/app/publish", methods=["POST"])
def api_publish():
    """生成跨环境通用的静态 TVBox 订阅包（tvbox-dist），可直接托管到任意静态平台。"""
    data = request.get_json(force=True, silent=True) or {}
    source = data.get("source", "db")
    base = (data.get("base") or "").strip()
    out = data.get("out") or publisher_mod.OUT_DEFAULT
    try:
        res = publisher_mod.build_bundle(source=source, base=base or None, out_dir=out, clean=bool(data.get("clean", False)))
        return jsonify({"ok": True, **res})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/api/app/deploy_config", methods=["GET"])
def api_deploy_config():
    """返回已保存的部署凭据（Token 脱敏），供前端自动填充表单。"""
    cred = auth_store_mod.load()
    if not cred:
        return jsonify({"ok": True, "cred": None})
    return jsonify({
        "ok": True,
        "cred": {
            "platform": cred["platform"],
            "username": cred["username"],
            "repo": cred["repo"],
            "token_mask": auth_store_mod.mask_token(cred["token"]),
        },
    })


@app.route("/api/app/deploy", methods=["POST"])
def api_deploy():
    """
    一键部署：生成静态包并推送到 GitHub Pages / Gitee Pages，返回公网订阅地址。
    首次需提供 platform + token；之后 token 留空即自动用已记住的凭据，免重复输入。
    """
    data = request.get_json(force=True, silent=True) or {}
    platform = (data.get("platform") or "github").lower()
    token = (data.get("token") or "").strip()
    username = (data.get("username") or "").strip() or None
    repo = (data.get("repo") or "").strip() or None
    source = data.get("source", "db")
    out = publisher_mod.OUT_DEFAULT

    # 未传 token 时尝试用已记住的（仅当平台/仓库与记忆一致，避免误用）
    if not token:
        saved = auth_store_mod.load()
        if saved and saved["platform"] == platform and (not repo or repo == saved["repo"]):
            token = saved["token"]
            username = username or saved["username"] or None
            repo = repo or saved["repo"]

    if not token:
        return jsonify({"ok": False, "msg": "请先填写 Access Token（首次使用需去平台生成一次，工具会记住）。"}), 400

    try:
        if not username:
            username = deployer_mod.get_username(platform, token)
        if not username:
            return jsonify({"ok": False, "msg": "无法获取用户名，请在下方手动填写你的平台用户名。"}), 400
        repo = repo or "FilmCollector"
        base = deployer_mod.build_base(platform, username, repo)
        # 用真实 base 注入再生成包（保证 api.js/subscribe.json 地址正确）
        pub = publisher_mod.build_bundle(source=source, base=base, out_dir=out, clean=True)
        # 推送到公网
        res = deployer_mod.deploy(platform, token, out, repo, username)
        # 加密记住凭据（含 token），下次免填
        auth_store_mod.save(platform, username, repo, token)
        res["ok"] = True
        res["count"] = pub.get("count")
        res["posters"] = pub.get("posters") or {"copied": 0, "missing": 0, "remote": 0}
        return jsonify(res)
    except deployer_mod.DeployError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": "部署异常：" + str(e)}), 500


# ----------------------- 全自动闭环 -----------------------
@app.route("/api/app/auto", methods=["POST"])
def api_auto():
    """立即跑一次全自动：找片→采集→打包→上传公网→喂指定 APK。"""
    data = request.get_json(force=True, silent=True) or {}
    max_new = data.get("max_new")
    upload = data.get("upload")
    categories = data.get("categories")
    try:
        report = auto_pipeline_mod.run_auto(max_new=max_new, upload=upload, categories=categories)
        return jsonify(report)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/api/app/auto/status", methods=["GET"])
def api_auto_status():
    cfg = store.load_config()
    st = auto_pipeline_mod.get_status()
    cred = auth_store_mod.load() if auth_store_mod.has() else None
    return jsonify({
        "ok": True,
        "running": st["running"],
        "last_run": st["last_run"],
        "last_result": st["last_result"],
        "auto_mode": cfg.get("auto_mode", False),
        "auto_on_launch": cfg.get("auto_on_launch", True),
        "auto_max_new": cfg.get("auto_max_new", 20),
        "auto_upload": cfg.get("auto_upload", True),
        "auto_categories": cfg.get("auto_categories", []),
        "has_token": bool(cred and cred.get("token")),
        "collections": auto_feed_mod.CURATED_COLLECTIONS,
    })


@app.route("/api/app/auto/settings", methods=["POST"])
def api_auto_settings():
    data = request.get_json(force=True, silent=True) or {}
    cfg = store.load_config()
    for k in ("auto_mode", "auto_on_launch", "auto_upload"):
        if k in data:
            cfg[k] = bool(data[k])
    if "auto_max_new" in data:
        cfg["auto_max_new"] = max(1, int(data["auto_max_new"]))
    if "auto_categories" in data:
        cfg["auto_categories"] = list(data["auto_categories"])
    store.save_config(cfg)
    return jsonify({"ok": True, **{k: cfg[k] for k in (
        "auto_mode", "auto_on_launch", "auto_upload", "auto_max_new", "auto_categories")}})


@app.route("/api/app/cloud_init", methods=["POST"])
def api_cloud_init():
    """一键开启云端无人值守：推源码(含定时任务)到代码仓库 + 部署订阅包到 Pages 仓库。"""
    data = request.get_json(force=True, silent=True) or {}
    platform = (data.get("platform") or "github").lower()
    token = (data.get("token") or "").strip()
    username = (data.get("username") or "").strip() or None
    code_repo = (data.get("code_repo") or "FilmCollector").strip()
    subscribe_repo = (data.get("subscribe_repo") or "filmcollector-pages").strip()
    try:
        res = cloud_init_mod.init_cloud(platform, token, code_repo, subscribe_repo, username)
        return jsonify(res)
    except deployer_mod.DeployError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": "云端初始化异常：" + str(e)}), 500


@app.route("/api/app/api/toggle", methods=["POST"])
def api_toggle():
    data = request.get_json(force=True, silent=True) or {}
    cfg = store.load_config()
    cfg["api_enabled"] = bool(data.get("enabled", not cfg["api_enabled"]))
    store.save_config(cfg)
    _api_enabled["on"] = cfg["api_enabled"]
    host, port = cfg["api_host"], cfg["api_port"]
    tvbox_url = f"http://{host}:{port}/api.php/provide/vod/"
    generic_url = f"http://{host}:{port}/api/generic/vod/"
    store.log("info", f"本地订阅 API {'已开启' if cfg['api_enabled'] else '已关闭'}：TVBox {tvbox_url} ｜ 通用 {generic_url}")
    return jsonify({"ok": True, "enabled": cfg["api_enabled"], "tvbox_url": tvbox_url, "generic_url": generic_url})


@app.route("/api/app/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify({"ok": True, **store.load_config()})
    data = request.get_json(force=True, silent=True) or {}
    cfg = store.load_config()
    cfg.update({k: v for k, v in data.items() if k in cfg})
    store.save_config(cfg)
    _api_enabled["on"] = cfg.get("api_enabled", False)
    return jsonify({"ok": True, **cfg})


@app.route("/api/app/network", methods=["GET"])
def api_network():
    cfg = store.load_config()
    lan = store.get_lan_ip()
    host = cfg["api_host"]
    # 显示用地址：0.0.0.0 时本机用 127.0.0.1，局域网用真实 IP
    local_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    port = cfg["api_port"]
    return jsonify({
        "ok": True,
        "api_host": host,
        "api_port": port,
        "lan_ip": lan,
        "local_base": f"http://{local_host}:{port}",
        "lan_base": f"http://{lan}:{port}",
    })


@app.route("/api/app/logs", methods=["GET"])
def api_logs():
    db = store.load_db()
    return jsonify({"ok": True, "logs": db.get("logs", [])})


@app.route("/api/app/backups", methods=["GET"])
def api_backups():
    return jsonify({"ok": True, "backups": store.list_backups()})


@app.route("/api/app/restore", methods=["POST"])
def api_restore():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "")
    ok = store.restore_backup(name)
    return jsonify({"ok": ok})


@app.route("/api/app/images", methods=["GET"])
def api_images():
    return jsonify({"ok": True, "images": image_cache.list_images(), "broken": image_cache.scan_broken()})


@app.route("/api/app/img/<name>")
def api_img(name):
    from .core import image_cache as _ic
    import urllib.parse
    name = urllib.parse.unquote(name)
    p = os.path.join(_ic.IMG_DIR, os.path.basename(name))
    if os.path.exists(p):
        return send_from_directory(_ic.IMG_DIR, os.path.basename(name))
    return Response(status=404)


def _cache_poster(url, referer):
    """海报防盗链请求头补全 + 下载到本地图库，返回本地访问路径（本机 I/O，不上传第三方）。"""
    try:
        headers = {"User-Agent": store.UA_POOL[0], "Referer": referer or url}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        ext = os.path.splitext(url.split("?")[0])[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        fn = hashlib.md5(url.encode("utf-8")).hexdigest() + ext
        path = os.path.join(image_cache.IMG_DIR, fn)
        with open(path, "wb") as f:
            f.write(r.content)
        return "/api/app/img/" + fn
    except Exception as e:
        store.log("warn", f"海报本地缓存失败 {url}：{e}")
        return None


@app.route("/api/app/cache_poster", methods=["POST"])
def api_cache_poster():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "msg": "url 为空"}), 400
    local = _cache_poster(url, data.get("referer", ""))
    if local:
        return jsonify({"ok": True, "local": local})
    return jsonify({"ok": False, "msg": "下载失败(防盗链/超时/404)"}), 502


@app.route("/api/app/clean_dead", methods=["POST"])
def api_clean_dead():
    """定时/手动巡检：重校验全部播放链接与图片，失效标记清除。"""
    db = store.load_db()
    cfg = store.load_config()
    ad_domains = store.load_ad_domains() if cfg.get("ad_filter", True) else []
    dead = 0
    ad_filtered = 0
    for it in db["items"]:
        kept = []
        for ep in it.get("episodes", []):
            if cleaner.is_ad_url(ep.get("url", ""), ad_domains):
                ad_filtered += 1
                continue
            ok, _ = cleaner.validate_url(ep.get("url", ""), cfg)
            if ok:
                kept.append(ep)
            else:
                dead += 1
        it["episodes"] = kept
        it["status"] = "ok" if kept else "dead"
        it["updated_at"] = _now()
    store.save_db(db)
    broken_images = len(image_cache.scan_broken())
    store.log("info", f"巡检完成：清除失效线路 {dead} 条，广告过滤 {ad_filtered} 条", dead)
    return jsonify({"ok": True, "dead": dead, "ad_filtered": ad_filtered, "broken_images": broken_images})


@app.route("/api/app/clear_cache", methods=["POST"])
def api_clear_cache():
    # 清空失效资源 + 图库破损图
    db = store.load_db()
    before = len(db["items"])
    db["items"] = [it for it in db["items"] if it.get("status") != "dead"]
    store.save_db(db)
    broken = image_cache.scan_broken()
    for p in broken:
        try:
            os.remove(p)
        except OSError:
            pass
    store.log("info", f"清空缓存：移除失效影片 {before - len(db['items'])} 条，删除破损图 {len(broken)} 张")
    return jsonify({"ok": True, "removed_items": before - len(db["items"]), "removed_images": len(broken)})


@app.route("/api/app/ad_domains", methods=["GET", "POST"])
def api_ad_domains():
    if request.method == "GET":
        return jsonify({"ok": True, "domains": store.load_ad_domains()})
    data = request.get_json(force=True, silent=True) or {}
    added = store.add_ad_domains([d.strip() for d in data.get("domains", []) if d.strip()])
    return jsonify({"ok": True, "added": added})


# ====================== 公共领域 / CC 采集源预设 ======================
@app.route("/api/app/presets", methods=["GET", "POST", "PUT", "DELETE"])
def api_presets():
    if request.method == "GET":
        return jsonify({"ok": True, "presets": presets_mod.load_presets()})
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        return jsonify({"ok": True, "preset": presets_mod.add_preset(d)})
    if request.method == "PUT":
        d = request.get_json(force=True, silent=True) or {}
        pid = d.get("id")
        upd = presets_mod.update_preset(pid, d)
        return jsonify({"ok": True, "preset": upd}) if upd else (jsonify({"ok": False, "msg": "未找到"}), 404)
    pid = request.args.get("id") or (request.get_json(force=True, silent=True) or {}).get("id")
    presets_mod.remove_preset(pid)
    return jsonify({"ok": True})


# ====================== 本地订阅 API（maccms 兼容） ======================
@app.route("/api.php/provide/vod/", methods=["GET"])
@app.route("/api.php/provide/vod", methods=["GET"])
def api_provide_vod():
    if not _api_enabled["on"]:
        return jsonify({"code": 0, "msg": "本地订阅 API 未开启，请在软件内开启", "list": []})
    ac = request.args.get("ac", "list")
    db = store.load_db()
    items = db.get("items", [])
    vods = _items_to_vods(items)

    if ac == "detail":
        ids = request.args.get("ids", "")
        want = set(ids.split(",")) if ids else set()
        vods = [v for v in vods if str(v.get("vod_id")) in want] if want else vods
        return jsonify({"code": 1, "msg": "ok", "list": vods})

    # list：支持分类 t、关键词 wd、分页 pg、limit
    t = request.args.get("t", "").strip()
    wd = request.args.get("wd", "").strip().lower()
    pg = int(request.args.get("pg", 1) or 1)
    limit = int(request.args.get("limit", 0) or 0)
    if t:
        vods = [v for v in vods if v.get("type_name") == t]
    if wd:
        vods = [v for v in vods if wd in (v.get("vod_name", "") + v.get("vod_actor", "") + v.get("vod_content", "")).lower()]
    total = len(vods)
    if limit:
        start = (pg - 1) * limit
        vods = vods[start:start + limit]
    pagecount = 1 if not limit else ((total + limit - 1) // limit or 1)
    return jsonify({"code": 1, "msg": "ok", "page": pg, "pagecount": pagecount, "limit": limit or total, "total": total, "list": vods})


@app.route("/api/generic/vod/", methods=["GET"])
@app.route("/api/generic/vod", methods=["GET"])
def api_generic_vod():
    """通用纯净影片数据接口：无 TVBox 私有字段，适配自研 APP / 网页系统 / 第三方播放器。"""
    if not _api_enabled["on"]:
        return jsonify({"code": 0, "msg": "本地订阅 API 未开启", "list": []})
    ac = request.args.get("ac", "list")
    db = store.load_db()
    items = db.get("items", [])
    vods = _items_to_generic(items)

    if ac == "detail":
        ids = request.args.get("ids", "")
        want = set(ids.split(",")) if ids else set()
        vods = [v for v in vods if str(v.get("id")) in want] if want else vods
        return jsonify({"code": 1, "msg": "ok", "list": vods})

    t = request.args.get("t", "").strip()
    wd = request.args.get("wd", "").strip().lower()
    pg = int(request.args.get("pg", 1) or 1)
    limit = int(request.args.get("limit", 0) or 0)
    vods = _generic_filter(vods, t, wd)
    total = len(vods)
    if limit:
        start = (pg - 1) * limit
        vods = vods[start:start + limit]
    pagecount = 1 if not limit else ((total + limit - 1) // limit or 1)
    return jsonify({"code": 1, "msg": "ok", "page": pg, "pagecount": pagecount, "limit": limit or total, "total": total, "list": vods})


@app.route("/api/app/export_folder", methods=["POST"])
def api_export_folder():
    """单独导出两套 JSON 至电脑任意文件夹（桌面端可调用系统文件夹选择器）。"""
    data = request.get_json(force=True, silent=True) or {}
    folder = data.get("folder", "").strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({"ok": False, "msg": "目标文件夹不存在"}), 400
    copied = []
    for sub in ("tvbox", "generic"):
        src = os.path.join(store.BASE_DIR, "output", "json", sub)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(folder, sub)
        os.makedirs(dst, exist_ok=True)
        for f in os.listdir(src):
            if f.endswith(".json"):
                shutil.copy(os.path.join(src, f), os.path.join(dst, f))
                copied.append(os.path.join(sub, f))
    store.log("info", f"导出两套 JSON 至 {folder}，共 {len(copied)} 个文件")
    return jsonify({"ok": True, "folder": folder, "files": copied})


@app.route("/api/app/save_client_export", methods=["POST"])
def api_save_client_export():
    """保存客户端双格式导出（TVBox catalog.json + 通用纯净 all.json），并备份两套格式历史。"""
    data = request.get_json(force=True, silent=True) or {}
    tvbox = data.get("tvbox", {}) or {}
    generic = data.get("generic", {}) or {}
    saved = []
    base = store.BASE_DIR
    for sub, bag in (("tvbox", tvbox), ("generic", generic)):
        out_dir = os.path.join(base, "output", "json", sub)
        os.makedirs(out_dir, exist_ok=True)
        for fname, obj in bag.items():
            p = os.path.join(out_dir, fname)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            saved.append(f"{sub}/{fname}")
    # 备份两套格式历史（每日自动备份，纯读写规避沙箱 shutil 拦截）
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = os.path.join(base, "output", "backup", "json_" + ts)
        json_root = os.path.join(base, "output", "json")
        if os.path.isdir(json_root):
            for sub in ("tvbox", "generic"):
                src_dir = os.path.join(json_root, sub)
                if not os.path.isdir(src_dir):
                    continue
                dst_dir = os.path.join(backup_root, sub)
                os.makedirs(dst_dir, exist_ok=True)
                for f in os.listdir(src_dir):
                    if f.endswith(".json"):
                        with open(os.path.join(src_dir, f), "r", encoding="utf-8") as sf:
                            content = sf.read()
                        with open(os.path.join(dst_dir, f), "w", encoding="utf-8") as df:
                            df.write(content)
    except Exception as e:
        store.log("warn", f"导出备份失败：{e}")
    store.log("info", f"客户端双格式导出完成，保存 {len(saved)} 个文件并备份两套格式历史")
    return jsonify({"ok": True, "saved": saved})


# ====================== 前端静态资源 ======================
@app.route("/client")
@app.route("/client/")
def client_index():
    return send_from_directory(CLIENT_DIR, "index.html")


@app.route("/client/<path:p>")
def client_static(p):
    return send_from_directory(CLIENT_DIR, p)


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:p>")
def static_frontend(p):
    return send_from_directory(FRONTEND_DIR, p)


# ====================== 定时任务 ======================
def _scheduler_loop():
    cfg = store.load_config()
    while True:
        try:
            cfg = store.load_config()
            if cfg.get("schedule", {}).get("enabled"):
                # 巡检 + 重新生成订阅源
                _bg_clean_and_generate()
            mode = cfg.get("schedule", {}).get("mode", "daily")
            sleep_sec = 3600 if mode == "hourly" else 86400
            time.sleep(sleep_sec)
        except Exception:
            time.sleep(60)


def _bg_clean_and_generate():
    db = store.load_db()
    cfg = store.load_config()
    ad_domains = store.load_ad_domains() if cfg.get("ad_filter", True) else []
    dead = 0
    ad_filtered = 0
    for it in db["items"]:
        kept = [ep for ep in it.get("episodes", [])
                if not cleaner.is_ad_url(ep.get("url", ""), ad_domains)
                and cleaner.validate_url(ep.get("url", ""), cfg)[0]]
        dead += len(it.get("episodes", [])) - len(kept)
        it["episodes"] = kept
        it["status"] = "ok" if kept else "dead"
    store.save_db(db)
    # 海报补全：把缺失/远程的海报下载进本地图库（APK 始终有图的兜底）
    poster_stats = image_cache.backfill_missing(db["items"])
    if poster_stats["fixed"]:
        store.save_db(db)
        store.log("info", f"定时补全海报 {poster_stats['fixed']} 张（无图源 {poster_stats['skipped']}，失败 {poster_stats['failed']}）")
    json_gen.generate()
    # 若有上传凭据且开启上传：重新发布到公网（含最新海报图库），让 APK 拿到更新
    if cfg.get("auto_upload", True):
        _maybe_redeploy(poster_stats)
    store.log("info", f"定时任务：巡检清除失效 {dead} 条并重新生成订阅源；海报补 {poster_stats['fixed']} 张")


def _maybe_redeploy(poster_stats=None):
    """重新发布到公网。触发条件：本轮补齐了新海报，或「今日精选」批次发生轮换。
    失败仅告警，不影响本地。"""
    cred = auth_store_mod.load() if auth_store_mod.has() else None
    if not cred or not cred.get("token"):
        return
    try:
        platform, token = cred["platform"], cred["token"]
        username, repo = cred.get("username"), cred.get("repo", "FilmCollector")
        base = deployer_mod.build_base(platform, username, repo)
        # 始终重新生成包（含海报仓库 + 今日精选），以便判断精选批次是否变化
        res = publisher_mod.build_bundle(source="db", base=base, out_dir=publisher_mod.OUT_DEFAULT, clean=True)
        new_batch = (res.get("featured") or {}).get("batch")
        cfg = store.load_config()
        last_batch = cfg.get("_last_featured_batch")
        posters_fixed = (poster_stats or {}).get("fixed", 0)
        changed = posters_fixed > 0 or (new_batch is not None and new_batch != last_batch)
        if not changed:
            return  # 没有新海报也没有精选轮换，跳过推送
        auto_pipeline_mod._write_apk_feed(base)
        deployer_mod.deploy(platform, token, publisher_mod.OUT_DEFAULT, repo, username)
        if new_batch is not None:
            cfg["_last_featured_batch"] = new_batch
            store.save_config(cfg)
        store.log("info", f"定时任务：已重新发布到公网（新海报 {posters_fixed} 张；精选第 {new_batch} 批）")
    except Exception as e:
        store.log("warn", f"定时重新发布失败：{e}")


def start_scheduler():
    global _sched_thread
    if _sched_thread is None:
        _sched_thread = threading.Thread(target=_scheduler_loop, daemon=True)
        _sched_thread.start()


def run(host="127.0.0.1", port=9911, debug=False):
    cfg = store.load_config()
    _api_enabled["on"] = cfg.get("api_enabled", False)
    start_scheduler()
    app.run(host=cfg.get("api_host", host), port=cfg.get("api_port", port), debug=False, threaded=True)


if __name__ == "__main__":
    run()
