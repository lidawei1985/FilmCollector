# -*- coding: utf-8 -*-
"""
test_hero_contract.py —— Hero 数据契约测试（FC-HERO-P2-006）
===========================================================

验证 tvbox-dist/hero.json 对外契约的稳定不变量（contract invariants）。
**不修改任何代码行为、不触碰真实 db.json / tvbox-dist**（用真实代码在临时目录重新生成并断言）。

核心契约（详见 docs/HERO_CONTRACT.md）：
  C1. hero_eligible 是 Hero 展示的唯一判决依据（single source of truth）。
  C2. 任一 hero_eligible=True 的条目，hero_poster 与 backdrop 必须【同时】非空
      （双高清资格：高清竖版海报 >=800px 且 高清横版主视觉 >=1280px）。
  C3. 非 eligible 条目的 hero_poster / backdrop 【可能】非空（asset 级候选图，partial assets）；
      契约允许其存在，仅要求消费端忽略。本测试【报告】此类条目，不强制为 0。
  C4. hero.json 绝不出现内部相对路径（output/ images/ posters/）。
  C5. 顶层结构：code==1；stats.hero_eligible + stats.hero_ineligible == stats.total == len(heroes)。
  C6. 每个 hero 含全部必需字段；eligible 的 reasons==[]，ineligible 的 reasons 非空。

运行：
  python tools/test_hero_contract.py
"""
import os
import sys
import json
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.core import store, hero

REQUIRED_FIELDS = [
    "hero_eligible", "movie_id", "title", "source_available", "source_health",
    "sources_count", "hero_score", "hero_grade", "hero_poster", "backdrop",
    "hero_poster_width", "hero_poster_height", "backdrop_width", "backdrop_height",
    "match_confidence", "image_quality", "image_resolution", "reasons",
]
INTERNAL_PATH_MARKERS = ("output/", "images/", "posters/")


def _build_in_temp():
    """用真实代码从真实 db.json 重新生成 hero.json 到临时目录（不写真实文件）。"""
    tmp = tempfile.mkdtemp(prefix="hero_contract_")
    orig_log = store.log
    store.log = lambda *a, **k: None  # 不写真实日志
    try:
        out_dir = os.path.join(tmp, "tvbox-dist")
        hero.build_hero_json("https://example.com/pages", out_dir)
        return json.load(open(os.path.join(out_dir, "hero.json"), encoding="utf-8"))
    finally:
        store.log = orig_log
        shutil.rmtree(tmp, ignore_errors=True)


def _check_invariants(hj, label):
    """对一份 hero.json 结构断言 C2/C4/C5/C6，并报告 C3。返回 (passed, msgs)。"""
    msgs = []
    heroes = hj.get("heroes", [])
    stats = hj.get("stats", {})

    # C5：顶层结构 + stats 计数一致性
    assert hj.get("code") == 1, "%s: code 应为 1" % label
    n = len(heroes)
    elig_n = sum(1 for x in heroes if x.get("hero_eligible"))
    inel_n = n - elig_n
    assert stats.get("total") == n, "%s: stats.total(%s) != len(heroes)(%s)" % (label, stats.get("total"), n)
    assert stats.get("hero_eligible") == elig_n, "%s: stats.hero_eligible(%s) != 实际(%s)" % (label, stats.get("hero_eligible"), elig_n)
    assert stats.get("hero_ineligible") == inel_n, "%s: stats.hero_ineligible(%s) != 实际(%s)" % (label, stats.get("hero_ineligible"), inel_n)
    msgs.append("  [%s] C5 stats 计数一致：total=%d eligible=%d ineligible=%d" % (label, n, elig_n, inel_n))

    # C6：必需字段齐全 + reasons 契约
    for i, x in enumerate(heroes):
        missing = [f for f in REQUIRED_FIELDS if f not in x]
        assert not missing, "%s: hero[%d]=%r 缺必需字段 %s" % (label, i, x.get("title"), missing)
        if x.get("hero_eligible"):
            assert x.get("reasons") == [], "%s: eligible 条目 reasons 必须为空，实际=%s" % (label, x.get("reasons"))
        else:
            assert x.get("reasons"), "%s: ineligible 条目 reasons 必须非空，title=%r" % (label, x.get("title"))
    msgs.append("  [%s] C6 必需字段齐全 + reasons 契约成立" % label)

    # C2：eligible ⇒ hero_poster 与 backdrop 同时非空
    empty_elig = [x for x in heroes if x.get("hero_eligible") and (not x.get("hero_poster") or not x.get("backdrop"))]
    assert not empty_elig, "%s: 存在 eligible 但字段为空的条目：%s" % (label, [x.get("title") for x in empty_elig])
    msgs.append("  [%s] C2 eligible⇒hero_poster+backdrop 同时非空：通过（eligible=%d）" % (label, elig_n))

    # C4：无内部相对路径泄漏
    blob = json.dumps(hj, ensure_ascii=False)
    leaked = [m for m in INTERNAL_PATH_MARKERS if m in blob]
    assert not leaked, "%s: hero.json 泄漏内部相对路径标记 %s" % (label, leaked)
    msgs.append("  [%s] C4 无内部相对路径泄漏：通过" % label)

    # C3：报告非 eligible 但字段非空的 partial 条目（契约允许，仅报告）
    partial = [x for x in heroes if (not x.get("hero_eligible")) and (x.get("hero_poster") or x.get("backdrop"))]
    if partial:
        msgs.append("  [%s] C3 非 eligible 但含 partial 字段（契约允许，消费端须忽略）：" % label)
        for x in partial:
            msgs.append("      - %r hp=%s bd=%s reasons=%s"
                        % (x.get("title"), bool(x.get("hero_poster")), bool(x.get("backdrop")), x.get("reasons")))
    else:
        msgs.append("  [%s] C3 无非 eligible 的 partial 字段（当前数据未触发，属正常）" % label)

    return True, msgs


def test_contract_on_regenerated():
    """对真实代码重新生成的 hero.json 断言全部不变量（权威，反映当前代码行为）。"""
    print("【C】契约测试 · 重新生成（真实代码 + 真实 db.json，临时目录）")
    hj = _build_in_temp()
    _check_invariants(hj, "regen")
    print("  [regen] 全部不变量 PASS")


def test_contract_on_deployed():
    """对已部署的 tvbox-dist/hero.json 断言同样不变量（确认线上契约符合规范）。"""
    print("【D】契约测试 · 已部署产物（tvbox-dist/hero.json，只读）")
    path = os.path.join(store.BASE_DIR, "tvbox-dist", "hero.json")
    if not os.path.isfile(path):
        print("  [deployed] 未找到 tvbox-dist/hero.json，跳过（不影响 regen 结论）")
        return
    hj = json.load(open(path, encoding="utf-8"))
    _check_invariants(hj, "deployed")
    print("  [deployed] 全部不变量 PASS")


if __name__ == "__main__":
    print("=== Hero 数据契约测试（FC-HERO-P2-006）===")
    test_contract_on_regenerated()
    test_contract_on_deployed()
    print("ALL HERO CONTRACT TESTS PASS")
