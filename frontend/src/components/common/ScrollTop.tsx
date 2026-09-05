/* =========================================================
   换页回到页首

   此前没有这一段，于是出现过一个很容易被当成"页面坏了"的现象：
   在首页往下滚到 2400px 再点顶栏的"全景图谱"，落地时滚动位置原封不动 ——
   新页面停在第 2400px 处，正好把信号传导时间线拦腰截断，
   上方是半张图、下方是前瞻热度排行，页头一个字也看不到。
   页面越长越明显，而全景图谱整页 6000px 以上。

   只认 pathname，不认 search：岗位洞察在左列换一个岗位走的是 `?id=`，
   那是同一页内的选中，不该把人弹回页首。

   behavior 显式写 auto —— 全站 html 上开着 scroll-behavior: smooth，
   不写会变成换页时先看着旧页面滚一段，比不滚更奇怪。
   ========================================================= */

import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

export function ScrollTop() {
  const { pathname } = useLocation();

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
  }, [pathname]);

  return null;
}
