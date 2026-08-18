# -*- coding: utf-8 -*-
"""
CI 自动供片：被 GitHub Actions 调用（实现"不依赖本机、永不掉线"的自动供片）。
凭据从环境变量注入（FC_DEPLOY_TOKEN 等）。

关键点（保证"源源不断"且不会把定时任务跑挂）：
- 片库连续性：run_auto 会先从「已部署的 Pages 仓库 db.json」回拉目录（_ensure_continuity），
  所以即使 CI 机器每次都是全新环境，片库也能接着长，不会清零。
- 把增长后的 db.json 与 config.json（含 last_deploy_base 连续性基地址）提交回代码仓库，双保险。
- 无论结果如何都 exit 0：状态写入 health.json / run_status.json，GitHub 不会因连续失败而禁用定时任务。
"""
import os
import sys
import subprocess
import time

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

    # 把增长后的片库目录 + 连续性配置提交回代码仓库，保证下次运行能接着长
    try:
        _git("config", "user.email", "filmcollector@ci.local")
        _git("config", "user.name", "FilmCollector CI")
        _git("add", "backend/data/db.json", "backend/data/config.json")
        code, msg = _git("commit", "-m", "chore: 自动更新片库目录 " + time.strftime("%Y-%m-%d %H:%M"))
        if code == 0:
            _git("push")
            print("片库目录已提交回代码仓库（双保险连续性）。")
        else:
            print("无需提交 db（无变化）。")
    except Exception as e:
        print("db 提交跳过：", e)

    # 永远成功退出：状态已写入 health.json / run_status.json，避免 GitHub 禁用定时任务
    sys.exit(0)


if __name__ == "__main__":
    main()
