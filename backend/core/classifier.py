# -*- coding: utf-8 -*-
"""
自动分类打标签：划分 电影/连续剧/短剧/动漫/综艺/纪录片/少儿 七大分区，并标记题材标签。
"""
import re

TYPE_KEYWORDS = {
    "短剧": ["短剧", "微短剧", "短视频剧"],
    "动漫": ["动漫", "动画", "番剧", "anime"],
    "综艺": ["综艺", "真人秀", "脱口秀", "选秀"],
    "纪录片": ["纪录片", "纪录", "documentary"],
    "少儿": ["少儿", "儿童", "幼儿", "育儿", "儿歌"],
}

GENRE_KEYWORDS = {
    "悬疑": ["悬疑", "推理", "谜", "凶杀", "侦探"],
    "喜剧": ["喜剧", "搞笑", "幽默", "欢乐"],
    "动作": ["动作", "武打", "格斗", "功夫"],
    "爱情": ["爱情", "恋爱", "甜", "言情", "浪漫"],
    "科幻": ["科幻", "宇宙", "未来", "外星", "机甲"],
    "恐怖": ["恐怖", "惊悚", "灵异", "鬼"],
    "剧情": ["剧情", "现实", "家庭", "成长"],
    "战争": ["战争", "军旅", "抗战", "军事"],
    "犯罪": ["犯罪", "黑帮", "警匪", "卧底"],
    "奇幻": ["奇幻", "魔法", "神话", "仙侠"],
    "历史": ["历史", "古装", "王朝", "宫斗"],
}


def classify_type(item):
    text = " ".join([str(item.get("title", "")), str(item.get("type", "")),
                     str(item.get("description", ""))]).lower()
    for t, kws in TYPE_KEYWORDS.items():
        if any(k.lower() in text for k in kws):
            return t
    # 含分集且偏长 -> 连续剧；否则电影
    if len(item.get("episodes", [])) > 1:
        return "连续剧"
    return "电影"


def classify_genres(item):
    text = " ".join([str(item.get("title", "")), str(item.get("description", "")),
                     str(item.get("aliases", ""))]).lower()
    genres = []
    for g, kws in GENRE_KEYWORDS.items():
        if any(k.lower() in text for k in kws):
            genres.append(g)
    return genres


def classify(items):
    for it in items:
        if not it.get("type"):
            it["type"] = classify_type(it)
        else:
            # 归一化用户填的类型到标准分区
            t = str(it["type"])
            mapped = None
            for std, kws in TYPE_KEYWORDS.items():
                if any(k in t for k in kws):
                    mapped = std
                    break
            it["type"] = mapped or ("连续剧" if len(it.get("episodes", [])) > 1 else "电影")
        it["genres"] = classify_genres(it)
        it["region"] = (it.get("region") or "").strip()
    return items
