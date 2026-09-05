import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/data/realGraph', () => ({ REAL_GRAPH_STATS: { from: '2022-05', to: '2023-06', windows: 12, spanMonths: 14, gapMonths: 2, jdSampled: 12345 } }));
vi.mock('@/data/provenance', () => ({ ABSENT_DIMENSIONS: [{ name: '证据全文', why: '迁移包未携带全文。' }] }));
vi.mock('@/data/dataSource', () => ({ IS_REAL_GRAPH: true }));
import { Panel } from '@/components/common/Panel';
import { HelpTip } from '@/components/common/HelpTip';
import { DemoTag } from '@/components/common/DemoTag';
import { NextSteps } from '@/components/common/NextSteps';
import { PageGuide } from '@/components/common/PageGuide';
import { Tooltip } from '@/components/common/Tooltip';
import { DataWindowBadge } from '@/components/common/DataWindowBadge';
import { WelcomeGuide } from '@/components/common/WelcomeGuide';
import { ScrollTop } from '@/components/common/ScrollTop';
import { JumpDock } from '@/components/common/JumpDock';
import { Icon } from '@/components/Icon';
import { ROUTE } from '@/data/journey';

describe('公共组件', () => {
  it('Panel 渲染标题、actions、内容与样式', () => {
    render(<Panel title="测试面板" actions={<button>操作</button>} bodyStyle={{ minHeight: 20 }}>正文</Panel>);
    expect(screen.getByRole('heading', { name: '测试面板' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '操作' })).toBeInTheDocument();
    expect(screen.getByText('正文')).toHaveStyle({ minHeight: '20px' });
  });

  it('HelpTip 支持点击展开、Esc 收起和外部点击收起', () => {
    render(<HelpTip text="说明正文" />);
    const btn = screen.getByRole('button', { name: '查看说明' });
    fireEvent.click(btn);
    expect(screen.getByRole('note')).toHaveTextContent('说明正文');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
    fireEvent.click(btn);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
  });

  it('DemoTag 无说明时静态显示，有说明时可展开', () => {
    const { rerender } = render(<DemoTag label="推导数据" />);
    expect(screen.getByText('推导数据')).toHaveClass('dtag-static');
    rerender(<DemoTag label="推导数据" text="推导口径" />);
    fireEvent.click(screen.getByRole('button', { name: '推导数据' }));
    expect(screen.getByRole('note')).toHaveTextContent('推导口径');
  });

  it('Icon 对已知与未知图标均保持稳定 SVG 容器', () => {
    const { container, rerender } = render(<Icon name="arrowR" size={20} className="x" />);
    expect(container.querySelector('svg')).toHaveAttribute('width', '20');
    expect(container.querySelector('svg')).toHaveClass('x');
    expect(container.querySelector('path')).not.toBeNull();
    rerender(<Icon name="not-exist" />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('NextSteps 最多渲染四个出口并显示路线下一站', () => {
    const items = Array.from({ length: 6 }, (_, i) => ({
      to: `/x${i}`, label: `动作${i}`, desc: `说明${i}`, icon: 'arrowR', primary: i === 0,
    }));
    render(<MemoryRouter><NextSteps from="/panorama" items={items} /></MemoryRouter>);
    expect(screen.getByText(/第 2 站 · 岗位洞察/)).toBeInTheDocument();
    expect(screen.getAllByRole('link')).toHaveLength(4);
  });

  it('PageGuide 显示站序、定位条并可取消定位', () => {
    const clear = vi.fn();
    render(<MemoryRouter><PageGuide station={ROUTE[1]} landed="算法工程师" onClearLanded={clear} /></MemoryRouter>);
    expect(screen.getByText('岗位洞察')).toBeInTheDocument();
    expect(screen.getByText(/算法工程师/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消定位' }));
    expect(clear).toHaveBeenCalledOnce();
  });

  it('Tooltip 为 null 时不渲染，有值时 portal 到 body 并约束位置', () => {
    const { rerender } = render(<Tooltip tip={null} />);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 400 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 300 });
    rerender(<Tooltip tip={{ x: 390, y: 290, content: '悬浮信息' }} />);
    expect(screen.getByRole('tooltip')).toHaveTextContent('悬浮信息');
  });

  it('DataWindowBadge 展示观测窗口并支持展开/收起口径', () => {
    render(<DataWindowBadge />);
    const btn = screen.getByRole('button', { name: /数据窗口/ });
    expect(btn).toHaveTextContent('2022-05 — 2023-06');
    fireEvent.click(btn);
    expect(screen.getByRole('note')).toHaveTextContent('12,345 条招聘信息');
    expect(screen.getByRole('note')).toHaveTextContent('证据全文');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
  });

  it('WelcomeGuide 锁定背景、展示四站路线并可关闭', () => {
    const close = vi.fn();
    const { unmount } = render(<MemoryRouter><WelcomeGuide onClose={close} /></MemoryRouter>);
    expect(screen.getByRole('dialog')).toHaveTextContent('四大功能');
    expect(screen.getAllByRole('button').length).toBeGreaterThanOrEqual(5);
    expect(document.body.style.overflow).toBe('hidden');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(close).toHaveBeenCalled();
    unmount();
    expect(document.body.style.overflow).toBe('');
  });

  it('ScrollTop 在路由 pathname 生效时回到页首', () => {
    render(<MemoryRouter initialEntries={['/panorama']}><ScrollTop /></MemoryRouter>);
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0, left: 0, behavior: 'auto' });
  });

  it('JumpDock 只展示带 short 的跨页动作并突出主出口', () => {
    const items = [
      { to: '/jobs?id=1', label: '查看岗位', desc: '说明', icon: 'arrowR', short: '岗位', primary: true },
      { to: '/match?id=1', label: '开始匹配', desc: '说明', icon: 'target', short: '匹配' },
      { to: '/ignore', label: '隐藏动作', desc: '说明', icon: 'arrowR' },
    ] as any;
    render(<MemoryRouter><JumpDock items={items} label="岗位快捷动作" /></MemoryRouter>);
    expect(screen.getByRole('navigation', { name: '岗位快捷动作' })).toBeInTheDocument();
    expect(screen.getAllByRole('link')).toHaveLength(2);
    expect(screen.getByRole('link', { name: '查看岗位' })).toHaveClass('primary');
    expect(screen.queryByRole('link', { name: '隐藏动作' })).not.toBeInTheDocument();
  });

});

