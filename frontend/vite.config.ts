import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  /* 端口默认 5176，可由环境变量 PORT 覆盖，便于同时跑两个实例作对照 */
  server: { port: Number(process.env.PORT) || 5176, host: '127.0.0.1' },
  /* 图谱数据在模块图求值期间以顶层 await 取回（见 src/data/graphData.ts），
     该语法要求构建目标支持 ES2022。 */
  build: { target: 'es2022' },
  esbuild: { target: 'es2022' },
  optimizeDeps: { esbuildOptions: { target: 'es2022' } },
});
