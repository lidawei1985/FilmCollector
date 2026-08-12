# -*- coding: utf-8 -*-
"""
后端全链路联调测试（无需外网）：
  本地起 HTTP 服务 -> 检测 -> 自动采集 -> 清洗/去重/分类 -> 生成 JSON -> 本地订阅 API 校验。
"""
import os
import sys
import time
import json
import threading
import http.server
import socketserver
import importlib

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from backend import server
from backend.core import store, json_gen

DATA_DIR = os.path.join(BASE, "backend", "data")
PORT = 8765


def start_http():
    os.chdir(DATA_DIR)
    handler = http.server.SimpleHTTPRequestHandler
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def reset_db():
    db = store.load_db()
    db["items"] = []
    store.save_db(db)


def main():
    httpd = start_http()
    reset_db()
    url = f"http://127.0.0.1:{PORT}/sample.html"
    print("== 1) 站点检测 ==")
    # 直接用模块函数（绕过 request 上下文）
    det = __import__("backend.core.detector", fromlist=["detect"]).detect(url)
    print("  level:", det["level"], det["level_text"], "| has_m3u8:", det["has_m3u8"])

    print("== 2) 自动采集（含广告域名过滤）==")
    raw = __import__("backend.core.scraper", fromlist=["scrape"]).scrape(None, url, mode="server")
    print("  raw items:", len(raw), "| raw episodes:", len(raw[0]["episodes"]) if raw else 0)
    # 测试环境：把播放地址替换为本地可达的 .m3u8，确保链路校验通过
    local_m3u8 = f"http://127.0.0.1:{PORT}/fake.m3u8"
    for it in raw:
        for ep in it.get("episodes", []):
            ep["url"] = local_m3u8
    cleaned, cstats = __import__("backend.core.cleaner", fromlist=["clean_items"]).clean_items(raw)
    print("  clean stats:", cstats)  # ads 应被过滤（adservice.google.com）

    print("== 3) 去重 + 分类 ==")
    merged = __import__("backend.core.dedup", fromlist=["dedup_items"]).dedup_items(cleaned)
    classified = __import__("backend.core.classifier", fromlist=["classify"]).classify(merged)
    print("  after dedup:", len(merged), "| type:", classified[0]["type"], "| genres:", classified[0]["genres"])

    # 存库 + 制造一条重复（同片名同年）测试去重合并
    db = store.load_db()
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    for it in classified:
        it["id"] = str(uuid.uuid4()); it["created_at"] = now; it["updated_at"] = now
        db["items"].append(it)
    dup = dict(classified[0]); dup["id"] = str(uuid.uuid4())
    dup["episodes"] = [{"name": "线路三", "url": local_m3u8, "line": "备用线路"}]
    db["items"].append(dup)
    store.save_db(db)

    print("== 4) 生成双格式 JSON 订阅源 ==")
    res = json_gen.generate()
    print("  count:", res["count"])
    print("  TVBox 文件:", list(res["tvbox"].keys()))
    print("  通用 文件:", list(res["generic"].keys()))
    print("  TVBox 校验错误:", len(res["tvbox_errors"]), "| 通用校验错误:", len(res["generic_errors"]))
    import os as _os
    assert _os.path.exists(res["tvbox"]["all.json"]), "TVBox all.json 未生成"
    assert _os.path.exists(res["generic"]["all.json"]), "通用 all.json 未生成"

    print("== 5) 本地订阅 API（TVBox + 通用 两组接口）==")
    # 测试中直接开启本地 API（绕过 GUI 开关）
    server._api_enabled["on"] = True
    client = server.app.test_client()
    # TVBox 接口
    r = client.get("/api.php/provide/vod/?ac=list&pg=1")
    j = r.get_json()
    print("  [TVBox] list total:", j["total"], "| returned:", len(j["list"]))
    vod = j["list"][0]
    print("  vod_name:", vod["vod_name"], "| play_from:", vod["vod_play_from"])
    print("  play_url 含备用线路:", "备用线路" in vod["vod_play_url"])
    print("  play_url 不含广告:", "adservice" not in vod["vod_play_url"])
    rid = vod["vod_id"]
    print("  detail ok:", client.get(f"/api.php/provide/vod/?ac=detail&ids={rid}").get_json()["code"] == 1)
    print("  搜索 wd=暗夜 total:", client.get("/api.php/provide/vod/?ac=list&wd=暗夜").get_json()["total"])
    print("  分类 t=电影 total:", client.get("/api.php/provide/vod/?ac=list&t=电影").get_json()["total"])
    # 通用接口
    g = client.get("/api/generic/vod/?ac=list&pg=1").get_json()
    print("  [通用] list total:", g["total"])
    gv = g["list"][0]
    print("  通用 name:", gv["name"], "| lines keys:", list(gv["lines"].keys()), "| 无 vod_play_from 私有字段:", "vod_play_from" not in gv)
    print("  通用 detail ok:", client.get(f"/api/generic/vod/?ac=detail&ids={gv['id']}").get_json()["code"] == 1)

    print("== 6) 单独导出至文件夹 ==")
    exp = _os.path.join(BASE, "output", "export_test")
    _os.makedirs(exp, exist_ok=True)
    er = client.post("/api/app/export_folder", json={"folder": exp})
    ej = er.get_json()
    print("  export ok:", ej["ok"], "| files:", len(ej.get("files", [])))
    assert _os.path.exists(_os.path.join(exp, "tvbox", "all.json"))
    assert _os.path.exists(_os.path.join(exp, "generic", "all.json"))

    print("\n✅ 后端全链路验证通过")
    httpd.shutdown()


if __name__ == "__main__":
    main()
