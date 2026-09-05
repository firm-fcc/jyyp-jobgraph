import { describe, expect, it } from 'vitest';
import { clamp, hashStr, logistic, mulberry32, pick, pickMany, rand01, randInt, randRange } from '@/utils/rng';

describe('rng 确定性随机工具', () => {
  it('同一字符串哈希稳定，不同字符串通常不同', () => {
    expect(hashStr('JobGraph')).toBe(hashStr('JobGraph'));
    expect(hashStr('JobGraph')).not.toBe(hashStr('jobgraph'));
  });

  it('同一 seed 产生相同序列且值落在 [0,1)', () => {
    const a = mulberry32(1234);
    const b = mulberry32(1234);
    const xs = Array.from({ length: 10 }, () => a());
    const ys = Array.from({ length: 10 }, () => b());
    expect(xs).toEqual(ys);
    expect(xs.every((x) => x >= 0 && x < 1)).toBe(true);
  });

  it('按 key 派生值稳定且遵守范围', () => {
    expect(rand01('x')).toBe(rand01('x'));
    expect(randRange('x', 10, 20)).toBeGreaterThanOrEqual(10);
    expect(randRange('x', 10, 20)).toBeLessThan(20);
    expect(randInt('x', 3, 7)).toBeGreaterThanOrEqual(3);
    expect(randInt('x', 3, 7)).toBeLessThanOrEqual(7);
  });

  it('pick / pickMany 稳定、去重且不超数组长度', () => {
    const arr = ['a', 'b', 'c', 'd'];
    expect(pick('k', arr)).toBe(pick('k', arr));
    const got = pickMany('k', arr, 10);
    expect(got).toHaveLength(4);
    expect(new Set(got).size).toBe(4);
    expect(got).toEqual(pickMany('k', arr, 10));
  });

  it('clamp 与 logistic 边界正确', () => {
    expect(clamp(-1)).toBe(0);
    expect(clamp(2)).toBe(1);
    expect(clamp(5, 2, 4)).toBe(4);
    expect(logistic(0, 0, 1)).toBeCloseTo(0.5);
    expect(logistic(10, 0, 1)).toBeGreaterThan(0.99);
  });
});
