import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const testsRoot = fileURLToPath(new URL('.', import.meta.url));
const frontendRoot = fileURLToPath(new URL('..', import.meta.url));
const src = fileURLToPath(new URL('../src', import.meta.url));

const testReact = fileURLToPath(new URL('./node_modules/react', import.meta.url));
const testReactDom = fileURLToPath(new URL('./node_modules/react-dom', import.meta.url));
const testRouter = fileURLToPath(new URL('./node_modules/react-router-dom', import.meta.url));

// 覆盖率插件装在 tests/node_modules 下，而 Vite 的模块解析以 root（frontend/）为基准，
// 不会向下查找子目录的 node_modules，故按绝对路径登记。
const coverageV8 = fileURLToPath(new URL('./node_modules/@vitest/coverage-v8/dist/index.js', import.meta.url));

export default defineConfig({
  root: frontendRoot,

  plugins: [react()],

  resolve: {
    alias: [
      { find: '@', replacement: src },
      { find: 'react', replacement: testReact },
      { find: 'react-dom', replacement: testReactDom },
      { find: 'react-router-dom', replacement: testRouter },
      { find: '@vitest/coverage-v8', replacement: coverageV8 },
    ],
    dedupe: ['react', 'react-dom', 'react-router-dom'],
  },

  server: {
    fs: {
      allow: [frontendRoot, testsRoot],
    },
  },

  test: {
    environment: 'jsdom',

    setupFiles: [fileURLToPath(new URL('./setup.ts', import.meta.url))],
    include: ['tests/unit/**/*.test.{ts,tsx}'],

    clearMocks: true,
    restoreMocks: true,
    mockReset: true,
    testTimeout: 15000,

    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'json-summary', 'lcov'],
      reportsDirectory: 'tests/coverage',

      include: [
        'src/utils/**/*.ts',
        'src/api/**/*.ts',
        'src/hooks/**/*.ts',

        'src/data/authenticity.ts',
        'src/data/explore.ts',
        'src/data/jobProfile.ts',
        'src/data/jobSpace.ts',
        'src/data/jobviz.ts',
        'src/data/journey.ts',
        'src/data/matchLive.ts',
        'src/data/matchLiveDerived.ts',
        'src/data/matching.ts',
        'src/data/provinces.ts',
        'src/data/searchSeed.ts',

        'src/components/common/**/*.tsx',
        'src/components/common/**/*.ts',
        'src/components/Icon.tsx',
        'src/components/TopBar.tsx',

        'src/pages/Landing.tsx',
      ],

      exclude: [
        'src/**/*.d.ts',
        'src/types/**',
      ],

      thresholds: {
        lines: 60,
        statements: 60,
        functions: 60,
        branches: 60,
      },
    },
  },
});
