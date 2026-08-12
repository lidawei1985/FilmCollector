// -*- coding: utf-8 -*-
// platform.js —— 平台检测 + TV 方向键空间导航（无第三方依赖）
(function (global) {
  'use strict';

  const TV_UA = /TV|Android TV|AppleTV|CrKey|SmartTV|Web0S|Tizen|Hisense|MAG|WindVista/i;
  const IOS_UA = /iPhone|iPad|iPod/i;
  const MOBILE_UA = /Mobi|Mobile|Android(?! TV)|Windows Phone/i;

  function detectPlatform() {
    const ua = navigator.userAgent || '';
    if (TV_UA.test(ua)) return 'tv';
    if (IOS_UA.test(ua)) return 'ios';
    if (MOBILE_UA.test(ua)) return 'phone';
    // 大屏触摸也可视为 TV
    if ((global.screen && global.screen.width >= 1280) && ('ontouchstart' in global) && !IOS_UA.test(ua)) return 'tv';
    return 'desktop';
  }

  function apply(platform) {
    document.body.setAttribute('data-platform', platform);
    global.localStorage && localStorage.setItem('fc_platform', platform);
  }

  function initPlatform() {
    let p = (global.localStorage && localStorage.getItem('fc_platform')) || detectPlatform();
    apply(p);
    return p;
  }

  // —— 方向键空间导航 ——
  function focusables() {
    return Array.from(document.querySelectorAll('.focusable'))
      .filter(el => el.offsetParent !== null && !el.disabled && el.style.display !== 'none');
  }
  function rectOf(el) { return el.getBoundingClientRect(); }
  function center(r) { return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }

  function spatialNext(curr, dir) {
    const els = focusables();
    if (!els.length) return null;
    if (!curr) return els[0];
    const cr = rectOf(curr), cc = center(cr);
    let best = null, bestScore = Infinity;
    for (const el of els) {
      if (el === curr) continue;
      const r = rectOf(el), c = center(r);
      let dx = c.x - cc.x, dy = c.y - cc.y;
      if (dir === 'left' && dx >= -1) continue;
      if (dir === 'right' && dx <= 1) continue;
      if (dir === 'up' && dy >= -1) continue;
      if (dir === 'down' && dy <= 1) continue;
      // 主要轴向距离 + 垂直/水平偏移惩罚
      let primary = (dir === 'left' || dir === 'right') ? Math.abs(dx) : Math.abs(dy);
      let cross = (dir === 'left' || dir === 'right') ? Math.abs(dy) : Math.abs(dx);
      let score = primary + cross * 2;
      if (score < bestScore) { bestScore = score; best = el; }
    }
    return best;
  }

  function initSpatialNav() {
    document.addEventListener('keydown', (e) => {
      const map = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' };
      if (!map[e.key]) return;
      // 播放器全屏时不抢方向键
      if (document.getElementById('player-overlay') && !document.getElementById('player-overlay').classList.contains('hidden')) return;
      e.preventDefault();
      const active = document.activeElement && document.activeElement.classList.contains('focusable') ? document.activeElement : null;
      const next = spatialNext(active, map[e.key]);
      if (next) { next.focus(); next.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
    });
    // Enter / 确认键激活
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        const a = document.activeElement;
        if (a && a.classList && a.classList.contains('focusable') && a.tagName === 'BUTTON') {
          e.preventDefault(); a.click();
        }
      }
    });
  }

  const api = { detectPlatform, initPlatform, apply, initSpatialNav };
  global.FCPlatform = api;
})(typeof window !== 'undefined' ? window : globalThis);
