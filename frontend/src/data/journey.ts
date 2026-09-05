/* =========================================================
   路线元数据 —— 全站唯一一份"五个页面各回答什么问题"

   此前这件事没有任何一处写下来：顶栏只有五个名字，页面里没有职责说明，
   页与页之间也没有出口（实测：全景图谱与职业探索的跨页链接各为 0 条，
   岗位洞察 1 条，只有匹配报告页脚做对了）。于是从评委视角看，
   五个页面像五个并列的标签，而不是一条链。

   四个工具页排成一条有方向的路线，顺序与顶栏一致，也与整套系统的
   处理链路一致：先看领域全貌，再落到具体岗位，然后从能力反查方向，
   最后落到某一份简历与某一个岗位之间的差距。前两站看市场，后两站看个人。

   这一份数据同时供四处使用，口径因此只可能有一个：
   顶栏导航的悬停说明、首访引导弹窗的路线图、页首的职责条、页尾的下一步。
   ========================================================= */

export interface Station {
  /** 路由 */
  to: string;
  /** 顶栏与各处统一使用的页面名 */
  label: string;
  /** 站序。首页是起点，不占站序 */
  step: number | null;
  /**
   * 这一页给什么 —— 顶栏悬停、页首职责条、弹窗路线图共用的唯一一句（首页除外，见下）。
   *
   * 一句写完，不再另设展开项：页面自己的分区标题与图上的说明标已经在讲怎么读，
   * 页首再挂一段读法长文只是把它们抄一遍。因此这一句要写到能替代那段长文 ——
   * 说清这一页产出什么、以什么为依据，而不是只给一个类别名。
   */
  duty: string;
  /** 这一站属于市场侧还是个人侧 */
  side: '市场侧' | '个人侧' | '入口';
  /** 图标名（见 components/Icon.tsx） */
  icon: string;
}

export const STATIONS: Station[] = [
  {
    to: '/home',
    label: '首页',
    step: null,
    side: '入口',
    icon: 'spark',
    /* 首页这一条不在界面上出现：顶栏只给四个工具页挂悬停说明，首页也没有页首职责条。
       留在这里是因为路线数据要完整 —— 站序、上一站与下一站都从这一份数组上算。 */
    duty: '本期结论与四大功能入口。',
  },
  {
    to: '/panorama',
    label: '全景图谱',
    step: 1,
    side: '市场侧',
    icon: 'graph',
    duty:
      '按论文、新闻、招聘三类来源交叉验证，' +
      '呈现新一代信息技术领域的岗位、任务、技能、技能点四层结构与前瞻信号',
  },
  {
    to: '/jobs',
    label: '岗位洞察',
    step: 2,
    side: '市场侧',
    icon: 'cap',
    duty:
      '识别新岗位，追踪既有岗位能力要求的演变，' +
      '每一条结论均可回到招聘、论文、新闻三类原文核对',
  },
  {
    to: '/explore',
    label: '职业探索',
    step: 3,
    side: '个人侧',
    icon: 'route',
    duty:
      '选中一项能力即列出要求它的岗位，或按能力结构把岗位聚成簇，' +
      '逐个对照城市、薪资与能力构成',
  },
  {
    to: '/match',
    label: '人岗匹配',
    step: 4,
    side: '个人侧',
    icon: 'target',
    duty:
      '测算一份简历与目标岗位的差距：真实性核验、岗位要求达成率、' +
      '能力差距明细与逐项学习路径，逐项可回到简历原文',
  },
];

/** 四个工具页，按站序排列 —— 路线图与上一站/下一站都走这一份 */
export const ROUTE = STATIONS.filter((s) => s.step !== null);

export const stationOf = (pathname: string) => STATIONS.find((s) => s.to === pathname) ?? null;

/** 下一站。末站之后回到首页看本期结论 */
export function nextOf(pathname: string): Station | null {
  const i = ROUTE.findIndex((s) => s.to === pathname);
  if (i < 0) return ROUTE[0];
  return ROUTE[i + 1] ?? null;
}

export function prevOf(pathname: string): Station | null {
  const i = ROUTE.findIndex((s) => s.to === pathname);
  if (i <= 0) return null;
  return ROUTE[i - 1];
}
