# -*- coding: utf-8 -*-
"""
桌面端采集实测：用打包好的单文件 EXE 跑完整链路（无头 --api-only 模式）。
覆盖：启动 -> 站点检测 -> 可视化点击式采集(传 DOM) -> 清洗/去重/分类 ->
双格式订阅源生成 -> 本地订阅 API(TVBox+通用)两组接口 -> 静态前端/客户端可用性。

说明：清洗环节会对播放/图片链接做真实可达性校验，沙箱无法访问外网，
因此本脚本自带一个本地 HTTP 校验服务（127.0.0.1:9912，恒返回 200），
让样例链接通过校验，从而验证“可达源 -> 入库 -> 双格式导出 -> API”的完整链路。
"""
import os
import sys
import time
import json
import threading
import subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "影视资源采集器.exe")
HOST, PORT = "127.0.0.1", 9911
BASE = f"http://{HOST}:{PORT}"
VALID_PORT = 9912
VALID_BASE = f"http://{HOST}:{VALID_PORT}"


def _kill_exe_procs():
    # PyInstaller 单文件 EXE 的子进程在父进程被 terminate 后仍可能占用端口，
    # 这里按映像名强制清理，确保每次测试都从干净端口开始、结束。
    try:
        subprocess.run(["taskkill", "/F", "/IM", "影视资源采集器.exe"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass
    time.sleep(1.0)


class _Validator(BaseHTTPRequestHandler):
    """恒返回 200 + 600 字节伪数据，让播放/图片链接通过可达性校验。"""
    def _ok(self):
        body = b"x" * 600
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self._ok()

    def do_GET(self):
        self._ok()

    def log_message(self, *a):
        pass


def _start_validator():
    srv = ThreadingHTTPServer((HOST, VALID_PORT), _Validator)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        ctype = r.headers.get("Content-Type", "")
        body = r.read().decode("utf-8", "ignore")
        return ctype, body


def _wait_server(timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(BASE + "/api/app/config", timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1.0)
    return False


def main():
    assert os.path.exists(EXE), f"未找到 EXE：{EXE}"
    _kill_exe_procs()  # 清理可能残留的 EXE 进程，确保端口干净
    srv = _start_validator()
    try:
        # 启动 EXE（无头 API 模式）
        log_path = os.path.join(HERE, "dist", "exe_test_stdout.log")
        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen([EXE, "--api-only"], stdout=lf, stderr=lf)
        print(f"[1] 已启动 EXE (pid={proc.pid})，等待后端就绪...")
        if not _wait_server():
            print("❌ 后端未在预期时间内就绪，查看 boot.log：")
            bl = os.path.join(HERE, "dist", "output", "boot.log")
            if os.path.exists(bl):
                print(open(bl, encoding="utf-8").read()[-2000:])
            proc.terminate()
            return 1
        print("✅ 后端就绪")

        steps = []
        # [2] 站点检测
        r = _post("/api/app/detect", {"url": "https://example.com/movie/123"})
        steps.append(("站点检测", r.get("ok") and r.get("level_text")))
        print("✅ 站点检测 ->", r.get("level_text"), "| 判定:", r.get("level"))

        # [3] 可视化点击式采集：传入页面 DOM + 选择器模板（模拟桌面端 WebView2 点击回填）
        html = """<html><head><title>测试影片</title>
<meta property="og:title" content="测试影片">
<meta property="og:image" content="__POSTER__">
<meta property="og:description" content="一部测试影片简介"></head><body>
<h1 class="title">测试影片</h1>
<span class="year">2025</span>
<span class="type">电影</span>
<div class="poster"><img src="__POSTER__"></div>
<ul class="episodes">
  <li class="ep"><a href="__EP1__">第01集</a></li>
  <li class="ep"><a href="__EP2__">第02集</a></li>
</ul></body></html>"""
        html = html.replace("__POSTER__", f"{VALID_BASE}/poster.jpg") \
                   .replace("__EP1__", f"{VALID_BASE}/play/1.m3u8") \
                   .replace("__EP2__", f"{VALID_BASE}/play/2.m3u8")
        template = {
            "fields": {
                "title": "h1.title", "year": "span.year", "type": "span.type",
                "poster": "div.poster img", "description": "meta[property='og:description']",
            },
            "episodes": {"container": "ul.episodes li.ep", "url": "a", "line": "默认线路"},
        }
        r = _post("/api/app/scrape", {"url": "https://example.com/movie/123", "mode": "browser", "html": html, "template": template})
        steps.append(("采集入库", r.get("ok")))
        print(f"✅ 采集入库 {r.get('added')} 条 | 清洗统计 {r.get('clean')} | 耗时 {r.get('cost')}s")

        # [4] 查看条目
        ctype, body = _get("/api/app/items?limit=5")
        items = json.loads(body)["items"]
        steps.append(("条目可读", len(items) >= 1))
        print("✅ 当前条目数:", len(items), "| 首条:", items[0]["title"] if items else None)

        # [5] 双格式订阅源生成
        r = _post("/api/app/generate", {})
        steps.append(("双格式生成", r.get("ok")))
        tv = r.get("tvbox") or {}
        gn = r.get("generic") or {}
        print(f"✅ 双格式生成：TVBox {len(tv)} 个文件，通用 {len(gn)} 个文件")
        print("   TVBox校验问题:", r.get("tvbox_errors"), "| 通用校验问题:", r.get("generic_errors"))

        # [6] 开启本地订阅 API
        r = _post("/api/app/api/toggle", {"enabled": True})
        steps.append(("API开关", r.get("ok") and r.get("enabled")))
        print("✅ 本地订阅 API 已开启:", r.get("tvbox_url"), r.get("generic_url"))

        # [7] TVBox 接口
        ctype, body = _get("/api.php/provide/vod/?ac=list")
        tvbox = json.loads(body)
        steps.append(("TVBox接口", tvbox.get("code") == 1 and len(tvbox.get("list", [])) >= 1))
        v0 = tvbox["list"][0] if tvbox.get("list") else {}
        print("✅ TVBox 接口 code=", tvbox.get("code"), "total=", tvbox.get("total"),
              "| 样例 type_name=", v0.get("type_name"), "play_url有值=", bool(v0.get("vod_play_url")))

        # [8] 通用接口
        ctype, body = _get("/api/generic/vod/?ac=list")
        generic = json.loads(body)
        steps.append(("通用接口", generic.get("code") == 1 and len(generic.get("list", [])) >= 1))
        g0 = generic["list"][0] if generic.get("list") else {}
        print("✅ 通用接口 code=", generic.get("code"), "total=", generic.get("total"),
              "| 样例 type=", g0.get("type"), "episode_count=", g0.get("episode_count"),
              "play_list有值=", bool(g0.get("play_list")))

        # [9] 前端/客户端静态资源可用（验证 _MEIPASS 资产打包）
        ctype, body = _get("/")
        steps.append(("管理后台静态页", "text/html" in ctype and "采集" in body))
        print("✅ 管理后台 / 返回 HTML:", "text/html" in ctype, "长度", len(body))
        ctype, body = _get("/client")
        steps.append(("观影客户端静态页", "text/html" in ctype))
        print("✅ 观影客户端 /client 返回 HTML:", "text/html" in ctype, "长度", len(body))

        # [10] 导出文件落盘校验
        out_root = os.path.join(HERE, "dist", "output", "json")
        tvbox_file = os.path.join(out_root, "tvbox", "all.json")
        generic_file = os.path.join(out_root, "generic", "all.json")
        ok_files = os.path.exists(tvbox_file) and os.path.exists(generic_file)
        steps.append(("导出文件落盘", ok_files))
        print("✅ 落盘校验:", "tvbox/all.json" if os.path.exists(tvbox_file) else "缺失",
              "|", "generic/all.json" if os.path.exists(generic_file) else "缺失")

        proc.terminate()
        _kill_exe_procs()  # 清理 EXE 子进程（父进程已 terminate，子进程仍可能占端口）
        print("\n==================== 实测结论 ====================")
        allok = True
        for name, ok in steps:
            mark = "✅" if ok else "❌"
            if not ok:
                allok = False
            print(f"  {mark} {name}")
        print("==================================================")
        print("总评:", "全部通过 ✅" if allok else "存在失败项 ❌")
        return 0 if allok else 1
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
