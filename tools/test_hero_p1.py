# -*- coding: utf-8 -*-
"""
test_hero_p1.py —— Hero 两个 P1 定点回归测试（仅验证 P1-1 / P1-2 修复，不改动业务）

运行：
  python tools/test_hero_p1.py

覆盖：
  P1-1：整轮采集 0 个成功资产（超时/源站临时故障）时，旧高清 poster/backdrop 必须保留，
        不得被清理；正常成功采集的更新/清理语义保持不变。
  P1-2：_copy_hero_image 复制失败时，hero.json 的 hero_poster / backdrop 字段必须置空，
        绝不把 output/posters/...、output/...、images/... 等内部相对路径写入对外契约。
"""
import os
import sys
import json
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image

from backend.core import hero, store
import tools.hero_fetch as hf


def _make_img(path, w, h):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (w, h), (120, 90, 60)).save(path, "JPEG")


def _patch_failing_providers():
    """把图源替换为『全部失败』，模拟整轮源站故障/超时（不触网）。"""

    def _fail(clean_title, year, mtype, api_key=None):
        raise hf._Fail("MATCH_UNCERTAIN", "simulated total failure")

    hf.provider_tmdb = _fail
    hf.provider_wikimedia = _fail


# ---------------- P1-1 ----------------
def test_p1_1_total_failure_preserves_old():
    """P1-1-A：整轮 0 成功资产 → 旧高清 poster/backdrop 必须保留。"""
    tmp = tempfile.mkdtemp(prefix="hero_p1_")
    orig_base = store.BASE_DIR
    store.BASE_DIR = tmp
    hf.store.BASE_DIR = tmp
    try:
        film = os.path.join(tmp, "output", "posters", "OldFilm")
        old_p = os.path.join(film, "poster_1000x1500_old.jpg")
        old_b = os.path.join(film, "backdrop_2000x1000_old.jpg")
        _make_img(old_p, 1000, 1500)
        _make_img(old_b, 2000, 1000)

        _patch_failing_providers()
        cache = hf.load_cache()
        stats = {"scanned": 0, "posters_found": 0, "backdrops_found": 0, "hd_posters": 0,
                 "hd_backdrops": 0, "matched": 0, "cache_hits": 0, "duplicates": 0,
                 "failures": {}, "reject_reasons": {}}
        item = {"title": "OldFilm", "year": "", "type": "", "episodes": [], "id": "test-oldfilm"}
        hf.collect_one(item, None, cache, stats)

        assert os.path.isfile(old_p), "旧高清 Poster 被误删！"
        assert os.path.isfile(old_b), "旧高清 Backdrop 被误删！"
        print("  [P1-1-A] 整轮失败保留旧资产 ... PASS")
    finally:
        store.BASE_DIR = orig_base
        hf.store.BASE_DIR = orig_base
        shutil.rmtree(tmp, ignore_errors=True)


def test_p1_1_helper_unit():
    """P1-1-B/C/D：_cleanup_stale_assets 单元逻辑（对应种类清理 + 整轮无成功不清理）。"""
    tmp = tempfile.mkdtemp(prefix="hero_p1c_")
    try:
        d = os.path.join(tmp, "film")
        os.makedirs(d, exist_ok=True)
        pa = os.path.join(d, "poster_1000x1500_a.jpg")
        pb = os.path.join(d, "poster_1000x1500_b.jpg")
        ba = os.path.join(d, "backdrop_2000x1000_a.jpg")
        bb = os.path.join(d, "backdrop_2000x1000_b.jpg")
        _make_img(pa, 1000, 1500)
        _make_img(pb, 1000, 1500)
        _make_img(ba, 2000, 1000)
        _make_img(bb, 2000, 1000)

        # ① 全部 None → 不删任何文件
        hf._cleanup_stale_assets(d, None, None)
        assert os.path.isfile(pa) and os.path.isfile(ba), "无成功资产不应清理"
        print("  [P1-1-B] 无成功资产不清理 ... PASS")

        # ② 仅 saved_poster → 删旧 poster，保留全部 backdrop
        hf._cleanup_stale_assets(d, "poster_1000x1500_a.jpg", None)
        assert not os.path.isfile(pb), "旧 poster 应被清理"
        assert os.path.isfile(pa), "新 poster 应保留"
        assert os.path.isfile(ba) and os.path.isfile(bb), "backdrop 不应被清理"
        print("  [P1-1-C] 仅存新poster→清旧poster/保留backdrop ... PASS")

        # ③ 两者都新 → 清其余、留新
        pc = os.path.join(d, "poster_1000x1500_c.jpg")
        bc = os.path.join(d, "backdrop_2000x1000_c.jpg")
        _make_img(pc, 1000, 1500)
        _make_img(bc, 2000, 1000)
        hf._cleanup_stale_assets(d, "poster_1000x1500_a.jpg", "backdrop_2000x1000_a.jpg")
        assert os.path.isfile(pa) and os.path.isfile(ba), "新资产应保留"
        assert not os.path.isfile(bb), "旧 backdrop 应被清理"
        assert not os.path.isfile(pc), "非 keep 的 poster 应被清理"
        print("  [P1-1-D] 两者新→清其余 ... PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- P1-2 ----------------
def test_p1_2_copy_failure_blanks_field():
    """P1-2-A：copy 失败 → 字段置空，且 hero.json 绝不出现内部相对路径。"""
    tmp = tempfile.mkdtemp(prefix="hero_p1b_")
    orig_base = store.BASE_DIR
    store.BASE_DIR = tmp
    hero.store.BASE_DIR = tmp
    try:
        items = [{
            "id": "mov-1", "title": "Test Movie", "type": "电影", "year": "2020",
            "poster": "images/poster_Test.jpg", "episodes": [],
            "hero_poster_url": "output/posters/Test Movie/poster_2056x3000_wiki.jpg",
            "hero_poster_width": 2056, "hero_poster_height": 3000,
            "backdrop_url": "output/posters/Test Movie/backdrop_6000x1465_wiki.jpg",
            "backdrop_width": 6000, "backdrop_height": 1465,
            "hero": {"eligible": True, "score": 93, "grade": "A", "source_available": True,
                     "source_health": 88, "sources_count": 1, "image_available": True,
                     "image_quality": 90, "image_resolution": "backdrop", "image_match": 1.0,
                     "match_confidence": 1.0, "has_hero_poster": True, "has_backdrop": True,
                     "reasons": []},
        }]
        # 注入假数据（store.DB_PATH 为 import 期固定常量，不能直接写文件覆盖，改为 monkeypatch）
        orig_load = store.load_db
        store.load_db = lambda: {"items": items, "logs": []}
        # 禁止 build_hero_json 内的 store.log → store.save_db 把假 db 写回真实 DB_PATH
        orig_save = store.save_db
        store.save_db = lambda *a, **k: None
        # 强制 _copy_hero_image 失败（返回 None）
        orig_copy = hero._copy_hero_image
        hero._copy_hero_image = lambda *a, **k: None
        out_dir = os.path.join(tmp, "tvbox-dist")
        try:
            hero.build_hero_json("https://example.com/pages", out_dir)
        finally:
            hero._copy_hero_image = orig_copy
            store.load_db = orig_load
            store.save_db = orig_save

        hj = json.load(open(os.path.join(out_dir, "hero.json"), encoding="utf-8"))
        h = hj["heroes"][0]
        assert h["hero_poster"] == "", "copy 失败 hero_poster 必须为空，实际=%r" % h["hero_poster"]
        assert h["backdrop"] == "", "copy 失败 backdrop 必须为空，实际=%r" % h["backdrop"]
        blob = json.dumps(hj, ensure_ascii=False)
        assert "output/posters" not in blob, "hero.json 泄漏内部相对路径 output/posters"
        assert "output/" not in blob, "hero.json 泄漏内部相对路径 output/"
        assert "images/" not in blob, "hero.json 泄漏内部相对路径 images/"
        print("  [P1-2-A] copy 失败字段置空且无私密路径 ... PASS")
    finally:
        store.BASE_DIR = orig_base
        hero.store.BASE_DIR = orig_base
        shutil.rmtree(tmp, ignore_errors=True)


def test_p1_2_copy_success_writes_url():
    """P1-2-B：copy 成功 → 写入公网 URL，repo/hero 落盘 2 张。"""
    tmp = tempfile.mkdtemp(prefix="hero_p1s_")
    orig_base = store.BASE_DIR
    store.BASE_DIR = tmp
    hero.store.BASE_DIR = tmp
    try:
        film = os.path.join(tmp, "output", "posters", "Test Movie")
        _make_img(os.path.join(film, "poster_2056x3000_wiki.jpg"), 2056, 3000)
        _make_img(os.path.join(film, "backdrop_6000x1465_wiki.jpg"), 6000, 1465)
        items = [{
            "id": "mov-1", "title": "Test Movie", "type": "电影", "year": "2020",
            "poster": "images/poster_Test.jpg", "episodes": [],
            "hero_poster_url": "output/posters/Test Movie/poster_2056x3000_wiki.jpg",
            "hero_poster_width": 2056, "hero_poster_height": 3000,
            "backdrop_url": "output/posters/Test Movie/backdrop_6000x1465_wiki.jpg",
            "backdrop_width": 6000, "backdrop_height": 1465,
            "hero": {"eligible": True, "score": 93, "grade": "A", "source_available": True,
                     "source_health": 88, "sources_count": 1, "image_available": True,
                     "image_quality": 90, "image_resolution": "backdrop", "image_match": 1.0,
                     "match_confidence": 1.0, "has_hero_poster": True, "has_backdrop": True,
                     "reasons": []},
        }]
        orig_load = store.load_db
        store.load_db = lambda: {"items": items, "logs": []}
        # 禁止 store.log → store.save_db 把假 db 写回真实 DB_PATH
        orig_save = store.save_db
        store.save_db = lambda *a, **k: None
        out_dir = os.path.join(tmp, "tvbox-dist")
        try:
            hero.build_hero_json("https://example.com/pages", out_dir)
        finally:
            store.load_db = orig_load
            store.save_db = orig_save

        hj = json.load(open(os.path.join(out_dir, "hero.json"), encoding="utf-8"))
        h = hj["heroes"][0]
        assert h["hero_poster"].startswith("https://example.com/pages/repo/hero/"), h["hero_poster"]
        assert h["backdrop"].startswith("https://example.com/pages/repo/hero/"), h["backdrop"]
        hd = os.path.join(out_dir, "repo", "hero")
        n = len([f for f in os.listdir(hd) if f.endswith(".jpg")])
        assert n == 2, "repo/hero 应落盘 2 张，实际 %d" % n
        print("  [P1-2-B] copy 成功写入公网 URL + 落盘 ... PASS")
    finally:
        store.BASE_DIR = orig_base
        hero.store.BASE_DIR = orig_base
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=== Hero P1 定点回归测试 ===")
    test_p1_1_total_failure_preserves_old()
    test_p1_1_helper_unit()
    test_p1_2_copy_failure_blanks_field()
    test_p1_2_copy_success_writes_url()
    print("ALL P1 TESTS PASS")
