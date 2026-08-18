"""
一键部署器：把 tvbox-dist 静态订阅包推送到 GitHub Pages / Gitee Pages，
返回可公网访问的订阅地址。全程只需用户提供一个 Personal Access Token（一次）。

设计原则：
- 零本地 git 依赖：用平台 Git Data API 直接创建 blob/tree/commit/ref。
- 纯静态：推送的就是 publisher 生成的 tvbox-dist 全部文件。
- 友好报错：捕获无 token / 权限不足 / 网络异常，给出小白能懂的提示。
"""
import os
import base64
import time
import json
import shutil
import tempfile

try:
    import requests
except Exception:  # 极端情况下（不应发生）降级为 urllib
    import urllib.request as _urllib
    import ssl as _ssl
    requests = None

TIMEOUT = 40


class DeployError(Exception):
    """部署过程中的用户级错误，消息应直接展示给小白。"""


def _http(method, url, token, json_data=None, params=None, headers_extra=None):
    """统一请求。返回 (status_code, json_or_text)。"""
    if requests:
        headers = {"User-Agent": "FilmCollector", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"token {token}"
        if headers_extra:
            headers.update(headers_extra)
        try:
            r = requests.request(method, url, headers=headers, json=json_data,
                                 params=params, timeout=TIMEOUT)
        except Exception as e:
            raise DeployError(f"网络请求失败：{e}（请检查网络是否能访问该平台）")
        try:
            body = r.json()
        except Exception:
            body = r.text
        return r.status_code, body
    else:  # urllib 降级
        import urllib.request
        import ssl
        headers = {"User-Agent": "FilmCollector", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"token {token}"
        if headers_extra:
            headers.update(headers_extra)
        data = json.dumps(json_data).encode() if json_data is not None else None
        if params:
            from urllib.parse import urlencode
            url = url + ("&" if "?" in url else "?") + urlencode(params)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT,
                                        context=_ssl._create_unverified_context()) as resp:
                raw = resp.read().decode("utf-8", "replace")
                try:
                    body = json.loads(raw)
                except Exception:
                    body = raw
                return resp.status, body
        except Exception as e:
            return getattr(e, "code", 0), str(e)


def _read_files(source_dir):
    """递归读取源目录下所有文件，key 为相对路径（含子目录，如 images/xxx.jpg）。"""
    files = {}
    for root, _dirs, names in os.walk(source_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                rel = os.path.relpath(p, source_dir).replace("\\", "/")
                with open(p, "rb") as f:
                    files[rel] = f.read()
    return files


def build_base(platform, username, repo):
    """根据用户名/仓库拼出订阅根地址（用于注入 api.js / subscribe.json / index.html）。"""
    if platform == "gitee":
        return f"https://{username}.gitee.io/{repo}"
    return f"https://{username}.github.io/{repo}"


def get_username(platform, token):
    """用 token 反查用户名，避免小白手动填。失败返回 None。"""
    if platform == "github":
        sc, me = _http("GET", "https://api.github.com/user", token)
        if sc == 200:
            return me.get("login")
    elif platform == "gitee":
        sc, me = _http("GET", "https://gitee.com/api/v5/user", token)
        if sc == 200:
            return me.get("login") or me.get("name")
    return None


def deploy(platform, token, source_dir, repo="FilmCollector", username=None):
    """入口。platform: 'github' | 'gitee'。返回 dict（含 subscribe 等公网地址）。"""
    if not token or not token.strip():
        raise DeployError("请先填写你的 Access Token（第一次用需要去平台生成一次，工具会记住）。")
    token = token.strip()
    if not os.path.isdir(source_dir):
        raise DeployError(f"未找到静态包目录：{source_dir}（请先点「生成静态订阅包」）。")
    if platform == "github":
        return _deploy_github(token, source_dir, repo, username)
    elif platform == "gitee":
        return _deploy_gitee(token, source_dir, repo, username)
    else:
        raise DeployError(f"不支持的平台：{platform}")


# ----------------------------- GitHub -----------------------------
def _deploy_github(token, source_dir, repo, username):
    import subprocess
    api = "https://api.github.com"

    # 1) 当前用户
    sc, me = _http("GET", f"{api}/user", token)
    if sc != 200:
        raise DeployError("GitHub Token 无效或已过期（请重新生成一个带 public_repo 权限的 Token）。")
    if not username:
        username = me.get("login")
    if not username:
        raise DeployError("无法获取 GitHub 用户名，请手动填写。")

    repo_url = f"{api}/repos/{username}/{repo}"
    # 2) 仓库是否存在（不存在则建）
    sc, rj = _http("GET", repo_url, token)
    if sc == 404:
        sc, cj = _http("POST", f"{api}/user/repos", token, json_data={
            "name": repo, "private": False,
            "description": "FilmCollector 公共片库 · 纯静态 TVBox 订阅源",
            "auto_init": False,
        })
        if sc not in (200, 201):
            msg = (cj.get("message") if isinstance(cj, dict) else str(cj))
            raise DeployError(f"GitHub Token 权限不足或创建仓库失败（请重新生成带 public_repo 权限的 Token）：{msg}")

    # 3) 部署到「实际被 Pages 服务」的 main 分支（该分支还含 APK 需要的 combined.json）。
    #    采用「克隆 → 覆盖包文件 → 提交 → 推送」的非破坏方式：
    #    只更新静态包内的文件，仓库里原有的 combined.json / apk_feed.json 等一律保留。
    branch = "main"
    auth_remote = f"https://{token}@github.com/{username}/{repo}.git"
    tmp = tempfile.mkdtemp(prefix="fc_deploy_")
    prev_sha = None
    try:
        r = subprocess.run(["git", "clone", "--depth", "1", "--branch", branch,
                            auth_remote, tmp], capture_output=True, text=True)
        if r.returncode != 0:
            # main 尚不存在（全新空仓库）：本地初始化一个 main 分支
            subprocess.run(["git", "init", "-q", tmp], check=True)
            subprocess.run(["git", "-C", tmp, "branch", "-M", branch], check=True)
            subprocess.run(["git", "-C", tmp, "remote", "add", "origin", auth_remote], check=True)
        else:
            # 推送前版本（用于失败回滚）
            prev_sha = _github_prev_sha(tmp)
        # 把静态包内容覆盖进克隆区（目录整体替换，文件直接覆盖；不碰仓库其它文件）
        for name in os.listdir(source_dir):
            s = os.path.join(source_dir, name)
            d = os.path.join(tmp, name)
            if os.path.isdir(s):
                if os.path.isdir(d):
                    shutil.rmtree(d)
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
        subprocess.run(["git", "-C", tmp, "config", "user.email", "filmcollector@local"], check=True)
        subprocess.run(["git", "-C", tmp, "config", "user.name", "FilmCollector"], check=True)
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
        r = subprocess.run(["git", "-C", tmp, "commit", "-q",
                            "-m", "FilmCollector 订阅更新 " + time.strftime("%Y-%m-%d %H:%M")],
                           capture_output=True, text=True)
        if "nothing to commit" in (r.stdout + r.stderr):
            pass  # 内容无变化，无需推送
        elif r.returncode != 0:
            raise DeployError(f"git 提交失败：{(r.stderr or r.stdout)[:200]}")
        else:
            r = subprocess.run(["git", "-C", tmp, "push", "-f", "origin", branch],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise DeployError(f"推送到 GitHub 失败：{(r.stderr or '')[:200]}")
    except DeployError:
        raise
    except Exception as e:
        raise DeployError(f"git 推送异常：{e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 4) 确保 Pages 服务 main 分支（根目录）
    sc, _ = _http("GET", f"{repo_url}/pages", token)
    if sc == 404:
        _http("POST", f"{repo_url}/pages", token,
              json_data={"source": {"branch": branch, "path": "/"}})

    return {
        "platform": "github",
        "username": username,
        "repo": repo,
        "prev_sha": prev_sha,
        "subscribe": f"https://{username}.github.io/{repo}/subscribe.json",
        "api_js": f"https://{username}.github.io/{repo}/api.js",
        "data_json": f"https://{username}.github.io/{repo}/data.json",
        "repo_url": f"https://github.com/{username}/{repo}",
        "pages_note": "GitHub Pages 首次开通需等待 1~3 分钟生效，之后每次更新即时可见。",
    }

# ----------------------------- Gitee -----------------------------
def _deploy_gitee(token, source_dir, repo, username):
    api = "https://gitee.com/api/v5"
    branch = "master"

    sc, me = _http("GET", f"{api}/user", token)
    if sc != 200:
        raise DeployError("Gitee Token 无效或已过期（请重新生成带 projects 权限的令牌）。")
    if not username:
        username = me.get("login") or me.get("name")
    if not username:
        raise DeployError("无法获取 Gitee 用户名，请手动填写。")

    owner_repo = f"{username}/{repo}"
    repo_url = f"{api}/repos/{owner_repo}"
    sc, rj = _http("GET", repo_url, token, params={"access_token": token})
    if sc == 404:
        sc, cj = _http("POST", f"{api}/user/repos", token, json_data={
            "access_token": token, "name": repo, "private": False,
            "description": "FilmCollector 公共片库 · 纯静态 TVBox 订阅源",
        })
        if sc not in (200, 201):
            msg = (cj.get("message") if isinstance(cj, dict) else str(cj))
            raise DeployError(f"创建 Gitee 仓库失败：{msg}")
    elif sc != 200:
        msg = (rj.get("message") if isinstance(rj, dict) else str(rj))
        raise DeployError(f"Gitee Token 失效或权限不足（请重新生成带 projects 权限的令牌）：{msg}")

    # 父提交
    parent = None
    ref_url = f"{repo_url}/git/refs/heads/{branch}"
    sc, rj = _http("GET", ref_url, token, params={"access_token": token})
    if sc == 200:
        parent = rj["object"]["sha"]

    files = _read_files(source_dir)
    blobs = {}
    for name, content in files.items():
        sc, bj = _http("POST", f"{repo_url}/git/blobs", token, json_data={
            "access_token": token,
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        if sc not in (200, 201):
            raise DeployError(f"上传文件 {name} 失败（{sc}）。")
        blobs[name] = bj["sha"]

    tree_items = [{"path": n, "mode": "100644", "type": "blob", "sha": s}
                  for n, s in blobs.items()]
    sc, tj = _http("POST", f"{repo_url}/git/trees", token, json_data={
        "access_token": token, "tree": tree_items})
    if sc not in (200, 201):
        raise DeployError("创建文件树失败，请重试。")
    tree_sha = tj["sha"]

    msg = "FilmCollector 订阅更新 " + time.strftime("%Y-%m-%d %H:%M")
    sc, cj = _http("POST", f"{repo_url}/git/commits", token, json_data={
        "access_token": token, "message": msg, "tree": tree_sha,
        "parents": [parent] if parent else [],
    })
    if sc not in (200, 201):
        raise DeployError("创建提交失败，请重试。")
    commit_sha = cj["sha"]

    if parent:
        sc, _ = _http("PATCH", ref_url, token, json_data={
            "access_token": token, "sha": commit_sha})
    else:
        sc, _ = _http("POST", f"{repo_url}/git/refs", token, json_data={
            "access_token": token, "ref": f"heads/{branch}", "sha": commit_sha})
    if sc not in (200, 201):
        raise DeployError("推送到 Gitee 失败，请重试。")

    # Gitee Pages 需实名，尽力开启，失败给提示
    pages_note = ""
    sc, _ = _http("POST", f"{repo_url}/pages", token, json_data={
        "access_token": token, "branch": branch, "path": "/"})
    if sc not in (200, 201):
        pages_note = ("Gitee Pages 需先实名认证：请到 Gitee 网页「服务」→「Gitee Pages」"
                      "手动开启本仓库的 Pages（分支 master，目录 /），再使用下方地址。")

    return {
        "platform": "gitee",
        "username": username,
        "repo": repo,
        "prev_sha": parent,
        "subscribe": f"https://{username}.gitee.io/{repo}/subscribe.json",
        "api_js": f"https://{username}.gitee.io/{repo}/api.js",
        "data_json": f"https://{username}.gitee.io/{repo}/data.json",
        "repo_url": f"https://gitee.com/{owner_repo}",
        "pages_note": pages_note or "Gitee Pages 更新可能有几分钟延迟。",
    }


if __name__ == "__main__":
    # 简单自检：无 token 时应友好报错
    try:
        deploy("github", "", "tvbox-dist")
    except DeployError as e:
        print("OK 友好报错:", e)


def verify(base, timeout=30):
    """部署后自检：确认线上订阅已真正生效（检查实际发布的 subscribe.json）。"""
    if not base:
        return False
    url = base.rstrip("/") + "/subscribe.json?cb=" + str(int(time.time()))
    try:
        if requests:
            r = requests.get(url, headers={"User-Agent": "FilmCollector"}, timeout=timeout)
        else:
            return False
        if r.status_code != 200:
            return False
        j = r.json()
        # subscribe.json 含 sites 列表即视为生效
        if isinstance(j, dict) and j.get("sites"):
            return True
        # 兜底：某些形态直接是 data.json 结构（list），也可视为生效
        if isinstance(j, dict) and j.get("list"):
            return True
        return False
    except Exception:
        return False


# ----------------------------- 部署前检查 / 线上健康检查 / 回滚 -----------------------------
def preflight(platform, token, source_dir, repo="FilmCollector", username=None, base=None):
    """部署前检查：把一切可在「推送前」发现的问题挡在门外，避免把坏包推上线。

    返回 {ok: bool, checks: [ {name, ok, detail} ]}。任何一项不通过 → ok=False。
    无 Token 时只检查本地部分（仍可生成本地包），不触碰公网。
    """
    checks = []
    # 1) 静态包目录存在且非空
    if os.path.isdir(source_dir) and os.listdir(source_dir):
        checks.append({"name": "静态包目录", "ok": True, "detail": source_dir})
    else:
        checks.append({"name": "静态包目录", "ok": False, "detail": f"未找到或为空：{source_dir}"})

    # 2) 关键产物齐全（subscribe / data / health）
    for fn in ("subscribe.json", "data.json", "health.json"):
        p = os.path.join(source_dir, fn)
        checks.append({"name": f"产物 {fn}", "ok": os.path.isfile(p),
                       "detail": "ok" if os.path.isfile(p) else "缺失"})

    # 3) Token（无则本地流程可继续，但公网推送会被拦）
    if token and token.strip():
        checks.append({"name": "Token", "ok": True, "detail": "已配置"})
        # 4) 平台 API 可达 + Token 有效（仅在有 Token 时联网检查）
        if platform == "github":
            sc, _ = _http("GET", "https://api.github.com/user", token)
            checks.append({"name": "GitHub Token 有效", "ok": sc == 200,
                           "detail": f"HTTP {sc}"})
        elif platform == "gitee":
            sc, _ = _http("GET", "https://gitee.com/api/v5/user", token)
            checks.append({"name": "Gitee Token 有效", "ok": sc == 200,
                           "detail": f"HTTP {sc}"})
        else:
            checks.append({"name": "平台", "ok": False, "detail": f"不支持：{platform}"})
    else:
        checks.append({"name": "Token", "ok": False,
                       "detail": "未配置（仅本地生成，不推送公网）"})

    # 5) 仓库可达（有 Token 时）— 防止推到一个不存在/无权限的仓库
    if token and token.strip() and platform in ("github", "gitee"):
        if platform == "github" and username:
            sc, _ = _http("GET", f"https://api.github.com/repos/{username}/{repo}", token)
            checks.append({"name": "GitHub 仓库可达", "ok": sc in (200, 404),
                           "detail": f"HTTP {sc}（404=将自动创建）"})
        elif platform == "gitee" and username:
            sc, _ = _http("GET", f"https://gitee.com/api/v5/repos/{username}/{repo}",
                          token, params={"access_token": token})
            checks.append({"name": "Gitee 仓库可达", "ok": sc in (200, 404),
                           "detail": f"HTTP {sc}（404=将自动创建）"})

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks}


def online_health_check(base, timeout=30):
    """部署后线上健康检查：逐个确认关键文件在线且为合法 JSON。

    返回 {ok: bool, files: {文件名: {ok, status, kind}}}。
    比 verify() 更细：不只看 subscribe，还看 data / health，便于定位哪类文件没生效。
    """
    out = {}
    ok_all = True
    if not base:
        return {"ok": False, "files": {}}
    files = {
        "subscribe.json": "subscription",
        "data.json": "catalog",
        "health.json": "health",
    }
    for fn, kind in files.items():
        url = base.rstrip("/") + "/" + fn + "?cb=" + str(int(time.time()))
        rec = {"ok": False, "status": 0, "kind": kind}
        try:
            if requests:
                r = requests.get(url, headers={"User-Agent": "FilmCollector"}, timeout=timeout)
                rec["status"] = r.status_code
                if r.status_code == 200:
                    try:
                        r.json()
                        rec["ok"] = True
                    except Exception:
                        rec["ok"] = False
            else:
                rec["status"] = 0
        except Exception:
            rec["status"] = 0
        if not rec["ok"]:
            ok_all = False
        out[fn] = rec
    return {"ok": ok_all, "files": out}


def _github_prev_sha(tmp_clone):
    """读取本地克隆区的当前 HEAD sha（推送前的版本，用于回滚）。"""
    try:
        import subprocess
        r = subprocess.run(["git", "-C", tmp_clone, "rev-parse", "HEAD"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def github_rollback(token, username, repo, branch, prev_sha):
    """把 GitHub 仓库某分支强制回退到 prev_sha（部署失败/验证失败时的回滚）。

    通过 Git Data API 直接更新分支 ref 到旧 commit，等价于「撤销本次推送」。
    返回 True/False。
    """
    if not prev_sha:
        return False
    api = "https://api.github.com"
    ref_url = f"{api}/repos/{username}/{repo}/git/refs/heads/{branch}"
    sc, _ = _http("PATCH", ref_url, token, json_data={"sha": prev_sha})
    return sc in (200, 201)


def rollback(platform, token, repo, username, prev_sha, branch=None):
    """部署失败/线上验证失败后的回滚：把仓库分支强制回到 prev_sha（撤销本次推送）。

    返回 True/False。prev_sha 由 deploy() 返回（推送前的版本）。
    若 prev_sha 为空（如全新空仓库首推），则无旧版本可回，返回 False（本就没什么可丢）。
    """
    if not prev_sha:
        return False
    if platform == "gitee":
        branch = branch or "master"
        ref_url = f"https://gitee.com/api/v5/repos/{username}/{repo}/git/refs/heads/{branch}"
        sc, _ = _http("PATCH", ref_url, token, json_data={"access_token": token, "sha": prev_sha})
        return sc in (200, 201)
    # 默认 github
    branch = branch or "main"
    return github_rollback(token, username, repo, branch, prev_sha)
