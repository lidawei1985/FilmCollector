# -*- coding: utf-8 -*-
"""
publisher.py —— 跨环境通用的 TVBox 静态订阅发布器
=================================================
把「抓取 / 清洗好的内容」封装成标准 TVBox 订阅接口，输出为一组**纯静态文件**，
可直接丢到 GitHub Pages / Codeberg Pages / Gitee Pages / 任意静态托管，
作为订阅地址使用：

  - subscribe.json : 订阅文件（粘贴进任意 TVBox 基底软件的「订阅」框即可）
  - api.js         : TVBox 远程爬虫（type=2），在电视端本地运行，
                     自行读取同目录 data.json，完成 首页/分类/搜索/详情/播放，
                     无需任何服务器。
  - data.json      : 标准 provide/vod 响应（type=3 JSON 源也能直接用）
  - index.html     : 落地页，展示订阅地址与简明教程
  - DEPLOY.md       : 三步部署说明

通用性保障：
  1. 全部为静态文件，零后端、零运行时依赖，任何静态托管都一样工作。
  2. api.js 内的 BASE 由发布时一次性注入（--base），换托管只改这一处，
     代码与数据结构无需任何改动；爬虫运行时按此 BASE 自行定位 data.json。
  3. 同时提供 type=2（爬虫，支持搜索/分页）与 type=3（纯 JSON）两种站点，
     覆盖「支持爬虫」与「只认 JSON 接口」的各类 TVBox 软件。
  4. 片源本身为公共领域 / CC 公共直链（archive.org 等），全球可播，不经你的电脑。
"""
import argparse
import json
import os
import shutil
from datetime import datetime

from . import store, json_gen

DEMO_PATH = os.path.join(store.DATA_DIR, "demo_sources.json")
OUT_DEFAULT = os.path.join(store.BASE_DIR, "tvbox-dist")

SOURCE_NAME = "FilmCollector 公共片库"

# 本地海报图库（采集时 image_cache 已把第三方海报下载到此处）
IMG_SRC_DIR = os.path.join(store.BASE_DIR, "output", "images")


# ---------------- 数据源 ----------------
def load_items(source="db"):
    """读取可发布的影片条目（统一为 db.json 的 item 结构）。"""
    if source == "demo":
        if os.path.exists(DEMO_PATH):
            return json.load(open(DEMO_PATH, encoding="utf-8"))
        print("[publisher] 未找到 demo_sources.json，先运行 tools/fetch_demo_sources.py")
        return []
    # db：仅取有效且有播放地址的
    items = store.load_db().get("items", [])
    alive = [it for it in items if it.get("status") != "dead"]
    playable = [it for it in alive if it.get("episodes")]
    return playable or alive


def _to_vods(items):
    return [json_gen._to_tvbox(it) for it in items]


# ---------------- 海报图库随包发布 ----------------
def _attach_posters(vods, base, out_dir):
    """把本地图库中实际被引用的海报复制进静态包，并把相对地址改写为公网绝对地址。

    核心原则：每张海报只依赖第三方图源一次 —— 采集时已下载进本地图库，
    发布时随包一起上传，之后电视/手机上的海报永远从用户自己的 Pages 域名加载，
    不再受第三方图床 / CDN / 防盗链失效影响。

    返回统计 dict：
      copied  已随包发布的海报张数（vod_pic + vod_cover 各算一张）
      missing 本地图库缺失、已置空的张数（避免电视端拿失效相对路径白屏）
      remote  仍是第三方远程链接、未入库的张数
    """
    copied = missing = remote = 0
    img_out = os.path.join(out_dir, "images")
    os.makedirs(img_out, exist_ok=True)
    for v in vods:
        for key in ("vod_pic", "vod_cover"):
            ref = (v.get(key) or "").strip()
            if ref.startswith("images/"):
                fname = os.path.basename(ref[len("images/"):])  # basename 防路径穿越
                src = os.path.join(IMG_SRC_DIR, fname)
                if os.path.isfile(src):
                    dst = os.path.join(img_out, fname)
                    if not os.path.exists(dst):
                        shutil.copy2(src, dst)
                    v[key] = base + "images/" + fname
                    copied += 1
                else:
                    v[key] = ""  # 本地图库没有这张图，置空避免 404
                    missing += 1
            elif ref.startswith("http"):
                remote += 1
    return {"copied": copied, "missing": missing, "remote": remote}


# ---------------- 输出文件 ----------------
def _write_json(path, obj, indent=2):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)


def build_data_json(vods):
    """标准 provide/vod 响应信封（type=3 源 / 兜底直连均可消费）。"""
    return {
        "code": 1, "msg": "ok",
        "page": 1, "pagecount": 1, "limit": len(vods), "total": len(vods),
        "list": vods,
    }


def build_subscribe(base, vods):
    """订阅文件：同时给出 type=2（爬虫）与 type=3（JSON）两个站点，最大化兼容。"""
    base = base.rstrip("/") + "/"
    lines = set()
    for v in vods:
        for ln in (v.get("vod_play_from") or "").split(","):
            if ln:
                lines.add(ln)
    flags = sorted(lines)
    return {
        "sites": [
            {
                "key": "filmcollector_spider",
                "name": SOURCE_NAME + "（爬虫·支持搜索）",
                "type": 2,
                "api": base + "api.js",
                "searchable": 1,
                "quickSearch": 1,
                "filterable": 0,
                "ext": {"flag": flags},
            },
            {
                "key": "filmcollector_json",
                "name": SOURCE_NAME + "（JSON 直连）",
                "type": 3,
                "api": base + "data.json",
                "searchable": 1,
                "quickSearch": 1,
                "filterable": 0,
            },
        ],
        "parses": [],
        "flags": flags,
        "spider": "FilmCollector",
    }


# ---------------- 远程爬虫 api.js ----------------
API_JS_TEMPLATE = r"""// FilmCollector 远程爬虫 (TVBox / CatVod 协议, type=2)
// 纯前端运行：自行读取同目录 data.json，完成 首页/分类/搜索/详情/播放。
// 零服务器、零后端，丢到任意静态托管即用。BASE 由发布工具一次性注入。
var BASE = "__BASE__";
var CACHE = null;

function request(url) {
  // TVBox/CatVod 引擎提供同步 request；此处为兜底（测试/特殊环境）。
  if (typeof __request__ === 'function') return __request__(url);
  throw new Error('request() 不可用');
}

function loadData() {
  if (CACHE) return CACHE;
  var raw = request(BASE + 'data.json');
  CACHE = JSON.parse(raw);
  return CACHE;
}

function classes() {
  var seen = {}, out = [];
  (loadData().list || []).forEach(function (v) {
    var t = v.type_name || '电影';
    if (!seen[t]) { seen[t] = 1; out.push({ type_id: t, type_name: t }); }
  });
  return out;
}

function home() {
  var d = loadData();
  return JSON.stringify({ class: classes(), list: d.list || [], page: 1, pageCount: 1, total: (d.list || []).length, limit: (d.list || []).length });
}
function homeVod() { return home(); }
function homeContent() { return home(); }

function category(tid, pg, filter, extend) {
  var d = loadData();
  var list = (d.list || []).filter(function (v) { return !tid || (v.type_name || '') === tid; });
  return JSON.stringify({ class: classes(), list: list, page: 1, pageCount: 1, total: list.length });
}
function categoryContent(tid, pg, filter, extend) { return category(tid, pg, filter, extend); }

function detail(id) {
  var d = loadData();
  var v = (d.list || []).filter(function (x) { return String(x.vod_id) === String(id); })[0];
  return JSON.stringify({ list: v ? [v] : [] });
}

function search(wd) {
  wd = (wd || '').toLowerCase();
  var d = loadData();
  var list = (d.list || []).filter(function (v) {
    return (v.vod_name || '').toLowerCase().indexOf(wd) >= 0
      || (v.vod_actor || '').toLowerCase().indexOf(wd) >= 0
      || (v.vod_director || '').toLowerCase().indexOf(wd) >= 0
      || (v.type_name || '').toLowerCase().indexOf(wd) >= 0;
  });
  return JSON.stringify({ list: list, page: 1, pageCount: 1, total: list.length });
}

// 播放：传入的 id 即为直链（mp4 等），直接回包。m3u8 等如需解析可在此扩展。
function play(flag, id, flags) {
  return JSON.stringify({ url: id });
}
function proxy(opt) { return ''; }

var rule = { title: 'FilmCollector 公共片库', host: BASE, timeout: 5000, ua: 'Mozilla/5.0 FilmCollector' };
function init() { return JSON.stringify(rule); }
"""


def build_api_js(base):
    return API_JS_TEMPLATE.replace("__BASE__", base.rstrip("/") + "/")


# ---------------- 落地页 ----------------
def build_index_html(base, count, posters=None):
    base = base.rstrip("/") + "/"
    posters = posters or {"copied": 0, "missing": 0, "remote": 0}
    poster_note = (
        f'<div class="card"><h3>🖼 海报已随包发布（不再依赖第三方图源）</h3>'
        f'<p>本次共发布 <b>{posters["copied"]}</b> 张本地海报图到本仓库 <code>{base}images/</code>，'
        f'电视/手机端加载海报时直接读本仓库地址，不受第三方图床 / CDN / 防盗链失效影响。'
        f'{"另有 " + str(posters["remote"]) + " 张海报仍为第三方远程链接（尚未入库本地图库），可在采集工具的「本地素材图库」中处理后重新发布。" if posters["remote"] else ""}</p></div>'
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SOURCE_NAME} · 静态订阅</title>
<style>
 body{{font-family:system-ui,'Microsoft YaHei',sans-serif;max-width:760px;margin:40px auto;padding:0 18px;color:#1b2735;line-height:1.7}}
 h1{{font-size:24px}} code{{background:#eef2f7;padding:2px 6px;border-radius:5px;word-break:break-all}}
 .card{{border:1px solid #d8e0ea;border-radius:12px;padding:16px 18px;margin:14px 0;background:#fafcff}}
 .ok{{color:#1a9d5a}} b{{color:#0a6cff}}
</style>
</head>
<body>
<h1>🎬 {SOURCE_NAME}</h1>
<p class="ok">✅ 这是一套<strong>纯静态</strong>的 TVBox 订阅源，已内置 <b>{count}</b> 部公共领域 / CC 影片（真实可播直链）。</p>
{poster_note}
<div class="card">
  <h3>① 订阅地址（粘贴进 TVBox 的「订阅」框）</h3>
  <p><code>{base}subscribe.json</code></p>
</div>
<div class="card">
  <h3>② 也可直接写进第三方 JSON 配置的 sites[].api</h3>
  <p>支持爬虫的软件用：<br><code>{base}api.js</code>（type=2，支持搜索/分页）</p>
  <p>只认 JSON 接口的软件用：<br><code>{base}data.json</code>（type=3，标准 provide/vod）</p>
</div>
<div class="card">
  <h3>③ 部署方式</h3>
  <p>把本目录全部文件上传到 GitHub Pages / Codeberg Pages / Gitee Pages 等任意静态托管即可。
  详见同目录 <code>DEPLOY.md</code>。</p>
</div>
<p class="hint" style="color:#7a8aa0;font-size:13px">片源来自 Internet Archive 等公共领域 / CC 授权内容，仅供个人学习欣赏。请遵守所在地区法律法规与版权要求。</p>
</body>
</html>
"""


DEPLOY_MD = r"""# 部署说明 · 跨环境通用 TVBox 静态订阅

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
"""


# ---------------- 主流程 ----------------
def build_bundle(source="db", base=None, out_dir=OUT_DEFAULT, name=SOURCE_NAME, clean=False):
    if not base:
        base = "https://YOUR-USERNAME.github.io/FilmCollector"
        print("[publisher] 未指定 --base，已用占位地址，请部署前用 --base 重新生成！")
    base = base.rstrip("/") + "/"

    items = load_items(source)
    if not items:
        raise RuntimeError("没有可发布的内容（检查 --source 或先抓取内容）")
    vods = _to_vods(items)

    if clean and os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    # 0) 海报图库：把本地图库中引用的海报复制进包，并把相对地址改写为公网绝对地址
    posters = _attach_posters(vods, base, out_dir)

    # 1) data.json（含改写后的公网海报地址）
    _write_json(os.path.join(out_dir, "data.json"), build_data_json(vods))
    # 2) api.js（爬虫）
    with open(os.path.join(out_dir, "api.js"), "w", encoding="utf-8") as f:
        f.write(build_api_js(base))
    # 3) subscribe.json
    _write_json(os.path.join(out_dir, "subscribe.json"), build_subscribe(base, vods))
    # 4) 落地页
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index_html(base, len(vods), posters))
    # 5) 部署说明
    with open(os.path.join(out_dir, "DEPLOY.md"), "w", encoding="utf-8") as f:
        f.write(DEPLOY_MD)

    store.log("info", f"静态订阅包生成：{len(vods)} 部 → {out_dir}（base={base}；海报随包发布 {posters['copied']} 张，第三方远程 {posters['remote']} 张）")
    return {
        "out_dir": out_dir,
        "count": len(vods),
        "subscribe": base + "subscribe.json",
        "api_js": base + "api.js",
        "data_json": base + "data.json",
        "posters": posters,
    }


def main():
    ap = argparse.ArgumentParser(description="FilmCollector 静态 TVBox 订阅发布器")
    ap.add_argument("--source", default="db", choices=["db", "demo"], help="内容来源：db(已抓取) / demo(演示)")
    ap.add_argument("--base", default=None, help="托管根地址，如 https://user.github.io/repo")
    ap.add_argument("--out", default=OUT_DEFAULT, help="输出目录")
    ap.add_argument("--clean", action="store_true", help="先清空输出目录")
    args = ap.parse_args()
    r = build_bundle(args.source, args.base, args.out, clean=args.clean)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
