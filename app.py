# -*- coding: utf-8 -*-
"""
桌面外壳：用系统自带的 WebView2（Chromium 内核，Win10/11 内置，零额外安装）承载前端，
并提供一个「内置浏览器」窗口用于点击元素采集。双击 app.exe 即启动。
"""
import os
import sys
import uuid
import threading
import time
import traceback
from datetime import datetime, timezone

from backend import server
from backend.core import scraper, cleaner, dedup, classifier, store

BASE_DIR = server.store.BASE_DIR

# 注入到内置浏览器窗口的脚本：点击字段按钮 -> 点元素 -> 捕获选择器
INJECT_JS = r"""
(function(){
  if(window.__fc_injected) return; window.__fc_injected=true;
  var field=null, picks=[];
  var bar=document.createElement('div');
  bar.style.cssText='position:fixed;left:0;top:0;right:0;z-index:99999;background:#111c2b;color:#fff;padding:6px;font:13px sans-serif;display:flex;gap:6px;flex-wrap:wrap;align-items:center';
  bar.innerHTML='<b style="color:#4ea1ff">采集模式</b>';
  var fields=['title','aliases','year','region','type','director','actors','description','duration','rating','subtitle','poster','cover'];
  var names={title:'片名',aliases:'别名',year:'年份',region:'地区',type:'类型',director:'导演',actors:'演员',description:'简介',duration:'时长',rating:'评分',subtitle:'字幕',poster:'海报',cover:'封面'};
  fields.forEach(function(f){
    var b=document.createElement('button'); b.textContent=names[f]; b.style.cssText='background:#1f2630;color:#fff;border:1px solid #2a323d;border-radius:6px;padding:3px 8px;cursor:pointer';
    b.onclick=function(){ field=f; document.querySelectorAll('.fc-hl').forEach(function(e){e.style.outline='';e.classList.remove('fc-hl')}); bar.querySelectorAll('button').forEach(function(x){x.style.borderColor='#2a323d'}); b.style.borderColor='#4ea1ff'; };
    bar.appendChild(b);
  });
  var done=document.createElement('button'); done.textContent='完成采集'; done.style.cssText='background:#36c98d;color:#04121f;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;margin-left:auto';
  done.onclick=function(){ window.pywebview.api.submit(document.documentElement.outerHTML, JSON.stringify(picks)); };
  bar.appendChild(done);
  document.body.appendChild(bar);
  function genSel(el){
    if(el.id) return '#'+el.id;
    var parts=[], n=el, depth=0;
    while(n && n.nodeType===1 && depth<6 && n.tagName!=='BODY' && n.tagName!=='HTML'){
      var s=n.tagName.toLowerCase();
      if(n.className && typeof n.className==='string'){ var c=n.className.trim().split(/\s+/)[0]; if(c) s+='.'+c; }
      parts.unshift(s); n=n.parentElement; depth++;
    }
    return parts.join(' ');
  }
  document.addEventListener('click', function(e){
    if(!field) return;
    if(e.target.closest && e.target.closest('#'+bar.id)) return;
    e.preventDefault(); e.stopPropagation();
    var sel=genSel(e.target);
    e.target.style.outline='2px solid #4ea1ff'; e.target.classList.add('fc-hl');
    picks=picks.filter(function(p){return p.field!==field});
    picks.push({field:field, selector:sel, text:e.target.textContent.trim().slice(0,200)});
    field=null; bar.querySelectorAll('button').forEach(function(x){x.style.borderColor='#2a323d'});
  }, true);
})();
"""


class BrowserAPI:
    def __init__(self):
        self.win = None
        self.picks = []

    def open_browser(self, url):
        url = url or "about:blank"
        self.picks = []
        self.win = webview.create_window("内置浏览器 · 点击元素采集", url, js_api=self)
        self.win.loaded += lambda: self._inject()
        return True

    def _inject(self):
        try:
            self.win.evaluate_js(INJECT_JS)
        except Exception:
            pass

    def request_submit(self):
        if self.win:
            try:
                self.win.evaluate_js("window.pywebview.api.submit(document.documentElement.outerHTML, JSON.stringify(window.__fc_picks||[]))")
            except Exception:
                pass

    def submit(self, html, picks_json):
        import json
        try:
            picks = json.loads(picks_json) if picks_json else []
        except Exception:
            picks = []
        template = {"fields": {p["field"]: p["selector"] for p in picks if p.get("selector")}}
        self._run_pipeline(template, html, self._current_url())
        try:
            self.win.destroy()
        except Exception:
            pass

    def _current_url(self):
        try:
            return self.win.get_current_url() or "about:blank"
        except Exception:
            return "about:blank"

    def _run_pipeline(self, template, html, url):
        t0 = time.time()
        raw = scraper.parse_html(template, html, url) if html else []
        if not raw:
            store.log("warn", "内置浏览器采集为空，可能未选择字段或页面无内容")
            return
        cleaned, cstats = cleaner.clean_items(raw)
        merged = dedup.dedup_items(cleaned)
        classified = classifier.classify(merged)
        db = store.load_db()
        now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        for it in classified:
            it["id"] = str(uuid.uuid4())
            it["created_at"] = now
            it["updated_at"] = now
            db["items"].append(it)
        store.save_db(db)
        store.log("info", f"内置浏览器采集入库 {len(classified)} 条（耗时 {round(time.time()-t0,2)}s）")


def run_flask():
    server.run()


def _boot_log(msg):
    try:
        d = os.path.join(BASE_DIR, "output")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "boot.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def main():
    api_only = "--api-only" in sys.argv
    try:
        # 后端在后台线程运行
        t = threading.Thread(target=run_flask, daemon=True)
        t.start()
        time.sleep(1.5)
        cfg = store.load_config()
        host, port = cfg["api_host"], cfg["api_port"]
        _boot_log(f"后端已启动 http://{host}:{port}（api_only={api_only}）")

        # --api-only：仅运行后端 API（无头/服务器/自测模式，不创建 GUI 窗口）
        if api_only:
            _boot_log("API-ONLY 模式：管理后台 http://127.0.0.1:%s/ ｜ 观影客户端 http://127.0.0.1:%s/client" % (port, port))
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                return

        # --client 启动「观影客户端」模式（四端框架的 Windows 端入口）
        client_mode = "--client" in sys.argv

        # 全自动闭环：打开工具即后台自动找片→采集→打包→上传公网→喂指定 APK
        if not client_mode and cfg.get("auto_mode") and cfg.get("auto_on_launch"):
            try:
                from backend.core import auto_pipeline
                threading.Thread(target=auto_pipeline.run_auto, daemon=True).start()
                _boot_log("全自动模式：已在后台启动自动更新片库")
            except Exception as e:
                _boot_log("全自动模式启动失败：" + str(e))

        path = "/client" if client_mode else "/"
        # 注意：工具自身界面一律走本机回环 127.0.0.1（永远可达），
        # 绝不跟着 api_host（可能为 0.0.0.0）走，否则 WebView 加载 0.0.0.0 会 502。
        gui_host = "127.0.0.1"
        url = f"http://{gui_host}:{port}{path}"
        try:
            import webview
            api = BrowserAPI()
            title = "FilmCollector 观影客户端" if client_mode else "影视资源采集器"
            webview.create_window(title, url, js_api=api)
            webview.start()
        except Exception as e:
            # 无 GUI 环境（如服务器/测试）：仅保持后端运行
            _boot_log(f"WebView 不可用，仅启动后端 API：{e}")
            print("WebView 不可用，仅启动后端 API：", e)
            print("观影客户端可手动访问：", f"http://{gui_host}:{port}/client")
            while True:
                time.sleep(3600)
    except Exception:
        _boot_log("FATAL: " + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
