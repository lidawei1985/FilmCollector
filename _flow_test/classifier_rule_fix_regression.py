# -*- coding: utf-8 -*-
"""
classifier_rule_fix_regression.py —— P1 任务1：分类器误判修复回归测试
============================================================================
修复内容：
  1) adult explicit_ethics 双向匹配（cat in pat）→ 单向完整命中（pat in cat）
     修复 "欧美"⊂"欧美情色"、"日本"⊂"日本无码"、"同性"⊂"女同性恋" 假阳性
  2) 新增 explicit_exact 精确组：「伦理」整词命中，修复「家庭伦理」被吞
  3) movie_themes 新增同性题材词（同性/同性题材/同性片/同志电影）

验证要求（用户指令）：
  欧美剧 → normal / 日本动漫 → normal(或按源规则 child) / 同性题材普通影视 → normal
  日本无码 → adult / 伦理片 → adult
附加上下文红线：不降低成人隔离能力 + 生产 1066 条分布不劣化 + r18bak 误杀清零。
"""
import json
import os
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.core import content_classifier  # noqa: E402

rules = content_classifier._load_rules()
cks = content_classifier._get_child_known_sources(rules)

# (标题, 分类, 源, 期望channel, 说明)
CASES = [
    # ---- 用户指定回归 5 例 ----
    ("欧美剧测试", "欧美剧", "tvmaze", "normal", "用户要求：欧美剧→normal"),
    ("日本动漫测试", "日本动漫", "ffzy", "normal", "用户要求：日本动漫→normal（非儿童强词）"),
    ("同性题材测试", "同性题材", "ffzy", "normal", "用户要求：同性题材普通影视→normal"),
    ("日本无码测试", "日本无码", "gdlsp", "adult", "用户要求：日本无码→adult"),
    ("伦理片测试", "伦理片", "gdlsp", "adult", "用户要求：伦理片→adult"),
    # ---- 本次修复直接相关的边界 ----
    ("欧美测试", "欧美", "tvmaze", "normal", "修复点：短分类 欧美 不再被 欧美情色 吞"),
    ("日本测试", "日本", "tvmaze", "normal", "修复点：短分类 日本 不再被 日本无码 吞"),
    ("同性测试", "同性", "ffzy", "normal", "修复点：短分类 同性 不再被 女同性恋 吞"),
    ("家庭伦理测试", "家庭伦理", "ffzy", "normal", "修复点：家庭伦理 不被 伦理 吞（exact 组）"),
    ("伦理测试", "伦理", "gdlsp", "adult", "explicit_exact：整词 伦理 仍 adult（不降隔离）"),
    ("女同性恋测试", "女同性恋", "gdlsp", "adult", "完整命中仍 adult（不降隔离）"),
    ("欧美情色测试", "欧美情色", "gdlsp", "adult", "完整命中仍 adult（不降隔离）"),
    # ---- 成人隔离能力不回退（keyword 兜底短分类） ----
    ("无码测试", "无码", "gdlsp", "adult", "keyword 兜底：无码→adult"),
    ("有码测试", "有码", "gdlsp", "adult", "keyword 兜底：有码→adult"),
    ("少女测试", "少女", "gdlsp", "adult", "keyword 兜底：少女→adult（安全优先）"),
    ("人妻测试", "人妻", "gdlsp", "adult", "keyword/ explicit 仍 adult"),
    ("卡通动漫测试", "卡通动漫", "gdlsp", "adult", "成人动漫伪装不进 child"),
    ("成人动漫测试", "成人动漫", "gdlsp", "adult", "成人动漫绝不进 child"),
    ("三级测试", "三级", "gdlsp", "adult", "三级仍 adult"),
    ("写真测试", "写真", "gdlsp", "adult", "写真仍 adult"),
    # ---- 儿童/普通/体育红线不回退 ----
    ("儿童动画测试", "儿童动画", "dytt", "child", "child_strong 不变"),
    ("dytt动画片", "动画片", "dytt", "child", "白名单 child_weak 不变"),
    ("msnii普通动画", "动画", "msnii", "adult", "成人源动画按源级隔离→adult（防 H 动漫混入）"),
    ("dytt动作片", "动作片", "dytt", "normal", "儿童源动作片仍 normal"),
    ("dytt伦理片", "伦理片", "dytt", "adult", "儿童源伦理片仍 adult"),
    ("足球测试", "足球", "ffzy", "sports", "sports 不变"),
    ("动作片测试", "动作片", "ffzy", "normal", "normal 不变"),
    ("未知测试", "不明XYZ", "ffzy", "unknown", "unknown 不变"),
    # ---- P1 任务2：成人源源级隔离（adult_known_sources 白名单） ----
    ("gdlsp编号c25", "c25", "gdlsp", "adult", "成人源编号分类→adult（替代 unknown）"),
    ("msnii编号c28", "c28", "msnii", "adult", "成人源编号分类→adult"),
    ("kxgav编号c20", "c20", "kxgav", "adult", "成人源编号分类→adult"),
    ("ffzy编号c25", "c25", "ffzy", "unknown", "N5 隔离：普通源同编号不归 adult（非全局 tid 表）"),
    ("gdlsp动作片", "动作片", "gdlsp", "adult", "源级严格隔离：成人源普通分类也只进 adult"),
    ("gdlsp儿童动画", "儿童动画", "gdlsp", "adult", "儿童保护：成人源儿童强词绝不进 child"),
    ("bfzy普通动画", "动画", "bfzy", "normal", "普通源(非成人/非儿童白名单)动画→normal"),
    ("hongniu動作", "動作", "hongniu", "normal", "繁体长尾词→normal"),
    ("hongniu悬疑犯罪", "悬疑犯罪", "hongniu", "normal", "长尾词→normal"),
]

fails = []
for title, cat, src, expect, note in CASES:
    ch, conf, ev, pat = content_classifier.classify_with_title_heuristic(
        {"title": title, "original_category": cat, "source_id": src}, rules, cks)
    ok = ch == expect
    if not ok:
        fails.append({"title": title, "cat": cat, "src": src, "expect": expect, "got": ch, "ev": ev, "note": note})
    print("%s [%s] cat=%s src=%s -> %s (期望 %s) ev=%s" % (
        "✓" if ok else "✗", title, cat, src, ch, expect, ev))

print("\n规则级回归：%d/%d PASS" % (len(CASES) - len(fails), len(CASES)))

# ---- r18bak 770 条真实历史数据复测：误杀应清零 ----
def item_input(it):
    x = dict(it)
    if not x.get("source_id"):
        o = x.get("origins") or []
        if o:
            x["source_id"] = o[0].get("source_id") or ""
    if not x.get("original_category"):
        c = x.get("canonical_categories") or x.get("aggregated_categories") or []
        if c:
            x["original_category"] = str(c[0])
    return x

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
r18 = json.load(open(os.path.join(base, "backend", "data", "aggregated.r18bak.json"), encoding="utf-8"))
items = r18.get("items", {})
if isinstance(items, dict):
    items = list(items.values())
dist = collections.Counter()
adult_hits = []
for it in items:
    x = item_input(it)
    ch, conf, ev, pat = content_classifier.classify_with_title_heuristic(x, rules, cks)
    dist[ch] += 1
    if ch == "adult":
        adult_hits.append({"title": x.get("title"), "cat": x.get("original_category"), "ev": ev})
print("r18bak 修复后分布:", dict(dist))
print("r18bak adult 命中明细:", json.dumps(adult_hits, ensure_ascii=False))

out = {
    "rule_level": {"total": len(CASES), "fail": len(fails), "fails": fails},
    "r18bak_dist": dict(dist),
    "r18bak_adult_hits": adult_hits,
}
with open(os.path.join(base, "dist", "classifier_rule_fix_regression.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("\n[written] dist/classifier_rule_fix_regression.json")
sys.exit(1 if fails else 0)
