# -*- coding: utf-8 -*-
"""
poster_cache.py —— FilmCollector 内置「自建海报缓存仓库」
========================================================
把原先独立的 poster-warehouse 方案的能力（下载 → 压缩优化 → 自己托管）
正式并入 FilmCollector 主流程，成为自动流水线的一步，做到「一个整体」。

为什么需要它
------------
TVBox / 光幕影院 类 APK 默认去第三方图床取海报，图床失效 / 防盗链 / 被墙就会白屏。
本模块把每部片的海报：
  1. 下载到本地（archive.org 公共领域片用其官方缩略图服务，零版权风险）；
  2. 用 Pillow 压缩优化（最大宽度 500px、JPEG q82），省流量、加载快；
  3. 随订阅包一起上传到我们自己的 GitHub Pages（images/ 与 repo/img/），
     APK 永远从自己的地址读图，零依赖任何第三方图床 / CDN。

对外：
- backfill(items, save_db)        : 为库里缺失 / 远程海报的影片补齐本地海报（幂等）
- refresh_all(base, save_db, out) : 一站式 = 补齐海报 + 导出自建海报仓库(repo) + 今日精选
"""
from . import store, image_cache, poster_repo


def backfill(items=None, save_db=True):
    """补齐海报：对库里每部片，缺本地海报就用 archive.org 兜底下载并优化。幂等。

    返回 image_cache.backfill_missing 的统计 dict：fixed / skipped / failed。
    """
    if items is None:
        db = store.load_db()
        items = db.get("items", [])
    else:
        db = store.load_db()
    stats = image_cache.backfill_missing(items)
    if save_db:
        db.setdefault("items", [])
        db.setdefault("logs", [])
        db["items"] = items
        store.save_db(db)
        store.log("info",
                  f"海报补齐：新增 {stats['fixed']} / 无图源 {stats['skipped']} / 失败 {stats['failed']}")
    return stats


def refresh_all(base="", save_db=True, out_dir=None):
    """一站式：补齐海报 + 导出自建海报仓库(repo) + 生成今日精选。供发布 / 定时调用。"""
    stats = backfill(save_db=save_db)
    repo = poster_repo.refresh(base=base, out_dir=out_dir)
    total = len(store.load_db().get("items", []))
    store.log("info",
              f"海报仓库刷新完成：库 {total} 部 | 补齐 {stats['fixed']} | "
              f"repo/img {repo['repo']['img']} 张, slide {repo['repo']['slide']} 张 | 精选 {repo['featured']['count']} 部")
    return {
        "backfill": stats,
        "repo": repo["repo"],
        "featured": repo["featured"],
        "poster_cdn": (base.rstrip("/") + "/repo/") if base else "",
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description="FilmCollector 海报缓存仓库：补齐本地海报并刷新自建仓库")
    ap.add_argument("--base", default="", help="托管根地址，如 https://user.github.io/repo")
    ap.add_argument("--out", default=None, help="发布输出目录（默认 tvbox-dist）")
    args = ap.parse_args()
    r = refresh_all(base=args.base, out_dir=args.out)
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
