import {build, transform} from 'esbuild';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {gzipSync} from 'node:zlib';
for (const name of ['panel.js','errors.js','panel.css']) {
  const input=await readFile(`frontend/${name}`,'utf8');
  const options={minify:true,target:name.endsWith('.css')?'chrome100':'es2022',legalComments:'inline'};
  const code = name==='panel.js'
    ? (await build({...options,entryPoints:['frontend/panel.js'],bundle:true,format:'esm',write:false})).outputFiles[0].text
    : (await transform(input,{...options,loader:name.endsWith('.css')?'css':'js'})).code;
  await writeFile(`webadmin/${name}`,code);
  await writeFile(`webadmin/${name}.gz`,gzipSync(code,{level:9,mtime:0}));
}
let html=await readFile('frontend/index.html','utf8');
for (const name of ['panel.js','errors.js','panel.css']) {
  const code=await readFile(`webadmin/${name}`);
  const hash=createHash('sha256').update(code).digest('hex').slice(0,12);
  html=html.replaceAll(`/${name}`,`/${name}?v=${hash}`);
}
await writeFile('webadmin/index.html',html);
