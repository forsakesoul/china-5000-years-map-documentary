// 恢复 / 核对 assets/ 下的第三方素材，并重出 assets/sources.json（来源 + sha256）。
// 已存在的文件不会被覆盖，只做校验；缺哪个补哪个。
// 运行：node scripts/download_assets.cjs
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const root = path.resolve(__dirname, '..');
const out = path.join(root, 'assets');
const HB = 'https://github.com/aourednik/historical-basemaps/blob/master/geojson/';
const NE = 'https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/';

// 实际渲染用到的历史快照年份，与 src/map_scenes.py 里各场景的 src 一致
const YEARS = ['bc1000', 'bc500', 'bc300', 'bc200', 'bc100', '200', '400', '600', '700', '800',
  '1000', '1200', '1279', '1600', '1700', '1800', '1900'];

const RAW = (repo, p) => ({ url: `https://api.github.com/repos/${repo}/contents/${p}` });

const FILES = [
  ...YEARS.map(y => ({
    file: `world_${y}.geojson`, api: `https://api.github.com/repos/aourednik/historical-basemaps/contents/geojson/world_${y}.geojson`,
    source: `${HB}world_${y}.geojson`, license: 'GPL-3.0',
  })),
  { file: 'land50.geojson', ...RAW('nvkelso/natural-earth-vector', 'geojson/ne_50m_land.geojson'),
    source: `${NE}ne_50m_land.geojson`, license: 'Public domain (Natural Earth)' },
  { file: 'rivers.geojson', ...RAW('nvkelso/natural-earth-vector', 'geojson/ne_50m_rivers_lake_centerlines.geojson'),
    source: `${NE}ne_50m_rivers_lake_centerlines.geojson`, license: 'Public domain (Natural Earth)' },
  { file: 'MaShanZheng-Regular.ttf', ...RAW('google/fonts', 'ofl/mashanzheng/MaShanZheng-Regular.ttf'),
    source: 'https://github.com/google/fonts/tree/main/ofl/mashanzheng',
    license: 'SIL Open Font License 1.1',
    note: '上游修订会更新字体文件；随包副本与上游当前版本未必一致，OFL 允许再分发' },
  { file: 'LICENSE-historical-basemaps.txt', ...RAW('aourednik/historical-basemaps', 'LICENSE'),
    source: 'https://github.com/aourednik/historical-basemaps', license: 'GPL-3.0' },
  { file: 'README-historical-basemaps.md', ...RAW('aourednik/historical-basemaps', 'README.md'),
    source: 'https://github.com/aourednik/historical-basemaps', license: 'GPL-3.0' },
  { file: 'OFL-MaShanZheng.txt', ...RAW('google/fonts', 'ofl/mashanzheng/OFL.txt'),
    source: 'https://github.com/google/fonts/tree/main/ofl/mashanzheng', license: 'SIL Open Font License 1.1' },
];

const sha = f => crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const manifest = [];
  for (const item of FILES) {
    const target = path.join(out, item.file);
    if (!fs.existsSync(target)) {
      const r = await fetch(item.api, { headers: { Accept: 'application/vnd.github.raw+json' } });
      if (!r.ok) throw new Error(`${item.file}: HTTP ${r.status}`);
      const data = Buffer.from(await r.arrayBuffer());
      if (item.file.endsWith('.geojson') && !Array.isArray(JSON.parse(data).features)) {
        throw new Error(`Invalid GeoJSON: ${item.file}`);
      }
      fs.writeFileSync(target, data);
      console.log(`GET  ${item.file}`);
    } else {
      console.log(`OK   ${item.file}`);
    }
    const entry = { file: `assets/${item.file}`, source: item.source, sha256: sha(target) };
    for (const k of ['license', 'note']) if (item[k]) entry[k] = item[k];
    manifest.push(entry);
  }

  // 地形瓦片由 download_terrain.cjs 负责，这里只登记随包副本
  const terrainDir = path.join(out, 'terrain');
  const tiles = fs.existsSync(terrainDir) ? fs.readdirSync(terrainDir).filter(f => f.endsWith('.png')).sort() : [];
  const doc = {
    note: '本清单记录随包素材的来源与 sha256，由 scripts/download_assets.cjs 生成。',
    files: manifest,
    terrain: {
      source: 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png',
      license: 'AWS Open Data / Terrarium（公共高程瓦片）',
      zoom: 5,
      tiles: tiles.length,
      files: tiles.map(f => ({ file: `assets/terrain/${f}`, sha256: sha(path.join(terrainDir, f)) })),
    },
  };
  fs.writeFileSync(path.join(out, 'sources.json'), JSON.stringify(doc, null, 2) + '\n');
  console.log(`\n写出 assets/sources.json：${manifest.length} 个文件 + ${tiles.length} 块地形瓦片`);
})().catch(e => { console.error(e); process.exitCode = 1; });
