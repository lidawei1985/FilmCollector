# -*- coding: utf-8 -*-
"""
new_source_auto_routing_test.py —— P1 任务4：未来新增源模式验证（10 源模拟）
============================================================================
证明目标（用户指令）：
  未来新增源「只增加 sources.d/*.json」即可自动分仓，
  不允许修改 APK / 分类代码 / 路由代码；新增源自动：
  普通影视→normal、儿童→child、成人→adult、体育→sports。

方法（离线、零网络、零生产污染）：
  1. 在 _flow_test/new_sources_sim/sources.d/ 写 10 个模拟源 JSON（真实 sources.d 不动）；
  2. 用 FILMCOLLECTOR_SOURCES_DIR 环境变量把源发现指向模拟目录（该环境变量为项目既有能力）；
  3. 每个源构造典型「已标准化」条目（模拟 normalizer 输出），
     走**真实生产代码路径** content_classifier（规则加载 + sources.d 标志扫描 + 分类）；
  4. 逐条断言 channel，输出结果 JSON。

运行：cd E:\\FilmCollector && python _flow_test/new_source_auto_routing_test.py
"""
import json
import os
import sys
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM_DIR = os.path.join(BASE, "_flow_test", "new_sources_sim")
SIM_SOURCES_D = os.path.join(SIM_DIR, "sources.d")

# ---- 1. 写 10 个模拟源 JSON（仅 sources.d 形式；不触生产 sources.d） ----
def _src(sid, name, is_adult=False, is_child=False):
    return {
        "id": sid,
        "name": name,
        "protocol": "json",
        "is_adult": is_adult,
        "is_child_known_source": is_child,
        "channel_hint": "child" if is_child else ("adult" if is_adult else "mixed"),
        "fetch": {"type": "http_json", "timeout": 5,
                  "urls": ["http://127.0.0.1:9/%s/mock.json" % sid]},
        "source_capability": "full_play",
        "note": "P1任务4模拟源（离线测试，永不抓取）",
    }

SIM_SOURCES = {
    "sim_movies":  _src("sim_movies", "模拟电影源"),
    "sim_tv":      _src("sim_tv", "模拟剧集源"),
    "sim_kids":    _src("sim_kids", "模拟儿童源", is_child=True),
    "sim_adult":   _src("sim_adult", "模拟成人源", is_adult=True),
    "sim_sports":  _src("sim_sports", "模拟体育源"),
    "sim_variety": _src("sim_variety", "模拟综艺源"),
    "sim_doc":     _src("sim_doc", "模拟纪录片源"),
    "sim_short":   _src("sim_short", "模拟短剧源"),
    "sim_weird":   _src("sim_weird", "模拟未知分类源"),
    "sim_mixed":   _src("sim_mixed", "模拟混合源"),
}

os.makedirs(SIM_SOURCES_D, exist_ok=True)
for sid, cfg in SIM_SOURCES.items():
    with open(os.path.join(SIM_SOURCES_D, "%s.json" % sid), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ---- 2. 把源发现指向模拟目录（必须在 import backend 之前设置） ----
os.environ["FILMCOLLECTOR_SOURCES_DIR"] = SIM_SOURCES_D
sys.path.insert(0, BASE)

from backend.core import source_registry, content_classifier  # noqa: E402

discovered = source_registry.discover()
print("[discover] 发现模拟源 %d 个: %s" % (len(discovered), sorted(discovered.keys())))
assert set(discovered.keys()) >= set(SIM_SOURCES.keys()), "模拟源发现失败"

# ---- 3. 每源典型条目 + 期望 channel ----
# (源, 标题, 分类, 期望channel, 验证点)
CASES = [
    # 1 普通电影源
    ("sim_movies", "模拟动作大片", "动作片", "normal", "普通影视→normal"),
    ("sim_movies", "模拟喜剧", "喜剧片", "normal", ""),
    ("sim_movies", "模拟战争", "战争片", "normal", ""),
    # 2 剧集源
    ("sim_tv", "模拟国产剧", "国产剧", "normal", "剧集→normal"),
    ("sim_tv", "模拟韩剧", "韩剧", "normal", ""),
    ("sim_tv", "模拟美剧", "美剧", "normal", ""),
    # 3 儿童源（is_child_known_source=true，仅源 JSON 标志）
    ("sim_kids", "模拟儿童动画", "儿童动画", "child", "儿童强词→child"),
    ("sim_kids", "模拟动画片", "动画片", "child", "白名单源动画片→child"),
    ("sim_kids", "模拟儿歌", "儿歌", "child", ""),
    ("sim_kids", "儿童源里的动作片", "动作片", "normal", "红线：儿童源动作片不进 child"),
    ("sim_kids", "儿童源里的伦理片", "伦理片", "adult", "红线：儿童源伦理片进 adult"),
    # 4 成人源（is_adult=true，仅源 JSON 标志）
    ("sim_adult", "成人源编号内容A", "c20", "adult", "成人源编号→adult（源白名单）"),
    ("sim_adult", "成人源编号内容B", "c31", "adult", ""),
    ("sim_adult", "成人源伦理片", "伦理片", "adult", "词级+源级双保险"),
    ("sim_adult", "成人源儿童动画", "儿童动画", "adult", "红线：成人源儿童词绝不进 child"),
    # 5 体育源
    ("sim_sports", "模拟足球赛", "足球", "sports", "体育→sports"),
    ("sim_sports", "模拟篮球赛", "篮球", "sports", ""),
    ("sim_sports", "NBA经典赛事全集", "NBA", "normal", "标题豁免：含'全集'的体育词→normal"),
    # 6 综艺源
    ("sim_variety", "模拟综艺", "大陆综艺", "normal", "综艺→normal"),
    ("sim_variety", "模拟真人秀", "真人秀", "normal", ""),
    # 7 纪录片源
    ("sim_doc", "模拟纪录片", "纪录片", "normal", "纪录片→normal"),
    ("sim_doc", "模拟纪实", "纪实", "normal", ""),
    # 8 短剧源
    ("sim_short", "模拟短剧", "短剧", "normal", "短剧→normal"),
    ("sim_short", "模拟微短剧", "微短剧", "normal", ""),
    # 9 未知分类源
    ("sim_weird", "模拟未知内容A", "不明XYZ", "unknown", "未知分类→unknown（永不删）"),
    ("sim_weird", "模拟未知内容B", "奇怪类别Q", "unknown", ""),
    # 10 混合源（无标志位）——一源四仓 + 非白名单动画红线
    ("sim_mixed", "混合源动作片", "动作片", "normal", "混合源逐条分类"),
    ("sim_mixed", "混合源普通动画", "动画", "normal", "红线：非白名单源动画→normal（不污染 child）"),
    ("sim_mixed", "混合源伦理片", "伦理片", "adult", "词级 adult 不依赖源标志"),
    ("sim_mixed", "混合源足球", "足球", "sports", ""),
]

results = []
fails = []
chan_dist = collections.Counter()
for sid, title, cat, expect, note in CASES:
    # 走真实生产路径：不传 cks/aks → 分类器自行加载规则 + 扫描 sources.d 标志
    ch, conf, ev, pat = content_classifier.classify_with_title_heuristic(
        {"title": title, "original_category": cat, "source_id": sid})
    ok = ch == expect
    chan_dist[ch] += 1
    results.append({"source": sid, "title": title, "cat": cat, "expect": expect,
                    "got": ch, "ok": ok, "ev": ev, "note": note})
    if not ok:
        fails.append(results[-1])
    print("%s [%s|%s] cat=%s -> %s (期望 %s) ev=%s %s" % (
        "✓" if ok else "✗", sid, title, cat, ch, expect, ev, note))

total = len(CASES)
print("\n新增源自动路由：%d/%d PASS" % (total - len(fails), total))
print("分仓分布:", dict(chan_dist))

# ---- 4. 白名单生效证据（仅源 JSON 标志，未改规则） ----
rules = content_classifier._load_rules()
cks = content_classifier._get_child_known_sources(rules)
aks = content_classifier._get_adult_known_sources(rules)
print("child_known_sources:", sorted(cks))
print("adult_known_sources:", sorted(aks))
wl_proof = {
    "sim_kids_in_child_whitelist": "sim_kids" in cks,
    "sim_adult_in_adult_whitelist": "sim_adult" in aks,
    "生产白名单未受影响": {"dytt", "ffzy", "guangsu"} <= cks and {"msnii", "xrbsp", "gdlsp", "kxgav", "pgxdy"} <= aks,
}

out = {
    "discovered_sources": sorted(discovered.keys()),
    "total_cases": total,
    "pass": total - len(fails),
    "fails": fails,
    "channel_dist": dict(chan_dist),
    "whitelist_proof": wl_proof,
    "results": results,
}
os.makedirs(os.path.join(BASE, "dist"), exist_ok=True)
with open(os.path.join(BASE, "dist", "new_source_auto_routing_test.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("\n[written] dist/new_source_auto_routing_test.json")
sys.exit(1 if (fails or not all(wl_proof.values())) else 0)
