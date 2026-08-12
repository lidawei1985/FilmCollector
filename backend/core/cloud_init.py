# -*- coding: utf-8 -*-
"""
cloud_init.py —— 一键把 FilmCollector 接入云端无人值守（小白只需提供一次 Token）。

把三件事一次性自动化：
  1) 把本机源码（含 .github/workflows 定时任务）推到 GitHub 代码仓库（默认 FilmCollector）；
  2) 生成并部署纯静态订阅包到独立的 Pages 仓库（默认 filmcollector-pages）；
  3) 记住凭据，之后云端每天自动跑、光幕影院 APK 自动拉新片，电脑关机也照常。

仅依赖 requests（与 deployer 一致），零本地 git 依赖。
推源码复用 deployer 的 Git Data API（blob/tree/commit/ref）。
"""
import os
import base64
import time

from . import deployer, publisher, auth_store
from .deployer import _http, DeployError

# 项目根（backend/core/ -> 上两级）
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# 推代码仓库时跳过的目录
_SKIP_DIRS = {".git", "envs", "dist", "build", "tvbox-dist", "output",
              "__pycache__", "node_modules", ".workbuddy", ".idea", ".vscode"}
# 推代码仓库时跳过的文件类型（二进制 / 大文件，云端采集不需要）
_SKIP_EXT = {".exe", ".pyc", ".pyo", ".zip", ".log", ".so", ".dll", ".dmg",
             ".apk", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"}
_MAX_FILE = 1024 * 1024  # 1MB 以上不推（源码不应有，纯防护）


def _collect_source_files():
    """收集要推到代码仓库的源码文件：{相对路径: bytes}。"""
    files = {}
    for root, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in sorted(names):
            p = os.path.join(root, name)
            if not os.path.isfile(p):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in _SKIP_EXT:
                continue
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if sz > _MAX_FILE:
                continue
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            # 额外忽略：图片缓存（运行时生成，非源码）
            if rel.startswith("backend/data/image_cache/"):
                continue
            try:
                with open(p, "rb") as f:
                    files[rel] = f.read()
            except Exception:
                continue
    return files


def _push_source_repo_github(token, repo, username):
    """建代码仓库（含 workflow）并推入全部源码。返回用户名。"""
    api = "https://api.github.com"
    branch = "main"

    sc, me = _http("GET", f"{api}/user", token)
    if sc != 200:
        raise DeployError("GitHub Token 无效或已过期（请重新生成带 public_repo 权限的 Token）。")
    if not username:
        username = me.get("login")
    if not username:
        raise DeployError("无法获取 GitHub 用户名，请手动填写。")

    repo_url = f"{api}/repos/{username}/{repo}"
    sc, rj = _http("GET", repo_url, token)
    if sc == 404:
        sc, cj = _http("POST", f"{api}/user/repos", token, json_data={
            "name": repo, "private": False,
            "description": "FilmCollector 影视采集器（云端无人值守源码）",
            "auto_init": True,
        })
        if sc not in (200, 201):
            msg = (cj.get("message") if isinstance(cj, dict) else str(cj))
            if "name already exists" in str(msg).lower():
                raise DeployError(f"代码仓库 {repo} 已存在但属于其他账号，请换个仓库名。")
            raise DeployError(f"创建代码仓库失败：{msg}")
        # auto_init 创建了初始 commit，稍等后拉取父 sha
        time.sleep(3)
        sc, rj = _http("GET", f"{repo_url}/git/refs/heads/{branch}", token)
        parent = rj.get("object", {}).get("sha") if sc == 200 else None
    elif sc == 200:
        sc, rj = _http("GET", f"{repo_url}/git/refs/heads/{branch}", token)
        parent = rj.get("object", {}).get("sha") if sc == 200 else None
    else:
        msg = (rj.get("message") if isinstance(rj, dict) else str(rj))
        raise DeployError(f"访问代码仓库失败：{msg}")

    files = _collect_source_files()
    if ".github/workflows/auto-collect.yml" not in files:
        raise DeployError("缺少 workflow 文件，无法开启云端定时（请确认项目完整）。")

    blobs = {}
    for name, content in files.items():
        sc, bj = _http("POST", f"{repo_url}/git/blobs", token, json_data={
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        if sc not in (200, 201):
            raise DeployError(f"上传源码文件 {name} 失败（{sc}）。")
        blobs[name] = bj["sha"]

    tree_items = [{"path": n, "mode": "100644", "type": "blob", "sha": s}
                  for n, s in blobs.items()]
    sc, tj = _http("POST", f"{repo_url}/git/trees", token, json_data={"tree": tree_items})
    if sc not in (200, 201):
        raise DeployError("创建源码文件树失败，请重试。")
    tree_sha = tj["sha"]

    sc, cj = _http("POST", f"{repo_url}/git/commits", token, json_data={
        "message": "FilmCollector 源码（含云端定时任务 auto-collect）",
        "tree": tree_sha,
        "parents": [parent] if parent else [],
    })
    if sc not in (200, 201):
        raise DeployError("创建源码提交失败，请重试。")
    commit_sha = cj["sha"]

    ref_url = f"{repo_url}/git/refs/heads/{branch}"
    if parent:
        sc, _ = _http("PATCH", ref_url, token, json_data={"sha": commit_sha})
    else:
        sc, _ = _http("POST", f"{repo_url}/git/refs", token,
                      json_data={"ref": f"heads/{branch}", "sha": commit_sha})
    if sc not in (200, 201):
        raise DeployError("推送源码到 GitHub 失败，请重试。")
    return username


def init_cloud(platform="github", token="", code_repo="FilmCollector",
               subscribe_repo="filmcollector-pages", username=None):
    """
    一键开启云端无人值守。返回结构化结果 dict。

    code_repo:      代码仓库名（含源码 + workflow），默认 FilmCollector
    subscribe_repo: 订阅(Pages)仓库名（纯静态包，APK 导入此地址），默认 filmcollector-pages
    """
    token = (token or "").strip()
    if not token:
        # 回退到本机已记住的凭据（首次填过之后，重开即使框空也能用）
        saved = auth_store.load()
        if saved and saved.get("token"):
            token = saved["token"]
            username = username or saved.get("username") or None
    if not token:
        raise DeployError("请先填写你的 GitHub Access Token（只需一次，工具会记住）。")
    if platform != "github":
        raise DeployError("当前仅支持 GitHub 云端（免费、免实名）。")

    steps = []
    # 1) 推源码（含 workflow 定时任务）
    username = _push_source_repo_github(token, code_repo, username)
    steps.append(f"代码仓库已就绪：https://github.com/{username}/{code_repo}")

    # 2) 生成并部署静态订阅包（初始化 Pages 仓库）
    base = deployer.build_base(platform, username, subscribe_repo)
    publisher.build_bundle(source="db", base=base, out_dir=publisher.OUT_DEFAULT, clean=True)
    res = deployer.deploy(platform, token, publisher.OUT_DEFAULT, subscribe_repo, username)
    steps.append(f"订阅源已部署：{res.get('subscribe')}")

    # 3) 记住凭据（订阅仓库用 subscribe_repo，云端 Actions 据此推片）
    try:
        auth_store.save(platform, username, subscribe_repo, token)
    except Exception:
        pass

    return {
        "ok": True,
        "platform": platform,
        "username": username,
        "code_repo": f"https://github.com/{username}/{code_repo}",
        "subscribe": res.get("subscribe"),
        "pages_note": res.get("pages_note"),
        "steps": steps,
        "msg": ("云端已开启！以后每天自动采集并推送到订阅源，光幕影院 APK 导入一次即可自动更新。"
                "电脑关机、睡觉、出门都照常涨片。"),
    }
