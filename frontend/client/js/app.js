// -*- coding: utf-8 -*-
// app.js —— 跨平台客户端 SPA 主逻辑
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const api = globalThis.FCApi, platform = globalThis.FCPlatform, player = globalThis.FCPlayer;
  const PLATFORMS = ['desktop', 'tv', 'phone', 'ios'];
  const PLAT_LABEL = { desktop: '🖥 桌面', tv: '📺 TV', phone: '📱 手机', ios: '🍎 iOS' };

  function show(view) {
    document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
    $('view-' + view).classList.remove('hidden');
    document.querySelectorAll('.navbtn').forEach(b => b.classList.toggle('active', b.dataset.view === view));
    if (view === 'sources') renderSources();
    if (view === 'clean') renderClean();
    if (view === 'home') renderHome();
  }

  function posterHtml(pic, name) {
    if (pic) return `<img class="poster" src="${pic}" loading="lazy" onerror="this.outerHTML='<div class=&quot;poster-fallback&quot;>🎞</div>'">`;
    return `<div class="poster-fallback">🎞</div>`;
  }
  function tagsHtml(tag) {
    if (!tag) return '';
    return tag.split(/[，,]/).map(t => `<span class="tag-pill">${t}</span>`).join('');
  }
  function cardHtml(it) {
    const sub = [it.type, it.year, it.area].filter(Boolean).join(' · ');
    return `<div class="card focusable" data-id="${it.id}">
      ${posterHtml(it.pic, it.name)}
      <div class="meta"><div class="title">${it.name || '未命名'}</div><div class="sub">${sub}</div></div>
    </div>`;
  }
  function renderCards(container, list) {
    if (!list.length) { container.innerHTML = '<div class="empty">暂无数据，请先在采集工具生成订阅源或切换数据源。</div>'; return; }
    container.innerHTML = list.map(cardHtml).join('');
    container.querySelectorAll('.card').forEach(c => c.onclick = () => openDetail(c.dataset.id));
  }

  async function renderHome() {
    const cats = $('cat-grid');
    cats.innerHTML = api.CATEGORIES.map(c => `<div class="cat-card focusable" data-cat="${c.key}"><span class="emoji">${c.emoji}</span>${c.key}</div>`).join('');
    cats.querySelectorAll('.cat-card').forEach(el => el.onclick = () => doSearch('', el.dataset.cat));
    try {
      const src = api.getActive();
      const r = await api.fetchList(src, { pg: 1 });
      renderCards($('home-list'), r.list);
    } catch (e) { $('home-list').innerHTML = '<div class="empty">连接数据源失败：' + e.message + '</div>'; }
  }

  async function doSearch(wd, t) {
    wd = (wd !== undefined ? wd : $('search-input').value.trim());
    show('search');
    $('search-list').innerHTML = '<div class="empty">加载中…</div>';
    try {
      const src = api.getActive();
      const r = await api.fetchList(src, { wd: wd || undefined, t: t || undefined, pg: 1 });
      renderCards($('search-list'), r.list);
      $('search-empty').classList.toggle('hidden', r.list.length > 0);
    } catch (e) { $('search-list').innerHTML = '<div class="empty">搜索失败：' + e.message + '</div>'; }
  }

  async function openDetail(id) {
    const v = $('view-detail');
    v.classList.remove('hidden');
    document.querySelectorAll('.view').forEach(x => { if (x.id !== 'view-detail') x.classList.add('hidden'); });
    document.querySelectorAll('.navbtn').forEach(b => b.classList.remove('active'));
    v.innerHTML = '<div class="empty">加载详情中…</div>';
    try {
      const src = api.getActive();
      const it = await api.fetchDetail(src, id);
      if (!it) { v.innerHTML = '<div class="empty">未找到详情。</div>'; return; }
      const lines = it.play && it.play.length ? it.play : [];
      const hasPlay = lines.some(l => l.episodes.length);
      v.innerHTML = `
        <div class="detail-hero">
          <div class="detail-poster">${posterHtml(it.pic, it.name)}</div>
          <div class="detail-info">
            <h2>${it.name}</h2>
            <div class="row">${[it.type, it.year, it.area, it.lang].filter(Boolean).join(' · ')}</div>
            <div class="row">导演：${it.director || '—'} ｜ 演员：${it.actor || '—'}</div>
            <div class="row">评分：${it.score || '—'} ｜ ${tagsHtml(it.tag)}</div>
            <div class="desc">${it.desc || '暂无简介'}</div>
            <div class="episodes">
              <button id="play-now" class="primary focusable" ${hasPlay ? '' : 'disabled'}>▶ 立即播放</button>
              <div class="line-tabs" id="line-tabs"></div>
              <div class="ep-grid" id="ep-grid"></div>
            </div>
          </div>
        </div>`;
      let curLine = 0;
      function renderLines() {
        const lt = $('line-tabs'); lt.innerHTML = '';
        lines.forEach((g, i) => {
          const b = document.createElement('button');
          b.className = 'line-tab focusable' + (i === curLine ? ' active' : '');
          b.textContent = g.line; b.onclick = () => { curLine = i; renderLines(); renderEps(); };
          lt.appendChild(b);
        });
      }
      function renderEps() {
        const eg = $('ep-grid'); eg.innerHTML = '';
        (lines[curLine] ? lines[curLine].episodes : []).forEach(ep => {
          const b = document.createElement('button');
          b.className = 'ep-btn focusable'; b.textContent = ep.name;
          b.onclick = () => player.open(lines, { title: it.name + ' - ' + ep.name });
          eg.appendChild(b);
        });
      }
      renderLines(); renderEps();
      if (hasPlay) $('play-now').onclick = () => player.open(lines, { title: it.name });
    } catch (e) { v.innerHTML = '<div class="empty">详情加载失败：' + e.message + '</div>'; }
  }

  // —— 源管理 ——
  function renderSources() {
    const list = $('src-list');
    const active = api.getActive();
    list.innerHTML = api.loadSources().map(s => `
      <div class="src-item ${s.id === active.id ? 'active' : ''}">
        <div><div class="name">${s.name}</div><div class="url">${s.api}</div></div>
        <span class="badge">${s.kind === 'generic' ? '通用' : 'TVBox'}</span>
        <span class="spacer"></span>
        ${s.id !== active.id ? `<button class="focusable" data-act="use" data-id="${s.id}">使用</button>` : '<button disabled>使用中</button>'}
        ${!s.builtin ? `<button class="focusable" data-act="del" data-id="${s.id}">删除</button>` : ''}
      </div>`).join('');
    list.querySelectorAll('button[data-act]').forEach(b => b.onclick = () => {
      if (b.dataset.act === 'use') { api.setActive(b.dataset.id); renderSources(); }
      if (b.dataset.act === 'del') { api.removeSource(b.dataset.id); renderSources(); }
    });
  }
  async function onAddSource() {
    const url = $('src-url').value.trim();
    const kind = $('src-kind').value;
    try {
      const added = await api.addSource(url, kind);
      const names = Array.isArray(added) ? added.map(a => a.name).join(', ') : added.name;
      $('src-url').value = ''; alert('已添加：' + names);
      renderSources();
    } catch (e) { alert('添加失败：' + e.message); }
  }

  // —— 清洗过滤 ——
  async function runClean() {
    $('clean-stats').innerHTML = '<div class="stat"><div class="num">…</div><div class="lbl">巡检中</div></div>';
    try {
      const r = await fetch('/api/app/clean_dead', { method: 'POST' }).then(x => x.json());
      const dead = r.dead || 0, ad = r.ad_filtered || 0, broken = r.broken_images || 0;
      $('clean-stats').innerHTML = `
        <div class="stat"><div class="num">${dead}</div><div class="lbl">清除失效线路</div></div>
        <div class="stat"><div class="num">${ad}</div><div class="lbl">广告过滤</div></div>
        <div class="stat"><div class="num">${broken}</div><div class="lbl">破损图片</div></div>`;
      loadLogs();
    } catch (e) { $('clean-stats').innerHTML = '<div class="empty">清洗失败：' + e.message + '</div>'; }
  }
  async function loadLogs() {
    try {
      const r = await fetch('/api/app/logs?limit=60').then(x => x.json());
      const box = $('clean-log');
      box.innerHTML = (r.logs || []).map(l => {
        const cls = (l.level === 'error' || l.level === 'warn') ? 'lv-' + l.level : 'lv-info';
        return `<div class="${cls}">[${l.time}] ${l.level} ${l.msg}</div>`;
      }).join('') || '<div class="lv-info">暂无日志</div>';
      box.scrollTop = box.scrollHeight;
    } catch (e) { $('clean-log').innerHTML = '<div class="lv-error">日志读取失败：' + e.message + '</div>'; }
  }
  function renderClean() { $('clean-stats').innerHTML = ''; loadLogs(); }

  // —— 模式切换 ——
  function cyclePlatform() {
    const cur = document.body.getAttribute('data-platform') || 'desktop';
    const next = PLATFORMS[(PLATFORMS.indexOf(cur) + 1) % PLATFORMS.length];
    platform.apply(next);
    $('mode-toggle').textContent = PLAT_LABEL[next];
  }

  // —— 绑定 ——
  function bind() {
    document.querySelectorAll('.navbtn').forEach(b => b.onclick = () => show(b.dataset.view));
    $('search-btn').onclick = () => doSearch();
    $('search-input').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
    $('mode-toggle').onclick = cyclePlatform;
    $('source-btn').onclick = () => show('sources');
    $('src-add').onclick = onAddSource;
    $('clean-run').onclick = runClean;
    $('clean-cache').onclick = runClean;
  }

  function init() {
    platform.initPlatform();
    platform.initSpatialNav();
    bind();
    $('mode-toggle').textContent = PLAT_LABEL[document.body.getAttribute('data-platform')] || '📺 TV';
    show('home');
  }
  document.addEventListener('DOMContentLoaded', init);
})();
