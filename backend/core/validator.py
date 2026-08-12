# -*- coding: utf-8 -*-
"""
导出前格式校验：自动校验 JSON 语法与必填字段完整性，返回缺失/格式错误清单，避免客户端解析失败空白。
支持两套格式：TVBox(maccms) 与 通用纯净影片。
"""
import json
import os


def _is_url(v):
    return isinstance(v, str) and v.startswith("http")


def _is_image_ref(v):
    """海报/封面合法引用：外链 URL，或本地缓存的相对路径（如 images/poster_xxx.jpg）。"""
    if not isinstance(v, str) or not v:
        return False
    if v.startswith("http"):
        return True
    if v.startswith("images/") or v.startswith("output/images/"):
        return True
    if v.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return True
    return False


def validate_tvbox(vods):
    """TVBox / maccms 格式必填校验。"""
    errors = []
    for i, v in enumerate(vods):
        name = v.get("vod_name") or f"#{i}"
        if not (v.get("vod_name") and str(v.get("vod_name")).strip()):
            errors.append(f"[TVBox] 影片「{name}」缺少必填字段 vod_name")
        if not (v.get("vod_id")):
            errors.append(f"[TVBox] 影片「{name}」缺少 vod_id")
        if not (v.get("vod_play_url") and str(v.get("vod_play_url")).strip()):
            errors.append(f"[TVBox] 影片「{name}」缺少播放地址 vod_play_url")
        if v.get("vod_pic") and not _is_image_ref(v["vod_pic"]):
            errors.append(f"[TVBox] 影片「{name}」vod_pic 不是合法图片地址：{v.get('vod_pic')}")
    return errors


def validate_generic(vods):
    """通用纯净影片格式必填校验（对齐标准样例：id/name/type/play_list）。"""
    errors = []
    for i, v in enumerate(vods):
        name = v.get("name") or f"#{i}"
        if not (v.get("id") and str(v.get("id")).strip()):
            errors.append(f"[通用] 影片「{name}」缺少必填字段 id")
        if not (v.get("name") and str(v.get("name")).strip()):
            errors.append(f"[通用] 影片「{name}」缺少必填字段 name")
        if not (v.get("type") and str(v.get("type")).strip()):
            errors.append(f"[通用] 影片「{name}」缺少必填字段 type")
        has_url = any(e.get("url") for e in v.get("play_list", []))
        if not has_url:
            errors.append(f"[通用] 影片「{name}」没有任何播放地址(play_list)")
        if v.get("pic") and not _is_image_ref(v["pic"]):
            errors.append(f"[通用] 影片「{name}」pic 不是合法图片地址：{v.get('pic')}")
    return errors


def check_file_syntax(path):
    """重新读取写出的文件，确认 JSON 语法有效。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            json.load(f)
        return True, ""
    except Exception as e:
        return False, str(e)
