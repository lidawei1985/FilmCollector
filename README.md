# 影视资源采集器 · 零代码本地版

一款 **Windows 桌面端可视化影视资源采集工具**，纯傻瓜零代码操作：内置 Chromium 内核浏览器（系统 WebView2），
可视化点击采集，自动清洗 / 去重 / 分类 / 过滤广告，一键生成 **双格式标准订阅源** 并开放 **两组本地 API**，
所有数据仅存本机，永久免费、无广告、无功能锁。

> 说明：采集对象须为你有权访问的页面；遇到验证码 / 高强度加密站点，工具提供「手动复制播放地址」兜底，
> 不内置绕过站点安全校验的能力。

## 一、核心能力

| 模块 | 说明 |
|------|------|
| 站点检测 | 粘贴链接自动识别 ① 完全支持批量抓取 ② 轻度加密单集提取 ③ 高强度加密（手动兜底） |
| 零代码采集 | 自动嗅探 / 内置浏览器点击选字段 / 高强度站手动粘贴三种方式 |
| 自动清洗 | 失效链接剔除、广告域名黑名单过滤、海报本地缓存、名称规范化 |
| 智能去重 | 片名+年份唯一标识，重复影片合并多条播放线路 |
| 自动分类 | 电影 / 连续剧 / 短剧 / 动漫 / 综艺 / 纪录片 / 少儿 + 题材标签 |
| **双格式导出** | ① TVBox 标准订阅 JSON（maccms，影视仓/LunaTV/ZYPlayer/OK影视/猫影视等）② 通用纯净影片 JSON |
| **双 API** | TVBox 数据源接口 `/api.php/provide/vod/` + 通用接口 `/api/generic/vod/` |
| 格式校验 | 导出前校验 JSON 语法与必填字段，弹窗提示缺失 |
| 定时任务 | 7×24 后台静默巡检 + 重新生成（每小时 / 每日） |
| 备份回滚 | 每日自动备份数据 + 两套格式历史，支持一键回滚 |

## 二、目录结构

```
E:\FilmCollector\
├─ app.py                  # 桌面外壳（WebView2 承载前端 + 内置浏览器点击采集）
├─ backend\
│  ├─ server.py            # Flask 服务：前端 + 应用 API + 双格式本地 API
│  └─ core\
│     ├─ store.py          # 本地数据仓储（db.json / config.json / 广告黑名单）
│     ├─ detector.py       # 站点兼容性检测
│     ├─ scraper.py        # 可视化采集 / 自动嗅探 / 列表整站抓取
│     ├─ cleaner.py        # 链接校验 + 广告过滤 + 图片缓存
│     ├─ dedup.py          # 去重合并线路
│     ├─ classifier.py     # 分区 + 题材标签
│     ├─ json_gen.py       # 双格式 JSON 生成 + 备份
│     ├─ validator.py      # 导出前格式校验
│     └─ image_cache.py    # 本地图库
├─ frontend\
│  ├─ index.html\          # 零代码采集管理 UI（WebView2 内运行）
│  └─ client\              # 四端观影客户端（Web SPA：浏览/搜索/详情/播放/源管理/清洗）
├─ platforms\              # 四端原生壳源码（android / ios / windows 说明）
├─ build\                  # PyInstaller 单 EXE 打包配置
├─ output\json\{tvbox,generic}\  # 生成的双格式订阅源
├─ output\images\          # 本地海报图库
└─ output\backup\          # 每日备份
```

## 三、开发运行（已验证）

```bash
# 1) 创建虚拟环境并安装依赖（已内置在 managed python 的 envs/filmcollector）
pip install -r requirements.txt

# 2) 启动（前端在浏览器打开 http://127.0.0.1:9911/）
python -m backend.server
```

## 四、打包单文件 EXE（安装到 E 盘）

```bash
pip install -r requirements.txt        # 含 pyinstaller、pywebview
python build/build_exe.py              # 生成 dist/影视资源采集器.exe 并复制到 E:\FilmCollector\
```

成品为绿色程序：双击 `E:\FilmCollector\影视资源采集器.exe` 即启动，无需安装 Python / Docker / 运行库 /
浏览器插件。WebView2 为 Win10/11 自带（Chromium 内核），EXE 不捆绑浏览器，体积小、启动快。

## 五、使用流程（新手向导四步）

1. **站点检测**：粘贴链接 → 看三类结论。
2. **零代码采集**：自动嗅探（零配置）/ 内置浏览器点击选字段 / 手动粘贴播放地址。
3. **生成 & API**：一键生成双格式订阅源；开启本地 API 拿到两组接口地址。
4. **本地与备份**：图库管理海报、日志看进度、设置定时抓取与广告黑名单、回滚备份。

## 六、双格式订阅源对接

- **TVBox 类客户端**（影视仓 / LunaTV / ZYPlayer / OK影视 / 猫影视 等）：
  添加数据源 `http://127.0.0.1:9911/api.php/provide/vod/`（或导入 `output/json/tvbox/*.json`），无需改配置。
- **自研 / 网页 / 第三方播放器**：
  对接 `http://127.0.0.1:9911/api/generic/vod/`（或导入 `output/json/generic/*.json`），
  仅含基础元数据（分类、片名、别名、年份、海报、多清晰度线路、分集、简介、评分、字幕），无私有嵌套字段，无需二次转换。

两组 API 均支持 `ac=list|detail`、`t=分类`、`wd=关键词`、`pg=页码`、`limit=每页`。

## 七、已知边界（MVP 范围）

- 内置浏览器点击采集、单 EXE 桌面窗体需在真实 Windows + WebView2 环境验证（本仓库已在无头环境验证全部后端链路）。
- 仅服务端渲染页默认可直接抓取；JS 重度渲染页由内置浏览器 DOM 回传解析。
- 验证码 / 高强度加密站点按规格提供手动复制兜底，不内置破解能力。

## 八、四端观影客户端框架（安卓TV / 手机安卓 / Windows / 越狱iOS）

与采集工具配套的**观影客户端**：四端共用同一套 Web 客户端（`frontend/client/`），由各自原生 WebView 壳加载
后端 `/client` 页面，消费本机生成的 JSON 订阅源与本地 API。

### 1. 客户端功能（跨端一致）
- **界面**：自动识别平台并切换布局——TV 10 尺大字号 + 方向键（D-pad）空间导航 / 手机底部导航 / 桌面侧栏 / iOS 同手机。
- **浏览**：首页分类（电影/连续剧/短剧/动漫/综艺/纪录片/少儿）、搜索（片名/演员/导演）、详情页（分集 + 多线路）。
- **播放器**：HTML5 `<video>` + 本地化 hls.js（安卓离线可播 m3u8），iOS/tvOS 走系统原生 HLS；分集切换、全屏。
- **源管理**：默认内置「本机采集库（本地 API）」；可粘贴 `catalog.json` 或直连 api 地址添加订阅源，localStorage 持久化、切换/删除。
- **自动清洗过滤**：一键巡检（对接 `/api/app/clean_dead`），展示失效清除数、广告过滤数、破损图数，并读取实时日志。

### 2. 四端壳源码（`platforms/`）
| 端 | 技术 | 运行/安装 |
|---|---|---|
| 安卓 TV / 手机 | Kotlin + WebView（`platforms/android/`） | Android Studio 打开 → Run，或打包 APK |
| Windows | WebView2（`app.py --client`） | 双击 EXE 自动进观影模式；或浏览器开 `http://127.0.0.1:9911/client` |
| 越狱 iOS | Swift + WKWebView（`platforms/ios/`） | Xcode 编译后用 Sideloadly / TrollStore / SSH 装到越狱设备 |

> 壳源码均为「框架级」交付，APK / IPA 需在各自 SDK 编译；本仓库未含已编译产物。
> 详见 `platforms/README.md`。

### 3. 内置公共领域 / CC 免费影视采集
管理后台「公共领域源」标签页提供可编辑的采集源预设（默认不抓取，需你确认有权访问且内容为公有领域/CC 授权后，
一键「加入采集」走本地 检测→采集→清洗→JSON/API 链路）。预设存于 `output/`（经 `backend/core/presets.py` 读写）。

### 4. 联调验证（已做）
- 后端 `/client`、`/client/css`、`/client/js/*`（含 hls.min.js 413KB 本地化）均 200。
- 双格式 API 经真实 HTTP 验证：通用 `type` 输出 EN 枚举（movie/tv/...）、`play_list`/`pic`/`tag`/`desc` 齐全；
  TVBox `type_name` 输出中文；详情 `play_list` 含多线路。
- 客户端 JS 经 `node --check` 语法校验通过；归一化逻辑（TVBox `$$$` 多线路 / 通用 `play_list`）经单元测试覆盖。
- 接口契约：`/api/app/presets`（GET/POST/PUT/DELETE）、`/api/app/clean_dead`（返回 dead/ad_filtered/broken_images）。

> 注：Windows EXE 打包按你的要求**待本地环境就绪后再执行自测**，本阶段仅完成全端界面/播放器/源管理/自动清洗过滤功能与四端壳源码。

## 五、跨环境静态订阅发布（GitHub Pages / Codeberg / Gitee）

把抓取到的内容封装成**纯静态** TVBox 订阅源，上传到任意静态托管即得到一个
「全球可播、不依赖你电脑、不连你家 WiFi」的订阅地址——别人电视上任意 TVBox 粘贴即用。

### 方式 A（推荐，小白一键）· 工具内「一键部署到公网」
管理后台「生成 & API」页最下方 → 选平台（GitHub / Gitee）→ 点「如何获取 Token？」按提示生成一次
Access Token（GitHub 勾 `public_repo`；Gitee 勾 `projects`）→ 粘贴 Token（GitHub 用户名可留空自动获取）→
点 **「🚀 一键部署到公网」**。工具自动：生成静态包 → 建仓库 → 推送 → 开启 Pages → 弹出**真实订阅地址**供复制。
Token 本地记住，**以后只需点一下「更新部署」**。

> 唯一前提：你需有一个 GitHub / Gitee 账号（首次去生成一次 Token，约 1 分钟）。

### 方式 B · 手动上传
- 图形界面：管理后台「生成 & API」页 → 填托管根地址 → 点「生成静态订阅包」得到 `tvbox-dist/`。
- 命令行：`python -m backend.core.publisher --source db --base https://你的用户名.github.io/仓库名 --out tvbox-dist`
- 演示数据包：`python tools/fetch_demo_sources.py` 拉取真实公共领域影片后，用 `--source demo` 生成可播示例。
- 把 `tvbox-dist/` 全部文件传到托管平台对应仓库根目录，开启 Pages 即可。

生成的 `tvbox-dist/` 含：

| 文件 | 作用 |
|---|---|
| `subscribe.json` | 订阅文件（粘贴进 TVBox「订阅」框即可） |
| `api.js` | TVBox 远程爬虫（type=2），在电视端本地完成 首页/搜索/分类/详情/播放，零服务器 |
| `data.json` | 标准 `provide/vod` 响应（type=3 JSON 源直连兜底） |
| `index.html` | 落地页：展示订阅地址与简明教程 |
| `DEPLOY.md` | GitHub Pages / Codeberg / Gitee 三步部署说明 |

**通用性保障**：全部为静态文件，零后端、零运行时依赖；`api.js` 内 `BASE` 由发布时一次性注入，
换托管只改这一处，代码与数据结构无需任何改动；同时提供 type=2（爬虫，支持搜索/分页）与 type=3
（纯 JSON）两类站点，覆盖「支持爬虫」与「只认 JSON 接口」的各类 TVBox 基底软件；片源为公共领域 /
CC 公共直链（archive.org 等），全球有网即播，不经你的电脑。校验：`tools/validate_spider.js`
用 Node 模拟 TVBox 引擎跑通 `init/home/search/detail/play` 全部接口。

