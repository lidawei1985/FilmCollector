# 部署说明 · 跨环境通用 TVBox 静态订阅

生成的 `tvbox-dist/` 目录是一套**纯静态文件**，不依赖任何服务器。
把它上传到任意静态托管，拿到一个 `https://...` 地址即可。

> **海报说明**：包内的 `images/` 目录是本工具自动随包发布的本地海报图库。
> 订阅 JSON 里的海报地址已改写为你自己的托管域名（如 `https://user.github.io/repo/images/xxx.jpg`），
> 电视/手机加载海报时直接读你自己的仓库，不再依赖第三方图床 / CDN / 防盗链。
> 请务必把 `images/` 整个目录一起上传，否则海报会 404。

## 发布时指定 BASE（重要）

生成包时请用 `--base` 指明你最终的托管根地址（决定爬虫与订阅里写死的地址）：

```bash
python -m backend.core.publisher --source demo --base https://你的用户名.github.io/FilmCollector --out tvbox-dist
```

`--base` 只需改这一处，代码与数据无需任何改动即可换到 GitHub / Codeberg / Gitee。

## 方式一：GitHub Pages（推荐，免费）

1. 在 GitHub 新建一个仓库，例如 `FilmCollector`。
2. 把 `tvbox-dist/` 里的**所有文件**推到仓库（可直接放根目录，或放 `docs/` 并开 Pages 指向 docs）。
3. 仓库 Settings → Pages → 选分支与目录 → Save。
4. 几分钟后得到地址 `https://你的用户名.github.io/FilmCollector/`。
5. 订阅地址即：`https://你的用户名.github.io/FilmCollector/subscribe.json`

## 方式二：Codeberg Pages

1. 新建仓库，把 `tvbox-dist/` 全部文件推上去。
2. 仓库 Settings → Pages → 选分支（如 `main`）→ Save。
3. 地址形如 `https://你的用户名.codeberg.page/仓库名/`。

## 方式三：Gitee Pages

1. 新建仓库，推送文件。
2. 服务 → Gitee Pages → 部署分支 → 启动。
3. 地址形如 `https://你的用户名.gitee.io/仓库名/`。

## 在电视端使用

- 打开任意 TVBox 基底软件（影视仓 / 猫影视 / TvBox / OK影视 / ZYPlayer 等）。
- 找到「订阅」或「配置」→ 添加订阅 → 粘贴上面的 `subscribe.json` 地址 → 确认。
- 也可在软件的「源管理 / 自定义接口」里直接填写 `api.js` 或 `data.json` 地址（见落地页）。
- **无需开你电脑、无需连你家 WiFi**，全球有网即可观看。

## 更新片库

重新在你电脑的采集工具里抓取 / 生成后，再跑一次上面的发布命令，
把新的 `tvbox-dist/` 重新上传覆盖即可（建议开启 Pages 的强制刷新 / 等几分钟 CDN 生效）。

## 独立海报仓库（让 APK 永不依赖第三方图源）

本包除了订阅数据，还会生成一套**按片名哈希命名**的独立海报仓库：

    repo/img/<md5(片名)>.jpg    竖版海报（卡片 / 详情页 / 主视觉竖版）
    repo/slide/<md5(片名)>.jpg  横版主视觉（首页 hero 大背景，跑高清抓取后自动填充）
    repo/featured.json          今日精选（自动挑「有片源+有高清图」的影片，每 N 天轮换）

仓库地址即：`https://你的用户名.github.io/FilmCollector/repo/`

**为什么要它**：TVBox / Lumflix 类 APK 对每张海报默认去别人的图床取图，图床失效/防盗链就会白屏。
把海报随包发布到你的仓库后，APK 按 `md5(片名)` 直接读你自己的地址，零依赖第三方。

**对接 Lumflix / NetTV APK（com.nettv.app）**：
该 APK 前端已内置独立海报仓库机制（`assets/www/js/api.js` 的 `POSTER_CDN`）。
只需把它的 `POSTER_CDN` 指向上面的 `repo/` 地址（重建 APK 时改默认值，或运行时设
`localStorage.nettv_poster_cdn`），APK 就会：
  1. 所有影片（只要片名在仓库里有图）自动改用你的高清海报；
  2. 首页 hero 横版大背景改用你的 `repo/slide/` 横版主视觉；
  3. 自动加载 `repo/featured.json` 作为「今日精选」轮播，几天换一批，无需你手动操作。

> 想让某部片有横版主视觉：用 `tools/grab_posters.py` 抓它的高清图，
> 落盘到 `output/posters/<片名>/` 即可；重新发布时自动进入 `repo/slide/`。
