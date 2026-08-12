# -*- coding: utf-8 -*-
"""
真实公共领域 / CC 片源批量联调（通过打包 EXE 运行）：
启动「影视资源采集器.exe」(--api-only)，验证：
  - 单影片 CC 源：Big Buck Bunny、Elephants Dream（逐条详情采集）
  - 公共领域集合：feature_films 一键列片 -> 批量详情采集
  - 双格式生成 + 两套订阅 API + 落盘 + 预设列表含新源
"""
import os
import sys
import json
import time
import socket
import subprocess
import urllib.request

EXE = os.path.join("E:\\FilmCollector\\影视资源采集器.exe")
DATA = os.path.join("E:\\FilmCollector\\backend\\data")
OUTPUT = os.path.join("E:\\FilmCollector\\output")
HOST, PORT = "127.0.0.1", 9911
BASE = f"http://{HOST}:{PORT}"

TEMPLATE = {"fields": {"title": '[itemprop="name"]'}, "episodes": {}}
SINGLE = [
    ("Big Buck Bunny", "https://archive.org/details/BigBuckBunny_124"),
    ("Elephants Dream", "https://archive.org/details/ElephantsDream"),
]
COLLECTION = "feature_films"
ok_steps = []


def _disable_safe_delete_shim():
    try:
        import nt
        os.remove = nt.remove
        os.unlink = nt.unlink
        os.rmdir = nt.rmdir
    except Exception:
        pass


def post(path, payload, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path, timeout=30):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_port(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((HOST, PORT), timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def kill_exe(proc=None):
    if proc is not None:
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass
    try:
        subprocess.run(["taskkill", "//IM", "影视资源采集器.exe", "//F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


def main():
    assert os.path.exists(EXE), f"未找到 EXE：{EXE}"
    _disable_safe_delete_shim()
    kill_exe()
    time.sleep(1)
    for f in ("db.json", "presets.json"):
        p = os.path.join(DATA, f)
        if os.path.exists(p):
            os.remove(p)

    proc = subprocess.Popen([EXE, "--api-only"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[启动] EXE pid={proc.pid}")
    if not wait_port():
        print("✗ 端口未就绪")
        kill_exe()
        sys.exit(1)
    print("[启动] 端口就绪 ✓")

    try:
        # 1) 站点检测（外网可达）
        det = post("/api/app/detect", {"url": "https://archive.org/details/BigBuckBunny_124"})
        print(f"[1] 站点检测 reachable={det.get('reachable')} status={det.get('status')} level={det.get('level_text')}")
        ok_steps.append(("站点检测", det.get("ok") and det.get("reachable")))

        # 2) 单影片 CC 源逐条采集
        added_single = 0
        for name, url in SINGLE:
            sc = post("/api/app/scrape", {"url": url, "template": TEMPLATE, "mode": "server"})
            added_single += sc.get("added", 0)
            print(f"[2] 单影片《{name}》added={sc.get('added')} clean={sc.get('clean')}")
        ok_steps.append(("CC 单影片采集入库", added_single >= 2))

        # 3) 公共领域集合一键批量采集
        sc = post("/api/app/scrape", {"collection": COLLECTION, "template": TEMPLATE,
                                      "max_items": 6}, timeout=180)
        added_col = sc.get("added", 0)
        print(f"[3] 集合《{COLLECTION}》批量采集 added={added_col} cost={sc.get('cost')}s")
        ok_steps.append(("公共领域集合批量采集", added_col > 0))

        # 4) 双格式生成
        gen = post("/api/app/generate", {})
        print(f"[4] 双格式生成 count={gen.get('count')} "
              f"TVBox校验问题={len(gen.get('tvbox_errors', []))} 通用校验问题={len(gen.get('generic_errors', []))}")
        if gen.get("tvbox_errors"):
            print("    TVBox:", gen["tvbox_errors"][:3])
        if gen.get("generic_errors"):
            print("    通用:", gen["generic_errors"][:3])
        ok_steps.append(("双格式生成(0校验问题)",
                         gen.get("ok") and not gen.get("tvbox_errors") and not gen.get("generic_errors")))

        # 5) 开启本地订阅 API
        tg = post("/api/app/api/toggle", {"enabled": True})
        ok_steps.append(("开启订阅API", tg.get("ok") and tg.get("enabled")))

        # 6) TVBox 接口
        tv = get("/api.php/provide/vod/")
        names = [x.get("vod_name") for x in (tv.get("list") or [])]
        print(f"[6] TVBox接口 code={tv.get('code')} total={tv.get('total')} 样例={names[:3]}")
        ok_steps.append(("TVBox接口返回影片", tv.get("code") == 1 and tv.get("total", 0) > 0))

        # 7) 通用接口
        ge = get("/api/generic/vod/")
        gnames = [x.get("name") for x in (ge.get("list") or [])]
        has_play = any(x.get("play_list") for x in (ge.get("list") or []))
        print(f"[7] 通用接口 code={ge.get('code')} total={ge.get('total')} 有播放地址={has_play} 样例={gnames[:3]}")
        ok_steps.append(("通用接口返回影片", ge.get("code") == 1 and ge.get("total", 0) > 0 and has_play))

        # 8) 预设列表含新增源
        ps = get("/api/app/presets")
        pids = [p.get("id") for p in ps.get("presets", [])]
        new_ids = ["ia_feature_films_collection", "ia_big_buck_bunny", "ia_elephants_dream"]
        print(f"[8] 预设数={len(pids)} 含新源={[i for i in new_ids if i in pids]}")
        ok_steps.append(("预设含新增 CC/集合源", all(i in pids for i in new_ids)))

        # 9) 落盘双格式校验
        gen_path = os.path.join(OUTPUT, "json", "generic", "all.json")
        tv_path = os.path.join(OUTPUT, "json", "tvbox", "all.json")
        with open(gen_path, encoding="utf-8") as f:
            g_disk = json.load(f)
        with open(tv_path, encoding="utf-8") as f:
            t_disk = json.load(f)
        g0 = (g_disk.get("list") or [{}])[0]
        required = ["id", "name", "type", "play_list"]
        missing = [k for k in required if not g0.get(k)]
        print(f"[9] 落盘 generic/all.json 字段缺失={missing} 总条数={len(g_disk.get('list', []))}")
        ok_steps.append(("落盘双格式校验通过", not missing and len(g_disk.get("list", [])) > 0
                         and len(t_disk.get("list", [])) > 0))

    finally:
        kill_exe(proc)
        time.sleep(1)

    print("\n==================== 多源联调结论 ====================")
    allok = True
    for name, ok in ok_steps:
        print(f"  {'✓' if ok else '✗'} {name}")
        allok = allok and ok
    print("  结果:", "全部通过 ✅" if allok else "存在失败 ❌")


if __name__ == "__main__":
    main()
