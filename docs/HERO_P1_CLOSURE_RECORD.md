# Hero 模块 P1 修复收口记录

- **收口时间**：2026-08-17 01:59 (GMT+8)
- **项目**：FilmCollector 影视采集器（Hero 主视觉模块）
- **收口结论**：`HERO_FINAL_AUDIT = PASS`（P1 缺陷已修复并验证通过）

---

## 一、本轮授权的修改（仅 1 处）

| 文件 | 行号 | 改动 |
|------|------|------|
| `tools/verify_hero.py` | 第 34 行 | `build_hero_json` 的 base 参数由 `"https://example.com/repo"` 改为 `"https://example.com"` |

**根因**：旧 base 已自带 `/repo`，而 `_copy_hero_image`（`backend/core/hero.py:598`）返回的相对路径是 `"repo/hero/<md5>.jpg"`，拼接后生成双层 `/repo/repo/hero/...`，导致部署后 Hero 图片 404。
**修复后**：base 取站点根（与真实生产管线 `publisher.py` 的 base 语义一致），最终公开 URL 为单层 `https://example.com/repo/hero/<md5>.jpg`。

---

## 二、最终验证项（全部 PASS）

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | `verify_hero.py:34` 为站点根 base，无重复 `/repo` | ✅ `"https://example.com"` |
| 2 | `hero.json` 中 `/repo/repo/` 数量 | ✅ 0 |
| 3 | `repo/hero/` 资源 ↔ `hero.json` 引用一致 | ✅ 引用 3、磁盘 3、0 缺失、0 孤儿 |
| 4 | `backend/data/db.json` 影片数 | ✅ 30 部 |
| 5 | Hero 双高清硬门槛 | ✅ `HERO_POSTER_MIN_W=800`、`BACKDROP_MIN_W=1280`、`image_resolution_ok = has_hero_poster and has_backdrop`（常量与逻辑未变） |
| 6 | `tools/test_hero_p1.py` 回归 | ✅ 6/6 PASS（P1-1-A/B/C/D、P1-2-A/B） |
| 7 | `verify_hero.py` 验收（真实 + 合成 A–F–G） | ✅ 真实 30 部（`hero_eligible=1`）+ 合成 7/7 PASS |
| 8 | 新增 P0/P1/P2 | ✅ 无 |
| 9 | 本轮非授权修改 | ✅ 无（仅 `verify_hero.py` 于本轮回改；db.json/hero.json/repo/hero 的变更均来自上轮已授权修复运行） |

**资格判定实证**：Wings（poster 2056×3000 + backdrop 6000×1465）仍 `eligible=True`；Elephants Dream（poster 4242×6000 但无 backdrop）仍因 `no_backdrop` 正确落选。

---

## 三、严守的边界

- 未修改 `backend/core/hero.py`、`tools/hero_fetch.py`、`tools/test_hero_p1.py`、`backend/core/publisher.py`、`json_gen.py`。
- 未修改 poster/cover/episodes 契约；未改动双高清硬门槛。
- 未接入 TMDB Key；未扩大采集源。
- 未处理 P2；未处理 APK 侧 `hero.json` 消费层。
- 未碰 HotOps / 网盘大厅 / LUMFLIX APK 项目。
- 未做电视端实机测试；未进入新 Phase。
- **未 commit、未 push、未 release。**

---

## 四、已知遗留（非本轮范围，按需后续授权）

- **APK 集成**：`docs/LUMFLIX_HERO_INTEGRATION_AUDIT.md` 结论仍为 `BLOCKED`——APK 侧零引用 `hero.json`，需后续单独授权处理。
- **P2 项**：`docs/HERO_COLLECTOR_PIPELINE_AUDIT.md` 记录 7 个 P2，本轮未处理。
- **沙箱环境提示**：运行 `verify_hero.py` 时 `hero.py:613` 的 `shutil.rmtree(tvbox-dist/repo/hero)` 偶被 Windows 沙箱 safe-delete 拦截（回收站不可用，fail-closed），重试时沙箱放行即通过；属执行环境限制，非代码缺陷。

---

*本记录为只读收口文档，不含任何代码改动。*
