# -*- coding: utf-8 -*-
"""
hero.py —— Hero 主视觉资源采集与质量管理模块（独立增强层）
================================================================
设计目标（对照需求 21 条）：
  - 建立「影片数据源 + 高清视觉素材」双重资格体系（宁缺毋滥）。
  - 每部影片独立 Hero Eligibility 判断：hero_eligible=True 必须意味着：
        ① 有可靠影片数据源 ② 数据可用 ③ 有合格高清图
        ④ 图与影片准确匹配 ⑤ 图片质量达 Hero 门槛 ⑥ 无数据异常。
  - 图片质量分级：Poster / HeroPoster / Backdrop 三类严格区分，
        普通 Poster 绝不自动伪装成 HeroPoster / Backdrop。
  - 综合 分辨率 / 比例 / 模糊 / 放大 / 压缩 / 来源 / 重复 / 更高质量候选 评分。
  - 源健康度（复用 quality 状态机）参与 Hero 评分，源严重不健康直接淘汰。
  - 多源（episodes 多线路）多图候选、phash 内容去重、标题+年份+外部ID 匹配。
  - 可增量自动更新（每次重算：发现更高清自动升级、源降权自动重评估、源恢复重新进入）。
  - 不破坏现有 poster / cover / episodes / 订阅契约 —— 仅新增字段 + 独立 hero.json。

数据层（新增到 item，向后兼容，绝不删除/修改现有字段）：
  item["hero"]          = { eligible, score, grade, source_available, source_health,
                            sources_count, image_available, image_quality, image_resolution,
                            image_match, match_confidence, has_hero_poster, has_backdrop,
                            reasons[], updated_at }
  item["hero_assets"]   = 去重后的候选图列表（精简）
  item["hero_poster_url"] / hero_poster_width / hero_poster_height
  item["backdrop_url"]    / backdrop_width    / backdrop_height
  item["poster_width"]  / poster_height   （补充普通 poster 尺寸，便于 APK）
"""
import os
import re
import io
import json
import time
import hashlib
import shutil
import statistics
from datetime import datetime

from . import store, quality, poster_repo

# ---- 质量门槛（可按实际采集结果调整）----
HERO_MIN_SCORE = 70          # B 档门槛：score < 70 不得进入 Hero（宁缺毋滥）
MIN_SOURCE_HEALTH = 40       # 综合源健康低于此值视为「源不健康」，淘汰
MIN_MATCH = 0.6              # 匹配置信度低于此值，宁可没有 Hero（防张冠李戴）
HERO_POSTER_MIN_W = 800      # Hero 竖版海报达标最小宽
BACKDROP_MIN_W = 1280        # Backdrop 横版达标最小宽
MAX_HERO_CANDIDATES = 8      # 每片最多保留候选图数（防止异常膨胀）

# Pillow 可选：有则做模糊度 + 感知哈希，无则降级（仅尺寸+字节）
try:
    from PIL import Image
    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False


def _grade(score):
    if score >= 95:
        return "S"
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    return "F"


# ---------------- 图片分析 ----------------
def _analyze_local(path):
    """分析本地图片文件，返回 dict 或 None（不存在/过小/解析失败）。"""
    try:
        sz = os.path.getsize(path)
    except OSError:
        return None
    if sz < 500:
        return None
    try:
        with open(path, "rb") as f:
            # 读到足够大的头部：真实 JPEG 常带 EXIF/APP 段，SOF 标记可能在 64 字节之后，
            # 只读 64 字节会导致尺寸解析为 (0,0)。读 64KB 覆盖绝大多数 EXIF 段。
            head = f.read(64 * 1024)
        w, h = poster_repo._img_dims(head)
    except Exception:
        return None
    if not (w and h):
        return None
    info = {
        "path": path, "url": None,
        "width": w, "height": h, "bytes": sz,
        "format": os.path.splitext(path)[1].lower().lstrip("."),
        "phash": None, "blur": None, "damaged": False,
        "source": "local_ia", "kind_hint": "poster",
        "flags": [], "score": 0, "match_confidence": 0.0,
    }
    if _HAVE_PIL:
        try:
            im = Image.open(path).convert("L")
            small = im.resize((32, 32))
            px = list(small.getdata())
            # 清晰度代理：相邻像素方差（越大=细节越多=越清晰）
            var = statistics.pvariance(px)
            info["blur"] = float(var)
            # average hash（8x8）
            ah = im.resize((8, 8), Image.LANCZOS)
            apx = list(ah.getdata())
            avg = sum(apx) / len(apx)
            bits = "".join("1" if p > avg else "0" for p in apx)
            info["phash"] = "%016x" % int(bits, 2)
        except Exception:
            info["damaged"] = True
    return info


def _analyze_remote(url, timeout=20):
    """下载远程图到内存分析（限流安全；默认不调用，避免破坏现有流程）。"""
    from . import net
    try:
        r = net.get(url, timeout=timeout, min_interval=1.0, stream=True)
        if r is None or getattr(r, "status_code", 0) >= 400:
            return None
        data = r.content
    except Exception:
        return None
    if len(data) < 500:
        return None
    try:
        w, h = poster_repo._img_dims(data[:64 * 1024])
    except Exception:
        return None
    if not (w and h):
        return None
    info = {
        "path": None, "url": url, "width": w, "height": h,
        "bytes": len(data), "format": (url.split(".")[-1] or "jpg")[:4].lower(),
        "phash": None, "blur": None, "damaged": False,
        "source": "remote", "kind_hint": "auto", "flags": [],
        "score": 0, "match_confidence": 0.0,
    }
    if _HAVE_PIL:
        try:
            im = Image.open(io.BytesIO(data)).convert("L")
            small = im.resize((32, 32))
            info["blur"] = float(statistics.pvariance(list(small.getdata())))
            ah = im.resize((8, 8), Image.LANCZOS)
            apx = list(ah.getdata())
            avg = sum(apx) / len(apx)
            bits = "".join("1" if p > avg else "0" for p in apx)
            info["phash"] = "%016x" % int(bits, 2)
        except Exception:
            info["damaged"] = True
    return info


# ---------------- 候选发现 + 去重 + 分类 ----------------
def _discover_candidates(item):
    """扫描本地图库，找出该影片的全部候选图（去重后）。"""
    base = store.BASE_DIR
    title = item.get("title") or item.get("vod_name") or ""
    cands = []

    # ① item.poster / cover 本地图（采集时落盘的普通海报）
    for key in ("poster", "cover"):
        ref = (item.get(key) or "").strip()
        if ref.startswith("images/"):
            p = os.path.join(base, "output", ref)
            if os.path.isfile(p):
                a = _analyze_local(p)
                if a:
                    a["source"] = "local_ia"
                    a["kind_hint"] = "poster"
                    cands.append(a)

    # ② 高清抓取目录 output/posters/<normalize(title)>/（含竖版+横版，跑 grab_posters 后填充）
    norm = poster_repo.normalize_name(title)
    for d in (norm, title):
        post_dir = os.path.join(base, "output", "posters", d)
        if os.path.isdir(post_dir):
            for fn in os.listdir(post_dir):
                if not fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp")):
                    continue
                a = _analyze_local(os.path.join(post_dir, fn))
                if a:
                    a["source"] = "local_hires"
                    a["kind_hint"] = "auto"
                    cands.append(a)
            break  # 命中一个目录即可

    # phash 内容去重（避免同一张图被多个来源重复保存）
    seen = {}
    deduped = []
    dup = 0
    for a in cands:
        key = a["phash"] or "%dx%d:%d" % (a["width"], a["height"], a["bytes"])
        if key in seen:
            dup += 1
            continue
        seen[key] = a
        deduped.append(a)

    for a in deduped:
        a["duplicates"] = dup
        _classify(a)
    return deduped


def _classify(a):
    """按宽高比把候选图归类为 backdrop / hero_poster / poster。"""
    w, h = a["width"], a["height"]
    if not (w and h):
        a["kind"] = "poster"
        return
    r = w / h
    if r >= 1.4:
        a["kind"] = "backdrop"            # 横版主视觉优先
    elif (h / w) >= 1.15:
        a["kind"] = "hero_poster"         # 竖版高清海报
    else:
        a["kind"] = "poster"              # 方形/接近方形，仅普通 poster 兜底（不伪装 Hero）


# ---------------- 图片质量评分 ----------------
def _score_image(a, item):
    """综合评分 0-100：分辨率 + 比例契合 + 模糊 + 来源 + 完整性/压缩。"""
    w, h = a["width"], a["height"]
    kind = a["kind"]

    # 分辨率分（0-40）
    if kind == "backdrop":
        if w >= 1920:
            res = 40
        elif w >= 1280:
            res = 30
        elif w >= 1000:
            res = 18
        else:
            res = 8
    else:  # hero_poster / poster
        if w >= 1080:
            res = 40
        elif w >= 800:
            res = 28
        elif w >= 500:
            res = 16
        else:
            res = 6

    # 比例契合分（0-15）
    if kind == "backdrop":
        r = w / h if h else 0
        ratio = 15 if 1.6 <= r <= 2.1 else (10 if 1.4 <= r < 1.6 else 4)
    else:
        r = h / w if w else 0
        ratio = 15 if 1.25 <= r <= 1.7 else (10 if 1.1 <= r < 1.25 else 5)

    # 模糊度分（0-20）
    blur = a.get("blur")
    if blur is None:
        sharp = 10
    elif blur >= 120:
        sharp = 20
    elif blur >= 60:
        sharp = 14
    elif blur >= 20:
        sharp = 8
    else:
        sharp = 3

    # 来源可靠分（0-15）
    src = a.get("source", "local_ia")
    source_score = {"local_hires": 15, "local_tmdb": 14, "local_ia": 12, "remote": 8}.get(src, 10)

    # 完整性 / 压缩分（0-10）
    px = w * h
    if px and a["bytes"] > 2000:
        bpp = (a["bytes"] * 8) / px  # bits per pixel
        if bpp >= 1.0:
            integrity = 10
        elif bpp >= 0.4:
            integrity = 8
        elif bpp >= 0.15:
            integrity = 5
        else:
            integrity = 2   # 过度压缩
        # 放大检测：尺寸极大但 bpp 极低 => 疑似放大
        if bpp < 0.12 and (w >= 1500 or h >= 1500):
            a["flags"].append("likely_upscaled")
    else:
        integrity = 4

    total = res + ratio + sharp + source_score + integrity
    a["score"] = max(0, min(100, total))
    a["res_score"] = res
    a["ratio_score"] = ratio
    a["sharp_score"] = sharp
    a["source_score"] = source_score
    a["integrity_score"] = integrity
    return a["score"]


# ---------------- 源健康（复用 quality 状态机）----------------
def _hero_source_health(ep):
    """Hero 视角的「源健康分」（0-100），与播放清晰度无关，只看健康状态/失败/延迟。
    对应需求：源健康度必须参与 Hero 评分——好源=高分、频繁超时/降权=低分、完全死=0；
    低于 MIN_SOURCE_HEALTH 直接淘汰（hero_eligible=false）。
      active   ≈ 88（基准高分，稳定可播）
      warning  ≈ 55（软告警仍可播，保留资格）
      degraded ≈ 30（已降权，低于门槛→重评估）
      disabled =  0（彻底失效）
    慢源（延迟大）额外扣分，体现「频繁超时=低分」。"""
    h = ep.get("health") or {}
    status = h.get("status", quality.STATUS_ACTIVE)
    if status == quality.STATUS_DISABLED:
        return 0
    if status == quality.STATUS_DEGRADED:
        base = 30
    elif status == quality.STATUS_WARNING:
        base = 55
    else:  # active
        base = 88
    # 延迟惩罚：极慢 / 频繁超时
    latency = h.get("latency")
    if isinstance(latency, (int, float)):
        if latency >= 5.0:
            base -= 20
        elif latency >= 3.0:
            base -= 10
    # active 但仍有未清零的失败计数 → 轻微抖动扣分（刚恢复）
    fails = int(h.get("fails", 0) or 0)
    if status == quality.STATUS_ACTIVE and fails > 0:
        base -= min(fails, 3) * 5
    return max(0, min(100, base))


def _source_health(item):
    """返回 (综合源健康分, 可用源数)。完全失效 → (0, 0)。"""
    eps = item.get("episodes") or []
    usable = []
    for ep in eps:
        if quality.is_disabled(ep):
            continue
        if not (ep.get("urls") or [ep.get("url")]):
            continue
        usable.append(ep)
    if not usable:
        return 0, 0
    # 综合：取最佳可用源的 Hero 健康分（优先绑定最佳源，但保留多源）
    best = max(_hero_source_health(ep) for ep in usable)
    return best, len(usable)


# ---------------- 图片 ↔ 影片匹配 ----------------
def _match_confidence(item, a):
    """匹配置信度 0-1。低置信度不得进入 Hero（宁缺毋滥，防张冠李戴）。"""
    title = (item.get("title") or "").strip().lower()
    if a.get("source") in ("local_ia", "local_hires", "local_tmdb"):
        # 来自该片自身抓取流程：文件名/文件夹名含片名 => 高置信
        name_token = os.path.basename(a.get("path") or a.get("url") or "").lower()
        folder = ""
        if a.get("path"):
            folder = os.path.basename(os.path.dirname(a.get("path") or "")).lower()
        hay = (name_token + " " + folder).lower()
        if title and (title in hay or poster_repo.normalize_name(title) in poster_repo.normalize_name(hay)):
            return 1.0
        toks = re.findall(r"[a-z0-9\u4e00-\u9fff]+", title)
        if toks and any(t in hay for t in toks if len(t) > 2):
            return 0.85
        return 0.7
    # 远程/外部来源：默认保守，需后续更严格校验
    return 0.5


# ---------------- 综合 Hero Score ----------------
def _compose_score(src_score, img_q, has_hp, has_bd, match, src_cnt):
    s = 0.0
    s += min(src_score, 100) * 0.30      # 源健康 30%
    s += img_q * 0.35                     # 图片质量 35%
    s += (10 if has_bd else 0)            # 有 Backdrop +10
    s += (5 if has_hp else 0)             # 有 HeroPoster +5
    s += match * 15                       # 匹配 15%
    s += min(src_cnt, 3) * 1.5            # 多源（最多 +4.5）
    return max(0, min(100, round(s)))


# ---------------- 单部影片评估 ----------------
def _local_to_rel(a):
    """本地图 → 相对 output 的路径标记（发布时由 build_hero_json 改写成公网 URL）。"""
    if a.get("path"):
        p = a["path"].replace("\\", "/")
        idx = p.find("output/")
        if idx >= 0:
            return p[idx:]            # output/images/... 或 output/posters/...
        if p.startswith(store.BASE_DIR.replace("\\", "/")):
            return "output/" + p[len(store.BASE_DIR) + 1:]
    return a.get("url") or ""


def evaluate_item(item):
    """评估单部影片的 Hero 资格，写回 item 的 hero* 字段（不改动现有字段）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = item.get("title") or ""
    reasons = []

    # ① 源：可用 + 健康
    src_score, src_cnt = _source_health(item)
    source_available = src_cnt > 0
    if not source_available:
        reasons.append("no_source")
    source_health_ok = src_score >= MIN_SOURCE_HEALTH
    if source_available and not source_health_ok:
        reasons.append("source_unhealthy")

    # ② 图片候选：分析 + 评分 + 匹配
    cands = _discover_candidates(item)
    for a in cands:
        _score_image(a, item)
        a["match_confidence"] = _match_confidence(item, a)

    posters = [a for a in cands if a["kind"] in ("hero_poster", "poster")]
    backdrops = [a for a in cands if a["kind"] == "backdrop"]
    best_poster = max(posters, key=lambda a: a["score"]) if posters else None
    best_backdrop = max(backdrops, key=lambda a: a["score"]) if backdrops else None

    has_hero_poster = bool(best_poster and best_poster["width"] >= HERO_POSTER_MIN_W)
    has_backdrop = bool(best_backdrop and best_backdrop["width"] >= BACKDROP_MIN_W)
    image_available = bool(best_poster or best_backdrop)
    if not image_available:
        reasons.append("no_image")
    # 需求十七（最终验收）：高清 Poster 与 高清 Backdrop 必须【同时具备】才达标；
    # 缺任一即为 false，绝不降低标准（也不把低清图塞进契约）。
    image_resolution_ok = has_hero_poster and has_backdrop
    if image_available:
        if not has_hero_poster:
            reasons.append("no_hero_poster")   # 缺高清竖版海报（或无 / 低于 800px）
        if not has_backdrop:
            reasons.append("no_backdrop")       # 缺高清横版主视觉（或无 / 低于 1280px）

    # 选中资产（优先 Backdrop，否则 HeroPoster）
    chosen = best_backdrop if has_backdrop else best_poster
    if chosen is None and (best_poster or best_backdrop):
        chosen = best_poster or best_backdrop
    match = chosen["match_confidence"] if chosen else 0.0
    if chosen and match < MIN_MATCH:
        reasons.append("match_failed")

    image_quality = 0
    if best_poster:
        image_quality = max(image_quality, best_poster["score"])
    if best_backdrop:
        image_quality = max(image_quality, best_backdrop["score"])
    if image_available and image_quality < 50:
        reasons.append("quality_insufficient")

    damaged = any(a.get("damaged") for a in cands)
    if damaged:
        reasons.append("image_damaged")

    abnormal = False
    if not title:
        reasons.append("missing_title")
        abnormal = True

    score = _compose_score(src_score, image_quality, has_hero_poster, has_backdrop, match, src_cnt)
    grade = _grade(score)

    eligible = (
        source_available and source_health_ok and image_available
        and image_resolution_ok and (match >= MIN_MATCH)
        and (score >= HERO_MIN_SCORE) and not abnormal and not damaged
    )

    # —— 写回（新增字段，不触碰 poster/cover/episodes）——
    item["hero"] = {
        "eligible": eligible,
        "score": score,
        "grade": grade,
        "source_available": source_available,
        "source_health": src_score,
        "sources_count": src_cnt,
        "image_available": image_available,
        "image_quality": image_quality,
        "image_resolution": ("backdrop" if has_backdrop else ("hero_poster" if has_hero_poster else "none")),
        "image_match": round(match, 2),
        "match_confidence": round(match, 2),
        "has_hero_poster": has_hero_poster,
        "has_backdrop": has_backdrop,
        "reasons": reasons,
        "updated_at": now,
    }

    item["hero_assets"] = [{
        "kind": a["kind"], "width": a["width"], "height": a["height"],
        "score": a["score"], "source": a.get("source"),
        "match_confidence": round(a["match_confidence"], 2),
        "flags": a.get("flags", []),
    } for a in sorted(cands, key=lambda x: -x["score"])[:MAX_HERO_CANDIDATES]]

    # 选中最佳资产（本地相对路径，发布时改写公网 URL）
    # 仅当真正达到高清门槛才写入，绝不把低清/缺失图塞进契约（需求二/十七）
    if best_poster and has_hero_poster:
        item["hero_poster_url"] = _local_to_rel(best_poster)
        item["hero_poster_width"] = best_poster["width"]
        item["hero_poster_height"] = best_poster["height"]
    else:
        item["hero_poster_url"] = ""
        item["hero_poster_width"] = 0
        item["hero_poster_height"] = 0
    if best_backdrop and has_backdrop:
        item["backdrop_url"] = _local_to_rel(best_backdrop)
        item["backdrop_width"] = best_backdrop["width"]
        item["backdrop_height"] = best_backdrop["height"]
    else:
        item["backdrop_url"] = ""
        item["backdrop_width"] = 0
        item["backdrop_height"] = 0

    # 补充普通 poster 尺寸（便于 APK 区分 poster 与 hero_poster）
    p = (item.get("poster") or "")
    if p.startswith("images/"):
        pp = os.path.join(store.BASE_DIR, "output", p)
        if os.path.isfile(pp):
            aa = _analyze_local(pp)
            if aa:
                item["poster_width"] = aa["width"]
                item["poster_height"] = aa["height"]

    return item


# ---------------- 全量评估（增量更新）----------------
def evaluate_all():
    """遍历全部影片重算 Hero 资格，写回 db.json。天然支持增量更新。"""
    db = store.load_db()
    items = db.get("items", [])
    for it in items:
        try:
            evaluate_item(it)
        except Exception as e:
            it["hero"] = {"eligible": False, "score": 0, "grade": "F",
                          "reasons": ["evaluate_error:" + str(e)], "updated_at":
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    store.save_db(db)
    stats = _aggregate_stats(items)
    store.log("info", "Hero 评估完成：eligible %d / 总 %d（有源 %d，Hero海报 %d，Backdrop %d）"
              % (stats["hero_eligible"], stats["total"], stats["with_source"],
                 stats["with_hero_poster"], stats["with_backdrop"]))
    return {"count": len(items), "stats": stats}


def _aggregate_stats(items):
    stats = {
        "total": len(items), "with_source": 0, "with_poster": 0,
        "with_hero_poster": 0, "with_backdrop": 0,
        "hero_eligible": 0, "hero_ineligible": 0,
        "reasons": {},
    }
    for it in items:
        h = it.get("hero") or {}
        if h.get("source_available"):
            stats["with_source"] += 1
        if it.get("poster") or it.get("poster_width"):
            stats["with_poster"] += 1
        if h.get("has_hero_poster"):
            stats["with_hero_poster"] += 1
        if h.get("has_backdrop"):
            stats["with_backdrop"] += 1
        if h.get("eligible"):
            stats["hero_eligible"] += 1
        else:
            stats["hero_ineligible"] += 1
        for r in (h.get("reasons") or []):
            stats["reasons"][r] = stats["reasons"].get(r, 0) + 1
    return stats


# ---------------- 输出 Hero 数据契约（给 APK）----------------
def _copy_hero_image(ref, hero_dir, title, suffix=""):
    """把选中的本地 Hero 图复制到 repo/hero/<md5>.jpg。

    成功：返回相对路径 "repo/hero/<md5>.jpg"（由调用方拼公网 URL）。
    失败（文件缺失/复制异常）：返回 None —— 调用方必须将该字段置空，绝不可把内部
    相对路径（output/posters/...、images/...）写进对外 hero.json（P1-2）。
    远程 http 地址：原样返回（调用方直接拼 base）。
    """
    base = store.BASE_DIR
    local = None
    if ref.startswith("images/") or ref.startswith("posters/"):
        local = os.path.join(base, "output", ref)
    elif ref.startswith("output/"):
        local = os.path.join(base, ref)
    elif ref.startswith("http"):
        return ref
    elif os.path.isfile(ref):
        local = ref
    if local and os.path.isfile(local):
        h = hashlib.md5((poster_repo.normalize_name(title) + suffix).encode()).hexdigest()
        dst = os.path.join(hero_dir, h + ".jpg")
        try:
            import shutil
            shutil.copy2(local, dst)
            return "repo/hero/" + h + ".jpg"
        except Exception:
            return None
    return None


def build_hero_json(base, out_dir=None):
    """生成 hero.json（Hero 数据契约）。APK 只拿最佳结果，不需要知道内部多少来源。"""
    base = (base or "").rstrip("/") + "/"
    out_dir = out_dir or os.path.join(store.BASE_DIR, "tvbox-dist")
    db = store.load_db()
    items = db.get("items", [])
    hero_dir = os.path.join(out_dir, "repo", "hero")
    # 每次重建前清空：repo/hero 仅服务于 hero.json 契约，避免上一轮的低清/失效孤儿图堆积
    if os.path.isdir(hero_dir):
        shutil.rmtree(hero_dir)
    os.makedirs(hero_dir, exist_ok=True)

    heroes = []
    stats = _aggregate_stats(items)
    for it in items:
        h = it.get("hero") or {}
        title = it.get("title") or ""
        hpu = it.get("hero_poster_url") or ""
        bdu = it.get("backdrop_url") or ""
        if hpu:
            npu = _copy_hero_image(hpu, hero_dir, title)
            hpu = base + npu if npu else ""      # copy 失败 → 置空，绝不写内部相对路径（P1-2）
        else:
            hpu = ""
        if bdu:
            nbdu = _copy_hero_image(bdu, hero_dir, title, suffix="_bd")
            bdu = base + nbdu if nbdu else ""
        else:
            bdu = ""
        heroes.append({
            "hero_eligible": bool(h.get("eligible")),
            "movie_id": it.get("id") or it.get("source_url") or "",
            "title": title,
            "source_available": h.get("source_available", False),
            "source_health": h.get("source_health", 0),
            "sources_count": h.get("sources_count", 0),
            "hero_score": h.get("score", 0),
            "hero_grade": h.get("grade", "F"),
            "hero_poster": hpu,
            "backdrop": bdu,
            "hero_poster_width": it.get("hero_poster_width", 0),
            "hero_poster_height": it.get("hero_poster_height", 0),
            "backdrop_width": it.get("backdrop_width", 0),
            "backdrop_height": it.get("backdrop_height", 0),
            "match_confidence": h.get("match_confidence", 0),
            "image_quality": h.get("image_quality", 0),
            "image_resolution": h.get("image_resolution", "none"),
            "reasons": h.get("reasons", []),
        })

    # 排序：eligible 优先，按 hero_score 降序（首页 Hero 高分优先）
    heroes.sort(key=lambda x: (not x["hero_eligible"], -x["hero_score"]))

    out = {
        "code": 1, "msg": "ok",
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "stats": stats,
        "heroes": heroes,
    }
    path = os.path.join(out_dir, "hero.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    store.log("info", "Hero 契约生成：eligible %d / 总 %d（有源 %d，Hero海报 %d，Backdrop %d）"
              % (stats["hero_eligible"], stats["total"], stats["with_source"],
                 stats["with_hero_poster"], stats["with_backdrop"]))
    return out


# ---------------- 命令行 / 验证入口 ----------------
def main():
    import argparse
    ap = argparse.ArgumentParser(description="FilmCollector Hero 主视觉评估")
    ap.add_argument("--base", default="https://YOUR-USERNAME.github.io/FilmCollector",
                    help="托管根地址")
    ap.add_argument("--out", default=os.path.join(store.BASE_DIR, "tvbox-dist"), help="输出目录")
    args = ap.parse_args()
    res = evaluate_all()
    hero = build_hero_json(args.base, args.out)
    print(json.dumps({"evaluate": res, "hero_stats": hero["stats"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
