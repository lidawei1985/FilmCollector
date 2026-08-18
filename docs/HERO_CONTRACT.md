# Hero 数据契约（hero.json）规范

- **范围**：FilmCollector 采集器对外发布的 `tvbox-dist/hero.json`——被 LUMFLIX 等消费端读取的主视觉数据源。
- **权威实现**：`backend/core/hero.py`
  - `evaluate_item()`（L395–524）：单部影片资格评估，写回 `item["hero"]` / `hero_poster_url` / `backdrop_url` 等字段。
  - `build_hero_json()`（L604–669）：生成对外 `hero.json` 契约。
- **本文件性质**：文档级契约规范（对应任务 **FC-HERO-P2-006** 交付物），**不修改任何代码行为**。
- **配套契约测试**：`tools/test_hero_contract.py`（用真实代码从真实 `db.json` 重新生成并断言不变量）。
- **关联文档**：`docs/HERO_COLLECTOR_PIPELINE_AUDIT.md` 第十节（契约初稿，本文件为其正式独立版）。

---

## 1. 一句话契约（最高优先级）

> **`hero_eligible` 是 Hero 展示的唯一判决依据（single source of truth）。消费端「只认 `hero_eligible`」。**

凡 `hero_eligible == false` 的条目，**一律不得**作为 Hero 主视觉展示，无论其 `hero_poster` / `backdrop` 字段是否为空。`reasons[]` 仅用于说明未入选原因，不作为展示依据。

---

## 2. 顶层结构

```json
{
  "code": 1,
  "msg": "ok",
  "updated_at": "YYYY-MM-DDTHH:MM:SS",
  "stats": { ... },
  "heroes": [ { ... }, ... ]
}
```

- `code`：固定 `1`（成功生成）。
- `updated_at`：生成时刻（ISO 8601，本地时区）。
- `stats`：全量汇总（见 §5）；契约要求 `stats.hero_eligible + stats.hero_ineligible == stats.total == len(heroes)`。
- `heroes`：每部影片一条，按 `(not hero_eligible, -hero_score)` 排序——eligible 优先、分数降序。

---

## 3. 单条 hero 字段表

| 字段 | 类型 | 必填 | 允许空 | 含义 / 契约约束 |
|------|------|------|--------|----------------|
| `hero_eligible` | bool | ✅ | — | **唯一展示判决**。`true` = 完整双高清资格（见 §4/§6） |
| `movie_id` | str | ✅ | — | = `item["id"]`（UUID），关联主键 |
| `title` | str | ✅ | — | 片名 |
| `source_available` | bool | ✅ | — | 有可用播放源 |
| `source_health` | int 0–100 | ✅ | — | Hero 视角源健康分（见 `hero.py:_hero_source_health`） |
| `sources_count` | int | ✅ | — | 可用源数 |
| `hero_score` | int 0–100 | ✅ | — | 综合分（源30% + 图35% + Backdrop+10 + HeroPoster+5 + 匹配15% + 多源≤+4.5） |
| `hero_grade` | str | ✅ | — | `S/A/B/F`（≥95 / ≥85 / ≥70 / <70） |
| `hero_poster` | str(url) | — | ✅(`""`) | 公网 URL；**非 eligible 时可能非空（候选图），见 §4** |
| `backdrop` | str(url) | — | ✅(`""`) | 公网 URL；同上 |
| `hero_poster_width` / `hero_poster_height` | int | ✅ | — | 真实像素（0 = 无） |
| `backdrop_width` / `backdrop_height` | int | ✅ | — | 真实像素（0 = 无） |
| `match_confidence` | float 0–1 | ✅ | — | 图片↔影片匹配置信 |
| `image_quality` | int 0–100 | ✅ | — | 最佳候选图质量分 |
| `image_resolution` | str | ✅ | — | `"backdrop"` / `"hero_poster"` / `"none"` |
| `reasons[]` | list[str] | ✅ | — | 淘汰原因码；**eligible 必为 `[]`，ineligible 必非空**（见 §7） |

> 所有对外图片地址均为 `https://<base>/repo/hero/<md5>.jpg` 形式的公网 URL；`build_hero_json` 在 `_copy_hero_image` 失败时将该字段置空（P1-2 修复），**绝不会**把 `output/`、`images/`、`posters/` 等内部相对路径写入契约。

---

## 4. 关键契约：非 eligible 条目的 `hero_poster` / `backdrop` 可能非空

**这是本规范要重点约束、也是 FC-HERO-P2-006 的核心风险点。**

- `hero_poster_url` / `backdrop_url` 在 **asset 级达标**时即写入（见 `evaluate_item` L497–512）：
  - `hero_poster_url` 写入条件：`best_poster` 存在且 `width >= 800`（`HERO_POSTER_MIN_W`）。
  - `backdrop_url` 写入条件：`best_backdrop` 存在且 `width >= 1280`（`BACKDROP_MIN_W`）。
- 而 `hero_eligible == true` 要求 **两者同时达标**：`image_resolution_ok = has_hero_poster and has_backdrop`（L428）。
- 因此当某片**只有高清竖版海报（缺横版主视觉）**或**只有横版主视觉（缺高清海报）**时：
  - 对应 `hero_poster` / `backdrop` 字段**非空**（候选图真实可用）；
  - 但 `hero_eligible == false`（整体未达「双高清」资格），`reasons` 含 `no_backdrop` / `no_hero_poster`。
- **契约结论**：
  - 非 eligible 条目的非空 `hero_poster` / `backdrop` 仅代表 **asset 级候选图**，**不代表合格 Hero**。
  - 消费端**绝不可**仅凭这两个字段非空就展示 Hero；**必须且只能以 `hero_eligible == true` 为展示开关**。

> 反方向（eligible ⇒ 两个字段同时非空）恒成立，见契约不变量 C2（由 `tools/test_hero_contract.py` 断言）。

---

## 5. `stats` 汇总字段

```json
{
  "total": 33,
  "with_source": 33,
  "with_poster": 33,
  "with_hero_poster": 2,
  "with_backdrop": 1,
  "hero_eligible": 1,
  "hero_ineligible": 32,
  "reasons": { "no_backdrop": 32, "no_hero_poster": 31, "quality_insufficient": 1 }
}
```

- `with_hero_poster` / `with_backdrop`：asset 级达标的条目数（可大于 `hero_eligible`，因为 eligible 还需两者同时达标）。
- `reasons{}`：全量淘汰原因计数（同一片可计入多个原因码）。

---

## 6. eligible 资格公式（实现镜像，仅供消费端理解）

```
eligible = (source_available
            and source_health   >= MIN_SOURCE_HEALTH(40)
            and image_available
            and image_resolution_ok            # has_hero_poster and has_backdrop
            and match_confidence >= MIN_MATCH(0.6)
            and hero_score       >= HERO_MIN_SCORE(70)
            and not abnormal                   # 如缺片名
            and not image_damaged)
```

阈值常量见 `hero.py` L41–45：`HERO_MIN_SCORE=70`、`MIN_SOURCE_HEALTH=40`、`MIN_MATCH=0.6`、`HERO_POSTER_MIN_W=800`、`BACKDROP_MIN_W=1280`。

---

## 7. `reasons[]` 原因码表

每个原因码对应一类未入选因素；eligible 条目 `reasons == []`，ineligible 条目 `reasons` 至少含一个：

| 原因码 | 含义 |
|--------|------|
| `no_source` | 无可用播放源 |
| `source_unhealthy` | 源健康分低于 `MIN_SOURCE_HEALTH` |
| `no_image` | 无任何候选图 |
| `no_hero_poster` | 缺高清竖版海报（无 / 宽 < 800px） |
| `no_backdrop` | 缺高清横版主视觉（无 / 宽 < 1280px） |
| `match_failed` | 匹配置信低于 `MIN_MATCH` |
| `quality_insufficient` | 最佳图质量分 < 50 |
| `image_damaged` | 图片解析/损坏异常 |
| `missing_title` | 缺片名（异常片） |
| `evaluate_error:...` | 评估过程异常（代码层兜底） |

---

## 8. 消费端（如 LUMFLIX）必须遵守

1. 展示 Hero 前：`if not hero["hero_eligible"]: skip`。
2. 仅当 `hero_eligible == true` 时，才读取 `hero_poster` / `backdrop` 作为主视觉素材。
3. **不要**依据 `hero_poster` / `backdrop` 非空来推断资格（见 §4）。
4. `reasons[]` 仅用于诊断/日志，不作为展示依据。
5. 遇到 `hero_eligible == false`，回退到普通 `poster`（`item.poster`）或列表/网格视图，不要展示残缺主视觉。

---

## 9. 当前实证（2026-08-17 真实 `db.json`，33 部）

- `eligible = 1`，`ineligible = 32`。
- eligible 条目：`hero_poster` 与 `backdrop` **均非空** ✅（不变量 C2 成立）。
- **非 eligible 但 `hero_poster` 非空：1 条** —— `Elephants Dream`（`reasons = ["no_backdrop"]`）。这正是 §4 描述的「asset 级候选图但整体未入选」情形，须按契约忽略其 `hero_poster`。
- 无内部相对路径泄漏（`output/` `images/` `posters/` 均不出现于契约）。

> 实证随时可由 `tools/test_hero_contract.py` 复现与断言。

---

## 10. 契约测试

`tools/test_hero_contract.py` 用真实代码从真实 `db.json` 重新生成 `hero.json`（写入**临时目录**，不触碰真实 `db.json` / `tvbox-dist`），断言以下不变量：

- **C2**：`hero_eligible == true` ⇒ `hero_poster` 与 `backdrop` 同时非空。
- **C4**：契约 JSON 中无任何内部相对路径泄漏（`output/` `images/` `posters/`）。
- **C5**：`stats.hero_eligible` / `stats.hero_ineligible` 与 `heroes` 实际计数一致。
- **C6**：每个 hero 含全部必需字段；eligible 的 `reasons == []`，ineligible 的 `reasons` 非空。
- **C3**（报告项，不强制为 0）：列出非 eligible 但 `hero_poster` / `backdrop` 非空的 partial 条目——契约**允许**其存在，仅要求消费端忽略。

运行：

```bash
python tools/test_hero_contract.py
```

---

## 11. 变更记录

- 2026-08-17：FC-HERO-P2-006 落地——新增本契约规范 + `tools/test_hero_contract.py`。仅文档与测试，未改动任何运行时代码行为。
