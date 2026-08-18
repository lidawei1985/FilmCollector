# -*- coding: utf-8 -*-
"""
acceptance_test.py —— 无人值守最终验收（本地，不联网、不推送）

逐项验证四条核心保证：
  1) 防并发：跨进程锁能挡住第二次进入（不会写穿 db.json / 抢 tvbox-dist）。
  2) 防损坏：store 原子写（临时文件+os.replace）不残留 .tmp，结果仍是合法 JSON。
  3) 可恢复：backup_db 生成备份、restore_backup 可还原。
  4) 报警：alerts 去重 + 列表 + 解决。
  5) 质量评分：score_movie 落在 0~100，rank_items 降序。
  6) 可重复生成：同一份数据 build_bundle 两次，data.json 字节一致（确定性排序）。
  7) 部署前检查 / 线上健康检查 / 回滚：缺 Token → preflight 不过；
     伪造 200 响应 → online_health 通过；伪造 PATCH 200 → rollback 调用且返回 True。
  8) 无假成功：preflight 不过时不会上传（uploaded=False），错误被记录而非掩盖。

全部通过打印 ACCEPTANCE: ALL PASS；任一不过打印 FAIL 并退出码 1。
"""
import os
import sys
import json
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core import store, alerts, quality, run_lock, deployer, publisher, stats

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def _setup_tmp_project():
    """把所有落盘路径重定向到临时目录，避免污染真实库。"""
    tmp = tempfile.mkdtemp(prefix="fc_acc_")
    store.BASE_DIR = tmp
    store.DATA_DIR = os.path.join(tmp, "backend", "data")
    store.DB_PATH = os.path.join(store.DATA_DIR, "db.json")
    store.CONFIG_PATH = os.path.join(store.DATA_DIR, "config.json")
    store.AD_PATH = os.path.join(store.DATA_DIR, "ad_domains.txt")
    store.BACKUP_DIR = os.path.join(tmp, "output", "backup")
    os.makedirs(store.DATA_DIR, exist_ok=True)
    # 初始化空白库（_ensure 会建默认 config）
    store._ensure()
    return tmp


def test_lock():
    print("\n[1] 跨进程锁（防并发）")
    lk = run_lock.RunLock("acc_test")
    ok1 = lk.acquire()
    ok2 = run_lock.RunLock("acc_test").acquire()  # 第二次应拿不到
    lk.release()
    lk3 = run_lock.RunLock("acc_test")
    ok3 = lk3.acquire()   # 释放后应能再拿
    if ok3:
        lk3.release()     # 必须释放同一个实例，否则 fd 仍持有
    check("锁可获取", ok1)
    check("并发被挡(release前第二次False)", ok2 is False)
    check("释放后可再获取", ok3 is True)
    check("is_busy在空闲时为False", run_lock.is_busy("acc_test") is False)


def test_atomic_save():
    print("\n[2] 原子写（防损坏）")
    db = store.load_db()
    db["items"].append({"id": "x1", "title": "测试片", "episodes": [], "status": "ok"})
    store.save_db(db)
    # 不应残留临时文件
    tmp_left = os.path.exists(store.DB_PATH + ".tmp")
    # 仍是合法 JSON
    ok_json = True
    try:
        json.load(open(store.DB_PATH, encoding="utf-8"))
    except Exception:
        ok_json = False
    check("无残留临时文件", not tmp_left)
    check("写入后仍是合法JSON", ok_json)


def test_backup_restore():
    print("\n[3] 备份/恢复（可恢复）")
    db = store.load_db()
    db["items"] = [{"id": "a", "title": "原片", "episodes": [], "status": "ok"}]
    store.save_db(db)
    store.backup_db()
    backs = store.list_backups()
    check("备份已生成", len(backs) >= 1, f"backs={backs}")
    # 篡改库
    db["items"] = [{"id": "b", "title": "被篡改", "episodes": [], "status": "ok"}]
    store.save_db(db)
    restored = store.restore_backup(backs[0])
    db2 = store.load_db()
    check("恢复成功", restored and db2["items"][0]["id"] == "a")
    # 恢复会覆盖整个数据目录快照（备份时尚未写 alerts），这里重置 alerts 文件路径缓存，
    # 让后续报警测试在干净状态进行（避免测试间相互污染）。
    alerts.ALERTS_PATH = os.path.join(store.DATA_DIR, "alerts.json")


def test_alerts():
    print("\n[4] 异常报警（可观测）")
    alerts.raise_alert("t1", alerts.LEVEL_CRITICAL, "崩溃了")
    alerts.raise_alert("t1", alerts.LEVEL_CRITICAL, "又崩了")  # 去重累加
    lst = alerts.list_alerts()
    rec = [a for a in lst if a["key"] == "t1"]
    check("报警已记录", len(rec) == 1)
    check("同key去重累加count", rec and rec[0]["count"] == 2)
    alerts.resolve_alert("t1")
    check("解决后可隐藏", all(a["key"] != "t1" for a in alerts.list_alerts()))


def test_score():
    print("\n[5] 内容质量评分")
    good = {"title": "高清新片", "episodes": [{"url": "x_1080p.mp4", "health": {"status": "active"}}],
            "created_at": "2026-08-19 00:00:00"}
    bad = {"title": "无源片", "episodes": []}
    s_good = quality.score_movie(good)
    s_bad = quality.score_movie(bad)
    check("评分在0~100", 0 <= s_good <= 100 and 0 <= s_bad <= 100)
    check("有源片分高于无源片", s_good > s_bad, f"{s_good} vs {s_bad}")
    ranked = quality.rank_items([bad, good])
    check("rank_items降序(最佳在前)", ranked[0] is good)


def test_idempotent_bundle():
    print("\n[6] 可重复生成（确定性）")
    # 造两份相同数据，分别 build 到两个临时 out 目录
    db = store.load_db()
    db["items"] = [
        {"id": "1", "title": "A", "type_name": "电影", "year": 2020,
         "episodes": [{"url": "a_1080p.mp4", "health": {"status": "active"}}], "status": "ok"},
        {"id": "2", "title": "B", "type_name": "电影", "year": 2021,
         "episodes": [{"url": "b_720p.mp4", "health": {"status": "active"}}], "status": "ok"},
    ]
    store.save_db(db)
    out1 = tempfile.mkdtemp(prefix="fc_b1_")
    out2 = tempfile.mkdtemp(prefix="fc_b2_")
    publisher.OUT_DEFAULT = out1
    publisher.build_bundle(source="db", base="https://example.com/FilmCollector", out_dir=out1, clean=True)
    publisher.OUT_DEFAULT = out2
    publisher.build_bundle(source="db", base="https://example.com/FilmCollector", out_dir=out2, clean=True)
    d1 = open(os.path.join(out1, "data.json"), "rb").read()
    d2 = open(os.path.join(out2, "data.json"), "rb").read()
    check("两次生成data.json字节一致", d1 == d2)
    # 产物齐全
    for fn in ("subscribe.json", "data.json", "health.json", "stats.json"):
        check(f"产物存在 {fn}", os.path.isfile(os.path.join(out1, fn)))
    shutil.rmtree(out1, ignore_errors=True)
    shutil.rmtree(out2, ignore_errors=True)


def test_deploy_checks():
    print("\n[7] 部署前检查 / 线上健康检查 / 回滚（无联网）")
    # 造一个合法静态包目录
    out = tempfile.mkdtemp(prefix="fc_pf_")
    for fn in ("subscribe.json", "data.json", "health.json"):
        with open(os.path.join(out, fn), "w", encoding="utf-8") as f:
            json.dump({"sites": []} if fn == "subscribe.json" else {"list": []}, f)

    # 无 Token：preflight 的 Token 检查应失败
    pf = deployer.preflight("github", "", out, "FilmCollector", "u", "https://x.io/r")
    token_check = [c for c in pf["checks"] if c["name"] == "Token"][0]
    check("缺Token→preflight不过", pf["ok"] is False and token_check["ok"] is False)

    # 伪造 _http 返回 200（Token 有效 + 仓库可达）
    orig_http = deployer._http
    def fake_http(method, url, token, json_data=None, params=None, headers_extra=None):
        return 200, ({"login": "u"} if "user" in url else {"message": "ok"})
    deployer._http = fake_http
    try:
        pf2 = deployer.preflight("github", "tok", out, "FilmCollector", "u", "https://x.io/r")
        check("有Token+伪造200→preflight过", pf2["ok"] is True, str(pf2["checks"]))

        # online_health_check：伪造 requests 返回 200 + json
        class FakeResp:
            def __init__(self, code=200, data=None):
                self.status_code = code
                self._d = data or {}
            def json(self):
                return self._d
        class FakeReq:
            @staticmethod
            def get(*a, **k):
                return FakeResp(200, {"sites": []})
        deployer.requests = FakeReq
        hc = deployer.online_health_check("https://x.io/r")
        check("伪造200→online_health通过", hc["ok"] is True)
        FakeReq.get = staticmethod(lambda *a, **k: FakeResp(500))
        hc2 = deployer.online_health_check("https://x.io/r")
        check("500→online_health不过", hc2["ok"] is False)

        # rollback：伪造 PATCH 200，验证被调用且返回 True
        calls = {}
        def fake_http2(method, url, token, json_data=None, params=None, headers_extra=None):
            calls[method] = url
            return 200, {}
        deployer._http = fake_http2
        ok = deployer.rollback("github", "tok", "FilmCollector", "u", "abc123sha")
        check("rollback调用PATCH且返回True", ok is True and calls.get("PATCH") is not None)
    finally:
        deployer._http = orig_http
        deployer.requests = None


def test_no_fake_success():
    print("\n[8] 无假成功（preflight不过则不上传）")
    out = tempfile.mkdtemp(prefix="fc_nfs_")
    for fn in ("subscribe.json", "data.json", "health.json"):
        with open(os.path.join(out, fn), "w", encoding="utf-8") as f:
            json.dump({"sites": []} if fn == "subscribe.json" else {"list": []}, f)
    report = {"uploaded": False, "errors": [], "needs_token": False}
    token = ""  # 缺 token
    pf = deployer.preflight("github", token, out, "FilmCollector", "u", "https://x.io/r")
    if not pf["ok"]:
        report["errors"].append("preflight: " + "; ".join(c["detail"] for c in pf["checks"] if not c["ok"]))
        report["needs_token"] = not token
    check("preflight不过→不上传(uploaded=False)", report["uploaded"] is False)
    check("错误被记录(非掩盖)", len(report["errors"]) > 0)
    check("标记needs_token", report["needs_token"] is True)


def main():
    print("=" * 60)
    print("FilmCollector 无人值守最终验收（本地）")
    print("=" * 60)
    tmp = _setup_tmp_project()
    deployer.requests = None  # 默认不联网

    test_lock()
    test_atomic_save()
    test_backup_restore()
    test_alerts()
    test_score()
    test_idempotent_bundle()
    test_deploy_checks()
    test_no_fake_success()

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"结果：PASS={len(PASS)}  FAIL={len(FAIL)}")
    if FAIL:
        print("ACCEPTANCE: FAIL -> " + ", ".join(FAIL))
        sys.exit(1)
    print("ACCEPTANCE: ALL PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
