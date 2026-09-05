/* 路线引导的打开入口。顶栏与首页都要能召出它，而它的状态在 App 上，
   逐层透传要穿过 Routes，用 context 直接取。 */

import { createContext, useContext } from 'react';

export const GuideContext = createContext<() => void>(() => {});

export const useGuide = () => useContext(GuideContext);
