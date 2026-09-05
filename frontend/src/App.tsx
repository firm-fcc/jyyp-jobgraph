import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ScrollTop } from '@/components/common/ScrollTop';
import { WelcomeGuide } from '@/components/common/WelcomeGuide';
import { GuideContext } from '@/components/common/guideContext';
import { Landing } from '@/pages/Landing';

/* ---------------- 分块 ----------------

   封面页与系统内各页分属两个包。图谱产物十余兆，取回与建库都在系统这一侧；
   封面只报四个数与观测区间，读的是不足两千字节的数据清单。两者若同处一包，
   模块图的顶层 await 会把封面的首屏一并压在十余兆之后 —— 白屏的那几秒
   正出在这里，而那时页面上要显示的东西早就齐了。

   顶栏一并延后：它挂在系统一侧，且自身要读数据集。

   预取在封面挂载时即发起（见下），故"点进系统"这一步等的不是网络，
   而是已经取回的那份数据建库 —— 后者在本批数据下是数十毫秒的量级。 */
const TopBar = lazy(() => import('@/components/TopBar').then((m) => ({ default: m.TopBar })));
const Home = lazy(() => import('@/pages/Home').then((m) => ({ default: m.Home })));
const Jobs = lazy(() => import('@/pages/Jobs').then((m) => ({ default: m.Jobs })));
const Explore = lazy(() => import('@/pages/Explore').then((m) => ({ default: m.Explore })));
const Match = lazy(() => import('@/pages/Match').then((m) => ({ default: m.Match })));
const Panorama = lazy(() => import('@/pages/Panorama').then((m) => ({ default: m.Panorama })));

/** 预取整份图谱产物。封面一挂载就发起，读者读封面的这几秒即已取回 */
function prefetchSystem() {
  void import('@/pages/Home');
}

/** 分块尚未就位时的占位。一行说明加一条进度条，不作骨架屏 —— 这一步等的是
    十余兆产物的取回，骨架屏会让人以为内容已经在了、只是还没画出来 */
function Booting() {
  return (
    <div className="booting" role="status" aria-live="polite">
      <div className="booting-bar">
        <span />
      </div>
      <p className="booting-text">正在载入图谱数据</p>
    </div>
  );
}

/* 封面页（/landing）不挂顶栏：它是系统之外的一屏，靠"开始探索"进入系统。
   顶栏为 sticky 且占 58px，挂上去封面就不再等于一个完整视口，
   与"封面不出现滚动条"这一条直接冲突。 */
export default function App() {
  const { pathname } = useLocation();
  const onLanding = pathname === '/landing';

  /* 路线引导在**进入系统之后**才出现，不压在封面上：封面本身就是一屏定位说明，
     两屏说明叠在一起等于把同一件事说两遍，而且封面按设计不产生滚动，
     再盖一层弹窗会把那一屏的完整性破坏掉。 */
  const [guideOpen, setGuideOpen] = useState(false);

  /* 触发条件是"这一次跳转来自封面"，不是"这个人没看过"。

     此前记在 localStorage 里，只弹给第一次打开的人。那样有一个现场才发现得了的
     问题：调试或彩排时已经看过一次，正式演示再从封面进来就不弹了，
     要先清掉浏览器存储才能复现。改成认来路之后，从封面进系统必定看见，
     而在系统内部换页、或直接带 hash 打开某一页都不会弹。

     记上一次的 pathname 而不是只看当前：只有从 /landing 走到别处这一步算数。 */
  const prevPath = useRef<string | null>(null);
  useEffect(() => {
    if (prevPath.current === '/landing' && !onLanding) setGuideOpen(true);
    prevPath.current = pathname;
  }, [pathname, onLanding]);

  /* 停在封面的这段时间正是取数的窗口。直接进内页时不必再预取一次 ——
     那一页自己的分块已经在路上。

     预取排在空闲时段而不是挂载当帧：那一侧的模块图求值本身要占住主线程若干
     百毫秒，与封面的首次绘制抢同一根线程，紧挨着挂载发起会把封面的成屏
     推后同样长的一段。 */
  useEffect(() => {
    if (!onLanding) return;
    let live = true;
    const go = () => {
      if (!live) return;
      live = false;
      prefetchSystem();
    };
    /* 连排两帧再发起：第一帧回调时封面刚提交、尚未绘制，第二帧回调必在
       首次绘制之后。空闲回调本身不保证这一点 —— 提交完主线程即空闲，
       它会紧接着抢在绘制前跑起来。

       另挂一道定时器兜底：标签页在后台时不排帧，只靠帧回调会一直不预取，
       等切回前台才开始取十余兆产物，与不预取无异。 */
    const f1 = requestAnimationFrame(() => requestAnimationFrame(go));
    const t = window.setTimeout(go, 600);
    return () => {
      live = false;
      cancelAnimationFrame(f1);
      window.clearTimeout(t);
    };
  }, [onLanding]);

  const openGuide = useCallback(() => setGuideOpen(true), []);
  const closeGuide = useCallback(() => setGuideOpen(false), []);

  return (
    <GuideContext.Provider value={openGuide}>
      <ScrollTop />
      <Suspense fallback={null}>{!onLanding && <TopBar />}</Suspense>
      <main className={onLanding ? undefined : 'view-root'}>
        <Suspense fallback={<Booting />}>
          <Routes>
            <Route path="/" element={<Navigate to="/landing" replace />} />
            <Route path="/landing" element={<Landing />} />
            <Route path="/home" element={<Home />} />
            <Route path="/panorama" element={<Panorama />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/explore" element={<Explore />} />
            <Route path="/match" element={<Match />} />
            <Route path="*" element={<Navigate to="/landing" replace />} />
          </Routes>
        </Suspense>
      </main>
      {guideOpen && !onLanding && <WelcomeGuide onClose={closeGuide} />}
    </GuideContext.Provider>
  );
}
