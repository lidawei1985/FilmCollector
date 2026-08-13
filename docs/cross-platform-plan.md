# 跨平台一键流软件方案（桌面安装版 + 移动安装版）

## 一、产品定位

| 版本 | 作用 | 目标平台 | 是否依赖电脑 |
|---|---|---|---|
| **桌面安装版** | 采集 + 管理 + 一键发布到公网 | Windows / macOS / Linux | 仅发布/更新时需要 |
| **移动安装版** | 观影客户端：从公网订阅地址同步片库 | Android / iOS | 完全不依赖 |

用户最终路径（一键流）：

1. 打开桌面工具 → 粘贴/选择片源网址
2. 点 **「一键抓取」** → 系统自动解析、清洗、去重、分类
3. 抓取成功后点 **「一键上传公网」**
4. 第一次输入平台 + Token（工具记住，下次自动填充）
5. 获得 `https://.../subscribe.json` 订阅地址
6. 手机/电视 TVBox 粘贴该地址 → 永久可用，不开电脑

---

## 二、代码结构总览

```
FilmCollector/
├── backend/                          # 桌面版核心后端
│   ├── core/
│   │   ├── auth_store.py             # ⭐ 账号/Token 自动记忆与自动填充
│   │   ├── publisher.py              # 生成纯静态 TVBox 订阅包（已存在）
│   │   ├── deployer.py               # ⭐ 一键推送到 GitHub/Gitee Pages（已存在，需接入 auth_store）
│   │   ├── scraper.py                # 一键抓取（已存在）
│   │   ├── cleaner.py                # 清洗去重（已存在）
│   │   ├── classifier.py             # 分类（已存在）
│   │   ├── json_gen.py               # TVBox 字段转换（已存在）
│   │   └── store.py                  # 配置/数据持久化（已存在）
│   └── server.py                     # 本地 HTTP API + 静态资源服务（已存在）
│
├── frontend/                         # 桌面版 WebView UI
│   ├── app.js                        # ⭐ 一键流交互编排（抓取→上传→复制地址）
│   ├── index.html                    # 管理后台页面（已存在）
│   ├── styles.css                    # 样式（已存在）
│   └── client/                       # 观影客户端 UI（移动/桌面共用）
│       ├── index.html
│       ├── js/
│       └── css/
│
├── platforms/                        # 移动端原生壳（WebView 承载 client UI）
│   ├── android/                      # Android Studio 工程 → APK / AAB
│   └── ios/                          # Xcode 工程 → IPA
│
├── build/                            # 跨平台打包脚本
│   ├── build_exe.py                  # Windows 单文件 EXE（已存在）
│   ├── build_mac.py                  # macOS .app + .dmg
│   ├── build_linux.py                # Linux AppImage / deb
│   ├── build_android.py              # 调用 Gradle 编 APK
│   ├── build_ios.py                  # 调用 Xcode 编 IPA
│   └── ci-matrix.yml                 # GitHub Actions 自动三端打包
│
├── mobile/                           # 移动安装版特有逻辑
│   └── sync_engine.py                # 订阅同步、缓存、离线播放
│
└── docs/
    └── cross-platform-plan.md          # 本方案
```

---

## 三、关键模块设计

### 3.1 `auth_store.py` — 账号凭证自动记忆与填充

职责：把平台、用户名、仓库名、Token 加密存在本地；下次打开自动回填表单；提供「一键部署」直接调用。

```python
# backend/core/auth_store.py
import os
from cryptography.fernet import Fernet
from . import store

_KEY_PATH = os.path.join(store.DATA_DIR, ".key")
_CFG_KEY  = "deploy_credentials"

class AuthStore:
    @staticmethod
    def _key():
        # 优先用系统密钥环；没有则退化为机器绑定本地 key
        try:
            import keyring
            k = keyring.get_password("FilmCollector", "deploy_key")
            if k: return k.encode()
        except Exception:
            pass
        if os.path.exists(_KEY_PATH):
            return open(_KEY_PATH, "rb").read()
        k = Fernet.generate_key()
        open(_KEY_PATH, "wb").write(k)
        try:
            import keyring
            keyring.set_password("FilmCollector", "deploy_key", k.decode())
        except Exception:
            pass
        return k

    @classmethod
    def save(cls, platform, username, repo, token):
        """加密保存；token 仅密文本地存储。"""
        f = Fernet(cls._key())
        cfg = store.load_config()
        cfg[_CFG_KEY] = {
            "platform": platform,
            "username": username,
            "repo": repo,
            "token": f.encrypt(token.encode()).decode(),
        }
        store.save_config(cfg)

    @classmethod
    def load(cls):
        """返回解密后的凭据，用于自动填充表单和一键部署。"""
        cfg = store.load_config()
        c = cfg.get(_CFG_KEY)
        if not c:
            return None
        try:
            f = Fernet(cls._key())
            return {
                "platform": c.get("platform"),
                "username": c.get("username"),
                "repo": c.get("repo"),
                "token": f.decrypt(c["token"].encode()).decode(),
            }
        except Exception:
            return None

    @classmethod
    def has(cls):
        return cls.load() is not None

    @classmethod
    def mask_token(cls, token):
        """前端展示用：ghp_***abcd 只显示后 4 位。"""
        if not token: return ""
        if len(token) <= 8: return "*" * len(token)
        return token[:4] + "****" + token[-4:]
```

> **依赖**：新增 `cryptography`（已含在构建依赖）和可选 `keyring`。

---

### 3.2 `deployer.py` — 一键上传公网

已有模块，主要修改点：

```python
# backend/core/deployer.py（改造后入口）
def one_click_deploy(source="db"):
    """不需要前端再传 token；从 auth_store 读取，零输入部署。"""
    cred = AuthStore.load()
    if not cred:
        raise DeployError("首次使用请先粘贴 Access Token 并保存")
    return deploy(
        platform=cred["platform"],
        token=cred["token"],
        source_dir=Publisher.build_bundle(source=source, base=build_base(...))["out_dir"],
        repo=cred["repo"],
        username=cred["username"],
    )
```

前端 `/api/app/deploy` 接口也改造为：不传 token 时默认读 `AuthStore`。

---

### 3.3 `frontend/app.js` — 一键流交互

核心状态机：

```
[粘贴网址] → [一键抓取] → [抓取成功]
                                   ↓
              [一键上传公网] ← [已保存 Token?]
                                   ↓ yes
              [复制订阅地址] → [粘贴到 TVBox]
```

新增/完善函数：

```js
// 加载部署表单时自动填充已保存凭证
async function loadDeployConfig() {
  const r = await api('/api/app/deploy_config');
  if (r.ok && r.cred) {
    $('dep-platform').value = r.cred.platform;
    $('dep-username').value = r.cred.username || '';
    $('dep-repo').value = r.cred.repo || '';
    $('dep-token').placeholder = r.cred.token_mask; // 已记住，留空表示用旧的
    $('dep-msg').textContent = '已记住账号，下次一键部署无需再输入';
  }
}

// 一键抓取：自动判断是单页/列表/集合
async function doOneClickScrape() {
  const url = $('source-url').value.trim();
  if (!url) return;
  $('scrape-btn').disabled = true;
  $('scrape-btn').textContent = '正在抓取…';
  // 调检测接口，自动选择 preset / 内置浏览器 / 普通抓取
  const r = await api('/api/app/scrape_one_click', { method: 'POST', body: JSON.stringify({ url }) });
  $('scrape-btn').textContent = '一键抓取';
  $('scrape-btn').disabled = false;
  if (!r.ok) { alert(r.msg); return; }
  $('upload-box').classList.remove('hidden');   // 成功后显示上传区
  $('upload-btn').classList.add('pulse');       // 提示点下一步
  showMsg(`抓取完成，入库 ${r.count} 部`);
}

// 一键上传：优先用记住的 token，除非用户重新填了
async function doOneClickDeploy() {
  const token = $('dep-token').value.trim();   // 空 = 用记住的
  const r = await api('/api/app/deploy', {
    method: 'POST',
    body: JSON.stringify({ token, source: 'db' })
  });
  if (!r.ok) { alert(r.msg); return; }
  copyText(r.subscribe);
  showMsg('订阅地址已复制，请到 TVBox 粘贴');
}
```

---

## 四、跨平台打包方案

### 4.1 桌面安装版

| 平台 | 打包产物 | 工具/脚本 | 说明 |
|---|---|---|---|
| Windows | `影视资源采集器.exe` | PyInstaller + Inno Setup | 单文件或安装包；用户双击即开 |
| macOS | `FilmCollector.app` + `.dmg` | PyInstaller + `create-dmg` | 签名可用开发者证书或用户手动放行 |
| Linux | `FilmCollector.AppImage` | PyInstaller + appimagetool | 零依赖，双击运行 |

统一入口：

- 都用同一个 `app.py`，自动检测平台。
- `store.py` 区分 `_MEIPASS`（打包态）和源码目录（开发态）。
- 默认依赖清单 `requirements.txt` 中增加 `cryptography`、`keyring`、`requests`、`flask`、`pywebview`。

CI 矩阵（`.github/workflows/build.yml`）：

```yaml
strategy:
  matrix:
    os: [windows-latest, macos-latest, ubuntu-latest]
    include:
      - os: windows-latest: output: FilmCollector-Windows.exe
      - os: macos-latest:   output: FilmCollector-macOS.dmg
      - os: ubuntu-latest:  output: FilmCollector-Linux.AppImage
```

### 4.2 移动安装版

| 平台 | 产物 | 技术 | 说明 |
|---|---|---|---|
| Android | `FilmCollector.apk` / `.aab` | WebView + 本地订阅缓存 | 壳工程 `platforms/android`，加载 `frontend/client` |
| iOS | `FilmCollector.ipa` | WKWebView + 本地订阅缓存 | 壳工程 `platforms/ios`，加载 `frontend/client` |

移动版简化逻辑：

1. 首次打开 → 让用户粘贴订阅地址（或扫二维码）。
2. 把 `subscribe.json` 下载到本地缓存。
3. 后续启动直接读缓存；夜间/手动刷新时重新拉取。
4. 播放时直接走 archive.org 公共直链，不经过任何服务器。

```
mobile/
└── sync_engine.py
    ├── load_subscription(url)        # 拉 subscribe.json
    ├── cache_subscription(data)        # 本地持久化
    ├── get_home()                    # 返回首页分类
    ├── search(keyword)               # 本地搜索
    └── play(vod_id)                  # 返回真实播放 url
```

---

## 五、账号安全与隐私

- **Token 不裸存**：本地只存 Fernet 密文，key 尽量走系统密钥环。
- **不上传用户密码**：GitHub/Gitee 使用 Personal Access Token（平台生成的令牌），可随时在平台撤销，不需要给工具真实密码。
- **账号信息仅用于推送**：用户名/仓库名只用于拼 Pages 地址和调用平台 API。

---

## 六、下一步落地顺序

1. **先补 `auth_store.py`**：把现有 `config.json` 里明文/分散的 Token 迁移到加密存储。
2. **改造 `/api/app/deploy`**：无 token 时自动读 `auth_store`；首次保存 token。
3. **前端一键流 UI 调优**：抓取成功后自动展开「一键上传公网」，Token 框 placeholder 显示「已记住」。
4. **补齐 macOS / Linux 桌面打包脚本**。
5. **补 Android / iOS 观影客户端壳**：让 `frontend/client` 能离线缓存订阅。
6. **接入 CI**：GitHub Actions 自动出三端安装包。

按这个顺序，你就能得到：
- 桌面安装版：采集 + 一键抓取 + 一键上传公网 + 自动记忆账号
- 移动安装版：填入订阅地址即可观影，不依赖电脑
