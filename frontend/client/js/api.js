// -*- coding: utf-8 -*-
// api.js —— 本地 API 客户端 + 源管理 + 双格式归一化（纯前端）
(function (global) {
  'use strict';

  // 当期来源主机：经后端 /client 访问时用相对地址；原生壳 file:// 打开时用存储的主机
  function currentOrigin() {
    try {
      if (location.protocol.startsWith('http')) return location.origin;
    } catch (e) {}
    return (global.localStorage && localStorage.getItem('fc_host')) || 'http://127.0.0.1:9911';
  }

  function defaultSources() {
    const o = currentOrigin();
    return [
      { id: 'local_tvbox', name: '本机采集库 (TVBox)', kind: 'tvbox', api: o + '/api.php/provide/vod/', searchable: 1, builtin: true },
      { id: 'local_generic', name: '本机采集库 (通用纯净)', kind: 'generic', api: o + '/api/generic/vod/', searchable: 1, builtin: true },
    ];
  }

  function loadSources() {
    let s = [];
    try { s = JSON.parse(localStorage.getItem('fc_sources') || '[]'); } catch (e) {}
    if (!s.length) { s = defaultSources(); saveSources(s); }
    return s;
  }
  function saveSources(s) { localStorage.setItem('fc_sources', JSON.stringify(s)); }
  function getActive() {
    const id = localStorage.getItem('fc_active');
    const all = loadSources();
    return all.find(x => x.id === id) || all[0];
  }
  function setActive(id) { localStorage.setItem('fc_active', id); }

  // 添加源：支持 catalog.json（解析 sites[]）或直接 api 地址
  async function addSource(input, kind) {
    input = (input || '').trim();
    if (!input) throw new Error('链接为空');
    let apiUrl = input;
    if (/\.json(\?|$)/i.test(input) || /catalog/i.test(input)) {
      const res = await fetch(input);
      if (!res.ok) throw new Error('catalog.json 下载失败 ' + res.status);
      const cfg = await res.json();
      const added = [];
      (cfg.sites || []).forEach(s => {
        if (s.api) {
          const id = 'src_' + Date.now() + '_' + added.length;
          added.push({ id, name: s.name || s.key || '订阅源', kind: kind || 'tvbox', api: s.api, searchable: s.searchable !== 0 });
        }
      });
      if (!added.length) throw new Error('catalog.json 中未找到可用的 sites[].api');
      const all = loadSources(); added.forEach(a => all.push(a)); saveSources(all);
      return added;
    }
    // 直连 api
    if (!/^https?:\/\//i.test(apiUrl)) apiUrl = 'http://' + apiUrl;
    const id = 'src_' + Date.now();
    const all = loadSources();
    all.push({ id, name: input.replace(/^https?:\/\//, '').slice(0, 40), kind: kind || 'tvbox', api: apiUrl, searchable: 1 });
    saveSources(all);
    return all[all.length - 1];
  }
  function removeSource(id) {
    const all = loadSources().filter(s => s.id !== id);
    saveSources(all);
    if (getActive().id === id) setActive(all[0] && all[0].id);
  }

  // —— 请求 ——
  async function rawList(source, params) {
    const q = new URLSearchParams(Object.assign({ ac: 'list', pg: 1 }, params));
    const res = await fetch(source.api + '?' + q.toString());
    if (!res.ok) throw new Error('请求失败 ' + res.status);
    return await res.json();
  }

  function parseTvboxPlay(from, url) {
    const lines = String(from || '').split('$$$').filter(Boolean);
    const groups = String(url || '').split('$$$');
    return lines.map((ln, i) => ({
      line: ln,
      episodes: String(groups[i] || '').split('#').map(s => s.trim()).filter(Boolean)
        .map(ep => { const [n, u] = ep.includes('$') ? ep.split('$') : [null, ep]; return { name: n || '正片', url: u || ep }; })
        .filter(e => e.url)
    }));
  }
  function parseGenericPlay(play_list) {
    const map = {};
    (play_list || []).forEach(p => { (map[p.line || '默认线路'] = map[p.line || '默认线路'] || []).push({ name: p.name, url: p.url }); });
    return Object.keys(map).map(ln => ({ line: ln, episodes: map[ln] }));
  }

  function normalize(raw, kind) {
    if (kind === 'generic') {
      return {
        id: raw.id, name: raw.name, type: raw.type, year: raw.year || '', area: raw.area || '',
        lang: raw.lang || '', actor: raw.actor || '', director: raw.director || '', desc: raw.desc || '',
        pic: raw.pic || '', score: raw.score || '', tag: raw.tag || '', remarks: '', sub_url: raw.sub_url || '',
        play: parseGenericPlay(raw.play_list)
      };
    }
    return {
      id: raw.vod_id, name: raw.vod_name, type: raw.type_name || '', year: raw.vod_year || '', area: raw.vod_area || '',
      lang: raw.vod_lang || '', actor: raw.vod_actor || '', director: raw.vod_director || '', desc: raw.vod_content || '',
      pic: raw.vod_pic || '', score: raw.vod_score || '', tag: raw.vod_tag || '', remarks: raw.vod_remarks || '', sub_url: raw.vod_sub || '',
      play: parseTvboxPlay(raw.vod_play_from, raw.vod_play_url)
    };
  }

  async function fetchList(source, opts) {
    opts = opts || {};
    const params = { pg: opts.pg || 1 };
    if (opts.t) params.t = opts.t;
    if (opts.wd) params.wd = opts.wd;
    const data = await rawList(source, params);
    const list = (data.list || []).map(it => normalize(it, source.kind));
    return { total: data.total || list.length, page: data.page || 1, pagecount: data.pagecount || 1, list };
  }
  async function fetchDetail(source, id) {
    const q = new URLSearchParams({ ac: 'detail', ids: id });
    const res = await fetch(source.api + '?' + q.toString());
    const data = await res.json();
    const raw = (data.list || [])[0];
    return raw ? normalize(raw, source.kind) : null;
  }

  const CATEGORIES = [
    { key: '电影', emoji: '🎬' }, { key: '连续剧', emoji: '📺' }, { key: '短剧', emoji: '🎭' },
    { key: '动漫', emoji: '🌸' }, { key: '综艺', emoji: '🎤' }, { key: '纪录片', emoji: '🎞' }, { key: '少儿', emoji: '🧒' },
  ];

  const api = {
    currentOrigin, defaultSources, loadSources, saveSources, getActive, setActive, addSource, removeSource,
    fetchList, fetchDetail, normalize, CATEGORIES, parseTvboxPlay, parseGenericPlay
  };
  global.FCApi = api;
})(typeof window !== 'undefined' ? window : globalThis);
