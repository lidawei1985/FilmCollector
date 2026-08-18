# FilmCollector · Hero 高清素材链路只读审计报告（采集器侧）

> 审计范围：**影视采集器项目内部链路**
> `tools/hero_fetch.py → backend/core/hero.py → hero.json → publisher/build_bundle → 最终输出`
> 执行方式：**仅读，不改代码、不删文件、不重构、不构建 APK、不推送**。
> 审计日期：2026-08-17
> 基线状态：合成验收 A–F–G 7/7 PASS；真实 30 部 `hero_eligible = 1`（Wings）。

---

## 〇、链路总览（一张图）

```
[1] 采集                          [2] 评估/生成                       [3] 发布
tools/hero_fetch.py         →    backend/core/hero.py        →    publisher.build_bundle
 collect_one()                    evaluate_item()                  step 7.5 (L517-522)
  ├─ provider_tmdb                ├─ _discover_candidates()        evaluate_all()  → db.json hero*
  ├─ provider_wikimedia             (扫 output/posters/<片名>/       build_hero_json()
  ├─ verify_image() 质量门禁          + output/images)              ├─ 清空 repo/hero (L606-608)
  ├─ URL+image hash 缓存           ├─ _score_image() 评分           ├─ 复制达标图→repo/hero/<md5>.jpg
  └─ 落盘 output/posters/<片名>/    ├─ _match_confidence()          └─ 写 tvbox-dist/hero.json
     poster_*.jpg / backdrop_*.jpg  └─ _compose_score()
                                      ↓
                              item["hero"/"hero_*"/"hero_assets"]
                                      ↓ 持久化
                              backend/data/db.json (权威源)
                                      ↓ 发布
                    https://lidawei1985.github.io/filmcollector-pages/hero.json
                    https://.../repo/hero/<md5>.jpg   （高清图实际地址）
```

**权威数据源 = `backend/data/db.json`**（item 级 `hero*` 字段）。
**对外契约 = `tvbox-dist/hero.json`**（由 `build_hero_json` 从 db 派生，每轮重建）。
Hero 与普通 `data.json`/`vod` 是**平行两份数据**，靠 `movie_id = item["id"]` 关联，绝不混入 vod。

---

## 一、Hero 数据来源（调用链追踪）

| # | 问题 | 答案 | 证据（file:line） |
|---|------|------|-------------------|
| 1 | Hero 最终权威数据源？ | `backend/data/db.json` 中的 item `hero*` 字段；hero.json 是派生产物 | `hero.build_hero_json` 读 `store.load_db()` L602；`evaluate_all` 写回并 `store.save_db` L539 |
| 2 | hero.json 是最终契约还是中间产物？ | **最终契约**（对外给 APK），非中间产物 | `build_hero_json` 输出 `tvbox-dist/hero.json` L655-657 |
| 3 | Hero 数据最终保存在哪里？ | `tvbox-dist/hero.json` + `tvbox-dist/repo/hero/<md5>.jpg`（图）；同时 `db.json` 含 item 级字段 | L655、L588-592 |
| 4 | hero_poster_url 从哪里产生？ | `evaluate_item` 由 `best_poster` 经 `_local_to_rel` 生成相对路径 L497-504；`build_hero_json` 改写为公网 URL L617-620 | hero.py L497-504, L617-620 |
| 5 | backdrop_url 从哪里产生？ | 同 4，`best_backdrop` | hero.py L505-512, L621-624 |
| 6 | hero_eligible 从哪里产生？ | `evaluate_item` 复合布尔判定 L463-467，写入 `item["hero"]["eligible"]` L471 | hero.py L463-467, L471 |
| 7 | hero_score 从哪里产生？ | `_compose_score`（源30%+图35%+Backdrop+10+HeroPoster+5+匹配15%+多源）L371-379，调用 L460 | hero.py L371-379, L460 |
| 8 | Hero 与 item/vod 如何关联？ | 主键 `item["id"]`（UUID，30/30 唯一）；hero.json `movie_id = it.get("id")` L627；图关联用 `output/posters/<normalize(title)>` 目录 L172-184 | hero.py L627, L172-184；db 实测 id 唯一 |
| 9 | 最终输出是否一定含 Hero 数据？ | **是**。`build_bundle` 每轮调用 `build_hero_json` → hero.json 必然生成（即使 0 入选，仍含 `stats`+全量 `heroes` 列表） | publisher.py L517-522 |

> 注：vod（`data.json`/`api.js`）**不含** hero 字段——`json_gen._to_tvbox` 是显式白名单（L50-69），只用 `item.poster`/`item.cover`。Hero 与普通海报契约物理+逻辑双重隔离。

---

## 二、Hero 与普通海报隔离（验证）

| 检查项 | 结论 | 证据 |
|--------|------|------|
| Hero 不覆盖普通 poster | ✅ hero.py 从不写 `item.poster`/`item.cover` | hero.py L469 注释「不触碰 poster/cover/episodes」；`evaluate_item` 仅新增 `hero*` 字段 |
| Hero 不改变普通 poster URL/尺寸/比例/详情页 | ✅ vod_pic 固定来自 `item.poster`（json_gen L53） | `json_gen._to_tvbox` L50-69 |
| Hero 失败不影响普通 poster | ✅ 单部 try 包裹，仅设 `hero.eligible=False` | `evaluate_all` per-item try L533-538 |
| 普通 poster 失败不会错误生成 Hero | ✅ 普通低清 archive poster（~180px）经尺寸门禁 (<800) 不达标 | hero.py L421 `has_hero_poster` 需 ≥800 |
| repo/posters 与 repo/hero 隔离 | ✅ `output/posters`（原始采集）vs `tvbox-dist/repo/hero`（发布副本，md5 命名，每轮清空）物理隔离 | hero.py L604-608 `shutil.rmtree(hero_dir)` |
| 命名不撞车 | ✅ `grab_posters` 命名 `{宽}x{高}_{源}.jpg`（L302，**无** `poster_` 前缀）；`hero_fetch` 命名 `poster_/backdrop_` 前缀（L418）。清理逻辑只删 `poster_/backdrop_` 前缀 → 不误删 grab 产物 | `grab_posters.py` L302；`hero_fetch.py` L591-596 |

**发现的耦合（非缺陷，需知晓）**：`hero.py._discover_candidates` 会扫描 `output/images`（普通 poster 目录，L159-169）作为 `local_ia` 候选源。设计上允许「普通高清 poster 也可服务 Hero」，但若某普通 poster 恰好 ≥800px 宽且比例合适，会被 Hero 选用——这不算绕过质量门禁（仍经 `_score_image` 评分），只是来源多了一条。可接受。

---

## 三、Hero 与影片的关联关系（主键与串图风险）

**主键链路**
- 内部主键：`item["id"]` = UUID（实测 30/30 唯一、稳定）。hero.json `movie_id = it.get("id")`。✅ 稳定。
- 图片目录键：`output/posters/<normalize(title)>`（hero.py L172；hero_fetch L437 `_safe_dir(title)`）——**仅 title，不含 year**。

| 检查项 | 结论 | 风险等级 |
|--------|------|----------|
| 同名不同年份是否可能串图？ | 当前 30 部无重名。但图片目录键 title-only，未来若「Hamlet 1996 / Hamlet 2000」normalize 相同 → 同目录 → 理论串图 | **P2**（目录键应含 year） |
| 不同电影是否错误复用 Hero？ | TMDB 严格匹配 + 同 `movie_id` 天然保证（provider_tmdb L288-305）；Wikimedia 跨 id 资产降匹配至 ≤0.6（hero_fetch L493） | **P2**（见下） |
| 同一电影多源是否重复？ | episodes 多线路 → `_source_health` 取最佳（hero.py L345）；图片 phash 去重（L186-196） | ✅ 无重复 |
| 标题变化是否产生孤儿 Hero？ | 是（磁盘）：旧 `output/posters/<旧title>/` 不被 hero.py 删除；新 title 重新采集前该片 Hero 短暂消失（正确行为），旧目录残留 | **P2**（磁盘残留，不影响正确性） |
| 年份变化是否错误关联？ | `movie_id=id`（UUID 不变）不受影响；仅当年份变化伴随 title 重写时同「标题变化」 | **P2** |
| 外部 ID 是否稳定？ | TMDB `movie_id` 仅用于采集期同片绑定，不持久化入契约；契约只用内部 `id` | ✅ 稳定 |

> Wikimedia 跨 id 隐患细化：hero_fetch L493 把跨 id 资产 `match_score` 降为 `min(ms, 0.6)`，而 hero.py `MIN_MATCH = 0.6`（L43），`match >= MIN_MATCH` 通过（L465）。即跨片 Wikimedia 资产恰好 0.6 仍可被准入。建议降为 `<0.6`（如 0.55）以真正拦死。**P2**。

---

## 四、增量更新（8 场景只读分析）

| 场景 | Hero 行为 | 普通数据 | 旧 Hero 清理 | 新 Hero 覆盖 | 孤儿风险 |
|------|-----------|----------|--------------|--------------|----------|
| A 首次无 Hero | 全 item `eligible=False`（no_image） | 照常发布 | — | — | 无 |
| B 二次获 Poster+Backdrop | 重算→`eligible=True`，图写入 | 照常 | repo/hero 每轮清空 ✓ | item hero 字段覆盖写 ✓ | 无 |
| C 原 Hero 失效（图删/源失效） | 无候选→`no_image`→`eligible=False` | 照常 | repo/hero 重建不再复制 ✓ | hero.json 该条 False | ⚠ 若删图后不重跑，hero.json 可能引用失效图（操作风险 **P2**）|
| D 影片从源消失 | 该 item 不再遍历→不出 hero.json | 移除 | repo/hero 重建不再复制 ✓ | — | ⚠ `output/posters/<title>/` 残留（**P2** 磁盘）|
| E 标题变化 | 旧目录孤儿 + 新 title 重新采集前 Hero 短暂消失 | 照常（id 不变） | repo/hero 重建 ✓ | 新 title 命中后恢复 | ⚠ 旧目录残留（**P2**）|
| F 年份变化 | title 不变→目录不变→Hero 不受影响 | 照常 | — | — | 无（除非伴随 title 重写）|
| G Hero 下载失败但普通数据成功 | 失败只记 `failures`，不写图→`eligible=False` | ✅ 完全不受影响 | repo/hero 重建 ✓ | — | 无 |

**结论**：增量/升降级机制完整，普通数据与 Hero 互不污染。唯一需注意：删除素材后必须重跑 `build_bundle` 才同步（非自动），以及标题变化产生磁盘孤儿目录（不影响正确性）。

---

## 五、缓存机制

| 检查项 | 结论 | 证据 |
|--------|------|------|
| 同 URL 是否重复下载？ | 否。缓存 `cache["urls"][url]`，命中 `status=="ok"` 且本地存在 → 跳过下载 | hero_fetch L501-511 |
| 同图片不同 URL 能否去重？ | 能。`cache["hashes"][phash]`，下载后算 phash，已存在 → `DUPLICATE` 不落盘 | hero_fetch L531-538 |
| 已合格 Hero 是否重复处理？ | 不重下（缓存命中）；但 `evaluate_all` 每轮全量本地重算（仅本地分析，成本低） | hero.py L528-544 |
| 源站临时失败是否误删已有好图？ | **⚠ 会（P1）**。见「必须修复」P1-1 | hero_fetch L584-596 |
| 新高清图能否升级旧 Hero？ | 能。每片取 `max(score)`（hero.py L418-419）；旧 `poster_/backdrop_` 被清理（L591-596） | hero.py L418-419；hero_fetch L591-596 |
| 缓存失效机制是否合理？ | 仅成功时更新；**无 TTL**（P2）。本地已存好图，重算仍可用；长期可能陈旧但不影响正确性 | hero_fetch L539-544 |

---

## 六、质量门禁（HeroAssetQuality）

**所有进入 `repo/hero` / `hero.json` 的数据必经的校验：**

1. `hero_fetch.verify_image`（L220-257）：真实像素解码 + 格式白名单(JPEG/PNG/WEBP/BMP) + `Poster<800`/`Backdrop<1280` 淘汰 + phash + 模糊方差。所有 hero_fetch 落盘图必经。
2. `hero.py._analyze_local`（L67-109）：尺寸<500B 拒绝、Pillow 解码失败标记 `damaged`（L108）。
3. `hero.py._score_image`（L220-295）：分辨率/比例/模糊/来源/压缩综合评分。
4. `hero.py` 资格（L426-467）：**高清 Poster 与高清 Backdrop 必须同时具备**（`image_resolution_ok = has_hero_poster and has_backdrop`，L428），缺任一**不写 URL 到契约**（L497-512 仅达标才写，否则空串）。
5. 无低清 fallback：无「低清图放大伪装」路径；`likely_upscaled` 标记（L283-284）+ 尺寸硬门槛双重拦截。

**绕过路径排查（结论：无实质绕过）**
- 普通 poster：经 `_analyze_local`+`_score_image` 评分，低清不达标，不进 Hero。✅
- 旧缓存：命中时不**重新** verify，但 hero.py 发布前会重分析 `output/posters`，能拦截损坏。✅（残留 P2：缓存命中未重校验文件完整性）
- publisher / fallback / 手工生成 / 旧字段：publisher 只复制+写 json，不生成图；无 fallback 进 Hero；无手工生成路径。✅

**原则达成**：宁可没有 Hero，也不把低清/缩略图/错图/串片/损坏/不达标图放入 Hero。✅

---

## 七、发布与输出链路

```
build_bundle (publisher.py)
 ├─ step 0  _attach_posters      → images/      （普通海报，与 Hero 无关）
 ├─ step 1  data.json            （vod，不含 hero 字段）
 ├─ step 2  api.js
 ├─ step 3  subscribe.json
 ├─ step 4  index.html
 ├─ step 5  DEPLOY.md
 ├─ step 6  health.json / status.html
 ├─ step 7  poster_repo.refresh  → repo/img, repo/slide, featured.json
 ├─ step 7.5 ★ Hero 评估         → hero.evaluate_all() + build_hero_json(base, out_dir)
 │            └─ 整段 try/except 包裹（L517-522）：异常只 log warn，不中断发布
 └─ step 8  db.json 拷贝
```

**排查「Hero 成功但 publisher 没携带」**：`build_hero_json` 在 `build_bundle` 内**直接调用**（非独立进程），无中间丢失环节。hero.json 必然写入 `tvbox-dist/`，随 `cp tvbox-dist/.` 发布到 pages 根。✅

唯一弱点：若 Hero 步骤自身抛异常，被 L521 `except` 吞掉 → hero.json 保留**上一轮**产物（不缺失，但可能不是最新），且仅 log warn 无声提示。**P2**。

---

## 八、失败隔离（增强层验证）

| 检查项 | 结论 | 证据 |
|--------|------|------|
| Hero 全部失败 → 普通采集仍成功 | ✅ | hero_fetch 失败只记 failures；evaluate_all per-item try L533-538；publisher hero 步骤 try/except L517-522 |
| 单部 Hero 失败 → 其他继续 | ✅ | ThreadPoolExecutor L638 + per-item try L533 |
| Hero 下载超时 → 不拖死任务 | ✅ | 单部超时由 http_get_bytes RETRY/BACKOFF（L183-209），CONCURRENCY=4 隔离 |
| Hero 数据异常 → 不破坏普通数据 | ✅ | evaluate_item 只写 hero 字段，poster/源不变 |
| hero.json 生成异常 → 是否拖垮 publisher？ | **否**。被 try/except 包裹，仅 log warn | publisher.py L517-522 |
| repo/hero 写入异常 → 破坏已有数据？ | **⚠ 部分（P1）**。见 P1-2 | `_copy_hero_image` 异常返回 None（L593-594）→ hero_poster 字段保留相对路径而非公网 URL |

**结论**：失败隔离设计到位，Hero 是真正独立的增强层，单点故障不影响主链路与发布。

---

## 九、性能与资源隔离（仅采集器）

| 检查项 | 结论 |
|--------|------|
| Hero 并发是否合理？ | ✅ `CONCURRENCY=4`（hero_fetch L70） |
| 是否占满普通海报采集资源？ | ✅ 独立脚本，运行时机由用户/CI 触发；不与 grab_posters 并发（除非手动同时跑）。二者写同一 `output/posters` 但命名前缀不同，互不误删 |
| 无限重试？ | ✅ `RETRY=3`（L71）上限 |
| 无限扫描？ | ✅ `max_films` 上限，遍历 db 有限集 |
| 重复下载？ | ✅ URL+image hash 双缓存 + 早期停止（L469-470 凑齐即 break） |
| timeout 有效？ | ✅ `TIMEOUT=25`（L67） |
| retry/backoff 有效？ | ✅ `BACKOFF=2.0` 指数退避（L72, L176） |
| early stop 有效？ | ✅ 拿到合格 HD Poster+Backdrop 即停（L469-470） |
| Hero 是否拖慢普通采集？ | ✅ 独立进程；publisher 内 Hero 步骤在末尾且 `evaluate_all` 仅本地分析，快 |

**结论**：Hero 是增强采集层，不会拖垮影视采集主链路。✅

---

## 十、最终数据/API 契约（hero.json）

**当前对外字段**（build_hero_json L625-644）：

| 字段 | 必填 | 允许空 | 说明 |
|------|------|--------|------|
| `hero_eligible` | ✅ | — | bool，唯一展示判决依据 |
| `movie_id` | ✅ | — | = `item["id"]`（UUID） |
| `title` | ✅ | — | 片名 |
| `hero_score` | ✅ | — | 0-100 |
| `hero_grade` | ✅ | — | S/A/B/F |
| `source_available` / `source_health` / `sources_count` | ✅ | — | 源健康 |
| `hero_poster` | — | ✅(`""`) | 公网 URL；**非 eligible 时可能为空或仅为候选图** |
| `backdrop` | — | ✅(`""`) | 公网 URL；同上 |
| `hero_poster_width/height` / `backdrop_width/height` | ✅ | — | 真实像素 |
| `match_confidence` | ✅ | — | 匹配置信 |
| `image_quality` / `image_resolution` | ✅ | — | |
| `reasons[]` | ✅ | — | 淘汰原因码 |

**契约语义**
- Hero 不存在 / 采集失败 / 失效：该条仍出现在 `heroes` 列表，`hero_eligible=False`，`reasons` 含失败码（如 `no_backdrop`/`MATCH_UNCERTAIN`）。下游**必须且只能根据 `hero_eligible==true` 决定展示**。
- ⚠ **重要**：`hero_poster`/`backdrop` 在非 eligible 条目上**可能非空**（asset 级达标但整体未入选的候选图）。下游若误读这两个字段展示，会放出不合格 Hero。**契约需文档强约束：只认 `hero_eligible`。**（P2）
- 下游无需知道内部多少图片源——hero.json 自包含。✅

---

## 十一、当前缺口分类

### 已完成（不要重复开发）
- Hero 采集 pipeline（hero_fetch.py）：provider 抽象、质量门禁、严格匹配、同片绑定、URL+image hash 缓存、限速/重试/退避/并发/早停。
- 质量门禁（HeroAssetQuality）：尺寸/比例/格式/解码/hash/来源/匹配。
- 数据模型（hero.py）：item `hero*` 字段 + hero.json 契约。
- 增量更新 / 升降级机制。
- repo/hero 孤儿清理（每轮 rmtree 重建）。
- 失败隔离（多层 try/except）。
- 发布链路（build_bundle step 7.5）。
- 外部契约（hero.json 自包含）。

### 必须修复（P0）
**无 P0。** 采集器侧链路完整、可用、不破坏现有系统；APK 侧消费缺失为独立问题（前轮审计已定 `HERO_INTEGRATION = BLOCKED`），不属本阶段。

### 建议修复（P1）
- **P1-1｜源站临时失败会误删已有好图**（违反需求五·4）。
  - 证据：`hero_fetch.collect_one` 清理逻辑 L584-596 —— 若本轮**无任何 saved 资产**（如全网超时），`keep` 为空，会 `os.remove` 掉目录内所有 `poster_/backdrop_` 前缀文件，即把上一轮下载的好高清图删掉。
  - 影响：一次网络抖动可能清空已积累的高清素材。
  - 最小修复：仅当本轮至少命中 1 个新资产时才执行旧文件清理；或改为「只删被新资产取代的同 kind 旧文件」。

- **P1-2｜copy 失败导致 hero.json 泄漏相对路径**。
  - 证据：`_copy_hero_image` 在 copy 异常时返回 `None`（L593-594）；`build_hero_json` L617-624 `if npu: hpu=base+npu` —— 若 `npu is None`，`hpu` 保留为 `_local_to_rel` 的原值 `"output/posters/..."`（相对路径，hero.py L387-389），写入 hero.json 后 APK 拿到不可访问的相对路径。
  - 影响：极端磁盘错误时 hero.json 含坏 URL。
  - 最小修复：`_copy_hero_image` 失败时，调用方应将 `hero_poster`/`backdrop` 置为空串（而非保留相对路径），宁缺毋滥。

### 可以暂缓（P2）
- **P2-1** 图片目录键 title-only（不含 year），同名不同年理论串图 → 目录键建议含 year。
- **P2-2** Wikimedia 跨 id 资产 `match_score` 降为 0.6 恰好等于 `MIN_MATCH`，可被准入 → 建议降为 <0.6（如 0.55）。
- **P2-3** 缓存命中（L503-511）不重新校验本地文件完整性；长期可能用到损坏文件（hero.py 发布前重分析能兜底）。
- **P2-4** 标题/影片变化产生 `output/posters/<旧title>/` 孤儿目录（不影响正确性，仅磁盘残留）。
- **P2-5** Hero 步骤异常被 publisher except 吞掉后，hero.json 保留上一轮产物且无声提示（仅 log warn）。
- **P2-6** hero.json 非 eligible 条目的 `hero_poster`/`backdrop` 可能非空，下游误用风险 → 契约文档须强调「只认 `hero_eligible`」。
- **P2-7** 删除素材后须手动重跑 `build_bundle` 才同步（非自动触发）。

### 不属于本阶段（防止范围膨胀）
- APK 侧 hero.json 消费层接入（前轮审计已定 `BLOCKED`，独立任务）。
- TMDB Key 配置与规模化覆盖率提升（内容问题，非链路问题）。
- 电视端实机性能/遥控器实测（禁止装设备）。
- 任何代码修改、构建、推送。

---

## 十二、最终输出（判定行）

```
HERO_FETCH_PIPELINE   = PASS      # pipeline 完整可用（含 P1 边界 bug，不影响主路径）
HERO_QUALITY_GATE     = PASS      # verify_image + _score_image + 双条件硬门槛，无低清 fallback
HERO_DATA_MODEL       = PASS      # item.id UUID 主键稳定；hero 字段独立；movie_id 关联清晰
HERO_UPDATE_FLOW      = PASS      # 增量重算+升降级；场景 A-G 分析见第四节（含 P1 误删注意）
HERO_CLEANUP_FLOW     = PASS      # repo/hero 每轮 rmtree 重建；output/posters 当轮清理（P1 误删需注意）
HERO_CACHE            = PASS      # URL hash + image hash 双缓存；去重有效；命中不重下（P2 无 TTL）
HERO_FAILURE_ISOLATION= PASS      # 多层 try/except；单部/全局失败不影响普通采集与发布
HERO_PUBLISH_FLOW     = PASS      # build_bundle 必带 hero.json；异常吞掉不阻断
HERO_EXTERNAL_CONTRACT= PASS      # hero.json 自包含、字段清晰（P2 需补文档强调只看 eligible）
HERO_PIPELINE_OVERALL = PASS      # 采集器侧链路完整可用（APK 消费不属本阶段，前轮定 BLOCKED）

P0_COUNT = 0
P1_COUNT = 2   # P1-1 误删好图 / P1-2 相对路径泄漏
P2_COUNT = 7   # 见第十一节

RECOMMENDED_NEXT_STEP =
  1) 修复 P1-1（误删好图）与 P1-2（相对路径泄漏）——两个都是小范围、低风险改动；
  2) 补 hero.json 契约文档，强约束「下游只根据 hero_eligible==true 展示」；
  3) 配置免费 TMDB Key 后跑规模化验证，观察真实入选覆盖率（当前 1/30 是素材覆盖问题，非链路问题）；
  4) （独立任务）APK 侧接入 hero.json 消费层——前轮已定 BLOCKED，需另行授权。
```

---

> 本报告仅记录采集器侧 Hero 链路现状与风险，**未修改任何代码、未删除任何文件、未重构、未构建 APK、未推送**。
> 如发现的问题仅以「问题 → 证据 → 影响 → 最小修复方案」形式列出，等待下一条授权再决定是否实施。
>
> `WAITING_FOR_AUTHORIZATION = YES`
