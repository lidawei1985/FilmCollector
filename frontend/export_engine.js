// -*- coding: utf-8 -*-
/*
 * export_engine.js —— 纯客户端双格式导出引擎（不依赖任何后端 ingest 逻辑）
 * ------------------------------------------------------------------
 * 职责：字段映射 → 双格式生成 → 必填/JSON 校验 → 海报防盗链本地缓存编排。
 * 全部在浏览器/WebView2 内运行；海报实际落盘由注入的 onPoster 回调负责
 * （本机 127.0.0.1 服务下载，不上传第三方）。
 *
 * 导出两套本地数据源：
 *   1) TVBox 标准：tvbox/catalog.json（数据源配置卡，影视仓/LunaTV/ZYPlayer/OK影视/猫影视可导入）
 *                   tvbox/vod_list.json（maccms 影片列表，影视客户端直接读）
 *   2) 通用纯净：generic/all.json（= 用户标准影片 JSON 样例结构，仅基础元数据，无 TVBox 私有字段）
 */
(function (global) {
  'use strict';
  const FM = (typeof require !== 'undefined') ? require('./field_map') : global.FieldMap;

  // —— TVBox 标准 catalog.json（数据源配置卡）——
  function toTvboxSiteConfig(items, apiBase) {
    return {
      sites: [{
        key: 'filmcollector',
        name: '本地影视采集库 (FilmCollector)',
        type: 1,                                   // 1 = maccms 采集站
        api: apiBase || 'http://127.0.0.1:9911/api.php/provide/vod/',
        searchable: 1,
        quickSearch: 1,
        filterable: 1,
        ext: ''
      }],
      total: items.length
    };
  }

  // —— TVBox 标准影片列表（maccms 兼容，影视客户端订阅源直读）——
  function groupByLine(plist) {
    const lines = {};
    plist.forEach(p => { (lines[p.line] = lines[p.line] || []).push(p); });
    return Object.keys(lines)
      .map(ln => lines[ln].map(e => `${e.name}$${e.url}`).join('#'))
      .join('$$$');
  }
  function toTvboxVodList(items) {
    const list = items.map(it => ({
      vod_id: it.id,
      vod_name: it.name,
      vod_remarks: it.tag ? String(it.tag).replace(/,/g, ' ') : (it.year ? String(it.year) : ''),
      type_name: FM.TYPE_CN[it.type] || it.type,   // EN 枚举 → 中文分区名
      vod_year: it.year ? String(it.year) : '',
      vod_area: it.area || '',
      vod_lang: it.lang || '',
      vod_director: it.director || '',
      vod_actor: it.actor || '',
      vod_content: it.desc || '',
      vod_tag: it.tag || '',
      vod_score: it.score || '',
      vod_pic: it.pic || '',
      vod_sub: it.sub_url || '',
      vod_play_from: [...new Set(it.play_list.map(p => p.line))].join('$$$'),
      vod_play_url: groupByLine(it.play_list)
    }));
    return { code: 1, msg: 'ok', page: 1, pagecount: 1, limit: list.length, total: list.length, list };
  }

  // —— 通用纯净格式（= 用户标准影片 JSON 样例结构，无 TVBox 私有字段）——
  function toGeneric(items) {
    const list = items.map(it => {
      // 单线路时 play_list 项只保留 {name,url}（与样例完全一致）；
      // 多线路时附带 line 字段以表达「多清晰度播放线路」。
      const multiLine = new Set(it.play_list.map(p => p.line)).size > 1;
      const playList = it.play_list.map(p => {
        const o = { name: p.name, url: p.url };
        if (multiLine) o.line = p.line;
        return o;
      });
      const o = {
        id: it.id,
        name: it.name,
        type: it.type,                  // EN 枚举（movie/tv/...），与样例一致
        year: it.year || '',
        area: it.area || '',
        lang: it.lang || '',
        actor: it.actor || '',
        desc: it.desc || '',
        pic: it.pic || '',
        tag: it.tag || '',
        episode_count: it.episode_count || playList.length,
        play_list: playList,
        score: it.score || '',
        director: it.director || '',
        sub_url: it.sub_url || '',
      };
      if (it.alias) o.alias = it.alias; // 可选扩展字段，样例未含，存在才输出
      return o;
    });
    return { code: 1, total: list.length, list };
  }

  // —— 必填字段校验（id/name/type/play_list）+ 返回缺失明细 ——
  function validateDataset(items) {
    const errors = [];
    items.forEach((it, i) => {
      const missing = [];
      FM.REQUIRED.forEach(f => {
        const v = it[f];
        if (v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0)) missing.push(f);
      });
      // play_list 非空但全部缺 url，同样视为无效
      if (Array.isArray(it.play_list) && it.play_list.length && !it.play_list.some(p => p && p.url)) {
        if (!missing.includes('play_list')) missing.push('play_list(url为空)');
      }
      if (missing.length) {
        errors.push({ index: i, id: it.id || '(无id)', name: it.name || '(未命名)', missing });
      }
    });
    return { ok: errors.length === 0, errors };
  }

  // —— 生成两套格式 + JSON 语法自检 ——
  async function exportBoth(items, opts) {
    opts = opts || {};
    const norm = items.map(FM.normalize);            // 字段映射（客户端本地）

    // 海报防盗链补全 + 本地缓存（注入 onPoster 回调，返回本地路径）
    if (opts.onPoster) {
      for (const it of norm) {
        if (it.pic) it.pic = (await opts.onPoster(it.pic, opts.referer)) || it.pic;
      }
    }

    const tvboxConfig = toTvboxSiteConfig(norm, opts.apiBase);
    const tvboxList = toTvboxVodList(norm);
    const generic = toGeneric(norm);

    // JSON 语法校验（导出前自动校验）
    const jsonOk = {};
    [['tvboxConfig', tvboxConfig], ['tvboxList', tvboxList], ['generic', generic]].forEach(([k, o]) => {
      try { JSON.parse(JSON.stringify(o)); jsonOk[k] = true; }
      catch (e) { jsonOk[k] = e.message; }
    });

    return {
      tvbox: { 'catalog.json': tvboxConfig, 'vod_list.json': tvboxList },
      generic: { 'all.json': generic },
      jsonOk,
      count: norm.length
    };
  }

  const api = { toTvboxSiteConfig, toTvboxVodList, toGeneric, validateDataset, exportBoth, normalize: FM.normalize };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.ExportEngine = api;
})(typeof window !== 'undefined' ? window : globalThis);
