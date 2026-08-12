// -*- coding: utf-8 -*-
const $ = (id) => document.getElementById(id);
const api = async (path, opts) => {
  const r = await fetch(path, opts || {});
  return r.json();
};

// 安全的 localStorage（部分 WebView2 隐私/无存储路径会抛 SecurityError）
const safeStorage = {
  get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} },
};

function initUI() {
  // ---------- 导航 ----------
  document.querySelectorAll('#nav button').forEach(b => {
    b.onclick = () => {
      document.querySelectorAll('#nav button').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      $('tab-' + b.dataset.tab).classList.add('active');
      if (b.dataset.tab === 'public') loadPresets();
    };
  });

  // ---------- 新手引导 ----------
  const STEPS = [
    ['第一步 · 粘贴链接检测', '打开「站点检测」页，粘贴目标影视网页链接，点击检测。工具会自动告诉你：① 完全支持批量抓取 ② 轻度加密需单集提取 ③ 高强度加密需手动复制。'],
    ['第二步 · 零代码采集', '进入「零代码采集」：最简单用「自动嗅探」粘贴链接即可；桌面端可用「内置浏览器」点击页面元素自动识别字段；高强度站点用「手动添加」粘贴播放地址。'],
    ['第三步 · 清洗与生成', '数据自动清洗、去重、分类、过滤广告。进入「生成 & API」一键生成标准 JSON 订阅源，并开启本地 API 给影视客户端调用。'],
    ['第四步 · 本地与备份', '所有数据仅存本机。图库管理海报，日志查看进度，设置里可定时循环抓取、管理广告黑名单、回滚备份。全程零代码。'],
  ];
  let obIdx = 0;
  function renderOnboard() {
    $('onboard-step').innerHTML = `<b>${STEPS[obIdx][0]}</b><p>${STEPS[obIdx][1]}</p>`;
    $('ob-prev').disabled = obIdx === 0;
    $('ob-next').textContent = obIdx === STEPS.length - 1 ? '完成' : '下一步';
  }
  function showOnboard() {
    if (safeStorage.get('fc_onboarded')) return;
    $('onboard').classList.remove('hidden');
    renderOnboard();
  }
  function finishOnboard() {
    safeStorage.set('fc_onboarded', '1');
    try { $('onboard').classList.add('hidden'); } catch (e) {}
    closeModal(); // 同时关掉可能叠加的弹窗
  }
  $('ob-next').onclick = () => { if (obIdx < STEPS.length - 1) { obIdx++; renderOnboard(); } else { finishOnboard(); } };
  $('ob-prev').onclick = () => { if (obIdx > 0) { obIdx--; renderOnboard(); } };
  $('ob-skip').onclick = finishOnboard;
  $('ob-close').onclick = finishOnboard;
  $('onboard').onclick = (e) => { if (e.target === $('onboard')) finishOnboard(); };
  showOnboard();

  // 弹窗：点击遮罩 / Esc / 关闭按钮 关闭
  $('modal').onclick = (e) => { if (e.target === $('modal')) closeModal(); };
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeModal(); finishOnboard(); } });
  const modalBtn = $('modal-close-btn');
  if (modalBtn) { modalBtn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); closeModal(); }; }
}

// ---------- ① 站点检测 ----------
let lastDetect = { url: '', level: 0, level_text: '' };

async function doDetect() {
  const url = $('detect-url').value.trim();
  if (!url) return;
  lastDetect.url = url;
  $('detect-result').classList.remove('hidden');
  $('detect-result').innerHTML = '检测中…';
  const r = await api('/api/app/detect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) });
  if (!r.ok) { $('detect-result').innerHTML = '失败：' + r.msg; return; }
  lastDetect.level = r.level;
  lastDetect.level_text = r.level_text;
  const lvl = 'lvl' + r.level;
  let action = '';
  if (r.level === 1) {
    action = `<div class="action-box">
      <p>这个链接<b>完全支持自动抓取</b>。确认采集吗？</p>
      <button class="big-btn primary" onclick="doOneClick()">✅ 一键抓取并生成 TVBox + 通用 JSON / API</button>
    </div>`;
  } else if (r.level === 2) {
    action = `<div class="action-box">
      <p>这个链接<b>轻度加密 / 需要单集提取</b>。你可以尝试自动嗅探，或换手动添加。</p>
      <button class="big-btn" onclick="doOneClickTry()">🔄 尝试自动嗅探采集</button>
      <button class="big-btn" onclick="switchTab('scrape')">✏️ 切换到手动添加兜底</button>
    </div>`;
  } else {
    action = `<div class="action-box">
      <p>这个链接<b>高强度加密 / 反爬严格</b>，工具无法自动抓取。</p>
      <button class="big-btn" onclick="switchTab('scrape')">✏️ 去手动添加播放地址</button>
    </div>`;
  }
  $('detect-result').innerHTML = `
    <div class="${lvl}"><b>${r.level_text}</b></div>
    <div>可达：${r.reachable ? '是 (HTTP ' + r.status + ')' : '否'} ｜ 含 m3u8：${r.has_m3u8 ? '是' : '否'} ｜ 含播放器：${r.has_player ? '是' : '否'}</div>
    <div>反爬信号：${r.anti_bot.length ? r.anti_bot.join('、') : '无'}</div>
    <div class="hint">${r.advice}</div>
    ${action}`;
}

function switchTab(tab) {
  document.querySelectorAll('#nav button').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  const btn = document.querySelector('#nav button[data-tab="' + tab + '"]');
  if (btn) btn.classList.add('active');
  const sec = $('tab-' + tab);
  if (sec) sec.classList.add('active');
}

// 一键全自动：检测通过后，采集 → 生成双格式 → 开启本地 API → 弹窗展示结果
async function doOneClick() {
  if (!lastDetect.url) return;
  $('detect-result').innerHTML += '<div class="progress" id="oneclick-progress">正在一键处理：采集 → 生成 → 开 API …</div>';
  try {
    // 1) 采集
    const s = await api('/api/app/scrape', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: lastDetect.url, mode: 'server', max_pages: 1 }) });
    const added = (s && s.added) || 0;
    if (!s || !s.ok) {
      showModal('采集失败', `<p>未能自动采集该页面。</p><p class="hint">${(s && s.msg) || '请尝试「手动添加」方式。'}</p>`);
      return;
    }
    // 2) 生成双格式
    const g = await api('/api/app/generate', { method: 'POST' });
    if (!g || !g.ok) {
      showModal('生成失败', `<p>已采集 ${added} 条，但生成 JSON 失败。</p><p class="hint">${(g && g.msg) || ''}</p>`);
      return;
    }
    // 3) 开启本地 API
    const cfg = await api('/api/app/config');
    const t = await api('/api/app/api/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: true }) });
    refreshApiStatus();
    // 4) 弹窗展示完成结果（同时给出本机地址与局域网地址）
    const net = await api('/api/app/network');
    const localBase = net.local_base, lanBase = net.lan_base;
    const tvboxLocal = `${localBase}/api.php/provide/vod/?ac=list&pg=1`;
    const tvboxLan = `${lanBase}/api.php/provide/vod/?ac=list&pg=1`;
    const genericLocal = `${localBase}/api/generic/vod/?ac=list&pg=1`;
    const genericLan = `${lanBase}/api/generic/vod/?ac=list&pg=1`;
    const tvboxFiles = Object.entries(g.tvbox || {}).map(([k, v]) => `<li>TVBox/${k}：<code>${v}</code></li>`).join('');
    const genericFiles = Object.entries(g.generic || {}).map(([k, v]) => `<li>通用/${k}：<code>${v}</code></li>`).join('');
    const clientUrl = `${localBase}/client`;
    const lanTip = (net.lan_ip && net.lan_ip !== '127.0.0.1')
      ? `<p class="hint">📱 手机 / 电视用「局域网地址」（需保持本软件运行）：<br>TVBox：<code>${tvboxLan}</code><br>通用：<code>${genericLan}</code></p>`
      : '';
    showModal('✅ 一键处理完成',
      `<div class="lvl1">已采集 ${added} 条，生成双格式并开启本地 API</div>
       <p><b>本机订阅地址</b>（电脑上用）：<br>TVBox：<code>${tvboxLocal}</code><br>通用：<code>${genericLocal}</code></p>
       ${lanTip}
       <p><b>本地文件</b></p><ul>${tvboxFiles}${genericFiles}</ul>
       <div class="action-box">
         <button class="big-btn primary" onclick="window.open('${clientUrl}')">🎬 去观影客户端看看</button>
         <button class="big-btn" onclick="copyText('${tvboxLan}')">📋 复制 TVBox(手机/电视)地址</button>
         <button class="big-btn" onclick="copyText('${genericLan}')">📋 复制通用(手机/电视)地址</button>
       </div>`);
  } catch (e) {
    showModal('一键处理出错', `<p>${e.message || e}</p><p class="hint">建议切换到「零代码采集」手动处理。</p>`);
  } finally {
    const pg = $('oneclick-progress');
    if (pg) pg.remove();
    loadLibrary();
  }
}

// 轻度加密时的一键尝试
async function doOneClickTry() {
  if (!lastDetect.url) return;
  $('detect-result').innerHTML += '<div class="progress" id="oneclick-progress">尝试自动嗅探采集中…</div>';
  try {
    const s = await api('/api/app/scrape', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: lastDetect.url, mode: 'server', max_pages: 1 }) });
    if (s && s.ok && s.added > 0) {
      await doOneClick();
    } else {
      showModal('自动嗅探未拿到数据', `<p>该页面轻度加密，自动嗅探未找到可入库的影片。</p><p class="hint">请使用「内置浏览器点击采集」或「手动添加」方式。</p><button class="big-btn" onclick="switchTab('scrape')">去手动添加</button>`);
    }
  } catch (e) {
    showModal('尝试失败', `<p>${e.message || e}</p>`);
  } finally {
    const pg = $('oneclick-progress');
    if (pg) pg.remove();
  }
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => alert('已复制'));
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    alert('已复制');
  }
}

// ---------- ② 采集 ----------
async function doAutoScrape() {
  const urls = $('scrape-urls').value.trim();
  if (!urls) return;
  $('scrape-progress').textContent = '采集中…';
  const r = await api('/api/app/import_urls', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ urls }) });
  $('scrape-progress').textContent = r.ok ? `完成，新增 ${r.added} 条` : '失败';
  loadLogs();
}
function hasPyWeb() { return window.pywebview && window.pywebview.api; }
function openBrowser() {
  const url = $('pick-url').value.trim();
  if (!hasPyWeb()) { alert('内置浏览器仅在桌面端 EXE 中可用；当前可用「自动嗅探」或「手动添加」方式。'); return; }
  window.pywebview.api.open_browser(url);
  $('pick-status').textContent = '已打开内置浏览器，点击页面元素选择字段，完成后点浏览器内「完成采集」。';
}
function doBrowserPick() {
  if (!hasPyWeb()) { alert('请使用桌面端 EXE 的内置浏览器点击采集。'); return; }
  window.pywebview.api.request_submit();
  $('pick-status').textContent = '已提交当前页面进行采集…';
}
async function doManualAdd() {
  const body = {
    title: $('m-title').value, year: $('m-year').value, type: $('m-type').value,
    region: $('m-region').value, poster: $('m-poster').value, line: $('m-line').value,
    play_urls: $('m-urls').value,
  };
  const r = await api('/api/app/manual_add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  alert(r.ok ? '已入库：' + body.title : '失败');
  loadLogs();
}

// ---------- ③ 资源库 ----------
async function loadLibrary() {
  const t = $('lib-type').value, q = $('lib-q').value;
  const r = await api('/api/app/items?type=' + encodeURIComponent(t) + '&q=' + encodeURIComponent(q) + '&limit=200');
  if (!r.ok) return;
  let html = `<table><thead><tr><th>片名</th><th>分区</th><th>年份</th><th>地区</th><th>标签</th><th>集数</th><th>状态</th><th>操作</th></tr></thead><tbody>`;
  for (const it of r.items) {
    const tags = (it.genres || []).map(g => `<span class="tag">${g}</span>`).join('');
    html += `<tr><td>${it.title || '(未命名)'}</td><td>${it.type || ''}</td><td>${it.year || ''}</td><td>${it.region || ''}</td><td>${tags}</td><td>${(it.episodes || []).length}</td><td>${it.status === 'dead' ? '<span class="lvl3">失效</span>' : '<span class="lvl1">正常</span>'}</td><td><button onclick="delItem('${it.id}')">删除</button></td></tr>`;
  }
  html += '</tbody></table>';
  $('lib-table').innerHTML = html;
}
async function delItem(id) {
  await api('/api/app/item/' + id, { method: 'DELETE' });
  loadLibrary(); loadLogs();
}
async function doCleanDead() {
  const r = await api('/api/app/clean_dead', { method: 'POST' });
  alert('巡检完成，清除失效线路 ' + (r.dead || 0) + ' 条');
  loadLibrary();
}

// ---------- ④ 生成 & API ----------
async function doGenerate() {
  const r = await api('/api/app/generate', { method: 'POST' });
  if (!r.ok) return;
  $('gen-result').classList.remove('hidden');
  const terr = (r.tvbox_errors || []).length, gerr = (r.generic_errors || []).length;
  let warn = '';
  if (terr || gerr) {
    warn = `<div class="lvl3">⚠ 校验发现 ${terr + gerr} 处问题（TVBox ${terr} / 通用 ${gerr}），请检查缺失字段：`
      + `<ul>` + [...(r.tvbox_errors || []), ...(r.generic_errors || [])].slice(0, 12).map(e => `<li>${e}</li>`).join('') + `</ul></div>`;
  } else {
    warn = `<div class="lvl1">✔ 两套格式校验通过，无缺失字段</div>`;
  }
  $('gen-result').innerHTML = `已生成 ${r.count} 条 · TVBox 与 通用纯净 各一套，已自动备份两套历史文件。${warn}`;
  const tb = Object.entries(r.tvbox || {}).map(([k, v]) => `<li>TVBox/${k}：<code>${v}</code></li>`).join('');
  const ge = Object.entries(r.generic || {}).map(([k, v]) => `<li>通用/${k}：<code>${v}</code></li>`).join('');
  $('gen-files').innerHTML = `<b>TVBox 标准</b><ul>${tb}</ul><b>通用纯净</b><ul>${ge}</ul>`;
  loadBackups();
}
async function toggleApi() {
  const cfg = await api('/api/app/config');
  const r = await api('/api/app/api/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !cfg.enabled }) });
  renderApiUrls(r);
  $('api-status').textContent = 'API：' + (r.enabled ? '已开启' : '未开启');
  refreshApiStatus();
}
function renderApiUrls(r) {
  if (!r.enabled) { $('api-url').textContent = 'API 未开启'; $('api-btn').textContent = '开启本地 API'; return; }
  $('api-btn').textContent = '关闭本地 API';
  let txt =
    '【本机 · TVBox 标准数据源】影视仓/LunaTV/ZYPlayer/猫影视 等粘贴：\n' + r.tvbox_url + '?ac=list&pg=1\n\n'
    + '【本机 · 通用纯净影片数据源】自研 APP / 网页播放器粘贴：\n' + r.generic_url + '?ac=list&pg=1';
  if (r.api_host === '0.0.0.0' && r.lan_ip && r.lan_ip !== '127.0.0.1') {
    txt += '\n\n【手机/电视 · 同局域网用这个】保持软件运行，TVBox 粘贴：\n' + r.lan_base + '/api.php/provide/vod/?ac=list&pg=1';
  } else {
    txt += '\n\n（如需在手机/电视上随时看，请使用下方「🚀 一键部署到公网」生成全球可用的订阅地址，无需开电脑）';
  }
  $('api-url').textContent = txt;
}
async function refreshApiStatus() {
  const cfg = await api('/api/app/config');
  $('api-status').textContent = 'API：' + (cfg.enabled ? '已开启' : '未开启');
  $('api-btn').textContent = cfg.enabled ? '关闭本地 API' : '开启本地 API';
  if (cfg.enabled) {
    const net = await api('/api/app/network');
    renderApiUrls({ enabled: true, api_host: net.api_host, tvbox_url: `${net.local_base}/api.php/provide/vod/`, generic_url: `${net.local_base}/api/generic/vod/`, lan_base: net.lan_base, lan_ip: net.lan_ip });
  }
}
async function exportFolder() {
  let folder = $('export-folder').value.trim();
  if (!folder) { $('export-msg').textContent = '请先填写目标文件夹路径'; return; }
  const r = await api('/api/app/export_folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder }) });
  $('export-msg').textContent = r.ok ? `已导出 ${r.files.length} 个文件至 ${r.folder}` : ('失败：' + (r.msg || ''));
}

// 一键部署到公网：生成静态包 + 推送到 GitHub/Gitee Pages，返回真实订阅地址
function openTokenHelp() {
  const p = $('dep-platform').value;
  const url = p === 'gitee'
    ? 'https://gitee.com/profile/personal_access_tokens/new'
    : 'https://github.com/settings/tokens/new?description=FilmCollector&scopes=public_repo';
  window.open(url, '_blank');
}

function onPlatformChange() {
  const p = $('dep-platform').value;
  $('dep-username').placeholder = p === 'gitee' ? '用户名（Gitee 必填）' : '用户名（GitHub 可留空）';
}

async function doDeploy() {
  const platform = $('dep-platform').value;
  const token = $('dep-token').value.trim();
  const username = $('dep-username').value.trim();
  const repo = $('dep-repo').value.trim() || 'FilmCollector';
  $('dep-msg').textContent = '正在生成并推送（首次约 10~30 秒）…';
  $('pub-result').classList.remove('hidden');
  $('pub-result').innerHTML = '⏳ 正在生成静态包并推送到公网，请稍候…';
  try {
    const r = await api('/api/app/deploy', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ platform, token, username, repo, source: 'db' }),
    });
    if (!r.ok) { $('pub-result').innerHTML = '❌ ' + (r.msg || '部署失败'); $('dep-msg').textContent = ''; return; }
    $('dep-msg').textContent = '✔ 已部署（Token 已记住）';
    const note = r.pages_note ? `<p class="hint">${r.pages_note}</p>` : '';
    const posters = r.posters || {copied: 0, missing: 0, remote: 0};
    let posterLine;
    if (posters.copied > 0) {
      posterLine = `<div class="lvl1" style="color:#1a9d5a">🖼 已把本地图库中的 <b>${posters.copied}</b> 张海报一起发布到你的仓库，电视/手机上的海报从此从你自己的地址读取，不再依赖任何第三方图源。</div>`;
    } else if (posters.remote > 0) {
      posterLine = `<div class="lvl1" style="color:#b8860b">⚠ 本次没有本地海报入库，仍有 <b>${posters.remote}</b> 张海报是远程链接（可能白屏）。请先在「本地素材图库」或重新采集补全后再发布。</div>`;
    } else {
      posterLine = `<div class="lvl1" style="color:#b8860b">⚠ 本地图库当前为空，发布出去的影片将无图。请先采集影片（采集会自动把海报下载进本地图库）再发布。</div>`;
    }
    $('pub-result').innerHTML =
      posterLine +
      `<div class="lvl1">✔ 已发布 ${r.count} 部到公网（仓库 <a href="${r.repo_url}" target="_blank">${r.repo_url}</a>）</div>` +
      `<p><b>📺 电视/手机 TVBox 订阅地址（粘贴即用）：</b><br><code>${r.subscribe}</code></p>` +
      note +
      `<div class="action-box">` +
      `<button class="big-btn" onclick="copyText('${r.subscribe}')">📋 复制订阅地址</button>` +
      `<button class="big-btn" onclick="copyText('${r.api_js}')">📋 复制爬虫地址(api.js)</button>` +
      `</div>` +
      `<p class="hint">以后只需回到这里点「🚀 一键部署到公网」即可更新（Token 已记住，无需再填）。</p>`;
  } catch (e) {
    $('pub-result').innerHTML = '❌ 出错：' + (e.message || e);
    $('dep-msg').textContent = '';
  }
}

// 页面加载时若已记住部署信息，预填并提示"更新部署"（Token 脱敏，免重复输入）
async function loadDeployConfig() {
  try {
    const r = await api('/api/app/deploy_config');
    const d = r.cred;
    if (d && d.platform) {
      $('dep-platform').value = d.platform;
      if (d.username) $('dep-username').value = d.username;
      if (d.repo) $('dep-repo').value = d.repo;
      if (d.token_mask) {
        const t = $('dep-token');
        if (t) {
          t.value = '';
          t.placeholder = '已记住（' + d.token_mask + '），留空即用旧的';
        }
        $('dep-btn').textContent = '🚀 更新部署（已记住 Token）';
        // 云端自动按钮同样预填"已记住"
        const ct = $('ci-token');
        if (ct) { ct.value = ''; ct.placeholder = '已记住（' + d.token_mask + '），留空即用旧的'; }
        const cb = $('ci-btn');
        if (cb) cb.textContent = '☁️ 开启云端自动（已记住 Token）';
      } else {
        $('dep-btn').textContent = '🚀 一键部署到公网';
      }
      if (typeof onPlatformChange === 'function') onPlatformChange();
    }
  } catch (e) { /* 忽略 */ }
}

// ---------- ☁️ 一键开启云端自动 ----------
async function doCloudInit() {
  const platform = $('ci-platform').value;
  const token = $('ci-token').value.trim();
  const username = $('ci-username').value.trim();
  $('ci-msg').textContent = '正在推源码+开定时+部署订阅（首次约 30~90 秒，请耐心等）…';
  $('ci-result').classList.remove('hidden');
  $('ci-result').innerHTML = '⏳ 正在把源码推到 GitHub 并开启每天定时，请稍候…';
  try {
    const r = await api('/api/app/cloud_init', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ platform, token, username, code_repo: 'FilmCollector', subscribe_repo: 'filmcollector-pages' }),
    });
    if (!r.ok) { $('ci-result').innerHTML = '❌ ' + (r.msg || '开启失败'); $('ci-msg').textContent = ''; return; }
    $('ci-msg').textContent = '✔ 云端已开启';
    const note = r.pages_note ? `<p class="hint">${r.pages_note}</p>` : '';
    $('ci-result').innerHTML =
      `<div class="lvl1">✔ <b>云端已开启！</b>${r.msg || ''}</div>` +
      `<div class="lvl1">📦 代码仓库：<a href="${r.code_repo}" target="_blank">${r.code_repo}</a></div>` +
      `<p><b>📺 光幕影院 APK 订阅地址（粘贴一次即可，之后自动更新）：</b><br><code>${r.subscribe}</code></p>` +
      `<div class="action-box"><button class="big-btn" onclick="copyText('${r.subscribe}')">📋 复制订阅地址</button></div>` +
      note +
      `<p class="hint">以后什么都不用做：每天自动采集→上传，电脑关着也照常。想立刻看效果，可在 GitHub 仓库 Actions 里手动 Run workflow 一次。</p>`;
  } catch (e) {
    $('ci-result').innerHTML = '❌ 出错：' + (e.message || e);
    $('ci-msg').textContent = '';
  }
}

// ---------- 🤖 全自动闭环：找片→采集→打包→上传公网→喂指定 APK ----------
let _autoPollTimer = null;

async function loadAutoStatus() {
  try {
    const st = await api('/api/app/auto/status');
    $('#auto-mode').checked = !!st.auto_mode;
    $('#auto-onlaunch').checked = !!st.auto_on_launch;
    $('#auto-upload').checked = !!st.auto_upload;
    $('#auto-maxnew').value = st.auto_max_new || 20;
    // 合集清单
    const list = (st.collections || []).map(c =>
      `<label class="switch"><input type="checkbox" class="auto-coll" value="${c.key}" ${((st.auto_categories||[]).length===0||st.auto_categories.includes(c.key))?'checked':''}> ${c.label}</label>`
    ).join(' ');
    $('#auto-coll-list').innerHTML = list || '（无）';
    if (!st.has_token) {
      $('#auto-explain').innerHTML = '⚠ 还没填过 Token：全自动的"上传公网+喂 APK"这步暂时跑不了，但<strong>找片+采集入库</strong>仍会自动进行。请先在下方「一键部署到公网」填一次 Token（会记住），之后全自动才完整。';
    }
    renderAutoResult(st.last_result, st.last_run);
  } catch (e) { /* 忽略 */ }
}

async function saveAutoSettings() {
  const colls = Array.from(document.querySelectorAll('.auto-coll:checked')).map(e => e.value);
  const body = {
    auto_mode: $('#auto-mode').checked,
    auto_on_launch: $('#auto-onlaunch').checked,
    auto_upload: $('#auto-upload').checked,
    auto_max_new: parseInt($('#auto-maxnew').value || '20', 10),
    auto_categories: colls,
  };
  try {
    await api('/api/app/auto/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) {}
  // 若关闭全自动，停止轮询
  if (!body.auto_mode && _autoPollTimer) { clearInterval(_autoPollTimer); _autoPollTimer = null; }
  if (body.auto_mode) startAutoPolling();
}

async function doAutoNow() {
  $('#auto-msg').textContent = '⏳ 全自动运行中（找片→采集→打包→上传→喂 APK），可稍后看结果…';
  $('#auto-result').classList.remove('hidden');
  $('#auto-result').innerHTML = '🤖 正在自动更新片库，请稍候…';
  startAutoPolling();
  try {
    const r = await api('/api/app/auto', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
    renderAutoResult({ added: r.added, uploaded: r.uploaded, subscribe: r.subscribe, needs_token: r.needs_token, candidates: r.candidates, errors: r.errors, cost: r.cost }, '');
    $('#auto-msg').textContent = r.ok ? '✔ 本次全自动完成' : ('❌ ' + (r.msg || ''));
  } catch (e) {
    $('#auto-msg').textContent = '❌ 出错：' + (e.message || e);
  }
}

function renderAutoResult(res, lastRun) {
  if (!res || (!res.added && !res.uploaded && !res.needs_token && !res.errors)) {
    if (lastRun) $('#auto-result').innerHTML = `<p class="hint">上次运行：${lastRun}</p>`;
    return;
  }
  let html = '';
  if (res.added !== undefined) {
    html += `<div class="lvl1">🤖 本次新增 <b>${res.added}</b> 部（候选 ${res.candidates ?? '-'} 部）`;
    if (res.cost) html += `，耗时 ${res.cost}s`;
    html += `</div>`;
  }
  if (res.uploaded) {
    html += `<p class="ok">✔ 已自动上传公网并喂给指定 APK</p>`;
    if (res.subscribe) {
      html += `<p><b>📺 订阅地址：</b><br><code>${res.subscribe}</code></p>`;
      html += `<div class="action-box"><button class="big-btn" onclick="copyText('${res.subscribe}')">📋 复制订阅地址</button></div>`;
    }
  } else if (res.needs_token) {
    html += `<p class="warn">⚠ 片已自动采集入库，但还没 Token 无法上传公网。请先在下方「一键部署到公网」填一次 Token，以后全自动就完整了。</p>`;
  }
  if (res.errors && res.errors.length) {
    html += `<p class="warn">部分异常：${res.errors.slice(0,3).join('；')}</p>`;
  }
  if (lastRun) html += `<p class="hint">上次运行：${lastRun}</p>`;
  $('#auto-result').innerHTML = html;
}

function startAutoPolling() {
  if (_autoPollTimer) return;
  _autoPollTimer = setInterval(async () => {
    try {
      const st = await api('/api/app/auto/status');
      if (st.running) {
        $('#auto-msg').textContent = '🤖 全自动正在后台运行…';
        $('#auto-result').classList.remove('hidden');
        $('#auto-result').innerHTML = '🤖 正在自动找片/采集/打包/上传，请稍候…';
      } else {
        // 运行结束，刷新一次结果
        renderAutoResult(st.last_result, st.last_run);
        if (st.last_run) $('#auto-msg').textContent = '✅ 上次自动运行已完成';
      }
    } catch (e) {}
  }, 4000);
}

// ---------- 客户端双格式导出（纯本地：映射/转换/校验，不依赖后端 ingest） ----------
async function doClientExport() {
  $('client-export-msg').textContent = '读取资源库并校验…';
  const r = await api('/api/app/items?limit=10000');
  if (!r.ok) { $('client-export-msg').textContent = '读取失败'; return; }
  const items = r.items || [];
  if (!items.length) { $('client-export-msg').textContent = '资源库为空，请先采集'; return; }

  // 1) 字段映射 + 必填校验（客户端本地）
  const norm = items.map(ExportEngine.normalize);
  const v = ExportEngine.validateDataset(norm);
  if (!v.ok) {
    const rows = v.errors.slice(0, 40).map(e => `<li><b>${e.name}</b> (${e.id}) — 缺失：${e.missing.join('、')}</li>`).join('');
    const extra = v.errors.length > 40 ? `<li>…另有 ${v.errors.length - 40} 条</li>` : '';
    showModal('⚠ 导出校验未通过',
      `<p>以下 <b>${v.errors.length}</b> 条影片存在必填字段缺失（id / name / type / play_list），已阻止导出：</p><ul class="errlist">${rows}${extra}</ul>`);
    $('client-export-msg').textContent = `校验未通过：${v.errors.length} 条缺失`;
    return;
  }

  // 2) 海报防盗链补全 + 本地缓存（onPoster 调本机接口落盘）
  $('client-export-msg').textContent = '海报本地缓存中…';
  const referer = '';
  const onPoster = async (url) => {
    if (!url || !/^https?:\/\//i.test(url)) return url;
    const rr = await api('/api/app/cache_poster', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url, referer }) });
    return rr.ok ? rr.local : url;
  };

  // 3) 生成两套格式 + JSON 语法自检
  const out = await ExportEngine.exportBoth(items, { onPoster, referer });
  const bad = Object.entries(out.jsonOk).filter(([, ok]) => ok !== true);
  if (bad.length) {
    showModal('⚠ JSON 语法错误', `<p>生成结果存在 JSON 语法问题，已阻止保存：</p><ul>${bad.map(([k, m]) => `<li>${k}: ${m}</li>`).join('')}</ul>`);
    $('client-export-msg').textContent = 'JSON 语法校验失败';
    return;
  }

  // 4) 保存本机（自动备份两套格式历史）
  const sr = await api('/api/app/save_client_export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tvbox: out.tvbox, generic: out.generic }) });
  if (!sr.ok) { $('client-export-msg').textContent = '保存失败'; return; }

  $('client-export-result').classList.remove('hidden');
  $('client-export-result').innerHTML =
    `<div class="lvl1">✔ 双格式校验通过，已生成并保存 ${out.count} 条</div>` +
    `<b>TVBox 标准</b><ul>${Object.keys(out.tvbox).map(k => `<li>${k}</li>`).join('')}</ul>` +
    `<b>通用纯净</b><ul>${Object.keys(out.generic).map(k => `<li>${k}</li>`).join('')}</ul>` +
    `<p class="hint">两套格式历史已自动备份至 output/backup</p>`;
  $('client-export-msg').textContent = `完成：保存 ${sr.saved.length} 个文件`;
  loadBackups();
}

function showModal(title, html) {
  $('modal-title').textContent = title || '';
  $('modal-body').innerHTML = html || '';
  $('modal').classList.remove('hidden');
  // 若弹窗内容为空，2 秒后自动消失（避免空弹窗卡死界面）
  if (!title && !html) {
    setTimeout(closeModal, 2000);
  }
}
function closeModal() {
  try { $('modal').classList.add('hidden'); } catch (e) {}
}

// ---------- ⑤ 图库 ----------
async function loadImages() {
  const r = await api('/api/app/images');
  const imgs = r.images || [];
  let html = imgs.map(n => `<div><img src="/api/app/img/${encodeURIComponent(n)}" onerror="this.style.opacity=.2"><br>${n}</div>`).join('');
  html += `<p class="hint">破损图：${(r.broken || []).length} 张</p>`;
  $('img-list').innerHTML = html || '<p class="hint">暂无图片</p>';
}
async function delBrokenImages() {
  const r = await api('/api/app/images');
  alert('请在设置页「一键清空缓存」中删除破损图（' + (r.broken || []).length + ' 张）');
}

// ---------- ⑥ 日志 ----------
async function loadLogs() {
  const r = await api('/api/app/logs');
  $('log-list').innerHTML = (r.logs || []).map(l => `<div>[${l.time}] <span class="lvl${({info:1,warn:2,error:3}[l.level]||1)}">${l.level}</span> ${l.msg}${l.count != null ? ' (' + l.count + ')' : ''}</div>`).join('');
}

// ---------- ⑦ 设置 ----------
async function loadConfig() {
  const c = await api('/api/app/config');
  $('set-interval').value = c.request_interval;
  $('set-rotate').checked = c.rotate_ua;
  $('set-adfilter').checked = c.ad_filter;
  $('set-imgcache').checked = c.image_cache;
  $('set-host').value = c.api_host; $('set-port').value = c.api_port;
  $('set-sched').checked = c.schedule && c.schedule.enabled;
  const ad = await api('/api/app/ad_domains');
  $('set-ad').value = (ad.domains || []).join('\n');
}
async function saveConfig() {
  const body = { request_interval: parseFloat($('set-interval').value), rotate_ua: $('set-rotate').checked, ad_filter: $('set-adfilter').checked, image_cache: $('set-imgcache').checked, api_host: $('set-host').value, api_port: parseInt($('set-port').value), schedule: { enabled: $('set-sched').checked, mode: 'daily' } };
  await api('/api/app/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  alert('已保存');
  refreshApiStatus();
}
async function saveAd() {
  const domains = $('set-ad').value.split('\n');
  await api('/api/app/ad_domains', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ domains }) });
  alert('已更新广告黑名单');
}
async function loadBackups() {
  const r = await api('/api/app/backups');
  const list = (r.backups || []).slice(0, 20).map(b => `<div>${b} <button onclick="restore('${b}')">回滚</button></div>`).join('');
  $('backup-list').innerHTML = list || '<p class="hint">暂无备份</p>';
}
async function restore(name) {
  if (!confirm('确定回滚到 ' + name + ' ？')) return;
  await api('/api/app/restore', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
  alert('已回滚'); loadLibrary();
}
async function clearCache() {
  if (!confirm('确定清空失效资源与破损图片？')) return;
  const r = await api('/api/app/clear_cache', { method: 'POST' });
  alert('已移除失效影片 ' + (r.removed_items || 0) + ' 条，破损图 ' + (r.removed_images || 0) + ' 张');
  loadLibrary(); loadImages();
}

// ---------- 公共领域 / CC 采集源预设 ----------
async function loadPresets() {
  const r = await api('/api/app/presets');
  const box = $('preset-list');
  if (!r.ok) { box.innerHTML = '<div class="hint">读取失败</div>'; return; }
  if (!r.presets.length) { box.innerHTML = '<div class="hint">暂无预设，可在上方添加。</div>'; return; }
  box.innerHTML = `<table><thead><tr><th>名称</th><th>授权</th><th>地址</th><th>状态</th><th>操作</th></tr></thead><tbody>`
    + r.presets.map(p => `<tr>
        <td>${p.name || ''}</td>
        <td>${p.license || '—'}</td>
        <td style="max-width:280px;word-break:break-all">${p.url || '<span class="lvl3">未填地址</span>'}</td>
        <td>${p.enabled ? '<span class="lvl1">已启用</span>' : '<span class="lvl3">未启用</span>'}</td>
        <td>
          ${p.url ? `<button onclick="joinPreset('${p.id}')">加入采集</button>` : ''}
          <button onclick="enablePreset('${p.id}',${!p.enabled})">${p.enabled ? '停用' : '启用'}</button>
          <button onclick="delPreset('${p.id}')">删除</button>
        </td></tr>`).join('') + '</tbody></table>';
}
async function addPreset() {
  const name = $('p-name').value.trim();
  const url = $('p-url').value.trim();
  const license = $('p-license').value.trim() || 'Public Domain';
  if (!name) { alert('请填写源名称'); return; }
  await api('/api/app/presets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, url, license, enabled: false, note: '用户添加' }) });
  $('p-name').value = ''; $('p-url').value = '';
  loadPresets();
}
async function enablePreset(id, on) {
  await api('/api/app/presets', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, enabled: on }) });
  loadPresets();
}
async function delPreset(id) {
  if (!confirm('删除该预设？')) return;
  await api('/api/app/presets?id=' + id, { method: 'DELETE' });
  loadPresets();
}
// 加入采集：走 检测→采集 本地链路（仅当你确认有权访问该页面）
async function joinPreset(id) {
  const r = await api('/api/app/presets');
  const p = (r.presets || []).find(x => x.id === id);
  if (!p || !p.url) { alert('该预设未填写地址'); return; }
  if (!confirm('确认你有权访问该页面、且内容为公有领域/CC 授权？\n即将对：' + p.url + '\n执行本地检测与采集。')) return;
  $('pick-status').textContent = '检测中：' + p.url;
  const d = await api('/api/app/detect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: p.url }) });
  if (d.level === 3) {
    alert('该站点高强度加密，已转入「手动添加」兜底（在「零代码采集-方式三」粘贴播放地址）。');
    document.querySelector('#nav button[data-tab="scrape"]').click();
    return;
  }
  const s = await api('/api/app/scrape', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: p.url, mode: d.level === 2 ? 'manual' : 'server', pages: 1 }) });
  alert('采集完成，新增 ' + (s.added || 0) + ' 条。可在「资源库」查看，再到「生成 & API」产出订阅源。');
  loadLibrary();
}

// 初始化：DOM 就绪后再绑定事件，避免 WebView2 中元素未找到
function boot() { initUI(); loadConfig(); loadDeployConfig(); loadAutoStatus(); refreshApiStatus(); loadLogs(); startAutoPolling(); }
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}

// 兜底保险：防止任何遮罩卡死界面
setTimeout(() => {
  // 若弹窗内容为空，强制关闭
  try {
    const mt = $('modal-title').textContent || '';
    const mb = ($('modal-body').textContent || '').trim();
    if (!$('modal').classList.contains('hidden') && !mt && !mb) closeModal();
  } catch (e) {}
  // 8 秒后若向导仍卡着，自动进入主界面
  try {
    if (!$('onboard').classList.contains('hidden')) {
      safeStorage.set('fc_onboarded', '1');
      $('onboard').classList.add('hidden');
      closeModal();
    }
  } catch (e) {}
}, 8000);
