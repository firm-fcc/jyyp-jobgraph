/* 导出岗位洞察页样本：先用 esbuild 把 jobs-sample.ts 连同路径别名与 JSON 打成一个
   可执行文件，再跑它。逻辑全在 jobs-sample.ts 里，这里只负责把 TS 变成能跑的东西。

     npm run sample
*/
import { build } from 'esbuild';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const outfile = resolve('node_modules/.cache/jobs-sample.mjs');

await build({
  entryPoints: ['scripts/jobs-sample.ts'],
  bundle: true,
  platform: 'node',
  format: 'esm',
  outfile,
  loader: { '.json': 'json' },
  alias: { '@': './src' },
  // 源码里读 import.meta.env.VITE_DATA 判断真实词表还是演示词表，node 下没有这个对象
  define: { 'import.meta.env': JSON.stringify({ VITE_DATA: 'real' }) },
  logLevel: 'error',
});

await import(pathToFileURL(outfile).href);
