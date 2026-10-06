// 下载公共 Terrarium 高程瓦片到 assets/terrain/（渲染地形晕渲用）。
// 只读取公共瓦片，不上传任何内容；已存在的文件跳过。
// 运行：node scripts/download_terrain.cjs
const fs = require('node:fs');
const path = require('node:path');
const out = path.join(__dirname, '..', 'assets', 'terrain');
fs.mkdirSync(out, {recursive:true});
(async () => {
  const jobs=[];
  for(let x=21;x<=28;x++)for(let y=9;y<=14;y++)jobs.push({x,y});
  for(let start=0;start<jobs.length;start+=4){
    await Promise.all(jobs.slice(start,start+4).map(async ({x,y})=>{
      const file=path.join(out, `5-${x}-${y}.png`);
      if(fs.existsSync(file))return;
      const url=`https://s3.amazonaws.com/elevation-tiles-prod/terrarium/5/${x}/${y}.png`;
      const r=await fetch(url);
      if(!r.ok)throw Error(`${url}: ${r.status}`);
      const b=Buffer.from(await r.arrayBuffer());
      if(b.readUInt32BE(0)!==0x89504e47)throw Error('Not a PNG: '+url);
      fs.writeFileSync(file,b);
    }));
    console.log(`TERRAIN ${Math.min(start+4,jobs.length)}/${jobs.length}`);
  }
})().catch(e=>{console.error(e);process.exitCode=1});
