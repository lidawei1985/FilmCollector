# -*- coding: utf-8 -*-
"""
content_classifier.py —— V2 内容分类器（基于 2026-09 ChannelRouter 闭环模拟验证版）
====================================================================================
- 目标: 替代 "Filter=删除"，改为 "Classifier=分类"
- 5 个 channel: normal / child / adult / sports / unknown
- 原则: 禁止删除任何影片；unknown 进入 unknown_bucket 等待人工归类
- 数据流: source_raw → aggregator aggregated.json → 本分类器 → channel_bucket[]
- 来源类型: 真实 ?ac=class 分类缓存 (priority=1) + 人工规则 (priority=2) + keyword (priority=3)

路径/配置全部相对化、可被环境变量覆盖、跨平台。
"""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import store

# ---- 路径（全部相对 BASE_DIR，可被环境变量覆写） ----
BASE_DIR = store.BASE_DIR
RULES_PATH = os.environ.get(
    "FILMCOLLECTOR_CLASSIFIER_RULES",
    os.path.join(BASE_DIR, "backend", "data", "classifier_rules.json"),
)
DEFAULT_RULES = os.path.join(BASE_DIR, "backend", "data", "classifier_rules.default.json")

CHANNELS = ("normal", "child", "adult", "sports", "unknown")
CHILD_KNOWN_SOURCES_DEFAULT = {"dytt", "guangsu", "ffzy"}
# 注册成人源（与 backend/data/adult_source_registry.json 白名单一致）：
# 源级血缘隔离 —— 这些源的内容只进 adult，不进 child/normal/sports（非全局 tid 表）
ADULT_KNOWN_SOURCES_DEFAULT = {"msnii", "xrbsp", "gdlsp", "kxgav", "pgxdy"}

# ---- 内置兜底规则（兜底作用，可被 classifier_rules.json 覆盖） ----
BUILTIN_RULES = {
    "normal": {
        "movie":  ["电影", "动作片", "喜剧片", "爱情片", "科幻片", "恐怖片", "剧情片",
                   "战争片", "悬疑片", "惊悚片", "犯罪片", "奇幻片", "4K电影", "院线",
                   "邵氏电影", "Netflix电影"],
        "tv":     ["电视剧", "剧集", "连续剧", "国产剧", "港剧", "韩剧", "日剧",
                   "美剧", "台剧", "泰剧", "网络剧", "情景剧", "大陆剧", "香港剧",
                   "港澳剧", "港台剧", "台湾剧", "韩国剧", "日本剧", "欧美剧",
                   "海外剧", "泰国剧", "Netflix自制剧"],
        "anime_normal": ["动漫", "番剧", "国产动漫", "日韩动漫", "日本动漫",
                         "欧美动漫", "港台动漫", "海外动漫", "有声动漫", "漫剧",
                         "中国动漫", "AI漫剧"],
        "variety": ["综艺", "真人秀", "大陆综艺", "港台综艺", "日韩综艺", "欧美综艺"],
        "doc":    ["纪录片", "纪实", "记录片"],
        "short":  ["短剧", "微短剧", "女频恋爱", "反转爽剧", "古装仙侠", "年代穿越",
                   "脑洞悬疑", "现代都市", "爽文短剧", "穿越年代", "甜宠", "霸总",
                   "逆袭", "女频", "爽文", "爽剧", "全集", "合集"],
    },
    "child": {
        "child_strong":  ["儿童动画", "少儿动画", "少儿动漫", "卡通动画",
                          "儿童动画电影", "动画电影", "国产动画电影",
                          "儿童电影", "少儿电影", "儿童儿歌", "儿歌", "幼儿",
                          "幼儿儿歌", "亲子", "儿童故事", "绘本", "睡前故事",
                          "宝宝", "科普学习", "科普", "科学教育", "知识动画",
                          "早教", "早教动画", "益智", "益智动画", "启蒙", "幼教",
                          "国产动画", "国产少儿", "国创", "日韩动画", "日本动画",
                          "韩国动画", "日番", "欧美动画", "欧美卡通", "港台动画",
                          "海外动画"],
        "child_weak":    ["动画", "动画片", "卡通"],  # 必须 source ∈ child_known_sources
    },
    "adult": {
        # explicit_exact：整词精确命中（"伦理" 必须整个分类就是"伦理"，
        # 避免 "家庭伦理" 等正常题材被 "伦理" 吞进 adult）
        "explicit_exact": ["伦理"],
        "explicit_ethics": ["伦理三级", "三级", "亚洲情色", "中文字幕",
                            "无码专区", "制服丝袜", "巨乳美乳", "群交淫乱",
                            "少女萝莉", "女同性恋", "强奸乱伦", "国产情色",
                            "欧美情色", "日本无码", "日本有码", "熟女人妻", "人妻",
                            "处女", "重口色情", "制服诱惑", "两性课堂", "港台三级",
                            "韩国伦理", "西方伦理", "日本伦理", "伦理片", "写真热舞",
                            "写真", "国产自拍", "偷拍自拍", "国产裸聊", "国产盗摄",
                            "模特", "自拍", "网友自拍", "街拍", "私房", "网红"],
        "adult_keyword": ["两性", "情色", "色情", "成人", "18禁", "18+", "激情",
                          "床戏", "裸", "擦边", "裸播", "裸聊", "卡通动漫",
                          "成人动漫", "里番", "H动漫", "无码", "有码", "群交",
                          "萝莉", "女同", "乱伦", "强奸", "人妻", "熟女",
                          "巨乳", "制服", "美乳", "少女", "中出", "颜射",
                          "口交", "肛交", "潮吹", "偷情", "出轨", "人兽",
                          "乱伦", "SM", "丝袜"],
    },
    "sports": {
        "sports_main": ["篮球", "足球", "网球", "斯诺克", "台球", "乒乓球",
                        "排球", "田径", "游泳", "橄榄球", "赛事", "比赛",
                        "体育", "世界杯", "NBA", "CBA", "F1", "直播",
                        "新闻", "资讯"],
    },
}


def _load_rules():
    """加载规则：用户自定义 > 内置 default > 内置兜底。

    返回 dict: {channel_name: {category: [patterns]}}
    """
    if os.path.isfile(RULES_PATH):
        try:
            with open(RULES_PATH, encoding="utf-8") as f:
                rules = json.load(f)
            return rules
        except Exception as e:
            store.log("warn", "[content_classifier] rules 解析失败，使用内置: %s" % e)
    elif os.path.isfile(DEFAULT_RULES):
        try:
            with open(DEFAULT_RULES, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # 内置兜底
    return BUILTIN_RULES


_SOURCE_FLAG_CACHE = {"mtime": None, "child": set(), "adult": set()}


def _scan_source_flags():
    """扫描 sources.d 源级标志（is_child_known_source / is_adult）。

    使「新增源 = 只丢 sources.d/*.json」对儿童/成人白名单同样成立
    （0 代码改动、0 规则配置改动；与 dytt.json 等已有 flag 约定一致）。
    结果按 sources.d 内 json 最大 mtime 缓存；任何失败静默降级为空集
    （退回 rules _meta / 内置白名单，不影响既有行为）。
    """
    global _SOURCE_FLAG_CACHE
    try:
        from . import source_registry
        d = source_registry.SOURCES_DIR
        try:
            mt = max((os.path.getmtime(os.path.join(d, f))
                      for f in os.listdir(d) if f.endswith(".json")), default=None)
        except Exception:
            mt = None
        if mt is not None and _SOURCE_FLAG_CACHE.get("mtime") == mt:
            return _SOURCE_FLAG_CACHE["child"], _SOURCE_FLAG_CACHE["adult"]
        child, adult = set(), set()
        for key, cfg in (source_registry.discover() or {}).items():
            sid = cfg.get("id") or key
            if cfg.get("is_child_known_source"):
                child.add(sid)
            if cfg.get("is_adult"):
                adult.add(sid)
        _SOURCE_FLAG_CACHE = {"mtime": mt, "child": child, "adult": adult}
        return child, adult
    except Exception:
        return set(), set()


def _get_child_known_sources(rules):
    """获取 child_known_sources 白名单（防止普通源污染儿童）。

    规则 _meta 白名单 ∪ sources.d 源 JSON 的 is_child_known_source 标志。
    """
    cks = (rules or {}).get("_meta", {}).get("child_known_sources") or list(CHILD_KNOWN_SOURCES_DEFAULT)
    scan_c, _ = _scan_source_flags()
    return set(cks) | scan_c


def _get_adult_known_sources(rules):
    """获取 adult_known_sources 白名单（注册成人源，源级隔离只进 adult）。

    规则 _meta 白名单 ∪ sources.d 源 JSON 的 is_adult 标志。
    """
    aks = (rules or {}).get("_meta", {}).get("adult_known_sources") or list(ADULT_KNOWN_SOURCES_DEFAULT)
    _, scan_a = _scan_source_flags()
    return set(aks) | scan_a


def _classify_one(category, source, rules, cks, aks=None):
    """单条分类。返回 (channel, confidence, evidence, matched_pattern)"""
    if not category or not str(category).strip():
        return ("unknown", 0.0, "no_category", None)
    cat = str(category).strip()
    if aks is None:
        aks = _get_adult_known_sources(rules)

    # 1) adult 优先（防儿童污染到 adult）
    # 安全匹配（2026-09-04 P1 修复）：
    # - explicit_exact：整词精确命中
    # - explicit_ethics：单向完整命中（pat in cat）；禁止反向包含（cat in pat），
    #   修复 "欧美"⊂"欧美情色"、"日本"⊂"日本无码"、"同性"⊂"女同性恋" 的假阳性
    for pat in rules.get("adult", {}).get("explicit_exact", []):
        if cat == pat:
            return ("adult", 1.0, "rule_explicit_exact", pat)
    for pat in rules.get("adult", {}).get("explicit_ethics", []):
        if pat in cat:
            return ("adult", 1.0, "rule_explicit_ethics", pat)
    for pat in rules.get("adult", {}).get("adult_keyword", []):
        if pat in cat:
            return ("adult", 0.95, "rule_adult_keyword", pat)

    # 1.5) 注册成人源白名单（源级血缘隔离，非全局 tid 表）：
    # 来源为注册成人源 → 其内容统一归 adult，绝不进 child/normal/sports；
    # 解决 adult_taxonomy 名称未解析时 c{tid} 编号分类滞留 unknown 的问题（P1 任务2）
    if source and source in aks:
        return ("adult", 0.85, "rule_adult_source_whitelist", source)

    # 2) child 严格匹配（仅白名单内 source 才进 child_weak）
    for pat in rules.get("child", {}).get("child_strong", []):
        if cat == pat:
            return ("child", 0.95, "rule_child_strong", pat)
    for pat in rules.get("child", {}).get("child_weak", []):
        if cat == pat:
            if source in cks:
                return ("child", 0.85, "rule_child_weak_whitelisted", pat)
            else:
                return ("normal", 0.7, "fallback_anime_to_normal", pat)

    # 3) sports
    for pat in rules.get("sports", {}).get("sports_main", []):
        if cat == pat:
            return ("sports", 0.95, "rule_sports", pat)

    # 4) normal 严格匹配
    for group, patterns in rules.get("normal", {}).items():
        for pat in patterns:
            if cat == pat:
                return ("normal", 0.9, "rule_%s" % group, pat)

    # 5) normal substring fallback（兜底，长尾词）
    for group, patterns in rules.get("normal", {}).items():
        for pat in patterns:
            if pat in cat or cat in pat:
                return ("normal", 0.7, "fallback_%s" % group, pat)

    # 6) unknown（永不删）
    return ("unknown", 0.0, "no_match", None)


def classify_with_title_heuristic(item, rules=None, cks=None, aks=None):
    """完整分类：基于 (title, category, type, source) 判定 channel。

    标题级豁免：
      - sports + title 含影视信号 → 降级 normal
      - child + title 含成人信号 → 升 adult
    """
    if rules is None:
        rules = _load_rules()
    if cks is None:
        cks = _get_child_known_sources(rules)
    if aks is None:
        aks = _get_adult_known_sources(rules)

    cat = item.get("original_category") or item.get("category") or item.get("type_name") or ""
    source = item.get("source_id") or item.get("source") or ""
    channel, conf, ev, pat = _classify_one(cat, source, rules, cks, aks)

    # 标题级豁免
    title = item.get("title") or item.get("name") or ""
    if channel == "sports":
        if any(k in title for k in ["全集", "第", "电影", "版", "剧场"]):
            return ("normal", max(conf - 0.2, 0.3),
                    "title_film_signal_overrides_sports", pat)
    if channel == "child":
        if any(k in title for k in ["伦理", "三级", "成人", "激情", "色情", "裸", "18禁"]):
            return ("adult", 0.9, "title_adult_signal_overrides_child", pat)
    return (channel, conf, ev, pat)


def classify_items(items, rules=None, cks=None):
    """批量分类：给 items 列表每条加 channel/confidence/evidence 三字段（in-place）。"""
    if rules is None:
        rules = _load_rules()
    if cks is None:
        cks = _get_child_known_sources(rules)
    aks = _get_adult_known_sources(rules)
    for it in items:
        ch, conf, ev, pat = classify_with_title_heuristic(it, rules, cks, aks)
        it["channel"] = ch
        it["classify_confidence"] = conf
        it["classify_evidence"] = ev
        it["classify_matched"] = pat
        it["classified_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return items


def route_to_buckets(items):
    """ChannelRouter：5 个 bucket 分仓。返回 {channel: [item, ...]} dict。

    关键: 永不丢失数据；unknown 永不被丢弃，仅打 unknown_bucket 标记。
    """
    buckets = {ch: [] for ch in CHANNELS}
    for it in items:
        ch = it.get("channel") or "unknown"
        if ch not in buckets:
            ch = "unknown"
        # 复制原 item 防止外部修改
        buckets[ch].append(dict(it))
    return buckets


def classify_and_route(items, rules=None):
    """一体化：分类 + 分仓。返回 buckets dict + 统计。"""
    classify_items(items, rules=rules)
    buckets = route_to_buckets(items)
    counts = {ch: len(buckets[ch]) for ch in CHANNELS}
    return buckets, counts


# ---- CLI 入口（单测 / 灰度验证） ----
if __name__ == "__main__":
    import pprint

    test_items = [
        {"title": "普通动作", "original_category": "动作片", "source_id": "ffzy"},
        {"title": "普通动漫", "original_category": "动漫", "source_id": "ffzy"},
        {"title": "儿童动画测试", "original_category": "儿童动画", "source_id": "ffzy"},
        {"title": "伦理片", "original_category": "伦理片", "source_id": "ffzy"},
        {"title": "足球", "original_category": "足球", "source_id": "ffzy"},
        {"title": "未知", "original_category": "不明XYZ", "source_id": "ffzy"},
    ]

    print("=" * 60)
    print("ContentClassifier V2 自检")
    print("=" * 60)
    buckets, counts = classify_and_route(test_items)
    print("counts:", counts)
    pprint.pprint(buckets)
