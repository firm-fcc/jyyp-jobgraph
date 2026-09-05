import { describe, expect, it, vi } from 'vitest';
import {
  TAU,
  annulusPath,
  arcPath,
  bundledPath,
  fitText,
  lerp,
  measureText,
  polar,
  smoothPath,
  trustOpacity,
  weightAt,
} from '@/utils/viz';

describe('viz 几何与展示数学', () => {
  it('极坐标方向与 2π 常量正确', () => {
    expect(TAU).toBeCloseTo(Math.PI * 2);
    expect(polar(10, 20, 5, 0)).toEqual([10, 15]);
    const [x, y] = polar(10, 20, 5, Math.PI / 2);
    expect(x).toBeCloseTo(15);
    expect(y).toBeCloseTo(20);
  });

  it('圆环与圆弧路径包含大弧标志', () => {
    expect(annulusPath(0, 0, 10, 20, 0, Math.PI / 2)).toContain(' A20,20 0 0 1 ');
    expect(annulusPath(0, 0, 10, 20, 0, Math.PI * 1.5)).toContain(' A20,20 0 1 1 ');
    expect(arcPath(0, 0, 20, 0, Math.PI * 1.5)).toContain(' 0 1 1 ');
  });

  it('捆绑路径与平滑路径对空、短、长序列均可输出', () => {
    expect(bundledPath(0, 0, 100, 0, 100, Math.PI)).toMatch(/^M/);
    expect(smoothPath([])).toBe('');
    expect(smoothPath([[0, 0]])).toBe('M0,0');
    expect(smoothPath([[0, 0], [1, 1]])).toBe('M0,0 L1,1');
    const d = smoothPath([[0, 0], [1, 10], [2, 0], [3, 8]]);
    expect(d).toContain(' C');
    expect(d).not.toContain('NaN');
  });

  it('权重、透明度和插值按公式计算', () => {
    const edge = { baseWeight: 0.4, deltaWeight: 0.2 } as never;
    expect(weightAt(edge, 0)).toBeCloseTo(0.4);
    expect(weightAt(edge, 0.5)).toBeCloseTo(0.5);
    expect(trustOpacity(0.7, false)).toBe(1);
    expect(trustOpacity(1, true)).toBe(1);
    expect(trustOpacity(0, true)).toBeCloseTo(0.1);
    expect(lerp(10, 20, 0.25)).toBe(12.5);
  });

  it('文字测量有 canvas 时走实测，fitText 可缩放或截断', () => {
    const spy = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      font: '',
      measureText: (text: string) => ({ width: text.length * 10 }),
    } as unknown as CanvasRenderingContext2D);
    expect(measureText('abc', 12)).toBe(30);
    expect(fitText('abc', 100, 12, 9.5)).toEqual({ size: 12, text: 'abc' });
    const shrink = fitText('abcdefghij', 80, 12, 9.5);
    expect(shrink.size).toBeLessThan(12);
    const cut = fitText('abcdefghijklmnopqrst', 20, 12, 9.5);
    expect(cut.text.endsWith('…')).toBe(true);
    spy.mockRestore();
  });
});
