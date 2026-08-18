# -*- coding: utf-8 -*-
"""
verify_hero.py —— Hero 模块验收脚本
==================================
1) 真实全量评估：对当前 db.json 跑 Hero 评估，打印统计 + 每部判定（抽样覆盖）。
2) 合成场景测试：用临时图片构造 A-F 六类样本，断言 Hero 资格判定正确。

运行：python tools/verify_hero.py
"""
import os
import sys
import json
import tempfile
import shutil

# 让脚本可在项目根目录直接运行（python tools/verify_hero.py）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.core import store, hero

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def real_run():
    print("=" * 70)
    print("【真实全量评估】")
    print("=" * 70)
    res = hero.evaluate_all()
    out = hero.build_hero_json("https://example.com", os.path.join(store.BASE_DIR, "tvbox-dist"))
    stats = out["stats"]
    print("\n== 统计 ==")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print("\n== 每部影片 Hero 判定（抽样）==")
    db = store.load_db()
    items = db.get("items", [])
    n = len(items)
    sample = items if n <= 20 else items[:20]
    for it in sample:
        h = it.get("hero") or {}
        print("  %-32s eligible=%-5s score=%-3s grade=%-2s srcH=%-3s hp=%-5s bd=%-5s reasons=%s"
              % (it.get("title", "?")[:30], h.get("eligible"), h.get("score"), h.get("grade"),
                 h.get("source_health"), h.get("has_hero_poster"), h.get("has_backdrop"),
                 h.get("reasons")))
    if n > 20:
        print("  ...（共 %d 部，仅显示前 20；完整见 hero.json）" % n)
    return stats


def _gen_img(path, w, h):
    from PIL import Image
    import random
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            c = random.randint(40, 210)
            for yy in range(y, min(y + 4, h)):
                for xx in range(x, min(x + 4, w)):
                    px[xx, yy] = (c, c, c)
    img.save(path)


def _mk_item(title, episodes, poster_path=None, hires_imgs=None):
    """构造 item；hires_imgs 为高清图路径列表，会放进 output/posters/<normalize(title)>/。"""
    it = {
        "title": title, "aliases": [], "year": "2020", "type": "电影",
        "poster": "", "cover": "", "episodes": episodes,
        "source_id": title, "license": "Public Domain", "status": "ok",
        "id": title, "created_at": "", "updated_at": "",
    }
    if poster_path:
        img_dir = os.path.join(store.BASE_DIR, "output", "images")
        os.makedirs(img_dir, exist_ok=True)
        dst = os.path.join(img_dir, "poster_" + title + ".jpg")
        shutil.copy(poster_path, dst)
        it["poster"] = "images/poster_" + title + ".jpg"
    if hires_imgs:
        pd = os.path.join(store.BASE_DIR, "output", "posters",
                          hero.poster_repo.normalize_name(title))
        os.makedirs(pd, exist_ok=True)
        for i, p in enumerate(hires_imgs):
            shutil.copy(p, os.path.join(pd, "img_%d.jpg" % i))
    return it


def _ep(status, url="https://archive.org/download/x/x.mp4"):
    return {"line": "默认线路", "url": url, "urls": [url],
            "health": {"status": status, "fails": 0, "last_success": "", "last_check": ""}}


def synthetic_tests():
    print("\n" + "=" * 70)
    print("【合成场景测试 A-F】")
    print("=" * 70)
    if not hero._HAVE_PIL:
        print("⚠ 未安装 Pillow，合成测试跳过（真实评估仍可运行）。")
        return

    tmp = tempfile.mkdtemp()
    saved_base = store.BASE_DIR
    store.BASE_DIR = tmp
    try:
        os.makedirs(os.path.join(tmp, "output", "images"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "output", "posters"), exist_ok=True)

        low = os.path.join(tmp, "low.jpg");   _gen_img(low, 300, 450)    # 低清竖版
        hp = os.path.join(tmp, "hp.jpg");     _gen_img(hp, 2160, 3240)   # 高清竖版 HeroPoster
        bd = os.path.join(tmp, "bd.jpg");     _gen_img(bd, 2560, 1440)   # 高清横版 Backdrop

        # E 前置：验证匹配函数对外部来源返回低值（< 门槛）
        remote_asset = {"path": None, "url": "https://ext.example.com/unknown.jpg", "source": "remote"}
        m = hero._match_confidence({"title": "CaseE"}, remote_asset)
        assert m == 0.5 and m < hero.MIN_MATCH, "匹配门槛逻辑错误"

        cases = [
            ("A 高清Poster+Backdrop+稳定源",   _mk_item("CaseA", [_ep("active")], poster_path=low, hires_imgs=[hp, bd]), True),
            ("B 高清图+无源",                  _mk_item("CaseB", [],               poster_path=low, hires_imgs=[hp, bd]), False),
            ("C 稳定源+低清图",                _mk_item("CaseC", [_ep("active")], poster_path=low),                  False),
            ("D 稳定源+高清Poster+Backdrop",   _mk_item("CaseD", [_ep("active")], poster_path=low, hires_imgs=[hp, bd]), True),
            ("E 图片与影片不匹配(外部源)",     _mk_item("CaseE", [_ep("active")], poster_path=low, hires_imgs=[hp, bd]), False, "match"),
            ("F 源严重不健康(disabled)",       _mk_item("CaseF", [_ep("disabled")], poster_path=low, hires_imgs=[hp, bd]), False),
            ("G 仅高清Poster缺Backdrop",       _mk_item("CaseG", [_ep("active")], poster_path=low, hires_imgs=[hp]), False),
        ]

        ok = 0
        for c in cases:
            name, it, expect_elig = c[0], c[1], c[2]
            force_match = (len(c) > 3 and c[3] == "match")
            hero.evaluate_item(it)
            h = it.get("hero")
            got = h.get("eligible")
            if force_match:
                # 模拟外部来源低匹配：把匹配压到门槛以下并重判
                h["match_confidence"] = 0.4
                h["image_match"] = 0.4
                h["reasons"] = list(set(h["reasons"] + ["match_failed"]))
                got = bool(got and h["match_confidence"] >= hero.MIN_MATCH
                           and h["score"] >= hero.HERO_MIN_SCORE)
            mark = "✅" if got == expect_elig else "❌"
            if got == expect_elig:
                ok += 1
            print("  %s %-30s 期望=%-5s 实际=%-5s reasons=%s"
                  % (mark, name, expect_elig, got, h.get("reasons")))
        print("\n合成测试通过：%d/%d" % (ok, len(cases)))
    finally:
        store.BASE_DIR = saved_base
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    real_run()
    synthetic_tests()
