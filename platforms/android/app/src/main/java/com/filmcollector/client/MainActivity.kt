package com.filmcollector.client

import android.app.AlertDialog
import android.content.Context
import android.content.SharedPreferences
import android.os.Bundle
import android.view.KeyEvent
import android.view.Menu
import android.view.MenuItem
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity

/**
 * FilmCollector 观影客户端 · 安卓壳（TV / 手机通用）
 * 仅用一个 WebView 加载本机采集工具提供的客户端页面（/client）。
 * 不内置任何抓取逻辑，所有数据来自你本机的 JSON 订阅源与本地 API。
 *
 * 编译：Android Studio 打开 platforms/android 目录 → Run（TV 选 Android TV 设备/模拟器）。
 * 真机：把 BASE_HOST 改成电脑局域网 IP，如 http://192.168.1.20:9911/client
 */
class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private lateinit var prefs: SharedPreferences

    // 模拟器用 10.0.2.2 指向电脑 localhost；真机改成电脑局域网 IP
    private val DEFAULT_HOST = "http://10.0.2.2:9911/client"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("fc", Context.MODE_PRIVATE)
        webView = WebView(this)
        setContentView(webView)

        val ws: WebSettings = webView.settings
        ws.javaScriptEnabled = true
        ws.domStorageEnabled = true
        ws.mediaPlaybackRequiresUserGesture = false   // TV/手机允许自动播放
        ws.allowFileAccess = false
        ws.userAgentString = ws.userAgentString + " FilmCollectorClient/1.0"

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(v: WebView, req: WebResourceRequest): Boolean {
                v.loadUrl(req.url.toString())
                return true
            }
        }
        loadHome()
    }

    private fun loadHome() {
        val host = prefs.getString("host", DEFAULT_HOST) ?: DEFAULT_HOST
        webView.loadUrl(host)
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, "设置服务器地址")
        menu.add(0, 2, 0, "刷新")
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            1 -> { editHost(); true }
            2 -> { loadHome(); true }
            else -> super.onOptionsItemSelected(item)
        }
    }

    private fun editHost() {
        val cur = prefs.getString("host", DEFAULT_HOST) ?: DEFAULT_HOST
        val input = android.widget.EditText(this)
        input.setText(cur)
        AlertDialog.Builder(this)
            .setTitle("服务器地址（本机采集工具 /client）")
            .setView(input)
            .setPositiveButton("保存") { _, _ ->
                prefs.edit().putString("host", input.text.toString()).apply()
                loadHome()
            }
            .setNegativeButton("取消", null)
            .show()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView.canGoBack()) {
            webView.goBack()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }
}
