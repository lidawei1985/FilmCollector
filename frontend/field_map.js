// -*- coding: utf-8 -*-
/*
 * field_map.js —— 标准影片 JSON 字段样例 & 映射规则（纯前端，无后端依赖）
 * ------------------------------------------------------------------
 * 本标准模型严格对齐用户提供的「标准影片 JSON 样例」：
 * {
 *   "id": "10001",
 *   "name": "测试影片",
 *   "type": "movie",
 *   "year": "2025",
 *   "area": "大陆",
 *   "lang": "普通话",
 *   "actor": "张三,李四",
 *   "desc": "影片简介内容",
 *   "pic": "https://test-img.com/demo.jpg",
 *   "tag": "喜剧,动作",
 *   "episode_count": 12,
 *   "play_list": [ { "name": "第01集", "url": "shturl.cc/KE2jwqKSmGWOUU5zzBOr" } ],
 *   "score": "7.2",
 *   "director": "导演A",
 *   "sub_url": "https://demo-sub.com/sub1.vtt"
 * }
 * 其中 id/name/type/play_list 为必填；其余为可选元数据。
 * alias（别名）为可选扩展字段，样例未含，存在时保留、缺失时省略。
 *
 * 若接入第三方样例，只需维护 STANDARD_SCHEMA / ALIASES / TYPE_EN / TYPE_CN，
 * 导出引擎与校验逻辑无需改动。
 */
(function (global) {
  'use strict';

  // ===== 标准影片模型字段样例（= 通用纯净格式字段集）=====
  const STANDARD_SCHEMA = {
    id:            { required: true,  desc: '唯一标识',                example: '10001' },
    name:          { required: true,  desc: '片名',                   example: '测试影片' },
    type:          { required: true,  desc: '分区枚举(电影/连续剧/短剧/动漫/综艺/纪录片/少儿)', example: 'movie' },
    play_list:     { required: true,  desc: '播放列表 [{name,url,line?}]', example: '[{name:"第01集",url:"..."}]' },
    year:          { required: false, desc: '上映年份',                example: '2025' },
    area:          { required: false, desc: '地区',                   example: '大陆' },
    lang:          { required: false, desc: '语言',                   example: '普通话' },
    actor:         { required: false, desc: '演员(逗号分隔字符串)',     example: '张三,李四' },
    desc:          { required: false, desc: '剧情简介',               example: '影片简介内容' },
    pic:           { required: false, desc: '高清海报地址',           example: 'https://test-img.com/demo.jpg' },
    tag:           { required: false, desc: '题材标签(逗号分隔字符串)', example: '喜剧,动作' },
    episode_count: { required: false, desc: '分集数',                 example: 12 },
    score:         { required: false, desc: '评分',                   example: '7.2' },
    director:      { required: false, desc: '导演',                   example: '导演A' },
    sub_url:       { required: false, desc: '字幕地址',               example: 'https://demo-sub.com/sub1.vtt' },
    alias:         { required: false, desc: '别名/又名(可选扩展)' },
  };

  // 导出前必填校验字段（按用户要求：id / name / type / play_list）
  const REQUIRED = ['id', 'name', 'type', 'play_list'];

  // 来源字段 → 标准字段的别名表（覆盖：用户样例 / maccms vod_* / 后端内部模型 / 爬虫原始）
  const ALIASES = {
    id:            ['id', 'vod_id', 'movie_id', 'tid', '_id', 'uid'],
    name:          ['name', 'vod_name', 'title', 'movie_name', 'film_name'],
    type:          ['type', 'vod_type', 'type_name', 'category', 'kind', '分区'],
    play_list:     ['play_list', 'playlist', 'episodes', 'vod_play', 'sources', '播放列表', '分集列表'],
    year:          ['year', 'vod_year', 'release_year', 'pub_year', '年份'],
    area:          ['area', 'vod_area', 'region', 'country', '地区'],
    lang:          ['lang', 'vod_lang', 'language', '语言'],
    actor:         ['actor', 'vod_actor', 'actors', 'cast', 'stars', '演员'],
    desc:          ['desc', 'description', 'vod_content', 'intro', 'plot', 'summary', '简介', '剧情简介'],
    pic:           ['pic', 'poster', 'vod_pic', 'image', 'img', 'cover_url', '海报', '高清海报'],
    tag:           ['tag', 'vod_tag', 'tags', 'genre', '题材', '标签'],
    episode_count: ['episode_count', 'episodecount', 'ep_count', '集数', '分集数'],
    score:         ['score', 'vod_score', 'rating', 'rate', '评分'],
    director:      ['director', 'vod_director', 'directors', '导演'],
    sub_url:       ['sub_url', 'subtitle', 'vod_subtitle', 'sub', '字幕', '字幕地址'],
    alias:         ['alias', 'vod_sub', 'aliases', 'aka', '又名', '别名'],
  };

  // 分区枚举：英文 ⇄ 中文（通用格式用 EN，TVBox 用 CN）
  const TYPE_EN = {
    '电影': 'movie', '连续剧': 'tv', '剧集': 'tv', '短剧': 'short',
    '动漫': 'anime', '动画片': 'anime', '综艺': 'variety',
    '纪录片': 'documentary', '少儿': 'kids', '儿童': 'kids',
  };
  const TYPE_EN_LOWER = {
    'movie': 'movie', 'movies': 'movie',
    'tv': 'tv', 'series': 'tv', 'teleplay': 'tv', 'drama': 'tv',
    'short': 'short', 'shortdrama': 'short',
    'anime': 'anime', 'dongman': 'anime', 'cartoon': 'anime',
    'variety': 'variety',
    'documentary': 'documentary', 'doc': 'documentary',
    'kids': 'kids', 'child': 'kids', 'children': 'kids',
  };
  const TYPE_CN = {
    'movie': '电影', 'tv': '连续剧', 'short': '短剧', 'anime': '动漫',
    'variety': '综艺', 'documentary': '纪录片', 'kids': '少儿',
  };

  function pick(raw, keys) {
    for (const k of keys) {
      const v = raw[k];
      if (v !== undefined && v !== null && v !== '') return v;
    }
    return undefined;
  }
  // 逗号分隔字符串（兼容中/英文逗号输入，统一输出英文逗号）
  function toCsv(v) {
    if (v === undefined || v === null || v === '') return '';
    if (Array.isArray(v)) return v.map(String).join(',');
    return String(v).split(/[，,]/).map(s => s.trim()).filter(Boolean).join(',');
  }
  function hashStr(s) {
    let h = 0; s = String(s || '');
    for (let i = 0; i < s.length; i++) { h = (h << 5) - h + s.charCodeAt(i); h |= 0; }
    return Math.abs(h).toString(36);
  }
  function normType(v) {
    if (!v) return '';
    const s = String(v).trim();
    const low = s.toLowerCase();
    if (TYPE_EN_LOWER[low]) return TYPE_EN_LOWER[low];
    if (TYPE_EN[s]) return TYPE_EN[s];
    return s; // 未知枚举原样保留
  }

  // 把任意来源的播放列表字段归一化为标准 [{name,url,line}]
  function toPlayList(v, raw) {
    let arr = v;
    if (!Array.isArray(arr)) arr = raw.episodes;
    if (Array.isArray(arr) && arr.length) {
      return arr.map((e, i) => {
        if (typeof e === 'string') {
          const [n, u] = e.includes('$') ? e.split('$') : [null, e];
          return { name: n || ('第' + (i + 1) + '集'), url: u || e, line: '默认线路' };
        }
        return { name: e.name || ('第' + (i + 1) + '集'), url: e.url || e.src || '', line: e.line || e.source || '默认线路' };
      }).filter(e => e.url);
    }
    // maccms 字符串格式：vod_play_from / vod_play_url
    if (raw.vod_play_from && raw.vod_play_url) {
      const lines = String(raw.vod_play_from).split('$$$');
      const groups = String(raw.vod_play_url).split('$$$');
      const out = [];
      lines.forEach((ln, gi) => {
        String(groups[gi] || '').split('#').map(s => s.trim()).filter(Boolean).forEach((ep, ei) => {
          const [n, u] = ep.includes('$') ? ep.split('$') : [null, ep];
          out.push({ name: n || ('第' + (ei + 1) + '集'), url: u || ep, line: ln });
        });
      });
      return out.filter(e => e.url);
    }
    return [];
  }

  // 归一化：任意来源对象 → 标准影片模型（字段对齐用户样例）
  function normalize(raw) {
    const m = {};
    for (const key of Object.keys(STANDARD_SCHEMA)) {
      const aliases = ALIASES[key] || [key];
      let val = pick(raw, aliases);
      if (key === 'play_list') val = toPlayList(val, raw);
      else if (key === 'actor' || key === 'tag') val = toCsv(val);
      else if (key === 'type') val = normType(val);
      else if (key === 'year') val = val ? String(val).replace(/\D/g, '').slice(0, 4) : '';
      else if (key === 'score') val = val ? String(val) : '';
      else if (key === 'episode_count') val = val ? Number(val) : 0;
      else if (key === 'alias') val = toCsv(val);
      m[key] = val;
    }
    // 缺失 id 时用 片名+年份 派生稳定 id
    if (!m.id) m.id = 'fc_' + hashStr(m.name + (m.year || ''));
    // 未给 episode_count 时由 play_list 推断
    if (!m.episode_count && m.play_list && m.play_list.length) m.episode_count = m.play_list.length;
    return m;
  }

  const api = { STANDARD_SCHEMA, REQUIRED, ALIASES, TYPE_EN, TYPE_CN, normalize, normType };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.FieldMap = api;
})(typeof window !== 'undefined' ? window : globalThis);
