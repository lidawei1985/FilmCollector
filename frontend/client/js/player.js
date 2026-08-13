// -*- coding: utf-8 -*-
// player.js —— 跨平台播放器（m3u8/mp4，hls.js 或原生 HLS 降级）
(function (global) {
  'use strict';

  let hls = null;

  function ensureOverlay() {
    let ov = document.getElementById('player-overlay');
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'player-overlay';
      ov.className = 'hidden';
      ov.innerHTML = `
        <video id="player-video" playsinline webkit-playsinline controls></video>
        <div class="player-bar">
          <span class="ptitle" id="player-title"></span>
          <button id="player-fs" class="focusable">⛶ 全屏</button>
          <button id="player-close" class="focusable">✕ 关闭</button>
        </div>
        <div id="player-eps" class="ep-grid" style="padding:12px 18px;overflow-y:auto;max-height:32vh"></div>`;
      document.body.appendChild(ov);
      ov.querySelector('#player-close').onclick = close;
      ov.querySelector('#player-fs').onclick = toggleFs;
    }
    return ov;
  }

  function destroyHls() { if (hls) { try { hls.destroy(); } catch (e) {} hls = null; } }

  function playUrl(video, url) {
    destroyHls();
    const isHls = /\.m3u8(\?|$)/i.test(url) || /m3u8/i.test(url);
    if (isHls && global.Hls && global.Hls.isSupported()) {
      hls = new global.Hls({ maxBufferLength: 30, capLevelToPlayerSize: true });
      hls.loadSource(url);
      hls.attachMedia(video);
      hls.on(global.Hls.Events.ERROR, (e, data) => {
        if (data && data.fatal) {
          if (data.type === global.Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
          else if (data.type === global.Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
          else destroyHls();
        }
      });
    } else if (isHls && video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url; // 原生 HLS：Safari / iOS / tvOS
    } else {
      video.src = url; // mp4 / 其它
    }
    video.play().catch(() => {});
  }

  function open(groups, opt) {
    opt = opt || {};
    const ov = ensureOverlay();
    ov.classList.remove('hidden');
    const video = ov.querySelector('#player-video');
    const titleEl = ov.querySelector('#player-title');
    const epsEl = ov.querySelector('#player-eps');
    titleEl.textContent = opt.title || '播放';

    // 展开所有线路的分集到一个网格，标注线路名
    let flat = [];
    groups.forEach(g => g.episodes.forEach((ep, i) => flat.push({ ...ep, line: g.line, idx: i })));

    function renderEps(activeUrl) {
      epsEl.innerHTML = '';
      flat.forEach(ep => {
        const b = document.createElement('button');
        b.className = 'ep-btn focusable' + (ep.url === activeUrl ? ' playing' : '');
        b.textContent = (groups.length > 1 ? '【' + ep.line + '】' : '') + ep.name;
        b.onclick = () => { playUrl(video, ep.url); renderEps(ep.url); };
        epsEl.appendChild(b);
      });
    }

    const start = flat[0];
    if (start) { playUrl(video, start.url); renderEps(start.url); }
    video.scrollIntoView();
  }

  function close() {
    const ov = document.getElementById('player-overlay');
    if (ov) { const v = ov.querySelector('#player-video'); if (v) { v.pause(); v.removeAttribute('src'); } destroyHls(); ov.classList.add('hidden'); }
  }
  function toggleFs() {
    const v = document.getElementById('player-video');
    if (!v) return;
    if (!document.fullscreenElement) (v.requestFullscreen || v.webkitRequestFullscreen || (() => {})).call(v);
    else (document.exitFullscreen || document.webkitExitFullscreen || (() => {})).call(document);
  }

  global.FCPlayer = { open, close };
})(typeof window !== 'undefined' ? window : globalThis);
