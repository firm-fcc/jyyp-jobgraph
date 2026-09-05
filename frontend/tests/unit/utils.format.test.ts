import { describe, expect, it } from 'vitest';
import {
  addMonths,
  compact,
  indexToMonth,
  monthDiff,
  monthIndex,
  monthRange,
  num,
  pct,
  shortMonth,
  signed,
} from '@/utils/format';

describe('format 时间与数字格式化', () => {
  it('月份索引可双向转换并跨年', () => {
    expect(monthIndex('2024-01')).toBe(2024 * 12);
    expect(indexToMonth(monthIndex('2024-12'))).toBe('2024-12');
    expect(addMonths('2024-11', 3)).toBe('2025-02');
    expect(monthDiff('2024-11', '2025-02')).toBe(3);
  });

  it('生成闭区间月份序列', () => {
    expect(monthRange('2024-11', '2025-02')).toEqual(['2024-11', '2024-12', '2025-01', '2025-02']);
    expect(monthRange('2025-02', '2025-01')).toEqual([]);
  });

  it('生成短日期、百分比、定点数与带符号数字', () => {
    expect(shortMonth('2026-09')).toBe('26/09');
    expect(pct(0.9647, 2)).toBe('96.47%');
    expect(num(3.14159, 3)).toBe('3.142');
    expect(signed(1.234, 1)).toBe('+1.2');
    expect(signed(-1.234, 1)).toBe('-1.2');
  });

  it('紧凑显示千、万和普通数字', () => {
    expect(compact(999)).toBe('999');
    expect(compact(1500)).toBe('1.5k');
    expect(compact(12500)).toBe('1.3w');
  });
});
