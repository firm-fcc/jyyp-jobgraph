#!/usr/bin/env node
/* ============================================================
   逐条招聘原文的聚合

   算法侧的窗口产物只给到汇总统计，逐条招聘原文另存于时间线目录
   （data/timeline/jd/{窗口}.csv）。汇总表未含城市、企业与原文三项，
   而原文表含 place、company 与 job_information 三列，可按 jobid
   与汇总表连接后按岗位聚合。

   本脚本另在同一次扫描内产出两项算法侧未直接给出、但由已有字段可推得的数据：

   ① 学历门槛。原文表的 degree 一列多数为空（2026-09-03 批次起该列开始有值，
      占连接条数的一成上下，此前各批整列为空），汇总表无此列；而正文多数写有
      “本科及以上学历”一类表述 —— 算法侧的职级（level）即由正文抽出
      （level_source 记作 text），学历沿同一路径可得。见 degreeOf。

   ② 能力要求的句级归因。汇总表逐条给出该条招聘信息要求的技能
      （skill_vec_01）及各技能下命中的技能点（skillpoint_map），原文表给出
      正文全文，两者按 jobid 连接后，以技能点名在正文中的落点反查所在句，
      即得“哪一句支撑哪一项能力要求”。见 attributeOf。

   原文表逐窗约数十万行、合计四吉字节，直接读进构建脚本会把一次构建
   拖到十分钟以上，故单列本脚本，产出中间件 jdraw.json 供构建脚本取用。
   原文表与中间件均不入版本库，产物入库。

   用法：
     node data-pipeline/jdraw.mjs [--src <目录>] [--jd <原文目录>] [--out <文件>]

   `--jd` 缺省取 `<src>/timeline/jd`，文件名为 `{窗口}.csv`。
   ============================================================ */

import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.resolve(HERE, '..');      /* frontend/ */
const REPO = path.resolve(APP, '..');      /* 仓库根 */

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const DEFAULT_SRC = path.resolve(REPO, '..', 'Reference-tmp', 'graph-2022-05_10(1)', 'graph-2022-05_10');
const SRC = path.resolve(arg('src', process.env.GRAPH_SRC || DEFAULT_SRC));
const JD_DIR = path.resolve(arg('jd', path.join(SRC, 'timeline', 'jd')));
const OUT = path.resolve(arg('out', path.join(SRC, 'jdraw.json')));
const SUM_DIR = path.join(SRC, 'jd-summaries');

for (const [label, p] of [['汇总表目录', SUM_DIR], ['原文目录', JD_DIR]]) {
  if (!fs.existsSync(p)) {
    console.error(`找不到${label}：${p}`);
    process.exit(1);
  }
}

/* ---------------- CSV ---------------- */

function splitCsv(line) {
  const out = [];
  let cur = '';
  let q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) {
      if (c === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } else q = false;
      } else cur += c;
    } else if (c === '"') q = true;
    else if (c === ',') { out.push(cur); cur = ''; }
    else cur += c;
  }
  out.push(cur);
  return out;
}

/** 引号内可以出现换行，一行读完后按引号数的奇偶判断记录是否还没结束 */
const unclosed = (s) => {
  let n = 0;
  for (const c of s) if (c === '"') n++;
  return n % 2 === 1;
};

/* ============================================================
   学历门槛

   原文表的 degree 一列此前整批为空，汇总表无此列，学历一维因而在界面上
   一直由补齐层填。但正文多数写有明确的门槛语，算法侧的职级（level）本就是
   这么抽出来的（level_source 记作 text），学历沿同一路径可得，故在此补一道抽取。
   列里有值时以列为准，但列值同样过一遍本函数归一到六档轴上：列里另有
   “中技/中专”“高中”两种写法，不归一时同一档会在轴上拆成两三个名字各占一格。

   六档由低到高排列，扫到的第一档即门槛：招聘原文写“大专及以上学历，
   本科优先”时，门槛是大专而不是本科，按低档优先扫恰好取到门槛。
   “学历不限”排在最前 —— 它是一条独立的读数，不是最低的一档。

   判定限于出现学历语境的条目：正文里既无“学历”“学位”“毕业”，
   也无任何学历名词时不作判定，此类条目不计入本维的分母。
   ============================================================ */

const DEGREE_TIERS = [
  { k: '学历不限', re: /学历不限|不限学历|学历\s*[:：]?\s*不限/ },
  { k: '高中及中专', re: /(?:高中|中专|技校|职高)(?:及?以上)?(?:学历|文凭)?/ },
  { k: '大专', re: /(?:大专|专科|高职)(?:及?以上)?(?:学历|文凭)?/ },
  { k: '本科', re: /(?:本科|学士|大学本科|统招本科|全日制本科)(?:及?以上)?(?:学历|学位)?/ },
  { k: '硕士', re: /(?:硕士|研究生)(?:及?以上)?(?:学历|学位)?/ },
  { k: '博士', re: /博士(?:研究生)?(?:及?以上)?(?:学历|学位)?/ },
];

/** 有没有谈到学历这件事。不谈的条目不进入本维，与“不限”不是一回事 */
const DEGREE_CTX = /学历|学位|毕业|文凭|本科|大专|专科|硕士|博士|研究生|高中|中专/;

/** 正文 → 学历门槛档；无从判定时返回空串 */
function degreeOf(text) {
  if (!text || !DEGREE_CTX.test(text)) return '';
  for (const t of DEGREE_TIERS) if (t.re.test(text)) return t.k;
  return '';
}

/* ============================================================
   能力要求的句级归因

   汇总表逐条给出该条招聘信息要求的技能（skill_vec_01）与各技能下命中的
   技能点（skillpoint_map，写作“技能:点1,点2;技能:点3”）；原文表给出正文
   全文。技能点是具体的技术名与工具名，在正文中以原字面出现，故可反查落点，
   再由落点定位所在句 —— 该句即这一项能力要求的支撑句。

   归因只做到句，不做到词：一句话里往往同时出现同一技能下的数个技能点
   （“熟练掌握 Spring、SpringBoot、MyBatis”），逐词切开对核对没有意义。

   与既有的原文摘录（samples）不同：摘录取每岗前三条整段正文，供核对表述；
   归因给的是“该岗位的这一项能力要求由哪些条、哪些句、哪些企业提出”，
   是证据链锚点表所需的那一份。
   ============================================================ */

/** 句边界。中文句读、换行与分号断句，另把“1、”“2.”一类的编号项起头也当作断点 */
const SENT_SPLIT = /(?<=[。；！？\n])|(?=\s*\d+\s*[、.）)])|(?<=[;!?])\s/;

/** 支撑句的长度上下限。过短的多是标题残句，过长的读者无从定位到那一处 */
const QUOTE_MIN = 6;
const QUOTE_MAX = 160;

/**
 * 定位一条招聘信息里各项能力要求的支撑句。
 *
 * @param text 正文全文
 * @param spMap 技能名 → 该技能下命中的技能点名
 * @returns 技能名 → { 支撑句, 句中命中的技能点 }
 */
function attributeOf(text, spMap) {
  const out = new Map();
  const keys = Object.keys(spMap);
  if (!text || !keys.length) return out;

  /* 全文只转一次小写：技能点名多为英文，大小写在正文里并不统一
     （Mysql / MySQL / mysql 三种写法同时存在），逐句转会重复几十遍 */
  const low = text.toLowerCase();

  /* 句边界只切一次，记下各句在全文中的起点，落点按起点归句 */
  const parts = text.split(SENT_SPLIT);
  const starts = [];
  const sents = [];
  let at = 0;
  for (const p of parts) {
    starts.push(at);
    sents.push(p);
    at += p.length;
  }

  /** 全文偏移落在第几句 */
  const sentAt = (pos) => {
    let lo = 0;
    let hi = starts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (starts[mid] <= pos) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  };

  for (const skill of keys) {
    /* 同一技能下多个技能点落在不同句时取最靠前的那一句：招聘原文的能力要求
       多按“要求”一段集中写，靠前的落点通常就在要求段内，靠后的常落在
       公司介绍或福利段里顺带提及的技术名上 */
    let best = -1;
    const hit = [];
    for (const pt of spMap[skill]) {
      /* 单字技能点（“C”“R”这类语言名）在正文里到处都是，按字面定位必然误中，
         归因不收；它们在技能点一层仍照常计量，只是不作为归因的锚 */
      if (pt.length < 2) continue;
      const pos = low.indexOf(pt.toLowerCase());
      if (pos < 0) continue;
      hit.push(pt);
      if (best < 0 || pos < best) best = pos;
    }
    if (best < 0) continue;
    let s = sents[sentAt(best)].replace(/\s+/g, ' ').trim();
    if (s.length < QUOTE_MIN) continue;
    if (s.length > QUOTE_MAX) s = `${s.slice(0, QUOTE_MAX)}…`;
    out.set(skill, { quote: s, points: hit });
  }
  return out;
}

/* ============================================================
   1. 汇总表：jobid → 岗位规范名与该条的能力要求

   连接键取 jobid。汇总表是原文表按岗位分层抽样后的子集，
   故连接只覆盖抽样入图的那一部分，与 jdstats 的口径一致。

   除岗位名外另取三列供句级归因用：skill_vec_01 是该条要求的技能清单，
   skillpoint_map 是各技能下命中的技能点，task_vec_01 是该条涉及的任务。
   ============================================================ */

/** "技能:点1,点2;技能:点3" → { 技能: [点1, 点2] } */
function parseSpMap(s) {
  const out = {};
  for (const seg of (s ?? '').split(';')) {
    const k = seg.indexOf(':');
    if (k < 0) continue;
    const skill = seg.slice(0, k).trim();
    const pts = seg
      .slice(k + 1)
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean);
    if (skill && pts.length) out[skill] = pts;
  }
  return out;
}

const JOB_OF = new Map();
/** `窗口|jobid` → { skills, spm }。只对进入归因的条目建表，其余不占内存 */
const REQ_OF = new Map();
const WINDOWS = [];
for (const f of fs.readdirSync(SUM_DIR).filter((x) => x.endsWith('.csv')).sort()) {
  const w = f.match(/(\d{4}-\d{2})/)?.[1];
  if (!w) continue;
  WINDOWS.push(w);
  const lines = fs.readFileSync(path.join(SUM_DIR, f), 'utf8').split(/\r?\n/);
  const header = splitCsv(lines[0].replace(/^﻿/, ''));
  const ix = Object.fromEntries(header.map((h, i) => [h, i]));
  for (let i = 1; i < lines.length; i++) {
    if (!lines[i]) continue;
    const c = splitCsv(lines[i]);
    const job = c[ix.std_job];
    const id = c[ix.jobid];
    if (!job || !id) continue;
    const key = `${w}|${id}`;
    JOB_OF.set(key, job);
    const spm = parseSpMap(c[ix.skillpoint_map]);
    if (Object.keys(spm).length) REQ_OF.set(key, spm);
  }
}
console.log(`汇总表 ${WINDOWS.length} 窗，抽样条目 ${JOB_OF.size}，带技能点映射 ${REQ_OF.size}`);

/* ============================================================
   2. 原文表：按岗位聚合城市、企业与原文摘录

   城市取 place 的第一段：该列写作“上海-浦东新区”，区一级过细，
   一个岗位在一个窗口里的区级分布多数只有个位数。

   分隔符有短横线与间隔号两种（“上海-浦东新区”与“上海·浦东新区”），
   后者自 2026-09-03 批次起出现。只切短横线时，带间隔号的取值整串留作城市名，
   同一座城会按区拆成数十个“城市” —— 该批 1240 个取值里有 849 个是这么来的。
   ============================================================ */

/** 每个岗位保留的原文条数。取三条：一条不足以看出该岗位的表述差异，
    多于三条则同一屏内读不完，且产物体积按岗位数放大一百倍 */
const SAMPLE_PER_JOB = 3;
/** 摘录长度。招聘原文多在千字上下，全文入库使产物超出一次加载的合理范围 */
const SNIPPET_LEN = 420;

/** 归因逐岗位保留的能力项数，按支撑条数降序取。一个岗位命中的技能有四十项上下，
    尾部多为个位数的条目，读不出企业之间的表述差异，而产物体积按岗位数放大百倍 */
const ATTRIB_TOP_SKILLS = 14;
/** 每一项能力保留的支撑句条数。三条方能看出不同企业的表述差异，
    且下面按企业去重取，三条即三家 */
const ATTRIB_QUOTES = 3;
/** 每一项能力保留的企业数与城市数，供跨条件复现按这两维切分子样本 */
const ATTRIB_FACETS = 8;

const byJob = new Map();
const rec = (job) =>
  byJob.get(job) ??
  (byJob.set(job, {
    n: 0,
    cities: new Map(),
    companies: new Map(),
    degrees: new Map(),
    /** 有学历读数的条数。学历的分母是它，不是该岗位的全部条数 */
    degreeN: 0,
    samples: [],
    /** 技能名 → { n, byCompany, byCity, quotes }，见句级归因一节 */
    attrib: new Map(),
  }),
  byJob.get(job));

const overall = {
  n: 0,
  matched: 0,
  cities: new Map(),
  companies: new Map(),
  degrees: new Map(),
  degreeN: 0,
  /** 归因命中的（条，能力项）对数与其中定位到支撑句的对数 */
  attribPairs: 0,
  attribHit: 0,
};
const perWindow = {};

for (const w of WINDOWS) {
  const p = path.join(JD_DIR, `${w}.csv`);
  if (!fs.existsSync(p)) {
    console.log(`  ${w} 无原文表，跳过`);
    continue;
  }
  const rl = readline.createInterface({
    input: fs.createReadStream(p, { encoding: 'utf8' }),
    crlfDelay: Infinity,
  });
  let header = null;
  let ix = null;
  let buf = '';
  let open = false;
  let rows = 0;
  let matched = 0;
  let degreeFilled = 0;
  let attribHit = 0;
  for await (const line of rl) {
    if (header === null) {
      header = splitCsv(line.replace(/^﻿/, ''));
      ix = Object.fromEntries(header.map((h, i) => [h, i]));
      continue;
    }
    buf = open ? `${buf}\n${line}` : line;
    open = unclosed(buf);
    if (open) continue;
    const c = splitCsv(buf);
    buf = '';
    if (c.length < header.length) continue;
    rows++;
    const job = JOB_OF.get(`${w}|${c[ix.jobid]}`);
    if (!job) continue;
    matched++;

    const r = rec(job);
    r.n++;
    overall.matched++;

    const city = (c[ix.place] ?? '').split(/[-·]/)[0].trim();
    if (city) {
      r.cities.set(city, (r.cities.get(city) ?? 0) + 1);
      overall.cities.set(city, (overall.cities.get(city) ?? 0) + 1);
    }
    const comp = (c[ix.company] ?? '').trim();
    if (comp) {
      r.companies.set(comp, (r.companies.get(comp) ?? 0) + 1);
      overall.companies.set(comp, (overall.companies.get(comp) ?? 0) + 1);
    }
    /* 学历以原文表的 degree 一列为准，该列为空时由正文的门槛语抽出。
       列里的取值不循六档轴的写法（另有“中技/中专”“高中”两种），故同样过一遍
       degreeOf 归一到轴上，否则同一档会在轴上拆成两三个名字各占一格。
       2026-09-03 批次该列开始有值，占连接条数的一成上下，此前各批整列为空 */
    const raw = (c[ix.degree] ?? '').trim();
    const text = c[ix.job_information] ?? '';
    const deg = degreeOf(raw) || degreeOf(text);
    if (deg) {
      degreeFilled++;
      r.degreeN++;
      overall.degreeN++;
      r.degrees.set(deg, (r.degrees.get(deg) ?? 0) + 1);
      overall.degrees.set(deg, (overall.degrees.get(deg) ?? 0) + 1);
    }

    /* ---- 句级归因 ---- */
    const spm = REQ_OF.get(`${w}|${c[ix.jobid]}`);
    if (spm) {
      const found = attributeOf(text, spm);
      overall.attribPairs += Object.keys(spm).length;
      overall.attribHit += found.size;
      attribHit += found.size;
      for (const [skill, { quote, points }] of found) {
        let a = r.attrib.get(skill);
        if (!a) {
          a = { n: 0, byCompany: new Map(), byCity: new Map(), quotes: [], seenComp: new Set() };
          r.attrib.set(skill, a);
        }
        a.n++;
        if (comp) a.byCompany.set(comp, (a.byCompany.get(comp) ?? 0) + 1);
        if (city) a.byCity.set(city, (a.byCity.get(city) ?? 0) + 1);
        /* 引文按企业去重：同一家企业的重复发布在正文上逐字相同，
           三条全来自一家等于把“不同企业怎么说”这一问答成了“同一家说了三遍” */
        if (a.quotes.length < ATTRIB_QUOTES && !(comp && a.seenComp.has(comp))) {
          if (comp) a.seenComp.add(comp);
          a.quotes.push({
            id: c[ix.jobid],
            w,
            company: comp,
            city,
            date: (c[ix.opentime] ?? '').trim(),
            points,
            text: quote,
          });
        }
      }
    }

    if (r.samples.length < SAMPLE_PER_JOB) {
      const flat = text.replace(/\s+/g, ' ').trim();
      /* 过短的条目多为“详见附件”一类的占位，摘不出可核对的表述 */
      if (flat.length >= 200) {
        r.samples.push({
          id: c[ix.jobid],
          w,
          title: (c[ix.job] ?? '').trim(),
          company: comp,
          city,
          salary: (c[ix.salary] ?? '').trim(),
          date: (c[ix.opentime] ?? '').trim(),
          text: flat.length > SNIPPET_LEN ? `${flat.slice(0, SNIPPET_LEN)}…` : flat,
          full: flat.length,
        });
      }
    }
  }
  overall.n += rows;
  perWindow[w] = { rows, matched, degreeFilled, attribHit };
  console.log(
    `  ${w} 原文 ${rows} 行，连接 ${matched} 条，学历判定 ${degreeFilled}，归因 ${attribHit} 项`,
  );
}

/* ============================================================
   3. 产出

   城市逐座给出，企业只留前若干家：城市要按省汇总、并支持逐城勾选，
   截尾会把尾部各城连同它们所属的省份一起算没；企业没有这一层汇总，
   四万余家逐家入库既读不出差别又按岗位数放大体积。
   ============================================================ */

const COMPANY_TOP = 10;

const topOf = (m, k) =>
  [...m.entries()]
    .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
    .slice(0, k);

/** 归因按支撑条数取前若干项能力，各留计数、两维切分与引文 */
function packAttrib(r) {
  const top = [...r.attrib.entries()].sort((a, b) => b[1].n - a[1].n).slice(0, ATTRIB_TOP_SKILLS);
  const out = {};
  /* 两维切分各带自己的分母：下游要算的是提及率（该子样本内提出此项的条数
     ÷ 该子样本内该岗位的总条数）。分母若让下游自己去别处查，只能查到
     产出时截过尾的那份企业清单，两份清单的交集之外就没有分母可用，
     那一维于是整行画不出来。故在此逐桶带上，格式为 [键, 支撑条数, 总条数]。 */
  const facets = (m, denom) =>
    topOf(m, ATTRIB_FACETS).map(([k, n]) => [k, n, denom.get(k) ?? n]);
  for (const [skill, a] of top) {
    out[skill] = {
      n: a.n,
      /* 两维的全量基数照给：清单被截断，计数不截断，否则跨条件复现
         按“这一项在几家企业出现过”读出来的数会小于实情 */
      nCompanies: a.byCompany.size,
      nCities: a.byCity.size,
      byCompany: facets(a.byCompany, r.companies),
      byCity: facets(a.byCity, r.cities),
      quotes: a.quotes,
    };
  }
  return out;
}

const packed = {};
for (const [job, r] of byJob) {
  packed[job] = {
    n: r.n,
    /* 城市逐座给出，不截尾：下游要按省汇总，截尾会把尾部各城连同它们所属的
       省份一起丢掉，而尾部恰是中西部各省的主要来源。三百余座城市按岗位存一份，
       产物增约六百千字节，换来的是省级汇总与城市级勾选两件事都能算准 */
    cities: Object.fromEntries(topOf(r.cities, Infinity)),
    nCities: r.cities.size,
    companies: topOf(r.companies, COMPANY_TOP),
    nCompanies: r.companies.size,
    degrees: Object.fromEntries(topOf(r.degrees, 8)),
    /* 学历的分母：正文里谈到学历的条数。该岗位的全部条数里有一部分
       通篇不提学历，把它们算进分母会让各档占比一律偏低 */
    degreeN: r.degreeN,
    samples: r.samples,
    attrib: packAttrib(r),
    /* 归因覆盖到的能力项数。清单截到前若干项，这个数不截 */
    nAttrib: r.attrib.size,
  };
}

const doc = {
  schema: '1.1',
  windows: WINDOWS,
  perWindow,
  overall: {
    rows: overall.n,
    matched: overall.matched,
    cities: Object.fromEntries(topOf(overall.cities, Infinity)),
    nCities: overall.cities.size,
    companies: topOf(overall.companies, 20),
    nCompanies: overall.companies.size,
    degrees: Object.fromEntries(topOf(overall.degrees, 8)),
    degreeN: overall.degreeN,
    attribPairs: overall.attribPairs,
    attribHit: overall.attribHit,
  },
  byJob: packed,
  sampleLimits: { perJob: SAMPLE_PER_JOB, snippet: SNIPPET_LEN },
  attribLimits: {
    topSkills: ATTRIB_TOP_SKILLS,
    quotes: ATTRIB_QUOTES,
    facets: ATTRIB_FACETS,
    quoteLen: QUOTE_MAX,
  },
};

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(doc), 'utf8');
const degSum = [...overall.degrees.values()].reduce((a, b) => a + b, 0);
console.log(
  `\n写出 ${OUT}  ${(fs.statSync(OUT).size / 1024).toFixed(1)} KB\n` +
    `岗位 ${byJob.size} · 原文 ${overall.n} 行 · 连接 ${overall.matched} 条 · ` +
    `城市 ${overall.cities.size} · 企业 ${overall.companies.size}\n` +
    `学历判定 ${degSum} 条（占连接数 ${((100 * degSum) / Math.max(1, overall.matched)).toFixed(1)}%）· ` +
    `分布 ${topOf(overall.degrees, 8)
      .map(([k, v]) => `${k} ${((100 * v) / Math.max(1, degSum)).toFixed(1)}%`)
      .join(' ')}\n` +
    `句级归因 ${overall.attribHit} / ${overall.attribPairs} 对（` +
    `${((100 * overall.attribHit) / Math.max(1, overall.attribPairs)).toFixed(1)}%）`,
);
