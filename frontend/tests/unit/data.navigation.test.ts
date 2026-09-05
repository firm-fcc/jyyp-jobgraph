import { describe, expect, it } from 'vitest';
import { nextOf, prevOf, ROUTE, stationOf, STATIONS } from '@/data/journey';
import { buildSearchIndex } from '@/data/searchSeed';
import { citiesOf, isNonGeo, PROVINCE_OTHER, PROVINCES_ALL, provinceOf, unmappedCities } from '@/data/provinces';

describe('路线元数据', () => {
  it('四个工具页站序连续且首页不占站序', () => {
    expect(STATIONS[0]).toMatchObject({ to: '/home', step: null });
    expect(ROUTE.map((s) => s.step)).toEqual([1, 2, 3, 4]);
    expect(stationOf('/jobs')?.label).toBe('岗位洞察');
    expect(stationOf('/missing')).toBeNull();
  });

  it('上一站/下一站边界正确', () => {
    expect(nextOf('/unknown')?.to).toBe('/panorama');
    expect(nextOf('/panorama')?.to).toBe('/jobs');
    expect(nextOf('/match')).toBeNull();
    expect(prevOf('/panorama')).toBeNull();
    expect(prevOf('/match')?.to).toBe('/explore');
  });
});

describe('全局搜索索引', () => {
  it('岗位、新岗位和非岗位节点落到正确路由', () => {
    const nodes = [
      { id: 'J:1', name: '算法工程师', kind: 'job', emerging: false },
      { id: 'J:2', name: '智能体编排师', kind: 'job', emerging: true },
      { id: 'T:1', name: '模型训练', kind: 'task' },
      { id: 'S:1', name: '深度学习', kind: 'skill' },
      { id: 'SP:1', name: 'PyTorch', kind: 'skillpoint' },
    ] as never;
    const index = buildSearchIndex(nodes);
    expect(index[0]).toEqual({ label: '算法工程师', type: '岗位', to: '/jobs?tab=existing&id=J%3A1' });
    expect(index[1].type).toBe('新岗位');
    expect(index[1].to).toContain('tab=new');
    expect(index[2].to).toBe('/panorama?focus=T%3A1');
    expect(index[3].type).toBe('能力');
    expect(index[4].type).toBe('技能点');
  });
});

describe('省市映射', () => {
  it('覆盖 34 个省级行政区并识别常见城市', () => {
    expect(PROVINCES_ALL).toHaveLength(34);
    expect(provinceOf('武汉')).toBe('湖北');
    expect(provinceOf('深圳')).toBe('广东');
    expect(provinceOf('上海')).toBe('上海');
  });

  it('可处理行政区后缀和未知城市', () => {
    expect(provinceOf('广东省')).toBe('广东');
    expect(provinceOf('喀什地区')).toBe('新疆');
    expect(provinceOf('不存在城')).toBe(PROVINCE_OTHER);
    expect(unmappedCities()).toContain('不存在城');
  });

  it('区分非地理取值并可反查省下城市', () => {
    expect(isNonGeo('远程办公')).toBe(true);
    expect(isNonGeo('武汉')).toBe(false);
    expect(citiesOf('湖北')).toContain('武汉');
    expect(citiesOf('北京')).toEqual(['北京']);
    expect(citiesOf(PROVINCE_OTHER)).toEqual([]);
  });
});
