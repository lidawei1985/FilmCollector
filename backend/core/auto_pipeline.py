# -*- coding: utf-8 -*-
"""
auto_pipeline.py —— 全自动闭环管道
==================================
把"找片 → 采集 → 打包 → 上传公网 → 喂给指定 APK"串成一步，小白只需第一次填 Token。

run_auto() 流程（韧性设计）：
  0. 运行前先做「本地库快照备份」；若本地库为空（清库/换机），自动从本地备份或
     已部署仓库的 db.json 回拉，保证目录增长不清零（连续性）。
  1. 自愈：清理失效播放地址（死链不入 APK，保证"库在涨、点开能播"）
  2. 自动从筛选合集发现新片（排除已入库）
  3. 逐条采集详情并入本地库（去重）
  4. 生成纯静态 TVBox 订阅包（subscribe.json / api.js / data.json）
  5. 若已记住 Token：自动推送到 GitHub/Gitee Pages（全球可播，不依赖电脑）
  6. 额外生成 apk_feed.json（含 subscribe_url），随包一起上传——闭环关键
  7. 瞬时网络失败自动重试一次；每次运行写入 run_status.json + auto.log，便于一眼看健康度

状态：模块级 _running 标志，供前端轮询"正在自动更新…"。
"""
import os
import json
import time
import re
from datetime import datetime

from . import store, auto_feed, publisher, deployer, auth_store, poster_cache, net

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


def _is_transient(e):
    """判断是否为瞬时网络类错误（可安全重试一次）。"""
    s = str(e).lower()
    return any(k in s for k in ("timed out", "timeout", "network", "connection",
                                "远程", "请求失败", "503", "502", "504", "429",
                                "临时", "reset"))


def _ensure_continuity(cfg):
    """保证目录连续性：本地库空时从本地备份 / 已部署仓库回拉，避免增长清零。返回恢复部数。"""
    db = store.load_db()
    if db.get("items"):
        return 0
    # 1) 本地最新备份
    backs = store.list_backups()
    if backs:
        if store.restore_backup(backs[0]):
            db = store.load_db()
            n = len(db.get("items", []))
            if n:
                store.log("info", f"目录连续性：从本地备份恢复 {n} 部")
                return n
    # 2) 已部署仓库的 db.json（跨机恢复）
    base = cfg.get("last_deploy_base")
    if base:
        try:
            remote = net.get_json(base.rstrip("/") + "/db.json", timeout=20, min_interval=0.5)
            if remote and remote.get("items"):
                store.save_db(remote)
                n = len(remote["items"])
                store.log("info", f"目录连续性：从已部署仓库恢复 {n} 部")
                return n
        except Exception as e:
            store.log("warn", "目录连续性：远程恢复失败 " + str(e))
    return 0


def _write_status(report, restored=0, health="ok"):
    """写入运行状态 JSON + 追加日志，便于一眼看健康度、不再'悄悄停更'。"""
    path = os.path.join(store.DATA_DIR, "run_status.json")
    log_path = os.path.join(store.DATA_DIR, "auto.log")
    try:
        cur = {}
        if os.path.exists(path):
            try:
                cur = json.load(open(path, encoding="utf-8"))
            except Exception:
                cur = {}
        history = cur.get("history", [])
        entry = {
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "added": report.get("added", 0),
            "restored": restored,
            "dead_removed": report.get("pruned_count", 0),
            "fixed": len(report.get("pruned_fixed", []) or []),
            "total": report.get("total", 0),
            "uploaded": report.get("uploaded", False),
            "health": health,
            "source": report.get("source_status", "ok"),
            "errors": (report.get("errors", []) or [])[:5],
            "cost": report.get("cost", 0),
        }
        history.insert(0, entry)
        history = history[:7]
        cur = {"last_run": entry["at"], "health": health, "history": history}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{entry['at']}] health={health} added={entry['added']} "
                    f"restored={entry['restored']} dead={entry['dead_removed']} "
                    f"fixed={entry['fixed']} total={entry['total']} up={entry['uploaded']} "
                    f"err={entry['errors']}\n")
    except Exception:
        pass


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


def _norm_key(title, year):
    """规范化片名+年份，作为强去重键（忽略标点/空格/大小写/内嵌年份/冠词）。"""
    t = (title or "").strip().lower()
    t = re.sub(r"[()\[\]\s\-_:.，。、（）【】]+", "", t)
    t = re.sub(r"(19|20)\d{2}", "", t)        # 去掉标题里内嵌的年份（如 "Matrix (1920)"）
    y = str(year or "").strip()
    return f"{t}|{y}" if y else t


def _run_core(cfg, max_new, upload, categories, source, cred, restored):
    """单次完整执行（run_auto 会包一层瞬时重试）。返回 report dict。

    韧性要点（对标 tvbox-source-aggregator / commoncrawl 等生产级做法）：
    - 上游(archive.org)被限流→标记 degraded 但仍发布现有片库、不 wipe、不硬撞；
    - 死链自检在源不可达时自动跳过（无法验证就保留，绝不误删好片）；
    - 库异常骤减（远多于死链能解释）时拒绝推送，保留上次有效订阅；
    - 强去重（规范化片名+年份），避免同片不同后缀重复入库污染增长。
    """
    t0 = time.time()
    # 0) 发现（跨启用源；任一源被限流则降级但保留现有片库）
    ids, titles = _existing_keys()
    candidates = auto_feed.discover_candidates(
        max_per=8, existing_ids=ids, existing_titles=titles, categories=categories
    )
    source_blocked = bool(getattr(auto_feed, "last_blocked", False))
    source_status = "blocked" if source_blocked else "ok"

    # 1) 自愈 + 分级健康：检测播放地址，死链移除，限流/超时按失败次数升级
    #    warning→degraded→disabled（disabled 资源仍保留在库、待恢复，仅 APK 隐藏）。
    #    上游被限流时跳过（无法验证，保留现有片库，避免误删好片）
    pruned_removed, pruned_fixed = [], []
    if not source_blocked:
        try:
            if cfg.get("link_check_enabled", True):
                from . import link_check, quality
                db0 = store.load_db()
                db0, pruned_removed, pruned_fixed = link_check.check_db_health(
                    db0, enabled=True, timeout=cfg.get("link_check_timeout", 10))
                store.save_db(db0)
                # 统计 disabled（已隐藏）资源，便于一眼看健康度
                disabled = 0
                for it in db0.get("items", []):
                    for ep in it.get("episodes") or []:
                        if quality.is_disabled(ep):
                            disabled += 1
                if pruned_removed or pruned_fixed or disabled:
                    store.log("info", f"分级健康自检：移除死片 {len(pruned_removed)} 部，"
                                f"状态变化 {len(pruned_fixed)} 部，当前隐藏 {disabled} 条资源")
        except Exception as e:
            store.log("warn", "分级健康自检异常：" + str(e))
    else:
        store.log("warn", "上游被限流，跳过健康自检（无法验证，保留现有片库）")

    # 2) 采集入库（强去重：规范化片名+年份）
    db = store.load_db()
    added = []
    norm_seen = set(titles)
    for c in candidates[:int(max_new)]:
        it = auto_feed.fetch_one(c["identifier"], c.get("provider", "ia"))
        if not it:
            continue
        nkey = _norm_key(it.get("title", ""), it.get("year", ""))
        if it.get("source_id") in ids or nkey in norm_seen:
            continue
        it["id"] = str(__import__("uuid").uuid4())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        it["created_at"] = now
        it["updated_at"] = now
        db["items"].append(it)
        ids.add(it["source_id"])
        titles.add(it["title"].strip().lower())
        norm_seen.add(nkey)
        added.append(it["title"])
    store.save_db(db)
    store.log("info", f"自动更新：新增 {len(added)} 部", len(added))

    # 计算本次将发布的片数（与上次部署对比，用于"保留上次有效订阅"保护）
    new_count = len([it for it in db.get("items", [])
                     if it.get("status") != "dead" and it.get("episodes")])
    prev_count = cfg.get("last_deploy_count") or 0

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
        "pruned_removed": pruned_removed,
        "pruned_fixed": pruned_fixed,
        "pruned_count": len(pruned_removed),
        "source_status": source_status,
        "new_count": new_count,
        "prev_count": prev_count,
    }

    # 2.5) 海报补齐（自建海报仓库）：保证每部片都有本地海报，随包上传后 APK 永不白屏
    try:
        pc = poster_cache.refresh_all(base="", save_db=True)
        report["posters"] = pc["backfill"]
    except Exception as e:
        report["errors"].append("poster:" + str(e))

    # 3)+4)+5) 生成订阅包（本地始终生成，保证"自动生成结果"）+ 有 Token 才部署公网
    if upload:
        if cred is None:
            cred = auth_store.load() if auth_store.has() else None
        token = (cred or {}).get("token")
        platform = (cred or {}).get("platform")
        username = (cred or {}).get("username")
        repo = (cred or {}).get("repo", "FilmCollector")
        # base：有凭据用部署 base；否则用上次部署 base 或占位（本地预览/手动部署用）
        if platform and username:
            base = deployer.build_base(platform, username, repo)
        else:
            base = cfg.get("last_deploy_base") or "https://YOUR-USERNAME.github.io/FilmCollector"

        # 始终生成本地订阅包（即使无 Token，也保证输出订阅数据，便于本地预览/手动部署）
        try:
            publisher.build_bundle(
                source=source, base=base, out_dir=publisher.OUT_DEFAULT,
                clean=True, meta={"source_status": source_status,
                                  "last_run_added": len(added),
                                  "blocked": source_blocked})
            _write_apk_feed(base)
            report["bundle_built"] = True
        except Exception as e:
            report["errors"].append("bundle:" + str(e))

        # 仅当存在有效 Token 才部署到公网（保留上次有效订阅保护仍适用部署动作）
        if token:
            # —— 保留上次有效订阅：库异常骤减（远超死链能解释）则拒绝推送 ——
            if new_count == 0:
                report["errors"].append("无可发布内容，已保留上次有效订阅（未覆盖线上）")
                report["kept_last_good"] = True
            elif prev_count and new_count < max(1, int(prev_count * 0.5)) \
                    and (prev_count - new_count) > len(pruned_removed):
                report["errors"].append(
                    f"片库异常骤减（{prev_count}→{new_count}），远超死链剔除量，"
                    f"已保留上次有效订阅，请检查本地数据库是否被损坏")
                report["kept_last_good"] = True
            else:
                try:
                    res = deployer.deploy(platform, token, publisher.OUT_DEFAULT, repo, username)
                    report["uploaded"] = True
                    report["subscribe"] = res.get("subscribe")
                    report["platform"] = platform
                    report["apk_feed"] = base.rstrip("/") + "/apk_feed.json"
                    cfg["last_deploy_base"] = base.rstrip("/")
                    cfg["last_deploy_count"] = new_count
                    # 部署后自检：确认线上订阅已真正更新
                    try:
                        report["deploy_verified"] = bool(deployer.verify(base))
                        if not report["deploy_verified"]:
                            report["errors"].append(
                                "deploy_verify: 线上订阅未确认（可能 CDN 缓存延迟，稍后自动恢复）")
                    except Exception as ve:
                        report["errors"].append("deploy_verify:" + str(ve))
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
    if cfg.get("last_deploy_base"):
        cfg2["last_deploy_base"] = cfg["last_deploy_base"]
    if cfg.get("last_deploy_count"):
        cfg2["last_deploy_count"] = cfg["last_deploy_count"]
    store.save_config(cfg2)

    cost = round(time.time() - t0, 1)
    store.log("info", f"自动更新完成：耗时 {cost}s，新增 {report['added']} 部，上传={report['uploaded']}，源状态={source_status}")
    report["cost"] = cost
    return report


def run_auto(max_new=None, upload=None, categories=None, source="db", cred=None):
    """全自动一步跑完（含连续性恢复 + 快照备份 + 瞬时重试 + 状态写入）。返回 report dict。

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

        # 0) 目录连续性：本地库空时回拉（必须在备份与跑核心之前）
        restored = _ensure_continuity(cfg)
        # 运行前快照备份（防损坏/误操作可回滚）
        try:
            store.backup_db()
        except Exception as e:
            store.log("warn", "运行前备份失败：" + str(e))

        report = None
        last_exc = None
        for attempt in range(2):
            try:
                report = _run_core(cfg, max_new, upload, categories, source, cred, restored)
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                if attempt == 0 and _is_transient(e):
                    store.log("warn", f"自动更新瞬时失败，30s 后重试：{e}")
                    time.sleep(30)
                    continue
                break

        if report is None:
            report = {"ok": False, "msg": str(last_exc)}

        # 健康度：上游封禁/异常保护→degraded；崩溃且未上传→error
        health = "ok"
        if report.get("source_status") == "blocked":
            health = "degraded"
        if report.get("kept_last_good") and not report.get("uploaded"):
            health = "degraded"
        if not report.get("ok") and not report.get("uploaded"):
            health = "error"
        elif report.get("errors") and health == "ok":
            health = "degraded"
        _write_status(report, restored=restored, health=health)
        return report
    except Exception as e:
        store.log("error", f"自动更新异常：{e}")
        _write_status({"ok": False, "msg": str(e), "added": 0, "total": 0,
                       "errors": [str(e)]}, health="error")
        return {"ok": False, "msg": str(e)}
    finally:
        _running = False
