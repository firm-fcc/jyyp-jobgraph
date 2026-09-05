/* 顶部导航 —— 结构与交互原样沿用初版前端（brand / 导航 / 全局搜索 / 数据窗口标 / 路线） */

import { useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { Icon } from './Icon';
import { useDataset } from '@/api/client';
import { buildSearchIndex, type SearchItem } from '@/data/searchSeed';
import { STATIONS } from '@/data/journey';
import { useGuide } from './common/guideContext';

/* 导航项与它们的一句话职责同源于 data/journey.ts。
   此前顶栏只有五个名字，第一次进来的人无法从名字里读出各页的分工，
   也读不出它们其实是一条有顺序的链 —— 悬停说明与站序标在这里补上。 */
const NAV = STATIONS;

const typeIcon = (t: SearchItem['type']) =>
  t.includes('岗位') ? 'user' : t === '任务' ? 'target' : 'spark';

export function TopBar() {
  const nav = useNavigate();
  const openGuide = useGuide();
  const d = useDataset();
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const boxRef = useRef<HTMLDivElement | null>(null);

  const index = useMemo(() => buildSearchIndex(d.nodes), [d.nodes]);

  /** 命中排序：岗位优先，其次前缀匹配，最后才是包含匹配 */
  const hits = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return [];
    return index
      .filter((it) => it.label.toLowerCase().includes(s))
      .sort((a, b) => {
        const rank = (x: SearchItem) =>
          (x.type.includes('岗位') ? 0 : 4) + (x.label.toLowerCase().startsWith(s) ? 0 : 2);
        return rank(a) - rank(b) || a.label.length - b.label.length;
      })
      .slice(0, 8);
  }, [q, index]);

  useEffect(() => {
    setOpen(hits.length > 0);
    setCursor(0);
  }, [hits.length, q]);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('click', onDocClick);
    return () => document.removeEventListener('click', onDocClick);
  }, []);

  const go = (it: SearchItem) => {
    nav(it.to);
    setQ('');
    setOpen(false);
  };

  return (
    <header className="topbar">
      <div className="topbar-inner">
        {/* 品牌回封面页，导航里的"首页"回内容页 —— 两者是不同的落点 */}
        <NavLink className="brand" to="/landing">
          <svg className="brand-mark" viewBox="0 0 32 32" width={30} height={30} aria-hidden="true">
            <defs>
              <linearGradient id="bg1" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stopColor="#2563eb" />
                <stop offset="1" stopColor="#0ea5b7" />
              </linearGradient>
            </defs>
            <circle cx="16" cy="7" r="3.4" fill="url(#bg1)" />
            <circle cx="7" cy="22" r="3.4" fill="url(#bg1)" opacity=".85" />
            <circle cx="25" cy="22" r="3.4" fill="url(#bg1)" opacity=".85" />
            <path
              d="M16 10.5 8.5 19.5 M16 10.5 23.5 19.5 M10.5 22h11"
              stroke="url(#bg1)"
              strokeWidth="1.8"
              fill="none"
              strokeLinecap="round"
            />
          </svg>
          <span className="brand-name">
            JobGraph
            <span className="brand-sub">就业有谱</span>
          </span>
        </NavLink>

        <nav className="topnav">
          {NAV.map((n) => (
            <span className="topnav-item" key={n.to}>
              <NavLink to={n.to} className={({ isActive }) => (isActive ? 'active' : '')}>
                {n.label}
              </NavLink>
              {/* 悬停说明走自绘浮层而不是原生 title：原生提示延迟出现、样式不可控，
                  全站已有的这条约定在这里同样适用。

                  只有四个工具页挂说明。首页不挂：它是唯一一个不需要解释的入口，
                  给它也配一条说明，反而让这排提示看上去是每一项都要读一遍。 */}
              {n.step !== null && (
                <span className="topnav-tip" role="note">
                  <em>第 {n.step} 站 · {n.side}</em>
                  {n.duty}
                </span>
              )}
            </span>
          ))}
        </nav>

        <div className="topbar-right">
          <div className="global-search" ref={boxRef}>
            <Icon name="search" size={15} />
            <input
              type="text"
              placeholder="搜索岗位 / 任务 / 技能…"
              value={q}
              autoComplete="off"
              aria-label="全局搜索"
              onChange={(e) => setQ(e.target.value)}
              onFocus={() => setOpen(hits.length > 0)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && hits[cursor]) go(hits[cursor]);
                if (e.key === 'Escape') {
                  setOpen(false);
                  (e.target as HTMLInputElement).blur();
                }
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setCursor((c) => Math.min(c + 1, hits.length - 1));
                }
                if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setCursor((c) => Math.max(c - 1, 0));
                }
              }}
            />
            <div className={open ? 'global-search-drop open' : 'global-search-drop'}>
              {hits.map((h, i) => (
                <button
                  key={h.label + h.type}
                  className="gs-item"
                  data-cursor={i === cursor ? '1' : undefined}
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => go(h)}
                >
                  <Icon name={typeIcon(h.type)} size={13} />
                  <span>{h.label}</span>
                  <span className="gs-type">{h.type}</span>
                </button>
              ))}
            </div>
          </div>

          {/* 此处原挂一枚数据窗口标（"数据窗口 2022-05 — 2026-04"）。观测区间在
              各页需要它的地方各自写着，顶栏每屏挂一遍是重复。 */}

          {/* 原来这个位置是一枚头像图标：它没有任何行为，点上去不发生任何事，
              而系统本身也不需要登录。换成"路线"——第一次进来的那一屏说明，
              随时可以重新打开，这是顶栏这个位置真正缺的东西。 */}
          <button className="topbar-guide" onClick={openGuide} aria-label="打开四大功能说明">
            <Icon name="route" size={16} />
            <span>路线</span>
          </button>
        </div>
      </div>
    </header>
  );
}
