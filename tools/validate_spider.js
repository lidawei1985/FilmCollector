// 用 Node 模拟 TVBox/CatVod 引擎，验证 api.js 的爬虫接口返回结构正确。
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const outDir = path.resolve('tvbox-dist');
const apiJs = fs.readFileSync(path.join(outDir, 'api.js'), 'utf8');

const sandbox = {
  console,
  // 引擎提供的同步 request：这里按文件名映射到本地 data.json
  __request__: (url) => {
    const m = url.match(/([^/]+\.json)$/);
    if (m && fs.existsSync(path.join(outDir, m[1]))) {
      return fs.readFileSync(path.join(outDir, m[1]), 'utf8');
    }
    throw new Error('request 未知地址: ' + url);
  },
};
vm.createContext(sandbox);
vm.runInContext(apiJs, sandbox);

let pass = 0, fail = 0;
function check(name, fn) {
  try {
    const r = JSON.parse(fn());
    console.log('  ✅', name, '|', JSON.stringify(r).slice(0, 130));
    pass++;
    return r;
  } catch (e) {
    console.log('  ❌', name, '|', e.message);
    fail++;
    return null;
  }
}

console.log('== init ==');
const init = JSON.parse(sandbox.init());
console.log('  rule.title =', init.title, '| host =', init.host);

console.log('== home ==');
const home = check('home()', sandbox.home);
const cls = home.class;
console.log('  分类:', cls.map(c => c.type_name).join('、'));
console.log('  首页影片数:', home.list.length, '| 首条:', home.list[0] && home.list[0].vod_name);

console.log('== search ==');
const s = check('search("bunny")', () => sandbox.search('bunny'));
console.log('  命中:', s.list.map(v => v.vod_name).join('、') || '(无)');

console.log('== category ==');
const cat = check('category(电影)', () => sandbox.category('电影', 1, null, null));
console.log('  电影数:', cat.list.length);

console.log('== detail ==');
const id = home.list[0].vod_id;
const det = check('detail(' + id.slice(0, 8) + '...)', () => sandbox.detail(id));
const v0 = det.list[0];
console.log('  片名:', v0.vod_name, '| vod_play_from:', v0.vod_play_from, '| play_url 片段:', (v0.vod_play_url || '').slice(0, 70));

console.log('== play ==');
const epUrl = (v0.vod_play_url || '').split('$')[1] || 'https://example.com/1.mp4';
const pl = check('play(官方线路, <直链>)', () => sandbox.play('官方线路', epUrl, ['官方线路']));
console.log('  返回播放地址:', pl.url.slice(0, 70));

console.log('== subscribe.json ==');
const sub = JSON.parse(fs.readFileSync(path.join(outDir, 'subscribe.json'), 'utf8'));
console.log('  站点数:', sub.sites.length, '| types:', sub.sites.map(s => s.type).join(','));
sub.sites.forEach(s => console.log('   -', s.name, '=>', s.api));

console.log('\n结果:', pass, '通过 /', fail, '失败');
process.exit(fail ? 1 : 0);
