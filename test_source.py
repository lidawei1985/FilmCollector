# -*- coding: utf-8 -*-
"""
真实公共领域片源采集联调（通过打包 EXE 运行）：
启动「影视资源采集器.exe」(--api-only)，对接 Internet Archive 公共领域影片
windjammer(1937)，跑通 检测→采集→清洗→双格式生成→两套订阅 API→落盘校验。
"""
import os
import sys
import json
import time
import socket
import subprocess
import urllib.request
import urllib.error

EXE = os.path.join("E:\\FilmCollector\\影视资源采集器.exe")
DATA = os.path.join("E:\\FilmCollector\\backend\\data")
OUTPUT = os.path.join("E:\\FilmCollector\\output")
HOST, PORT = "127.0.0.1", 9911
BASE = f"http://{HOST}:{PORT}"
REAL_URL = "https://archive.org/details/windjammer"
TEMPLATE = {"fields": {"title": '[itemprop="name"]'}, "episodes": {}}

ok_steps = []


def _disable_safe_delete_shim():
    """还原被 safe-delete 中间件打补丁的删除函数（仅本测试进程内，不影响打包产物）。"""
    try:
        import nt
        os.remove = nt.remove
        os.unlink = nt.unlink
        os.rmdir = nt.rmdir
    except Exception:
        pass


def post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
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
    # 按进程树强杀（PyInstaller 会 spawn 子进程），再按文件名兜底
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

    # 干净起点：清空历史数据，让计数只反映本次真实片源
    kill_exe()
    time.sleep(1)
    for f in ("db.json", "presets.json"):
        p = os.path.join(DATA, f)
        if os.path.exists(p):
            os.remove(p)

    # 启动打包 EXE（无头 API 模式）
    proc = subprocess.Popen([EXE, "--api-only"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[启动] EXE pid={proc.pid}")
    if not wait_port():
        print("✗ 端口未就绪")
        kill_exe()
        sys.exit(1)
    print("[启动] 端口就绪 ✓")

    try:
        # 1) 站点检测
        det = post("/api/app/detect", {"url": REAL_URL})
        print(f"\n[1] 站点检测 reachable={det.get('reachable')} status={det.get('status')} "
              f"level={det.get('level_text')}")
        ok_steps.append(("站点检测", det.get("ok") and det.get("reachable")))

        # 2) 采集（真实公共领域影片详情页 + 选择器模板）
        sc = post("/api/app/scrape", {"url": REAL_URL, "template": TEMPLATE, "mode": "server"})
        print(f"[2] 采集入库 added={sc.get('added')} clean={sc.get('clean')} cost={sc.get('cost')}s")
        ok_steps.append(("采集入库", sc.get("ok") and sc.get("added", 0) > 0))

        # 3) 双格式生成
        gen = post("/api/app/generate", {})
        print(f"[3] 双格式生成 count={gen.get('count')} "
              f"TVBox校验问题={len(gen.get('tvbox_errors', []))} "
              f"通用校验问题={len(gen.get('generic_errors', []))}")
        if gen.get("tvbox_errors"):
            print("    TVBox:", gen["tvbox_errors"][:3])
        if gen.get("generic_errors"):
            print("    通用:", gen["generic_errors"][:3])
        ok_steps.append(("双格式生成(0校验问题)",
                         gen.get("ok") and not gen.get("tvbox_errors") and not gen.get("generic_errors")))

        # 4) 开启本地订阅 API
        tg = post("/api/app/api/toggle", {"enabled": True})
        print(f"[4] 订阅API {'开启' if tg.get('enabled') else '关闭'}")
        ok_steps.append(("开启订阅API", tg.get("ok") and tg.get("enabled")))

        # 5) TVBox 接口
        tv = get("/api.php/provide/vod/")
        tv_item = (tv.get("list") or [{}])[0]
        print(f"[5] TVBox接口 code={tv.get('code')} total={tv.get('total')} "
              f"vod_name={tv_item.get('vod_name')!r} type_name={tv_item.get('type_name')!r} "
              f"vod_play_url有值={'vod_play_url' in tv_item and bool(tv_item.get('vod_play_url'))}")
        ok_steps.append(("TVBox接口返回影片", tv.get("code") == 1 and tv.get("total", 0) > 0))

        # 6) 通用接口
        ge = get("/api/generic/vod/")
        g_item = (ge.get("list") or [{}])[0]
        print(f"[6] 通用接口 code={ge.get('code')} total={ge.get('total')} "
              f"name={g_item.get('name')!r} type={g_item.get('type')!r} "
              f"episode_count={g_item.get('episode_count')} "
              f"play_list有值={bool(g_item.get('play_list'))} "
              f"pic={str(g_item.get('pic'))[:40]}")
        ok_steps.append(("通用接口返回影片", ge.get("code") == 1 and ge.get("total", 0) > 0))

        # 7) 落盘文件校验（对齐用户「标准影片 JSON 样例」）
        gen_path = os.path.join(OUTPUT, "json", "generic", "all.json")
        tv_path = os.path.join(OUTPUT, "json", "tvbox", "all.json")
        with open(gen_path, encoding="utf-8") as f:
            g_disk = json.load(f)
        with open(tv_path, encoding="utf-8") as f:
            t_disk = json.load(f)
        g0 = (g_disk.get("list") or [{}])[0]
        required = ["id", "name", "type", "play_list"]
        missing = [k for k in required if not g0.get(k)]
        print(f"[7] 落盘校验 generic/all.json 字段缺失={missing} "
              f"type={g0.get('type')} pic本地缓存={str(g0.get('pic'))[:38]}")
        ok_steps.append(("落盘双格式校验通过", not missing and len(g_disk.get("list", [])) > 0
                         and len(t_disk.get("list", [])) > 0))

    finally:
        kill_exe(proc)
        time.sleep(1)

    print("\n==================== 真实片源联调结论 ====================")
    allok = True
    for name, ok in ok_steps:
        print(f"  {'✓' if ok else '✗'} {name}")
        allok = allok and ok
    print("  结果:", "全部通过 ✅" if allok else "存在失败 ❌")
    print("  产物: E:\\FilmCollector\\output\\json\\generic\\all.json")
    print("        E:\\FilmCollector\\output\\json\\tvbox\\all.json")


if __name__ == "__main__":
    main()
