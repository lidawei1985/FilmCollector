# -*- coding: utf-8 -*-
"""
auto_pipeline.py —— 全自动闭环管道
==================================
把"找片 → 采集 → 打包 → 上传公网 → 喂给指定 APK"串成一步，小白只需第一次填 Token。

run_auto() 流程：
  1. 自动从筛选合集发现新片（排除已入库）
  2. 逐条采集详情并入本地库（去重）
  3. 生成纯静态 TVBox 订阅包（subscribe.json / api.js / data.json）
  4. 若已记住 Token：自动推送到 GitHub/Gitee Pages（全球可播，不依赖电脑）
  5. 额外生成 apk_feed.json（含 subscribe_url），随包一起上传——
     你的「指定 APK」首次启动读取它即可自动加载本订阅源，无需手动粘贴，形成闭环。
  无 Token 时：仍会本地入库，并标记 needs_token，等用户填一次 Token 后下次自动上传。

状态：模块级 _running 标志，供前端轮询"正在自动更新…"。
"""
import os
import json
import time
from datetime import datetime

from . import store, auto_feed, publisher, deployer, auth_store

_running = False
_last_run = {"at": "", "result": {}}


def _existing_keys():
    db = store.load_db()
    ids, titles = set(), set()
    for it in db.get("items", []):
        if it.get("source_id"):
            ids.add(it["source_id"])
        if it.get("title"):
            titles.add(it["title"].strip().lower())
    return ids, titles


def get_status():
    return {
        "running": _running,
        "last_run": _last_run["at"],
        "last_result": _last_run["result"],
    }


def _write_apk_feed(base):
    """生成 apk_feed.json：指定 APK 首启读取此文件即可自动加载订阅源（闭环关键）。"""
    b = base.rstrip("/") + "/"
    feed = {
        "app": "FilmCollector",
        "subscribe_url": b + "subscribe.json",
        "data_json": b + "data.json",
        "api_js": b + "api.js",
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "供「指定 APK」在首次启动时读取，自动加载本订阅源，无需手动粘贴地址。",
    }
    path = os.path.join(publisher.OUT_DEFAULT, "apk_feed.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)
    return path


def run_auto(max_new=None, upload=None, categories=None, source="db", cred=None):
    """全自动一步跑完。返回 report dict。

    cred: 可选，云端模式从环境变量注入的凭据字典
          {token, platform, username, repo}；为 None 时回退本机 auth_store。
    """
    global _running, _last_run
    if _running:
        return {"ok": False, "msg": "已有自动任务在运行中，请稍候。"}
    _running = True
    t0 = time.time()
    try:
        cfg = store.load_config()
        if max_new is None:
            max_new = cfg.get("auto_max_new", 20)
        if upload is None:
            upload = cfg.get("auto_upload", True)
        if categories is None:
            categories = cfg.get("auto_categories") or []

        # 1) 发现
        ids, titles = _existing_keys()
        candidates = auto_feed.discover_candidates(
            max_per=8, existing_ids=ids, existing_titles=titles, categories=categories
        )

        # 2) 采集入库（去重）
        db = store.load_db()
        added = []
        for c in candidates[:int(max_new)]:
            it = auto_feed.fetch_one(c["identifier"])
            if not it:
                continue
            if it.get("source_id") in ids or (it.get("title", "").strip().lower() in titles):
                continue
            it["id"] = str(__import__("uuid").uuid4())
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            it["created_at"] = now
            it["updated_at"] = now
            db["items"].append(it)
            ids.add(it["source_id"])
            titles.add(it["title"].strip().lower())
            added.append(it["title"])
        store.save_db(db)
        store.log("info", f"自动更新：新增 {len(added)} 部", len(added))

        report = {
            "ok": True,
            "added": len(added),
            "added_titles": added,
            "total": len(db.get("items", [])),
            "candidates": len(candidates),
            "uploaded": False,
            "needs_token": False,
            "subscribe": None,
            "errors": [],
        }

        # 3)+4)+5) 上传公网 + APK 源馈闭环
        if upload and added:
            if cred is None:
                cred = auth_store.load() if auth_store.has() else None
            if cred and cred.get("token"):
                try:
                    platform = cred["platform"]
                    token = cred["token"]
                    username = cred["username"]
                    repo = cred.get("repo", "FilmCollector")
                    base = deployer.build_base(platform, username, repo)
                    pub = publisher.build_bundle(
                        source=source, base=base, out_dir=publisher.OUT_DEFAULT, clean=True
                    )
                    _write_apk_feed(base)
                    res = deployer.deploy(platform, token, publisher.OUT_DEFAULT, repo, username)
                    report["uploaded"] = True
                    report["subscribe"] = res.get("subscribe")
                    report["platform"] = platform
                    report["apk_feed"] = base.rstrip("/") + "/apk_feed.json"
                except Exception as e:
                    report["errors"].append(str(e))
                    report["needs_token"] = ("Token" in str(e)) or ("token" in str(e).lower())
            else:
                report["needs_token"] = True

        # 记录运行状态
        _last_run = {
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": {
                "added": report["added"],
                "uploaded": report["uploaded"],
                "subscribe": report["subscribe"],
                "needs_token": report["needs_token"],
            },
        }
        cfg2 = store.load_config()
        cfg2["auto_last_run"] = _last_run["at"]
        cfg2["auto_last_result"] = _last_run["result"]
        store.save_config(cfg2)

        cost = round(time.time() - t0, 1)
        store.log("info", f"自动更新完成：耗时 {cost}s，新增 {report['added']} 部，上传={report['uploaded']}")
        report["cost"] = cost
        return report
    except Exception as e:
        store.log("error", f"自动更新异常：{e}")
        return {"ok": False, "msg": str(e)}
    finally:
        _running = False
