import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/data/manifest', () => ({
  MANIFEST: {
    counts: { jobs: 131, tasks: 98, skills: 65, skillpoints: 500, edges: { jt: 10, js: 20, ts: 30, ssp: 40 }, overlay: { jobs: 11 } },
    windows: ['2022-05', '2022-10', '2023-06'],
    latest: '2023-06',
  },
}));

const dataset = vi.hoisted(() => ({
  nodes: [
    { id: 'J:1', name: '算法工程师', kind: 'job', emerging: false },
    { id: 'J:2', name: '智能体编排师', kind: 'job', emerging: true },
    { id: 'S:1', name: '机器学习', kind: 'skill' },
  ],
}));
vi.mock('@/api/client', () => ({ useDataset: () => dataset }));

import { Landing } from '@/pages/Landing';
import { TopBar } from '@/components/TopBar';
import { GuideContext } from '@/components/common/guideContext';

describe('Landing 页面', () => {
  it('展示系统定位、规模指标、观测窗口和两个入口', () => {
    render(<MemoryRouter><Landing /></MemoryRouter>);
    expect(screen.getByText('JobGraph')).toBeInTheDocument();
    expect(screen.getByText(/动态技能图谱/)).toBeInTheDocument();
    expect(screen.getByText('131')).toBeInTheDocument();
    expect(screen.getByText('11')).toBeInTheDocument();
    expect(screen.getByText(/2022-05 — 2023-06/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /进入系统/ })).toHaveAttribute('href', '/home');
    expect(screen.getByRole('link', { name: '全景图谱' })).toHaveAttribute('href', '/panorama');
  });
});

describe('TopBar 全局导航与搜索', () => {
  it('按输入筛选图谱节点并显示岗位/能力类型', () => {
    const openGuide = vi.fn();
    render(
      <MemoryRouter initialEntries={['/home']}>
        <GuideContext.Provider value={openGuide}><TopBar /></GuideContext.Provider>
      </MemoryRouter>,
    );
    const input = screen.getByRole('textbox', { name: '全局搜索' });
    fireEvent.change(input, { target: { value: '算法' } });
    expect(screen.getByText('算法工程师')).toBeInTheDocument();
    expect(screen.getByText('岗位')).toBeInTheDocument();
  });

  it('键盘上下移动结果，Enter 选择，Esc 收起', () => {
    render(
      <MemoryRouter initialEntries={['/home']}>
        <GuideContext.Provider value={() => {}}><TopBar /></GuideContext.Provider>
      </MemoryRouter>,
    );
    const input = screen.getByRole('textbox', { name: '全局搜索' });
    fireEvent.change(input, { target: { value: '工程' } });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(input).toHaveValue('工程');
  });

  it('路线按钮调用引导上下文', () => {
    const openGuide = vi.fn();
    render(
      <MemoryRouter initialEntries={['/home']}>
        <GuideContext.Provider value={openGuide}><TopBar /></GuideContext.Provider>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: '打开四大功能说明' }));
    expect(openGuide).toHaveBeenCalledOnce();
  });
});
