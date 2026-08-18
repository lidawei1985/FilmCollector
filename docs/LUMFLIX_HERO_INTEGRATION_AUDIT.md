# Lumflix Hero 集成链路审计（只读）

> 阶段目标：仅确认「真实高清 Hero 素材是否以正确、快速、非阻塞方式进入 Lumflix APK 首页」。
> 本阶段**只读检查**，不修改代码、不构建、不安装到电视、不推送、不发布。
>
> 审计时间：2026-08-17
> 后端工程：`E:/FilmCollector`（hero.py / hero_fetch.py / publisher.py / tvbox-dist/hero.json）
> APK 侧资产：`C:/Users/sbqqq/WorkBuddy/2026-08-11-05-23-39/apk-build/`（解包 smali + res/raw）、`lumflix_inspect/`（api.js / app.js / data.js / sources.js / live-channels.json）

---

## 0. 一句话结论

**后端 `hero.json` 已经产出正确、真实的高清 Hero（Wings：竖版海报 2056×3000 + 横版主视觉 6000×1465），但 APK 当前完全没有读取 `hero.json` 的代码路径。电视端实际请求的 Hero 背景图，是仓库里的 180×124、3KB 低清缩略图（被 CSS 拉伸当大背景）。**

换句话说：**高清 Hero 在后端「生产」了，但没有「运输/展示」到电视。** 这条「后端 → APK」的链路是断开的，本阶段唯一真正的 P0 就是它。

---

## 1. Hero 素材层状态（后端）

- `tools/hero_fetch.py` 已落地：严格匹配、Poster+Backdrop 同片绑定、HeroAssetQuality 门禁、URL+image hash 双缓存、失败分类、限额并发。
- 真实 30 部采集结果（Wikimedia 免 Key 源）：
  - 扫描 30 ｜ 高清 Poster 2 ｜ 高清 Backdrop 1 ｜ 同片匹配 1
  - 仅 **Wings** 满足「稳定源 + 高清 Poster + 高清 Backdrop + 准确匹配」四条件。
- 素材文件已落盘并经验证：`tvbox-dist/repo/hero/` 内 3 张均为真实高清、可解码、0 张低清/坏图/孤儿。

## 2. hero.json 状态

- 路径（磁盘）：`E:/FilmCollector/tvbox-dist/hero.json`
- 发布后可读地址：`https://lidawei1985.github.io/filmcollector-pages/hero.json`
- 内容：`code=1`，`stats.total=30`，`hero_eligible=1`，`hero_ineligible=29`（原因 `no_backdrop:29` / `no_hero_poster:28` / `quality_insufficient:1`）。
- 唯一 eligible 条目（Wings）：
  - `hero_poster` = `https://lidawei1985.github.io/filmcollector-pages/repo/hero/aa9f3975e1ac31d104905da5d2fa2d79.jpg`，尺寸 **2056×3000**
  - `backdrop`   = `https://lidawei1985.github.io/filmcollector-pages/repo/hero/dd67c7314a0c13c74d3e5c9e9ca5f3f0.jpg`，尺寸 **6000×1465**
  - `hero_score=93`，`hero_grade=A`，`source_health=88`，`match_confidence=1.0`
- 字段完整：`backdrop / backdrop_height / backdrop_width / hero_eligible / hero_grade / hero_poster / hero_poster_height / hero_poster_width / hero_score / image_quality / image_resolution / match_confidence / movie_id / reasons / source_available / source_health / sources_count / title`。

## 3. APK 数据读取链路

逐层核查（全文检索 apk-build 解包树 + lumflix_inspect JS）：

| 检查项 | 结果 | 证据 |
|---|---|---|
| APK 是否读取 `hero.json` | **否** | 整个 `apk-build/` 与 `lumflix_inspect/` 对字符串 `hero.json` 零引用 |
| APK 是否读取 `hero_poster_url` 字段 | **否** | 对 `hero_poster`（字段）、`hero_eligible`、`hero_score` 零引用 |
| APK 是否读取 `backdrop_url` 字段 | **否** | 对 `backdrop`（hero 字段）零引用 |
| APK 现有 Hero 数据来源 | 订阅源 `vod_pic_slide` → 仓库竖版 `posterRepoUrl()` → `vod_pic` | `lumflix_inspect/api.js:831-863` 的 `getHeroBg/getHeroPoster/getHeroBlur/getDetailBg` |
| APK 焊死订阅源 | `https://lidawei1985.github.io/filmcollector-pages/combined.json` | `apk-build/all.json` |
| FilmCollector 发布的 vod 是否带 `vod_pic_slide`/`backdrop` | **否** | `tvbox-dist/data.json` 首条 vod 字段无此二项，仅 `vod_pic`（指向 180×124 低清图） |

**结论：APK 的数据入口完全不认识 `hero.json`。** 它从 `combined.json → data.json` 读取 vod 列表，再靠 `getHeroBg()` 拼 Hero 背景；而 FilmCollector 数据里既没有 `vod_pic_slide`，也没有任何 hero 字段，因此 `getHeroBg()` 必然落到 `posterRepoUrl(vod_name)` —— 即仓库里的低清缩略图。

## 4. Hero 图片实际请求链路

- `posterRepoUrl(name)` 定义（`api.js:101`）：
  `return POSTER_CDN + 'img/' + MD5(normalizeName(name)) + '.jpg';`
- 对 Elephants Dream，该 URL 解析为 `https://lidawei1985.github.io/filmcollector-pages/images/poster_ElephantsDream.jpg`，**实际文件 180×124 / 3KB**。
- 电视端当前实际请求的 Hero 背景图 = 这张 180×124 低清缩略图（被 CSS 拉伸填满横版背景）。
- 后端 `hero.json` 里的 6000×1465 高清 Backdrop（1179KB）**从未被任何 APK 代码请求**。

| 项目 | 真实值 |
|---|---|
| 原始高清图尺寸（hero.json 内 Wings backdrop） | 6000×1465（已用 PIL 验证文件） |
| hero.json 中 Backdrop URL | `…/filmcollector-pages/repo/hero/dd67c7314a0c13c74d3e5c9e9ca5f3f0.jpg` |
| APK 实际请求 URL | `…/filmcollector-pages/images/poster_<片名>.jpg`（180×124 低清） |
| 实际返回 Content-Length | 低清图约 3KB；高清图约 1179KB（后者未被请求） |
| 服务端缩略 / CDN resize | UNKNOWN（GitHub Pages 为纯静态，无 resize；但需实机抓包确认 APK 是否经其他 CDN） |
| CSS 拉伸 / object-fit 错误裁剪 | UNKNOWN（首页 Hero DOM 渲染代码不在本次提供的资产中，见第 5 节） |

## 5. Hero 实际显示链路

- `lumflix_inspect/api.js` 定义了 `getHeroBg / getHeroPoster / getHeroBlur`，但**全工程检索不到任何调用方**（只有定义，没有 `getHeroBg(` 的调用）。
- 首页真正构建 Hero DOM、给 `<img>` 赋 URL、处理 `onload/onerror`、启用焦点的代码，**不在本次提供的资产里**（`app.js` 全文无 `hero` 字样；`res/raw/index.html` 是 TVBox 文件管理器 WebUI，与首页无关）。
- 因此无法对「显示链路」做静态逐行核验，相关运行期指标标 UNKNOWN。

## 6. 高清素材是否真正被使用

**否。** 电视端目前使用的是 180×124 低清缩略图当 Hero 大背景；`hero.json` 的高清 Backdrop/Poster 没有被任何代码消费。这正是当初要根治的「低清海报放大当主视觉」问题，目前**在 APK 侧仍然存在**（因为高清图根本没接进去）。

## 7. Hero fallback 情况

- 存在低清 fallback：`getHeroBg()` 在无 `vod_pic_slide` 时返回 `posterRepoUrl()`（仓库低清图），否则返回 `vod_pic`。
- 该 fallback 会把 180×124 竖版低清图当作横版大背景，**必然被 CSS 拉伸放大、模糊**。属于「低清 fallback 被放大」的典型问题。
- `hero.json` 里的「无合格 Hero 时的安全占位」逻辑在后端已就绪（`hero_eligible=false` 即不入选），但 APK 没读，所以用不上。

## 8–11. 首页首次渲染性能 / Hero 首显 / 可交互 / 是否阻塞遥控器

| 指标 | 结论 | 说明 |
|---|---|---|
| T0 首页开始渲染 | UNKNOWN | 未实机运行（TV_INSTALL=NO） |
| T1 导航出现 | UNKNOWN | 同上 |
| T2 Hero 容器出现 | UNKNOWN | 首页 DOM 渲染代码不在资产中 |
| T3 Hero 高清图开始请求 | UNKNOWN | APK 不请求高清 Hero |
| T4 Hero 首次显示 | UNKNOWN | — |
| T5 首批货架出现 | UNKNOWN | — |
| T6 首批海报出现 | UNKNOWN | — |
| T7 首页完全可交互 | UNKNOWN | — |
| Hero 加载是否阻塞遥控器焦点 | UNKNOWN | `getHeroBg` 仅返回 URL 字符串、无 fetch/await，数据层非阻塞；但焦点启用时机依赖缺失的渲染代码，无法确认 |
| Hero 加载失败是否阻塞首页 | UNKNOWN | 缺失渲染代码，无法确认 `onerror` 处理 |
| Hero 加载慢时首页是否仍可用 | UNKNOWN | 同上 |

> 诚实说明：本阶段禁止装 TV/模拟器，且 APK 首页渲染代码未随资产提供，故**所有运行期时间线与遥控器行为均无法实测，一律标 UNKNOWN，不做猜测**。静态可确认的是：Hero 数据函数本身不发起请求、不阻塞 JS 主线程。

## 12. 与 v4.4.0 的性能差异

- 提供的 `lumflix_inspect/data.js` 标注 `version: '4.0.0'`，并非用户指定的 v4.4.0 基线。
- 因此「与 v4.4.0 严格对比」**无法基于现有资产完成**，标 UNKNOWN。
- 重要前提：无论 4.0 还是 4.4，**`hero.json` 都未被消费**这一事实与版本无关，是确定性结论。

## 13. 当前发现的 P0

- **P0 — Hero 集成链路断开**：后端已产出正确高清 `hero.json`，但 APK 无任何读取/匹配/展示 `hero.json` 的代码。后果：
  - 电视端 Hero 大背景实际是 180×124 低清缩略图（被拉伸），而非 hero.json 的 6000×1465 高清主视觉；
  - 用户投入建设的「高清 Hero 能力」当前对终端用户零可见价值。
  - 这是把「高清素材正确进入首页」目标卡住的唯一根因。

## 14. 当前发现的 P1

- **P1 — 版本基线不一致**：可用审计资产为 v4.0.0，而对照基线是 v4.4.0。需取得 v4.4.0 的真实 APK/Web 资产，才能做严格性能对比与「是否回归」判定。
- **P1 — 发布可达性待实机确认**：`hero.json` 发布后位于 pages 仓库根（`…/filmcollector-pages/hero.json`），asset 位于 `…/repo/hero/`。逻辑推断可达，但需在真实部署后抓包确认 200 + 正确 Content-Length。
- **P1 — 覆盖率现状**：当前仅 1 部 eligible，意味着即使接入 hero.json，首页 Hero 也只有 1 部走高清，其余仍走低清 fallback。这是素材覆盖率问题（非代码缺陷），需持续采集高清 Backdrop。

## 15. 当前发现的 P2

- **P2 — 低清 fallback 不应被当大背景**：`posterRepoUrl()` 返回的 180×124 图应仅用作卡片小图，绝不应被 `getHeroBg` 当作横版大背景拉伸。属于「显示侧」需要加固的防御。
- **P2 — 缺少 Hero 懒加载/缓存约定**：接入 hero.json 时应约定 APK 侧缓存 hero.json、Hero 图懒加载、渐进绘制，避免一次性请求阻塞。

## 16. 是否需要修改代码

**需要**，但**本阶段未执行任何修改**（遵守 CODE_MODIFICATION=NO）。要消掉 P0，必须由 APK 侧新增一段「消费 hero.json」的集成代码（纯增量，不改动现有 poster/cover/episodes 契约）。

## 17. 如果需要，具体应该改哪里（建议，未实施）

- **`lumflix_inspect/api.js`（或对应 APK Web 资产）新增数据接入**：
  - 启动时拉取 `BASE + 'hero.json'`，建立 `movie_id/title(归一化) → hero` 索引；
  - 新增 `getHeroFromJson(item)`：命中 eligible 时返回 `backdrop_url`（大背景）与 `hero_poster_url`（左侧竖版），并优先于现有 `getHeroBg` 的 slide/低清兜底。
- **首页渲染（缺失代码处）改为**：先查 hero.json 索引，命中则用高清 `backdrop_url` 作背景、`hero_poster_url` 作竖版；未命中再走现有 slide/仓库兜底；**绝不把低清缩略图当大背景拉伸**。
- **APK 侧**：缓存 hero.json（与现有 `cacheTimeout` 对齐）、Hero 图 `loading="lazy"`/渐进绘制、`onerror` 回退到现有兜底且不影响焦点。
- 后端无需再改（hero.py / hero_fetch.py 已满足契约）。

## 18. 修改风险

- 风险等级：**低**（纯增量层，只读 hero.json，不触碰 poster/cover/episodes/订阅契约）。
- 主要风险点：
  - 若 hero.json 请求失败/超时，必须有降级（回退现有 slide/仓库），不能白屏；
  - Hero 图懒加载必须保证「图片未到位时焦点仍可用」，避免回归到「图没加载完 → 遥控器失效」；
  - 归一化匹配键要与后端 `normalize_name` 对齐，防止「同名不同片」错配。

## 19. 推荐下一步（等待授权后执行）

1. 取得 **Lumflix v4.4.0** 真实 APK/Web 资产，重做本审计的「性能/遥控器」部分（实机或模拟器），把 UNKNOWN 收敛为实测值。
2. 在 APK 侧实现第 17 节的 hero.json 消费层（增量、带降级、懒加载、不阻塞焦点）。
3. 部署一次 pages，抓包确认 `hero.json` 与 `repo/hero/*.jpg` 返回 200 + 正确体积。
4. 接入后回头跑第 8–11 节性能核验，确认「加入高清 Hero 不比 v4.4.0 慢、遥控器不卡顿」。

---

## 判定汇总

```
HERO_ASSET_PIPELINE          = PASS      # 后端已产出正确真实高清 hero.json + 落盘文件经验证
HERO_INTEGRATION             = BLOCKED   # APK 完全未读取 hero.json，高清 Hero 未到达电视
HERO_PERFORMANCE             = UNKNOWN   # 未实机运行 + 首页渲染代码未随资产提供，无法实测
REMOTE_CONTROL_DURING_HERO_LOAD = UNKNOWN   # 同上
V440_REGRESSION              = UNKNOWN   # 提供资产为 v4.0.0 非 v4.4.0，且未实机对比

CODE_MODIFICATION = NO
APK_BUILD         = NO
TV_INSTALL        = NO
GITHUB_PUSH       = NO
PRODUCTION_RELEASE = NO
```

> 本阶段严格只读，未改动任何文件、未构建、未安装、未推送。报告完成后停止，等待下一条授权。
