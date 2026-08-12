# 四端客户端框架（安卓TV / 手机安卓 / Windows / 越狱iOS）

本目录是与「影视资源采集器」配套的**观影客户端**原生壳源码。四端共用同一套 Web 客户端
（`backend` 之上的 `frontend/client/`），所有数据来自本机采集工具生成的 JSON 订阅源与本地 API。

> 边界：客户端仅消费你本机的订阅源/本地 API，不内置任何抓取或绕过站点安全校验的能力。
> 采集对象以你有权访问的页面为前提。

## 架构
```
本机采集工具 (Windows EXE / 后端)
   └─ 生成 JSON 订阅源 + 开启本地 API (http://IP:9911)
         ├─ /api.php/provide/vod/   (TVBox 格式)
         └─ /api/generic/vod/       (通用纯净格式)
               ↑
   四端 WebView 壳加载 /client 页面（前端 SPA：浏览/搜索/详情/播放/源管理/清洗）
```

四端壳都是「一个 WebView 加载 `/client`」，区别只在宿主与安装方式：

| 端 | 目录 | 技术 | 安装/运行 |
|---|---|---|---|
| 安卓 TV / 手机 | `android/` | Kotlin + WebView | Android Studio 打开 `platforms/android` → Run；或打包 APK 安装 |
| Windows | `app.py` (`--client`) | WebView2 (系统自带 Chromium) | 双击 EXE → 自动开 `/client`；或浏览器开 `http://127.0.0.1:9911/client` |
| 越狱 iOS | `ios/` | Swift + WKWebView | Xcode 编译后用 Sideloadly / TrollStore / SSH 装到越狱设备 |

## 各端要点
- **安卓**：`MainActivity.kt` 用 WebView 加载 `/client`；菜单可改服务器地址（模拟器默认
  `http://10.0.2.2:9911/client`，真机改成电脑局域网 IP）。支持 TV 的 LEANBACK_LAUNCHER。
- **iOS（越狱）**：`ViewController.swift` 用 WKWebView 加载 `/client`；长按空白处改服务器地址。
  `Info.plist` 已放开 HTTP 明文（本机 http 访问）。编译产物用 Sideloadly/TrollStore 安装。
- **Windows**：`app.py --client` 直接以观影模式启动 WebView2；无 GUI 时也可浏览器访问 `/client`。
  单 EXE 打包见仓库 `build/build_exe.py`（按你的要求，待本地环境就绪后再执行自测）。

## 重要
- 安卓：`platforms/android` 已是**完整 Gradle 工程**（含 `settings.gradle` / 根 `build.gradle` /
  `gradle-wrapper.properties` / AppCompat 主题）。用 Android Studio 打开 `platforms/android` 即可编译 APK
  （首次打开会自动生成 `gradle-wrapper.jar`）。同时支持 TV（`LEANBACK_LAUNCHER`）与手机。
- iOS：`platforms/ios` 已含 **Xcode 工程**（`Client.xcodeproj/project.pbxproj`，已用 pbxproj 解析校验通过），
  用 Xcode 打开即可编译；真机（越狱）用 Sideloadly / TrollStore / SSH 安装。
- APK / IPA 需各自 SDK（Android Studio / Xcode）在对应系统编译，本仓库未包含已编译产物。
- 公共领域 / CC 采集源预设见后端 `backend/core/presets.py`：含单影片 CC 源
  （Big Buck Bunny、Elephants Dream）与「feature_films 公共领域合集」一键批量采集预设，
  均为合法授权源，默认开启。
- 所有播放走客户端内 HTML5 + hls.js（已本地化到 `frontend/client/js/hls.min.js`），
  安卓离线可播 m3u8；iOS/tvOS 走系统原生 HLS。
