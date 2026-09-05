# 前端单元测试覆盖率报告

- 生成方式：`npm run test:coverage`（vitest 2.1.9，provider v8）
- 用例：16 个文件、98 个用例，全部通过
- 口径：分母见 `vitest.config.ts` 的 `coverage.include`；四项门槛均为 60%

## 总览

| 指标 | 覆盖 / 总计 | 比例 |
| --- | ---: | ---: |
| 语句 | 4,136 / 4,570 | **90.5%** |
| 行 | 4,136 / 4,570 | **90.5%** |
| 函数 | 211 / 235 | **89.78%** |
| 分支 | 1,137 / 1,469 | **77.39%** |

## 分文件

| 文件 | 语句 | 分支 | 函数 | 行 |
| --- | ---: | ---: | ---: | ---: |
| `src/api/client.ts` | 90.47% | 75% | 100% | 90.47% |
| `src/api/matchApi.ts` | 94.38% | 72.72% | 84.61% | 94.38% |
| `src/components/Icon.tsx` | 100% | 100% | 100% | 100% |
| `src/components/TopBar.tsx` | 94.65% | 86.66% | 60% | 94.65% |
| `src/components/common/DataWindowBadge.tsx` | 96.66% | 92.85% | 80% | 96.66% |
| `src/components/common/DemoTag.tsx` | 88.57% | 91.66% | 50% | 88.57% |
| `src/components/common/HelpTip.tsx` | 100% | 85.71% | 100% | 100% |
| `src/components/common/JumpDock.tsx` | 100% | 85.71% | 100% | 100% |
| `src/components/common/NextSteps.tsx` | 90.9% | 80% | 100% | 90.9% |
| `src/components/common/PageGuide.tsx` | 100% | 100% | 50% | 100% |
| `src/components/common/Panel.tsx` | 100% | 100% | 100% | 100% |
| `src/components/common/ScrollTop.tsx` | 100% | 100% | 100% | 100% |
| `src/components/common/Tooltip.tsx` | 100% | 100% | 100% | 100% |
| `src/components/common/WelcomeGuide.tsx` | 95.08% | 100% | 40% | 95.08% |
| `src/components/common/guideContext.ts` | 100% | 100% | 100% | 100% |
| `src/data/authenticity.ts` | 94.63% | 64.86% | 100% | 94.63% |
| `src/data/explore.ts` | 97.22% | 77.85% | 96.29% | 97.22% |
| `src/data/jobProfile.ts` | 99.5% | 74.71% | 100% | 99.5% |
| `src/data/jobSpace.ts` | 97.9% | 86.36% | 100% | 97.9% |
| `src/data/jobviz.ts` | 98.03% | 70.42% | 100% | 98.03% |
| `src/data/journey.ts` | 100% | 100% | 100% | 100% |
| `src/data/matchLive.ts` | 89.95% | 60.46% | 100% | 89.95% |
| `src/data/matchLiveDerived.ts` | 46.73% | 79.56% | 66.66% | 46.73% |
| `src/data/matching.ts` | 94.58% | 73.79% | 100% | 94.58% |
| `src/data/provinces.ts` | 100% | 100% | 100% | 100% |
| `src/data/searchSeed.ts` | 100% | 100% | 100% | 100% |
| `src/hooks/useDevGraphs.ts` | 0% | 0% | 0% | 0% |
| `src/hooks/useMatchBackend.ts` | 93.47% | 74.72% | 92.85% | 93.47% |
| `src/hooks/useSize.ts` | 100% | 61.53% | 100% | 100% |
| `src/hooks/useZoomPan.ts` | 86.32% | 79.16% | 60% | 86.32% |
| `src/pages/Landing.tsx` | 100% | 83.33% | 100% | 100% |
| `src/utils/format.ts` | 100% | 100% | 100% | 100% |
| `src/utils/rng.ts` | 100% | 100% | 100% | 100% |
| `src/utils/viz.ts` | 97.29% | 84.21% | 100% | 97.29% |
