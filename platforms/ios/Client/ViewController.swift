// ViewController.swift —— FilmCollector 观影客户端 · 越狱 iOS 壳
// 仅用 WKWebView 加载本机采集工具提供的 /client 页面。不内置抓取逻辑。
// 编译/安装：Xcode 打开 platforms/ios → 真机（越狱）用 Sideloadly / TrollStore / SSH 安装；
// 或 Xcode 直接 Run 到设备。HTTP 明文已在 Info.plist 放开（NSAppTransportSecurity）。
import UIKit
import WebKit

class ViewController: UIViewController, WKNavigationDelegate {
    private var webView: WKWebView!
    private let defaults = UserDefaults.standard
    private let defaultHost = "http://localhost:9911/client"  // 真机改电脑局域网 IP

    override func viewDidLoad() {
        super.viewDidLoad()
        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        cfg.mediaTypesRequiringUserActionForPlayback = []   // 允许自动播放
        webView = WKWebView(frame: view.bounds, configuration: cfg)
        webView.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        webView.navigationDelegate = self
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        view.addSubview(webView)
        loadHome()
        let long = UILongPressGestureRecognizer(target: self, action: #selector(editHost))
        view.addGestureRecognizer(long)
    }

    private func host() -> String {
        return defaults.string(forKey: "fc_host") ?? defaultHost
    }
    private func loadHome() {
        if let u = URL(string: host()) { webView.load(URLRequest(url: u)) }
    }

    @objc private func editHost() {
        let alert = UIAlertController(title: "服务器地址", message: "本机采集工具 /client", preferredStyle: .alert)
        alert.addTextField { $0.text = self.host() }
        alert.addAction(UIAlertAction(title: "保存", style: .default) { _ in
            if let t = alert.textFields?.first?.text, !t.isEmpty {
                self.defaults.set(t, forKey: "fc_host")
                self.loadHome()
            }
        })
        alert.addAction(UIAlertAction(title: "取消", style: .cancel))
        present(alert, animated: true)
    }

    override var prefersStatusBarHidden: Bool { true }
}
