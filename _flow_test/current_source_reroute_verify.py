# -*- coding: utf-8 -*-
"""
current_source_reroute_verify.py —— 现有源全量重路由·五项确认点验证（只读）
============================================================================
验证目标（用户 2026-09-04 指令）：
  1. 普通影视是否进入 normal
  2. 儿童内容是否进入 child
  3. 成人内容是否进入 adult
  4. 体育是否进入 sports
  5. 以前 Filter 丢弃的数据是否可以重新分配（unknown 不删除）

约束：只读验证 + 内存模拟；不修改任何生产数据/代码/APK。
运行：cd E:\\FilmCollector && python _flow_test/current_source_reroute_verify.py
"""
import json
import os
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core import content_classifier  # noqa: E402
from backend.core import channel_router  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(BASE, "dist")
DATA = os.path.join(BASE, "backend", "data")
CHANNELS = ("normal", "child", "adult", "sports", "unknown")

result = {"checks": {}, "red_line_violations": [], "samples": {}}


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def item_classifier_input(it):
    """把任意历史 item 映射成分类器输入（与 channel_router._load_aggregated_items 同规则）。"""
    x = dict(it)
    if not x.get("source_id"):
        origins = x.get("origins") or []
        if origins and isinstance(origins, list):
            x["source_id"] = origins[0].get("source_id") or ""
    if not x.get("original_category"):
        ccs = x.get("canonical_categories") or x.get("aggregated_categories") or []
        if ccs:
            x["original_category"] = str(ccs[0])
        elif x.get("category"):
            x["original_category"] = str(x.get("category"))
    return x


# ============ 0. 加载重路由后的 5 频道 feed（P2-1/C4：router 产物在 dist/channels/） ============
feeds = {ch: load_json(os.path.join(DIST, "channels", "%s.json" % ch)) for ch in CHANNELS}
counts = {ch: len(feeds[ch]["items"]) for ch in CHANNELS}
total = sum(counts.values())

# ============ 1. 不变量：零丢失 + 频道互斥 ============
id_sets = {ch: set(i["dedup_id"] for i in feeds[ch]["items"]) for ch in CHANNELS}
overlap = 0
chs = list(CHANNELS)
for a in range(len(chs)):
    for b in range(a + 1, len(chs)):
        overlap += len(id_sets[chs[a]] & id_sets[chs[b]])
_agg_total = len(load_json(os.path.join(DATA, "aggregated.json")).get("items", {}))
result["checks"]["数据零丢失"] = (
    total == _agg_total,
    "sum(feeds)=%d aggregated_total=%d（动态基线，随聚合库实时读取）" % (total, _agg_total),
)
result["checks"]["频道互斥(无重复)"] = (overlap == 0, "跨频道重复 dedup_id=%d" % overlap)

# ============ 2. 规则加载 + 真值集 ============
rules = content_classifier._load_rules()
cks = content_classifier._get_child_known_sources(rules)
adult_pats = rules.get("adult", {}).get("explicit_ethics", []) + rules.get("adult", {}).get("adult_keyword", [])
child_strong = set(rules.get("child", {}).get("child_strong", []))
child_weak = set(rules.get("child", {}).get("child_weak", []))
sports_pats = set(rules.get("sports", {}).get("sports_main", []))
normal_pats = set()
for g, ps in rules.get("normal", {}).items():
    normal_pats.update(ps)

# ============ 3. 确认点1：普通影视 → normal（红线扫描：normal 不得混入成人/儿童/体育） ============
viol_normal = []
for it in feeds["normal"]["items"]:
    cat = str((it.get("aggregated_categories") or [""])[0] if it.get("aggregated_categories") else "")
    title = it.get("title", "")
    hit = None
    for p in adult_pats:
        if p and (p in cat or (cat and cat in p)):
            hit = ("adult", p)
            break
    if not hit and cat in child_strong:
        hit = ("child_strong", cat)
    if not hit and cat in sports_pats:
        hit = ("sports", cat)
    if hit:
        viol_normal.append({"title": title, "cat": cat, "hit": hit})
result["checks"]["确认点1_普通影视进normal且无污染"] = (
    counts["normal"] > 0 and not viol_normal,
    "normal=%d 部；红线违规 %d 条" % (counts["normal"], len(viol_normal)),
)
result["red_line_violations"].extend(viol_normal[:20])
ev_dist = collections.Counter(i.get("classify_evidence", "") for i in feeds["normal"]["items"])
result["samples"]["normal_分类证据分布"] = dict(ev_dist.most_common(10))
result["samples"]["normal_样例"] = [
    {"title": i["title"], "cat": (i.get("aggregated_categories") or [""])[0],
     "ev": i.get("classify_evidence"), "conf": i.get("classify_confidence")}
    for i in feeds["normal"]["items"][:5]
]
normal_adult_flag = sum(1 for i in feeds["normal"]["items"] if i.get("is_adult"))
result["samples"]["normal中is_adult标记数"] = normal_adult_flag

# ============ 4. 确认点2/3/4：全量库真值反查（aggregated 里确实有 child/adult/sports 内容吗？去了哪？） ============
agg_items = channel_router._load_aggregated_items()
chan_of = {}
for ch in CHANNELS:
    for i in feeds[ch]["items"]:
        chan_of[i["dedup_id"]] = ch

truth = {"child": [], "adult": [], "sports": []}
for it in agg_items:
    cat = str(it.get("original_category") or "")
    did = it.get("dedup_id")
    dest = chan_of.get(did, "?")
    if cat in child_strong or (cat in child_weak and it.get("source_id") in cks):
        truth["child"].append((it.get("title"), cat, it.get("source_id"), dest))
    is_adult_cat = any(p and (p in cat or (cat and cat in p)) for p in adult_pats)
    if is_adult_cat or it.get("is_adult"):
        truth["adult"].append((it.get("title"), cat, it.get("source_id"), dest, bool(it.get("is_adult"))))
    if cat in sports_pats:
        truth["sports"].append((it.get("title"), cat, it.get("source_id"), dest))

child_ok = all(d == "child" for *_x, d in truth["child"]) if truth["child"] else None
result["checks"]["确认点2_儿童内容进child"] = (
    (child_ok is True) or (child_ok is None and counts["child"] == 0),
    "聚合库中儿童真值 %d 条；child feed=%d；%s" % (
        len(truth["child"]), counts["child"],
        "全部进child" if truth["child"] else "当前聚合库无儿童内容（dytt 儿童源已配置但尚未抓取）"),
)
result["samples"]["child_真值样例"] = truth["child"][:10]

adult_dest = collections.Counter(d for *_x, d in [(t[0], t[1], t[2], t[3]) for t in truth["adult"]])
adult_leak = [t for t in truth["adult"] if t[3] in ("normal", "child", "sports")]
result["checks"]["确认点3_成人内容不进normal/child"] = (
    not adult_leak,
    "聚合库成人真值 %d 条；去向分布 %s；泄漏(normal/child/sports) %d 条" % (
        len(truth["adult"]), dict(adult_dest), len(adult_leak)),
)
result["samples"]["adult_真值去向"] = dict(adult_dest)
result["samples"]["adult_泄漏样例"] = adult_leak[:10]

sports_dest = collections.Counter(d for *_x, d in truth["sports"])
result["checks"]["确认点4_体育进sports"] = (
    (all(d == "sports" for *_x, d in truth["sports"]) if truth["sports"] else counts["sports"] == 0),
    "聚合库体育真值 %d 条；sports feed=%d；去向 %s" % (len(truth["sports"]), counts["sports"], dict(sports_dest)),
)
result["samples"]["sports_真值样例"] = truth["sports"][:10]

# ============ 5. 确认点5：以前 Filter 丢弃/隔离的数据 → 模拟重分类（内存，不写盘） ============
def simulate(path, label):
    try:
        d = load_json(path)
    except Exception as e:
        return {"label": label, "error": str(e)}
    items = d.get("items", d if isinstance(d, list) else [])
    if isinstance(items, dict):
        items = list(items.values())
    dist = collections.Counter()
    samples = collections.defaultdict(list)
    for it in items:
        x = item_classifier_input(it)
        ch, conf, ev, pat = content_classifier.classify_with_title_heuristic(x, rules, cks)
        dist[ch] += 1
        if len(samples[ch]) < 5:
            samples[ch].append({"title": x.get("title"), "cat": x.get("original_category"), "ev": ev})
    return {"label": label, "total": len(items), "重分类分布": dict(dist), "样例": dict(samples)}

result["checks"]["确认点5a_unknown永不删除"] = (
    total == _agg_total and counts["unknown"] >= 0,
    "unknown=%d 条；'永不删除'原则成立：所有条目必进 5 仓之一、零丢弃（P1 任务2 后成人源编号已迁 adult，unknown 清空属正常）" % counts["unknown"],
)
result["quarantine_200_模拟重分类"] = simulate(os.path.join(DATA, "quarantine.json"), "quarantine 隔离数据")
result["r18bak_770_模拟重分类"] = simulate(os.path.join(DATA, "aggregated.r18bak.json"), "R18 历史剥离数据")

# ============ 6. 规则级红线复测（不依赖既有报告，自己跑） ============
synthetic = [
    ("普通动作", "动作片", "ffzy", "normal"),
    ("普通电视剧", "国产剧", "ffzy", "normal"),
    ("儿童动画", "儿童动画", "dytt", "child"),
    ("动画片", "动画片", "dytt", "child"),
    ("普通源动漫", "动漫", "gdlsp", "adult", ),      # 源级隔离：成人源动漫→adult（P1 任务2 起）
    ("普通源动画", "动画", "msnii", "adult", ),      # 源级隔离：成人源动画→adult（P1 任务2 起）
    ("伦理片", "伦理片", "gdlsp", "adult"),
    ("成人动漫", "成人动漫", "gdlsp", "adult"),     # 红线：成人动漫绝不进 child
    ("足球", "足球", "ffzy", "sports"),
    ("未知", "不明XYZ", "ffzy", "unknown"),
    ("dytt动作片", "动作片", "dytt", "normal"),     # 红线：儿童源里的动作片仍进 normal
    ("dytt伦理片", "伦理片", "dytt", "adult"),      # 红线：儿童源里的伦理片进 adult
]
syn_fail = []
for title, cat, src, expect in synthetic:
    ch, conf, ev, pat = content_classifier.classify_with_title_heuristic(
        {"title": title, "original_category": cat, "source_id": src}, rules, cks)
    if ch != expect:
        syn_fail.append({"title": title, "cat": cat, "src": src, "expect": expect, "got": ch, "ev": ev})
result["checks"]["规则级红线复测(12例)"] = (not syn_fail, "失败 %d/12" % len(syn_fail))
result["samples"]["规则级失败明细"] = syn_fail

# ============ 输出 ============
print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
out = os.path.join(DIST, "reroute_verify_result.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print("\n[written]", out)
