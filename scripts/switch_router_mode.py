#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
switch_router_mode.py —— FilmCollector 生产灰度切换 / 回滚脚本（P2-0）
=====================================================================
用途：
  在本机完成「灰度切换条件准备」：自动备份 → 按模式重建 APK 契约 feed → 自动 diff →
  支持文件级回滚。**只操作本地 dist/，永不发布。**

用法（项目根目录执行，或任意目录均可，脚本自动定位项目根）：
  python scripts/switch_router_mode.py status            # 只读：当前模式 + dist feed 计数与 schema
  python scripts/switch_router_mode.py enable            # 开新链路：备份→env=1 重建 feed→diff 报告
  python scripts/switch_router_mode.py rollback          # 回旧链路：从最近 pre_switch 备份恢复 dist
  python scripts/switch_router_mode.py diff DIR_A DIR_B  # 只读：对比两个 feed 目录（如 staging/old staging/new）

铁律（与 P2_GRAY_READY_REPORT.md 一致）：
  ✗ 永不推送 Pages / 永不发布 feed
  ✗ 永不修改 APK / UI / 分类规则 / sources.d
  ✗ 永不删除历史数据 / 永不清理 backup
  ✓ enable 前强制自动备份到 backup/pre_switch_<ts>/
  ✓ FILMCOLLECTOR_USE_CHANNEL_ROUTER 仅通过子进程环境传递，不写入系统/用户环境变量
  ✓ 发布永远由人工执行（Pages 仓库推送 / GitHub Actions）
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

# ---- 项目根定位（可迁移：不绑定盘符/用户名） ----
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
BACKUP_ROOT = os.path.join(ROOT, "backup")
ENV_KEY = "FILMCOLLECTOR_USE_CHANNEL_ROUTER"
FEED_FILES = ("feed.normal.json", "feed.child.json", "feed.adult.json", "feed.all.json", "version.json")

ADULT_SOURCES = {"msnii", "xrbsp", "gdlsp", "kxgav", "pgxdy"}
CHILD_SOURCES = {"dytt", "guangsu", "ffzy"}


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load_feed(directory, name):
    path = os.path.join(directory, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _schema_of(items):
    if not items:
        return "EMPTY"
    keys = set(items[0].keys())
    if "dedup_id" in keys and "origins" in keys:
        return "ROUTER"
    if "name" in keys and "vodId" in keys:
        return "APK_CONTRACT"
    return "UNKNOWN"


def _ids(items):
    return {str(i.get("vodId")) for i in items}


def cmd_status():
    env_val = os.environ.get(ENV_KEY, "<unset>（代码默认 0=旧链路）")
    print("== 状态 ==")
    print("进程环境 %s = %s" % (ENV_KEY, env_val))
    print("（注意：本脚本 enable/rollback 通过子进程环境传值，不改本变量）")
    print()
    print("== dist/ feed 现状 ==")
    for name in FEED_FILES:
        d = _load_feed(DIST, name)
        if d is None:
            print("%-18s 不存在" % name)
            continue
        if name == "version.json":
            counts = {m: v.get("count") for m, v in d.items()}
            print("%-18s version=%s counts=%s" % (name, d.get("normal", {}).get("version", "?"), counts))
        else:
            items = d.get("items", [])
            print("%-18s items=%-5d schema=%s mode=%s" % (name, len(items), _schema_of(items), d.get("mode", "?")))
    print()
    print("提示：enable 后 feed.{normal,child,adult}.json 必须是 APK_CONTRACT schema。")
    return 0


def _auto_backup(tag):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_ROOT, "pre_switch_%s" % ts)
    os.makedirs(dest, exist_ok=True)
    copied = []
    for name in FEED_FILES:
        src = os.path.join(DIST, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, name))
            copied.append(name)
    with open(os.path.join(dest, "MANIFEST.txt"), "w", encoding="utf-8") as f:
        f.write("自动备份（%s，%s）\n切换前 dist 快照：%s\n" % (tag, _now(), ", ".join(copied)))
        f.write("回滚命令：python scripts/switch_router_mode.py rollback\n")
    print("[backup] 已写入 %s（%d 个文件）" % (os.path.relpath(dest, ROOT), len(copied)))
    return dest


def _build(env_value):
    """子进程内以指定 env 值重建 APK 契约 feed（apk_feed 契约层，env 在子进程生效）。"""
    code = "from backend.core import apk_feed; apk_feed.write_apk_feeds()"
    env = dict(os.environ)
    env[ENV_KEY] = env_value
    print("[build] FILMCOLLECTOR_USE_CHANNEL_ROUTER=%s 重建契约 feed ..." % env_value)
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit("[build] 失败，退出码 %d（dist 未变更成功，请检查备份）" % r.returncode)
    print("[build] 完成")


def diff_dirs(old_dir, new_dir, out_path=None):
    """对比两个 feed 目录，返回结果 dict（同时打印摘要）。"""
    result = {"generated_at": _now(), "old_dir": old_dir, "new_dir": new_dir, "modes": {}, "red_lines": {}}
    old_dist, new_dist = {}, {}
    for mode in ("normal", "child", "adult"):
        old_dist[mode] = _load_feed(old_dir, "feed.%s.json" % mode) or {"items": []}
        new_dist[mode] = _load_feed(new_dir, "feed.%s.json" % mode) or {"items": []}
    for mode in ("normal", "child", "adult"):
        old_items, new_items = old_dist[mode]["items"], new_dist[mode]["items"]
        old_ids, new_ids = _ids(old_items), _ids(new_items)
        entry = {
            "old_count": len(old_items),
            "new_count": len(new_items),
            "new_schema": _schema_of(new_items),
            "added": len(new_ids - old_ids),
            "removed": len(old_ids - new_ids),
        }
        result["modes"][mode] = entry
        print("  %-7s old=%-5d new=%-5d (+%-4d -%-4d) schema=%s"
              % (mode, entry["old_count"], entry["new_count"], entry["added"], entry["removed"], entry["new_schema"]))
    # 红线（对新目录）
    rl = result["red_lines"]
    for mode in ("normal", "child"):
        bad = [i.get("name") for i in new_dist[mode]["items"] if i.get("sourceId") in ADULT_SOURCES]
        rl["%s_成人源条目" % mode] = len(bad)
    rl["child_非儿童白名单源"] = len([i for i in new_dist["child"]["items"] if i.get("sourceId") not in CHILD_SOURCES])
    n, c, a = (_ids(new_dist[m]["items"]) for m in ("normal", "child", "adult"))
    rl["频道互斥重叠"] = len(n & c) + len(n & a) + len(c & a)
    print("  红线：%s" % json.dumps(rl, ensure_ascii=False))
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("  [written] %s" % os.path.relpath(out_path, ROOT))
    return result


def cmd_enable():
    print("== enable：切换到新链路（ContentClassifier 分仓）==")
    print("边界：只重建本地 dist/ 契约 feed，【不发布】。发布由人工按 P2_GRAY_CHECKLIST.md 执行。")
    print()
    backup_dir = _auto_backup("enable 新链路前")
    _build("1")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(DIST, "switch_diff_%s.json" % ts)
    print()
    print("== diff（备份前 vs 切换后）==")
    diff_dirs(backup_dir, DIST, out_path=out)
    print()
    print("[done] 新链路契约 feed 已在本地 dist/。")
    print("       未发布！灰度推送请按 P2_GRAY_CHECKLIST.md 逐 APK 执行（星幕→心屋→夜航）。")
    print("       回滚：python scripts/switch_router_mode.py rollback")
    return 0


def cmd_rollback():
    print("== rollback：恢复旧链路 ==")
    candidates = sorted(
        (d for d in os.listdir(BACKUP_ROOT) if d.startswith("pre_switch_") or d.startswith("pre_gray_")),
        reverse=True,
    )
    if not candidates:
        raise SystemExit("[rollback] backup/ 下无 pre_switch_* / pre_gray_* 备份，无法回滚。")
    src = os.path.join(BACKUP_ROOT, candidates[0])
    print("[rollback] 使用最近备份：%s" % os.path.relpath(src, ROOT))
    restored = 0
    for name in FEED_FILES:
        p = os.path.join(src, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(DIST, name))
            restored += 1
    print("[rollback] 已恢复 %d 个文件到 dist/。" % restored)
    _build("0")  # 用旧逻辑按当前聚合库重建，确保 version.json 与 feed 同源一致
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(DIST, "switch_diff_rollback_%s.json" % ts)
    print()
    print("== diff（备份 vs 回滚后）==")
    diff_dirs(src, DIST, out_path=out)
    print()
    print("[done] 已回旧链路（本地 dist/）。未发布！如需 Pages 同步回滚请人工推送备份文件。")
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "status":
        return cmd_status()
    if cmd == "enable":
        return cmd_enable()
    if cmd == "rollback":
        return cmd_rollback()
    if cmd == "diff" and len(argv) == 4:
        print("== diff %s vs %s ==" % (os.path.relpath(argv[2], ROOT), os.path.relpath(argv[3], ROOT)))
        diff_dirs(argv[2], argv[3])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台中文
    except Exception:
        pass
    sys.exit(main(sys.argv))
