# -*- coding: utf-8 -*-
"""
CI 自动供片：被 GitHub Actions 调用（实现"不依赖本机、永不掉线"的自动供片）。
凭据从环境变量注入（FC_DEPLOY_TOKEN 等）。

关键点（保证"源源不断"且不会把定时任务跑挂）：
- 片库连续性：run_auto 会先从「已部署的 Pages 仓库 db.json」回拉目录（_ensure_continuity），
  所以即使 CI 机器每次都是全新环境，片库也能接着长，不会清零。
- 注：按 2026-08-19 收口规则，db.json / config.json 已加入 .gitignore，不再回提交「代码仓库」；片库连续性改由 run_auto 从「已部署的 Pages 仓库」回拉（_ensure_continuity）保证。
- 无论结果如何都 exit 0：状态写入 health.json / run_status.json，GitHub 不会因连续失败而禁用定时任务。
"""
import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core import store, auto_pipeline


def _git(*args):
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def main():
    token = os.environ.get("FC_DEPLOY_TOKEN", "").strip()
    platform = os.environ.get("FC_PLATFORM", "github").strip()
    username = os.environ.get("FC_USERNAME", "").strip()
    repo = os.environ.get("FC_REPO", "filmcollector-pages").strip()

    cfg = store.load_config()
    cfg["auto_upload"] = bool(token)
    cfg["auto_max_new"] = int(os.environ.get("FC_MAX_NEW", "20"))
    store.save_config(cfg)

    cred = {"token": token, "platform": platform, "username": username, "repo": repo} if token else None
    try:
        rep = auto_pipeline.run_auto(upload=bool(token), cred=cred)
    except Exception as e:
        print("CI 自动更新异常（已记入 health）：", e)
        rep = {"ok": False, "msg": str(e)}

    print("CI 自动更新结果：", rep)

    if rep.get("needs_token"):
        print("[提醒] 未检测到有效 Token，本次仅本地更新片库、未部署。请在仓库 Secrets 配置 FC_DEPLOY_TOKEN。")

    # 按 2026-08-19 收口规则：backend/data/db.json 与 config.json 已加入 .gitignore，
    # 不再回提交「代码仓库」（避免把运行态/机器相关文件带进 git 历史）。
    # 片库连续性由 auto_pipeline.run_auto 从「已部署的 Pages 仓库」回拉（_ensure_continuity）保证；
    # 订阅产物经 cred 直接推送到独立的 filmcollector-pages 仓库，不经过此处 git 提交代码仓库。

    # 永远成功退出：状态已写入 health.json / run_status.json，避免 GitHub 禁用定时任务
    sys.exit(0)


if __name__ == "__main__":
    main()
