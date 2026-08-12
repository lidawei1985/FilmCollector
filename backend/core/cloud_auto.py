# -*- coding: utf-8 -*-
"""
cloud_auto.py —— 云端无界面入口（供 GitHub Actions / 任意定时任务调用）

设计目标：和本机「一键部署」跑同一套 run_auto 逻辑，但凭据从**环境变量**读取，
绝不落盘、不依赖本机 auth_store。这样同一份代码，本机点按钮、云端定时跑都通用。

环境变量：
  FC_TOKEN       必填，GitHub / Gitee Personal Access Token（带 public_repo 权限）
  FC_USERNAME    选填，缺省由平台反查
  FC_REPO        选填，Pages 仓库名；不存在会自动创建，默认 FilmCollector
  FC_PLATFORM    选填，默认 github
  FC_MAX_NEW     选填，本次最多新增几部，默认 20
  FC_CATEGORIES  选填，逗号分隔的合集筛选（留空=全部）

用法（在仓库根目录执行）：
  python -m backend.core.cloud_auto
"""
import os
import sys
import json

from . import auto_pipeline


def main():
    token = (os.environ.get("FC_TOKEN") or "").strip()
    platform = (os.environ.get("FC_PLATFORM") or "github").strip() or "github"
    username = (os.environ.get("FC_USERNAME") or "").strip() or None
    repo = (os.environ.get("FC_REPO") or "").strip() or "FilmCollector"
    try:
        max_new = int((os.environ.get("FC_MAX_NEW") or "20").strip() or "20")
    except ValueError:
        max_new = 20
    cats = [c.strip() for c in (os.environ.get("FC_CATEGORIES") or "").split(",") if c.strip()]

    # 云端模式：凭据只来自环境变量；本机模式（无 token）则回退 auth_store。
    cred = (
        {"token": token, "platform": platform, "username": username, "repo": repo}
        if token
        else None
    )

    try:
        report = auto_pipeline.run_auto(
            max_new=max_new, upload=True, categories=cats, cred=cred
        )
    except Exception as e:  # 兜底：任何意外都不让定时任务静默崩，把错误写进报告
        print(json.dumps({"ok": False, "msg": str(e)}, ensure_ascii=False))
        sys.exit(0)

    # 输出结构化报告，方便在 Actions 日志里直接看到订阅地址 / 新增数量。
    print(json.dumps(report, ensure_ascii=False, default=str))
    sys.exit(0)


if __name__ == "__main__":
    main()
