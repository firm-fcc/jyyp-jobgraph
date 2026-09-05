/* ============================================================
   新一代信息技术领域词表（人工智能 / 大数据 / 智能系统 / 物联网）
   —— 对应算法 §1.5 冷启动：岗位体系、任务体系、能力体系、技能点体系
   ============================================================ */

import type { SkillType, TechStack } from '@/types/graph';

export interface SkillSeed {
  name: string;
  category: TechStack;
  /** 组内技能点数 —— 真实数据下这一层唯一可计量的量 */
  realCount?: number;
}

export interface SkillPointSeed {
  name: string;
  /** 多对多：一个技能点可归属多个能力类别（算法 §1.4） */
  skills: string[];
  category: TechStack;
  /** 1 基础 · 2 进阶 · 3 前沿。@deprecated 真实数据无成熟度分档 */
  level: 1 | 2 | 3;
  firstSeen: string;
  /** 前瞻信号支持、JD 侧尚未确认或刚刚确认 */
  emerging?: boolean;
  /** 软硬技能两态（skills0821.json 真实字段，其中"软硬兼具"已按所属维度并入） */
  skillType?: SkillType;
  /** 技能点定义（skills0821.json 真实字段） */
  definition?: string;
}

export interface TaskSeed {
  name: string;
  category: TechStack;
  skills: string[];
  firstSeen: string;
  emerging?: boolean;
  /** 任务描述（任务体系 v0.3 真实字段） */
  definition?: string;
}

export interface JobSeed {
  name: string;
  category: TechStack;
  cluster: string;
  emerging?: boolean;
  firstSeen: string;
  definition: string;
  coreDuties: string[];
  mustSkills: string[];
  plusSkills: string[];
  scenarios: string[];
  tasks: string[];
  directSkills: string[];
  /** 薪资区间 k/月 */
  salary: [number, number];
  /** 招聘平台原始职能名，同时作为同义词进搜索 */
  aliases?: string[];
  funtypes?: string[];
  /** 归类判定关键词（岗位体系 v2.0 真实字段），也是本岗位最具体的技术指纹 */
  keywords?: string[];
  /** 与最易混淆的同侪岗位之间的判据（v2.0 真实字段） */
  boundary?: string;
  nameEn?: string;
  /** 招聘信息条数（v2.0 的 hits）—— 岗位这一层唯一的实测计量 */
  posts?: number;
  /** 市场规模的通用读数。真实体系下等于 posts */
  realCount?: number;
  /** 顶层祖先名 */
  topCategory?: string;
}

/* ---------------- 能力类别 S（稳定，跨越数年不变）---------------- */

export const SKILLS: SkillSeed[] = [
  { name: '编程能力', category: 'AI基础设施' },
  { name: 'AI框架能力', category: '大模型与AIGC' },
  { name: '机器学习理论', category: '数据与智能分析' },
  { name: '深度学习建模', category: '大模型与AIGC' },
  { name: '数据处理能力', category: '数据与智能分析' },
  { name: '分布式系统能力', category: 'AI基础设施' },
  { name: '系统架构设计', category: 'AI基础设施' },
  { name: '算法优化能力', category: 'AI基础设施' },
  { name: '数学与统计', category: '数据与智能分析' },
  { name: '云原生与运维', category: 'AI基础设施' },
  { name: '数据库能力', category: '数据与智能分析' },
  { name: '计算机视觉技术', category: '智能系统与感知' },
  { name: '自然语言处理技术', category: '大模型与AIGC' },
  { name: '嵌入式与硬件', category: '物联网与边缘计算' },
  { name: '安全与合规', category: '安全与合规' },
  { name: '产品与业务理解', category: '数据与智能分析' },
];

/* ---------------- 技能点 SP（随技术迭代快速更替）---------------- */

export const SKILL_POINTS: SkillPointSeed[] = [
  // 编程能力
  { name: 'Python', skills: ['编程能力', '数据处理能力'], category: 'AI基础设施', level: 1, firstSeen: '2019-01' },
  { name: 'C++', skills: ['编程能力', '算法优化能力'], category: 'AI基础设施', level: 2, firstSeen: '2019-01' },
  { name: 'Go', skills: ['编程能力', '系统架构设计'], category: 'AI基础设施', level: 2, firstSeen: '2019-06' },
  { name: 'Rust', skills: ['编程能力'], category: 'AI基础设施', level: 3, firstSeen: '2022-03' },
  { name: 'Java', skills: ['编程能力'], category: 'AI基础设施', level: 1, firstSeen: '2019-01' },
  { name: 'Shell/Linux', skills: ['编程能力', '云原生与运维'], category: 'AI基础设施', level: 1, firstSeen: '2019-01' },

  // AI 框架
  { name: 'PyTorch', skills: ['AI框架能力', '深度学习建模'], category: '大模型与AIGC', level: 1, firstSeen: '2019-03' },
  { name: 'TensorFlow', skills: ['AI框架能力'], category: '大模型与AIGC', level: 1, firstSeen: '2019-01' },
  { name: 'JAX', skills: ['AI框架能力'], category: '大模型与AIGC', level: 3, firstSeen: '2021-09' },
  { name: 'Transformers', skills: ['AI框架能力', '自然语言处理技术'], category: '大模型与AIGC', level: 1, firstSeen: '2020-06' },
  { name: 'LangChain', skills: ['AI框架能力', '自然语言处理技术'], category: '大模型与AIGC', level: 2, firstSeen: '2023-02' },
  { name: 'LlamaIndex', skills: ['AI框架能力', '自然语言处理技术'], category: '大模型与AIGC', level: 2, firstSeen: '2023-04' },
  { name: 'LangGraph', skills: ['AI框架能力', '系统架构设计'], category: '大模型与AIGC', level: 3, firstSeen: '2024-03', emerging: true },
  { name: 'CrewAI', skills: ['AI框架能力'], category: '大模型与AIGC', level: 3, firstSeen: '2024-05', emerging: true },
  { name: 'AutoGen', skills: ['AI框架能力'], category: '大模型与AIGC', level: 3, firstSeen: '2024-01', emerging: true },
  { name: 'Dify', skills: ['AI框架能力', '产品与业务理解'], category: '大模型与AIGC', level: 2, firstSeen: '2023-09' },
  { name: 'MCP协议', skills: ['AI框架能力', '系统架构设计'], category: '大模型与AIGC', level: 3, firstSeen: '2024-11', emerging: true },
  { name: 'A2A协议', skills: ['AI框架能力', '系统架构设计'], category: '大模型与AIGC', level: 3, firstSeen: '2025-04', emerging: true },

  // 机器学习理论
  { name: '监督学习', skills: ['机器学习理论'], category: '数据与智能分析', level: 1, firstSeen: '2019-01' },
  { name: '强化学习', skills: ['机器学习理论'], category: '数据与智能分析', level: 2, firstSeen: '2019-05' },
  { name: 'RLHF', skills: ['机器学习理论', '深度学习建模'], category: '大模型与AIGC', level: 3, firstSeen: '2022-06' },
  { name: 'DPO', skills: ['机器学习理论'], category: '大模型与AIGC', level: 3, firstSeen: '2023-08', emerging: true },
  { name: 'GRPO', skills: ['机器学习理论'], category: '大模型与AIGC', level: 3, firstSeen: '2024-06', emerging: true },
  { name: '贝叶斯方法', skills: ['机器学习理论', '数学与统计'], category: '数据与智能分析', level: 2, firstSeen: '2019-01' },

  // 深度学习建模
  { name: 'Transformer架构', skills: ['深度学习建模'], category: '大模型与AIGC', level: 1, firstSeen: '2019-06' },
  { name: 'Diffusion模型', skills: ['深度学习建模', '计算机视觉技术'], category: '大模型与AIGC', level: 2, firstSeen: '2022-02' },
  { name: 'MoE架构', skills: ['深度学习建模'], category: '大模型与AIGC', level: 3, firstSeen: '2023-11', emerging: true },
  { name: 'LoRA微调', skills: ['深度学习建模', 'AI框架能力'], category: '大模型与AIGC', level: 2, firstSeen: '2022-09' },
  { name: 'QLoRA', skills: ['深度学习建模'], category: '大模型与AIGC', level: 3, firstSeen: '2023-06' },
  { name: 'CLIP', skills: ['深度学习建模', '计算机视觉技术'], category: '智能系统与感知', level: 2, firstSeen: '2021-05' },
  { name: '长上下文技术', skills: ['深度学习建模', '自然语言处理技术'], category: '大模型与AIGC', level: 3, firstSeen: '2024-02', emerging: true },
  { name: 'Test-time Scaling', skills: ['深度学习建模'], category: '大模型与AIGC', level: 3, firstSeen: '2024-09', emerging: true },
  { name: '世界模型', skills: ['深度学习建模'], category: '智能系统与感知', level: 3, firstSeen: '2024-08', emerging: true },

  // 数据处理
  { name: 'Spark', skills: ['数据处理能力', '分布式系统能力'], category: '数据与智能分析', level: 1, firstSeen: '2019-01' },
  { name: 'Flink', skills: ['数据处理能力'], category: '数据与智能分析', level: 2, firstSeen: '2019-08' },
  { name: 'Kafka', skills: ['数据处理能力'], category: '数据与智能分析', level: 1, firstSeen: '2019-01' },
  { name: 'Pandas', skills: ['数据处理能力'], category: '数据与智能分析', level: 1, firstSeen: '2019-01' },
  { name: 'dbt', skills: ['数据处理能力'], category: '数据与智能分析', level: 2, firstSeen: '2021-07' },
  { name: 'Airflow', skills: ['数据处理能力', '云原生与运维'], category: '数据与智能分析', level: 2, firstSeen: '2019-10' },
  { name: 'DolphinScheduler', skills: ['数据处理能力'], category: '数据与智能分析', level: 2, firstSeen: '2021-03' },

  // 分布式
  { name: 'Ray', skills: ['分布式系统能力', 'AI框架能力'], category: 'AI基础设施', level: 3, firstSeen: '2021-11' },
  { name: 'DeepSpeed', skills: ['分布式系统能力', '算法优化能力'], category: 'AI基础设施', level: 3, firstSeen: '2021-06' },
  { name: 'Megatron-LM', skills: ['分布式系统能力'], category: 'AI基础设施', level: 3, firstSeen: '2022-04' },
  { name: 'FSDP', skills: ['分布式系统能力'], category: 'AI基础设施', level: 3, firstSeen: '2022-10' },
  { name: 'NCCL', skills: ['分布式系统能力', '算法优化能力'], category: 'AI基础设施', level: 3, firstSeen: '2021-02' },

  // 架构
  { name: '微服务架构', skills: ['系统架构设计'], category: 'AI基础设施', level: 2, firstSeen: '2019-01' },
  { name: '高并发设计', skills: ['系统架构设计'], category: 'AI基础设施', level: 2, firstSeen: '2019-01' },
  { name: '事件驱动架构', skills: ['系统架构设计'], category: 'AI基础设施', level: 2, firstSeen: '2020-03' },
  { name: 'AI Gateway', skills: ['系统架构设计', '安全与合规'], category: 'AI基础设施', level: 3, firstSeen: '2024-07', emerging: true },
  { name: '上下文工程', skills: ['系统架构设计', '自然语言处理技术'], category: '大模型与AIGC', level: 3, firstSeen: '2025-01', emerging: true },

  // 算法优化
  { name: 'CUDA', skills: ['算法优化能力', '分布式系统能力'], category: 'AI基础设施', level: 3, firstSeen: '2019-04' },
  { name: 'TensorRT', skills: ['算法优化能力'], category: 'AI基础设施', level: 3, firstSeen: '2020-05' },
  { name: 'ONNX', skills: ['算法优化能力'], category: 'AI基础设施', level: 2, firstSeen: '2020-01' },
  { name: '量化与蒸馏', skills: ['算法优化能力', '深度学习建模'], category: 'AI基础设施', level: 3, firstSeen: '2021-08' },
  { name: 'vLLM', skills: ['算法优化能力', 'AI框架能力'], category: 'AI基础设施', level: 3, firstSeen: '2023-07', emerging: true },
  { name: 'SGLang', skills: ['算法优化能力'], category: 'AI基础设施', level: 3, firstSeen: '2024-04', emerging: true },
  { name: 'Triton推理服务', skills: ['算法优化能力', '云原生与运维'], category: 'AI基础设施', level: 3, firstSeen: '2021-04' },

  // 数学统计
  { name: '线性代数', skills: ['数学与统计'], category: '数据与智能分析', level: 1, firstSeen: '2019-01' },
  { name: '概率统计', skills: ['数学与统计'], category: '数据与智能分析', level: 1, firstSeen: '2019-01' },
  { name: '最优化方法', skills: ['数学与统计', '算法优化能力'], category: '数据与智能分析', level: 2, firstSeen: '2019-01' },
  { name: '因果推断', skills: ['数学与统计'], category: '数据与智能分析', level: 3, firstSeen: '2021-01' },
  { name: 'AB实验', skills: ['数学与统计', '产品与业务理解'], category: '数据与智能分析', level: 2, firstSeen: '2019-03' },

  // 云原生
  { name: 'Docker', skills: ['云原生与运维'], category: 'AI基础设施', level: 1, firstSeen: '2019-01' },
  { name: 'Kubernetes', skills: ['云原生与运维', '分布式系统能力'], category: 'AI基础设施', level: 2, firstSeen: '2019-05' },
  { name: 'Prometheus', skills: ['云原生与运维'], category: 'AI基础设施', level: 2, firstSeen: '2019-09' },
  { name: 'MLflow', skills: ['云原生与运维', 'AI框架能力'], category: 'AI基础设施', level: 2, firstSeen: '2020-08' },
  { name: 'Kubeflow', skills: ['云原生与运维'], category: 'AI基础设施', level: 3, firstSeen: '2020-11' },
  { name: 'OpenTelemetry', skills: ['云原生与运维'], category: 'AI基础设施', level: 3, firstSeen: '2022-05' },

  // 数据库
  { name: 'MySQL', skills: ['数据库能力'], category: '数据与智能分析', level: 1, firstSeen: '2019-01' },
  { name: 'ClickHouse', skills: ['数据库能力'], category: '数据与智能分析', level: 2, firstSeen: '2020-07' },
  { name: 'Doris', skills: ['数据库能力'], category: '数据与智能分析', level: 2, firstSeen: '2021-10' },
  { name: 'Elasticsearch', skills: ['数据库能力', '自然语言处理技术'], category: '数据与智能分析', level: 2, firstSeen: '2019-02' },
  { name: 'Milvus', skills: ['数据库能力', '自然语言处理技术'], category: '数据与智能分析', level: 3, firstSeen: '2022-08' },
  { name: 'FAISS', skills: ['数据库能力'], category: '数据与智能分析', level: 2, firstSeen: '2020-09' },
  { name: 'Qdrant', skills: ['数据库能力'], category: '数据与智能分析', level: 3, firstSeen: '2023-05' },
  { name: 'pgvector', skills: ['数据库能力'], category: '数据与智能分析', level: 3, firstSeen: '2023-10', emerging: true },
  { name: 'Neo4j', skills: ['数据库能力'], category: '数据与智能分析', level: 2, firstSeen: '2019-11' },
  { name: 'GraphRAG', skills: ['数据库能力', '自然语言处理技术'], category: '大模型与AIGC', level: 3, firstSeen: '2024-05', emerging: true },

  // 计算机视觉
  { name: 'YOLO', skills: ['计算机视觉技术'], category: '智能系统与感知', level: 1, firstSeen: '2019-01' },
  { name: 'SAM分割', skills: ['计算机视觉技术'], category: '智能系统与感知', level: 3, firstSeen: '2023-04' },
  { name: '三维重建', skills: ['计算机视觉技术'], category: '智能系统与感知', level: 3, firstSeen: '2020-04' },
  { name: '点云处理', skills: ['计算机视觉技术'], category: '智能系统与感知', level: 2, firstSeen: '2019-07' },
  { name: 'VLA模型', skills: ['计算机视觉技术', '深度学习建模'], category: '智能系统与感知', level: 3, firstSeen: '2024-06', emerging: true },
  { name: 'Diffusion Policy', skills: ['计算机视觉技术', '机器学习理论'], category: '智能系统与感知', level: 3, firstSeen: '2024-03', emerging: true },

  // NLP
  { name: '文本向量化', skills: ['自然语言处理技术'], category: '大模型与AIGC', level: 1, firstSeen: '2019-01' },
  { name: 'RAG检索增强', skills: ['自然语言处理技术', 'AI框架能力'], category: '大模型与AIGC', level: 2, firstSeen: '2023-01' },
  { name: 'Agentic RAG', skills: ['自然语言处理技术'], category: '大模型与AIGC', level: 3, firstSeen: '2024-08', emerging: true },
  { name: '提示词工程', skills: ['自然语言处理技术', '产品与业务理解'], category: '大模型与AIGC', level: 1, firstSeen: '2022-12' },
  { name: 'Text2SQL', skills: ['自然语言处理技术', '数据库能力'], category: '数据与智能分析', level: 2, firstSeen: '2023-03' },
  { name: '知识图谱构建', skills: ['自然语言处理技术', '数据库能力'], category: '数据与智能分析', level: 2, firstSeen: '2019-04' },

  // 嵌入式
  { name: 'ROS2', skills: ['嵌入式与硬件'], category: '物联网与边缘计算', level: 2, firstSeen: '2020-02' },
  { name: 'FreeRTOS', skills: ['嵌入式与硬件'], category: '物联网与边缘计算', level: 2, firstSeen: '2019-01' },
  { name: 'MQTT', skills: ['嵌入式与硬件'], category: '物联网与边缘计算', level: 1, firstSeen: '2019-01' },
  { name: 'RISC-V', skills: ['嵌入式与硬件'], category: '物联网与边缘计算', level: 3, firstSeen: '2022-01' },
  { name: 'NPU部署', skills: ['嵌入式与硬件', '算法优化能力'], category: '物联网与边缘计算', level: 3, firstSeen: '2022-07' },
  { name: 'TinyML', skills: ['嵌入式与硬件'], category: '物联网与边缘计算', level: 3, firstSeen: '2021-12' },
  { name: 'LoRaWAN', skills: ['嵌入式与硬件'], category: '物联网与边缘计算', level: 2, firstSeen: '2019-06' },
  { name: '边缘推理框架', skills: ['嵌入式与硬件', '算法优化能力'], category: '物联网与边缘计算', level: 3, firstSeen: '2023-12', emerging: true },

  // 安全合规
  { name: '模型对齐', skills: ['安全与合规', '机器学习理论'], category: '安全与合规', level: 3, firstSeen: '2023-02' },
  { name: '红队测试', skills: ['安全与合规'], category: '安全与合规', level: 3, firstSeen: '2023-09', emerging: true },
  { name: 'OWASP LLM Top10', skills: ['安全与合规'], category: '安全与合规', level: 3, firstSeen: '2023-11', emerging: true },
  { name: '差分隐私', skills: ['安全与合规', '数学与统计'], category: '安全与合规', level: 3, firstSeen: '2020-10' },
  { name: '联邦学习', skills: ['安全与合规', '分布式系统能力'], category: '安全与合规', level: 3, firstSeen: '2020-03' },
  { name: '内容安全审核', skills: ['安全与合规'], category: '安全与合规', level: 2, firstSeen: '2019-08' },
  { name: '算法备案与合规', skills: ['安全与合规', '产品与业务理解'], category: '安全与合规', level: 2, firstSeen: '2023-01', emerging: true },

  // 产品业务
  { name: '需求分析', skills: ['产品与业务理解'], category: '数据与智能分析', level: 1, firstSeen: '2019-01' },
  { name: '指标体系设计', skills: ['产品与业务理解', '数学与统计'], category: '数据与智能分析', level: 2, firstSeen: '2019-02' },
  { name: '业务建模', skills: ['产品与业务理解'], category: '数据与智能分析', level: 2, firstSeen: '2019-01' },
];

/* ---------------- 任务 T ---------------- */

export const TASKS: TaskSeed[] = [
  // 大模型与AIGC
  { name: '大模型微调与对齐', category: '大模型与AIGC', firstSeen: '2022-08', skills: ['深度学习建模', 'AI框架能力', '机器学习理论', '分布式系统能力'] },
  { name: '检索增强生成系统构建', category: '大模型与AIGC', firstSeen: '2023-02', skills: ['自然语言处理技术', '数据库能力', 'AI框架能力', '系统架构设计'] },
  { name: 'Agent编排与工具调用', category: '大模型与AIGC', firstSeen: '2023-10', emerging: true, skills: ['AI框架能力', '系统架构设计', '编程能力', '自然语言处理技术'] },
  { name: '提示词设计与评测', category: '大模型与AIGC', firstSeen: '2023-01', skills: ['自然语言处理技术', '产品与业务理解', '数学与统计'] },
  { name: '长上下文与记忆优化', category: '大模型与AIGC', firstSeen: '2024-03', emerging: true, skills: ['深度学习建模', '算法优化能力', '系统架构设计'] },
  { name: '多模态对齐', category: '大模型与AIGC', firstSeen: '2023-05', skills: ['深度学习建模', '计算机视觉技术', 'AI框架能力'] },
  { name: '模型评测体系建设', category: '大模型与AIGC', firstSeen: '2024-01', emerging: true, skills: ['数学与统计', '自然语言处理技术', '产品与业务理解', '编程能力'] },
  { name: 'AIGC内容生成与治理', category: '大模型与AIGC', firstSeen: '2023-03', skills: ['深度学习建模', '安全与合规', '产品与业务理解'] },

  // 数据与智能分析
  { name: '数据管道构建', category: '数据与智能分析', firstSeen: '2019-01', skills: ['数据处理能力', '编程能力', '数据库能力'] },
  { name: '实时流处理', category: '数据与智能分析', firstSeen: '2019-06', skills: ['数据处理能力', '分布式系统能力', '系统架构设计'] },
  { name: '数据质量治理', category: '数据与智能分析', firstSeen: '2020-02', skills: ['数据处理能力', '数据库能力', '产品与业务理解'] },
  { name: '特征工程', category: '数据与智能分析', firstSeen: '2019-01', skills: ['数据处理能力', '机器学习理论', '数学与统计'] },
  { name: '指标体系与AB实验设计', category: '数据与智能分析', firstSeen: '2019-04', skills: ['数学与统计', '产品与业务理解', '数据处理能力'] },
  { name: '数据资产建模', category: '数据与智能分析', firstSeen: '2020-05', skills: ['数据库能力', '产品与业务理解', '数据处理能力'] },

  // 智能系统与感知
  { name: '目标检测与跟踪', category: '智能系统与感知', firstSeen: '2019-01', skills: ['计算机视觉技术', '深度学习建模', '编程能力'] },
  { name: '三维重建与SLAM', category: '智能系统与感知', firstSeen: '2019-08', skills: ['计算机视觉技术', '数学与统计', '算法优化能力'] },
  { name: '语音识别与合成', category: '智能系统与感知', firstSeen: '2019-03', skills: ['深度学习建模', '自然语言处理技术', '算法优化能力'] },
  { name: '推荐召回与排序', category: '智能系统与感知', firstSeen: '2019-01', skills: ['机器学习理论', '数据处理能力', '分布式系统能力'] },
  { name: '强化学习训练', category: '智能系统与感知', firstSeen: '2020-06', skills: ['机器学习理论', '深度学习建模', '数学与统计'] },
  { name: '具身操作策略学习', category: '智能系统与感知', firstSeen: '2024-05', emerging: true, skills: ['机器学习理论', '计算机视觉技术', '嵌入式与硬件', '深度学习建模'] },

  // 物联网与边缘计算
  { name: '端侧模型部署', category: '物联网与边缘计算', firstSeen: '2021-03', skills: ['嵌入式与硬件', '算法优化能力', '编程能力'] },
  { name: '传感器融合', category: '物联网与边缘计算', firstSeen: '2019-05', skills: ['嵌入式与硬件', '数学与统计', '计算机视觉技术'] },
  { name: '边缘算力调度', category: '物联网与边缘计算', firstSeen: '2022-09', emerging: true, skills: ['分布式系统能力', '嵌入式与硬件', '云原生与运维'] },
  { name: '设备接入与协议适配', category: '物联网与边缘计算', firstSeen: '2019-01', skills: ['嵌入式与硬件', '系统架构设计', '编程能力'] },

  // 安全与合规
  { name: '安全对齐与红队测试', category: '安全与合规', firstSeen: '2023-06', emerging: true, skills: ['安全与合规', '机器学习理论', '自然语言处理技术'] },
  { name: '内容风控', category: '安全与合规', firstSeen: '2019-09', skills: ['安全与合规', '自然语言处理技术', '机器学习理论'] },
  { name: '隐私计算方案设计', category: '安全与合规', firstSeen: '2020-08', skills: ['安全与合规', '分布式系统能力', '数学与统计'] },
  { name: '算法合规备案', category: '安全与合规', firstSeen: '2023-04', emerging: true, skills: ['安全与合规', '产品与业务理解'] },

  // AI 基础设施
  { name: '分布式训练调度', category: 'AI基础设施', firstSeen: '2021-05', skills: ['分布式系统能力', '云原生与运维', '算法优化能力'] },
  { name: '推理性能优化', category: 'AI基础设施', firstSeen: '2022-04', skills: ['算法优化能力', '编程能力', '分布式系统能力'] },
  { name: '模型量化与蒸馏', category: 'AI基础设施', firstSeen: '2021-09', skills: ['算法优化能力', '深度学习建模'] },
  { name: '算力资源调度', category: 'AI基础设施', firstSeen: '2022-06', skills: ['分布式系统能力', '云原生与运维', '系统架构设计'] },
  { name: '向量检索优化', category: 'AI基础设施', firstSeen: '2023-04', skills: ['数据库能力', '算法优化能力', '自然语言处理技术'] },
  { name: '模型服务化部署', category: 'AI基础设施', firstSeen: '2020-10', skills: ['云原生与运维', '系统架构设计', 'AI框架能力'] },
  { name: '知识图谱构建', category: 'AI基础设施', firstSeen: '2019-06', skills: ['自然语言处理技术', '数据库能力', '数据处理能力'] },
];

/* ---------------- 岗位 P/J ---------------- */

export const JOBS: JobSeed[] = [
  {
    name: '大模型算法工程师',
    category: '大模型与AIGC',
    cluster: '算法研发',
    firstSeen: '2022-09',
    salary: [40, 80],
    definition:
      '负责大规模预训练语言模型的训练、微调与对齐，围绕业务场景完成模型能力定制与效果迭代，是企业大模型能力建设的核心技术岗位。',
    coreDuties: [
      '主导基座模型的继续预训练与领域适配，制定数据配方与训练策略',
      '设计并实施 SFT / RLHF / DPO 等对齐流程，提升指令遵循与安全性',
      '搭建模型评测基线，量化分析效果瓶颈并驱动迭代',
      '与工程团队协作完成训练加速与推理落地',
    ],
    mustSkills: ['深度学习建模', 'AI框架能力', '分布式系统能力', '机器学习理论'],
    plusSkills: ['算法优化能力', '数学与统计'],
    scenarios: ['金融智能客服', '工业知识问答', '政务大模型', '医疗辅助诊断'],
    tasks: ['大模型微调与对齐', '模型评测体系建设', '多模态对齐', '长上下文与记忆优化'],
    directSkills: ['深度学习建模', 'AI框架能力', '编程能力', '数学与统计'],
  },
  {
    name: 'NLP算法工程师',
    category: '大模型与AIGC',
    cluster: '算法研发',
    firstSeen: '2019-03',
    salary: [30, 60],
    definition: '负责自然语言处理相关算法的研发与落地，覆盖文本理解、信息抽取、语义检索与对话系统。',
    coreDuties: ['构建文本分类、实体抽取与语义匹配模型', '设计语义检索与问答链路', '持续优化线上效果指标'],
    mustSkills: ['自然语言处理技术', '深度学习建模', '编程能力'],
    plusSkills: ['数据库能力', 'AI框架能力'],
    scenarios: ['智能客服', '舆情分析', '合同审阅', '搜索推荐'],
    tasks: ['检索增强生成系统构建', '提示词设计与评测', '内容风控', '知识图谱构建'],
    directSkills: ['自然语言处理技术', '编程能力', '深度学习建模'],
  },
  {
    name: '多模态算法工程师',
    category: '大模型与AIGC',
    cluster: '算法研发',
    firstSeen: '2023-04',
    salary: [38, 70],
    definition: '负责图文、音视频等多模态数据的统一表征与生成模型研发。',
    coreDuties: ['构建跨模态对齐与融合模型', '优化多模态理解与生成效果', '推动多模态能力在业务侧落地'],
    mustSkills: ['深度学习建模', '计算机视觉技术', 'AI框架能力'],
    plusSkills: ['算法优化能力'],
    scenarios: ['短视频内容理解', '工业质检', '数字人', '智能座舱'],
    tasks: ['多模态对齐', '大模型微调与对齐', 'AIGC内容生成与治理'],
    directSkills: ['深度学习建模', '计算机视觉技术', '编程能力'],
  },
  {
    name: 'AI应用开发工程师',
    category: '大模型与AIGC',
    cluster: '应用工程',
    firstSeen: '2023-03',
    salary: [25, 50],
    definition: '基于大模型 API 与开源框架构建面向业务的智能应用，打通模型能力与业务系统。',
    coreDuties: ['设计并实现大模型应用链路', '完成提示词与检索策略调优', '保障应用稳定性与成本可控'],
    mustSkills: ['AI框架能力', '编程能力', '自然语言处理技术'],
    plusSkills: ['系统架构设计', '产品与业务理解'],
    scenarios: ['企业知识库', '智能助手', '文档处理自动化'],
    tasks: ['检索增强生成系统构建', 'Agent编排与工具调用', '提示词设计与评测', '模型服务化部署'],
    directSkills: ['AI框架能力', '编程能力', '系统架构设计'],
  },
  {
    name: 'Agent编排工程师',
    category: '大模型与AIGC',
    cluster: '应用工程',
    emerging: true,
    firstSeen: '2024-06',
    salary: [35, 65],
    definition:
      '面向多智能体协作场景，负责智能体的任务分解、工具调用、状态管理与协作协议设计，是大模型应用从“单轮问答”走向“自主执行”后新出现的岗位。',
    coreDuties: [
      '设计多智能体的角色划分、协作拓扑与消息协议',
      '实现工具注册、调用编排与失败回退机制',
      '构建 Agent 轨迹追踪与效果评测体系',
      '优化长任务下的上下文与记忆管理',
    ],
    mustSkills: ['AI框架能力', '系统架构设计', '编程能力'],
    plusSkills: ['自然语言处理技术', '云原生与运维', '安全与合规'],
    scenarios: ['自动化运维', '智能投研', '流程机器人', '科研助手'],
    tasks: ['Agent编排与工具调用', '长上下文与记忆优化', '模型评测体系建设', '检索增强生成系统构建'],
    directSkills: ['AI框架能力', '系统架构设计', '编程能力'],
  },
  {
    name: '上下文工程师',
    category: '大模型与AIGC',
    cluster: '应用工程',
    emerging: true,
    firstSeen: '2025-02',
    salary: [32, 58],
    definition:
      '负责大模型上下文的组织、压缩与调度，通过检索、记忆、缓存与结构化拼装，在有限上下文窗口内最大化任务表现。',
    coreDuties: ['设计上下文分层与压缩策略', '构建长期记忆与会话状态管理', '优化上下文命中率与推理成本'],
    mustSkills: ['自然语言处理技术', '系统架构设计', 'AI框架能力'],
    plusSkills: ['数据库能力', '算法优化能力'],
    scenarios: ['长文档分析', '客服会话', '代码智能体'],
    tasks: ['长上下文与记忆优化', '检索增强生成系统构建', '向量检索优化'],
    directSkills: ['自然语言处理技术', '系统架构设计'],
  },
  {
    name: 'RAG系统工程师',
    category: '大模型与AIGC',
    cluster: '应用工程',
    emerging: true,
    firstSeen: '2024-02',
    salary: [30, 55],
    definition: '专注检索增强生成链路的端到端构建与调优，负责知识切分、向量检索、重排与生成质量把控。',
    coreDuties: ['设计文档解析与切分策略', '构建向量检索与重排链路', '建立 RAG 效果评测与归因体系'],
    mustSkills: ['自然语言处理技术', '数据库能力', 'AI框架能力'],
    plusSkills: ['数据处理能力', '系统架构设计'],
    scenarios: ['企业知识库', '法律检索', '医疗文献问答'],
    tasks: ['检索增强生成系统构建', '向量检索优化', '模型评测体系建设', '知识图谱构建'],
    directSkills: ['自然语言处理技术', '数据库能力'],
  },
  {
    name: '模型评测工程师',
    category: '大模型与AIGC',
    cluster: '质量与评测',
    emerging: true,
    firstSeen: '2024-09',
    salary: [28, 50],
    definition: '负责大模型与智能应用的效果评测体系设计，构建评测集、自动化评测流水线与效果归因分析。',
    coreDuties: ['构建分场景评测集与标注规范', '实现自动化评测与回归流水线', '输出效果归因与迭代建议'],
    mustSkills: ['数学与统计', '自然语言处理技术', '编程能力'],
    plusSkills: ['产品与业务理解', '安全与合规'],
    scenarios: ['模型选型', '版本回归', '安全评估'],
    tasks: ['模型评测体系建设', '提示词设计与评测', '安全对齐与红队测试'],
    directSkills: ['数学与统计', '编程能力', '产品与业务理解'],
  },
  {
    name: '大数据开发工程师',
    category: '数据与智能分析',
    cluster: '数据工程',
    firstSeen: '2019-01',
    salary: [22, 45],
    definition: '负责海量数据的采集、存储、计算与服务化，支撑上层分析与算法应用。',
    coreDuties: ['开发离线与实时数据管道', '优化计算任务性能与成本', '保障数据链路稳定性'],
    mustSkills: ['数据处理能力', '编程能力', '数据库能力'],
    plusSkills: ['分布式系统能力', '云原生与运维'],
    scenarios: ['用户画像', '经营分析', '风控数据集市'],
    tasks: ['数据管道构建', '实时流处理', '数据资产建模', '数据质量治理'],
    directSkills: ['数据处理能力', '编程能力', '数据库能力'],
  },
  {
    name: '数据平台工程师',
    category: '数据与智能分析',
    cluster: '数据工程',
    firstSeen: '2020-03',
    salary: [28, 52],
    definition: '负责数据基础平台的建设与演进，包括调度、元数据、计算引擎与自助分析能力。',
    coreDuties: ['建设统一调度与元数据体系', '优化平台稳定性与资源利用率', '推动数据服务标准化'],
    mustSkills: ['分布式系统能力', '数据处理能力', '系统架构设计'],
    plusSkills: ['云原生与运维'],
    scenarios: ['集团级数据中台', '湖仓一体平台'],
    tasks: ['数据管道构建', '数据资产建模', '算力资源调度', '数据质量治理'],
    directSkills: ['分布式系统能力', '系统架构设计', '数据处理能力'],
  },
  {
    name: '数据分析师',
    category: '数据与智能分析',
    cluster: '数据分析',
    firstSeen: '2019-01',
    salary: [15, 35],
    definition: '基于数据开展业务洞察与决策支持，构建指标体系并推动实验驱动的业务优化。',
    coreDuties: ['搭建业务指标体系与看板', '设计并分析 AB 实验', '产出可执行的业务洞察'],
    mustSkills: ['数学与统计', '产品与业务理解', '数据处理能力'],
    plusSkills: ['数据库能力'],
    scenarios: ['增长分析', '经营决策', '产品迭代'],
    tasks: ['指标体系与AB实验设计', '数据质量治理', '特征工程'],
    directSkills: ['数学与统计', '产品与业务理解', '数据库能力'],
  },
  {
    name: '数据治理工程师',
    category: '数据与智能分析',
    cluster: '数据工程',
    firstSeen: '2020-06',
    salary: [22, 42],
    definition: '负责数据标准、质量与安全合规体系建设，保障数据资产可信可用。',
    coreDuties: ['制定数据标准与质量规则', '建设数据血缘与资产目录', '推动数据安全分级与合规落地'],
    mustSkills: ['数据处理能力', '数据库能力', '产品与业务理解'],
    plusSkills: ['安全与合规'],
    scenarios: ['金融数据合规', '集团数据资产盘点'],
    tasks: ['数据质量治理', '数据资产建模', '隐私计算方案设计'],
    directSkills: ['数据处理能力', '产品与业务理解', '安全与合规'],
  },
  {
    name: '计算机视觉算法工程师',
    category: '智能系统与感知',
    cluster: '算法研发',
    firstSeen: '2019-01',
    salary: [28, 58],
    definition: '负责图像与视频的检测、分割、识别与重建算法研发及产业化落地。',
    coreDuties: ['研发检测分割与识别模型', '完成模型在端侧与云侧的部署优化', '构建数据闭环持续迭代'],
    mustSkills: ['计算机视觉技术', '深度学习建模', '编程能力'],
    plusSkills: ['算法优化能力', '嵌入式与硬件'],
    scenarios: ['工业质检', '智慧安防', '医学影像', '无人零售'],
    tasks: ['目标检测与跟踪', '三维重建与SLAM', '多模态对齐', '模型量化与蒸馏'],
    directSkills: ['计算机视觉技术', '深度学习建模', '编程能力'],
  },
  {
    name: '推荐算法工程师',
    category: '智能系统与感知',
    cluster: '算法研发',
    firstSeen: '2019-01',
    salary: [30, 60],
    definition: '负责召回、排序与重排全链路推荐算法的研发与优化。',
    coreDuties: ['优化召回与排序模型', '设计特征体系与样本方案', '通过实验驱动指标提升'],
    mustSkills: ['机器学习理论', '数据处理能力', '数学与统计'],
    plusSkills: ['分布式系统能力'],
    scenarios: ['内容分发', '电商推荐', '广告投放'],
    tasks: ['推荐召回与排序', '特征工程', '指标体系与AB实验设计', '实时流处理'],
    directSkills: ['机器学习理论', '数据处理能力', '编程能力'],
  },
  {
    name: '智能驾驶感知工程师',
    category: '智能系统与感知',
    cluster: '智能系统',
    firstSeen: '2020-04',
    salary: [35, 70],
    definition: '负责自动驾驶感知算法研发，包括多传感器融合、目标检测与场景理解。',
    coreDuties: ['研发多传感器融合感知算法', '优化车载端侧推理性能', '构建长尾场景数据闭环'],
    mustSkills: ['计算机视觉技术', '嵌入式与硬件', '深度学习建模'],
    plusSkills: ['算法优化能力', '数学与统计'],
    scenarios: ['乘用车智驾', '港口无人集卡', '矿区无人驾驶'],
    tasks: ['目标检测与跟踪', '传感器融合', '三维重建与SLAM', '端侧模型部署'],
    directSkills: ['计算机视觉技术', '嵌入式与硬件', '编程能力'],
  },
  {
    name: '具身智能算法工程师',
    category: '智能系统与感知',
    cluster: '算法研发',
    emerging: true,
    firstSeen: '2024-08',
    salary: [40, 75],
    definition:
      '面向机器人与具身智能体，负责视觉-语言-动作联合建模与操作策略学习，将大模型能力延伸到物理世界交互。',
    coreDuties: [
      '研发 VLA 等视觉-语言-动作联合模型',
      '设计仿真到真机的策略迁移方案',
      '构建遥操作与自动化数据采集流程',
      '优化控制策略的实时性与安全性',
    ],
    mustSkills: ['机器学习理论', '计算机视觉技术', '深度学习建模'],
    plusSkills: ['嵌入式与硬件', '算法优化能力'],
    scenarios: ['人形机器人', '柔性制造装配', '仓储分拣', '服务机器人'],
    tasks: ['具身操作策略学习', '强化学习训练', '多模态对齐', '传感器融合'],
    directSkills: ['机器学习理论', '计算机视觉技术', '嵌入式与硬件'],
  },
  {
    name: '物联网嵌入式工程师',
    category: '物联网与边缘计算',
    cluster: '嵌入式',
    firstSeen: '2019-01',
    salary: [18, 38],
    definition: '负责物联网终端设备的固件开发、协议适配与设备管理。',
    coreDuties: ['开发终端固件与驱动', '完成通信协议适配与联调', '保障设备稳定性与功耗'],
    mustSkills: ['嵌入式与硬件', '编程能力'],
    plusSkills: ['系统架构设计'],
    scenarios: ['智慧园区', '智能表计', '工业网关'],
    tasks: ['设备接入与协议适配', '传感器融合', '端侧模型部署'],
    directSkills: ['嵌入式与硬件', '编程能力'],
  },
  {
    name: '边缘智能工程师',
    category: '物联网与边缘计算',
    cluster: '嵌入式',
    emerging: true,
    firstSeen: '2024-04',
    salary: [26, 50],
    definition:
      '负责在边缘侧部署与调度 AI 模型，兼顾算力受限条件下的推理性能、功耗与云边协同。',
    coreDuties: ['完成模型的端侧量化与编译部署', '设计云边协同的推理与更新机制', '优化边缘算力调度策略'],
    mustSkills: ['嵌入式与硬件', '算法优化能力', '分布式系统能力'],
    plusSkills: ['云原生与运维'],
    scenarios: ['智能制造产线', '智慧交通路侧', '能源巡检'],
    tasks: ['端侧模型部署', '边缘算力调度', '模型量化与蒸馏', '设备接入与协议适配'],
    directSkills: ['嵌入式与硬件', '算法优化能力'],
  },
  {
    name: 'AI安全与对齐工程师',
    category: '安全与合规',
    cluster: '安全',
    emerging: true,
    firstSeen: '2024-03',
    salary: [32, 62],
    definition:
      '负责大模型系统的安全对齐、越狱防护与风险评估，建立面向生成式 AI 的攻防与治理体系。',
    coreDuties: [
      '设计并执行红队测试与越狱攻击评估',
      '建设内容安全与提示注入防护机制',
      '构建模型安全评测集与风险分级标准',
      '推动安全事件的溯源与应急响应',
    ],
    mustSkills: ['安全与合规', '自然语言处理技术', '机器学习理论'],
    plusSkills: ['系统架构设计', '编程能力'],
    scenarios: ['生成式 AI 上线评估', '内容平台风控', '金融合规审查'],
    tasks: ['安全对齐与红队测试', '内容风控', '模型评测体系建设', '算法合规备案'],
    directSkills: ['安全与合规', '自然语言处理技术'],
  },
  {
    name: '算法合规审计师',
    category: '安全与合规',
    cluster: '安全',
    emerging: true,
    firstSeen: '2024-10',
    salary: [25, 48],
    definition: '负责算法与生成式 AI 服务的合规审查、备案材料准备与持续审计。',
    coreDuties: ['开展算法影响评估与合规审查', '组织算法备案与材料归档', '建立持续审计与整改机制'],
    mustSkills: ['安全与合规', '产品与业务理解'],
    plusSkills: ['数学与统计'],
    scenarios: ['生成式 AI 备案', '数据出境评估', '金融算法审计'],
    tasks: ['算法合规备案', '内容风控', '隐私计算方案设计'],
    directSkills: ['安全与合规', '产品与业务理解'],
  },
  {
    name: 'MLOps工程师',
    category: 'AI基础设施',
    cluster: '基础设施',
    firstSeen: '2021-02',
    salary: [28, 55],
    definition: '负责机器学习全生命周期的工程化，涵盖训练、部署、监控与持续交付。',
    coreDuties: ['建设模型训练与发布流水线', '实现模型监控与回滚机制', '优化资源利用与交付效率'],
    mustSkills: ['云原生与运维', '分布式系统能力', '编程能力'],
    plusSkills: ['AI框架能力', '系统架构设计'],
    scenarios: ['企业 AI 中台', '模型工厂'],
    tasks: ['模型服务化部署', '分布式训练调度', '算力资源调度', '推理性能优化'],
    directSkills: ['云原生与运维', '编程能力', '分布式系统能力'],
  },
  {
    name: '推理优化工程师',
    category: 'AI基础设施',
    cluster: '基础设施',
    emerging: true,
    firstSeen: '2023-11',
    salary: [38, 72],
    definition:
      '专注大模型推理链路的吞吐与时延优化，覆盖算子实现、并行策略、批调度与显存管理。',
    coreDuties: [
      '优化推理引擎的批调度与显存复用',
      '实现或改造高性能算子与量化方案',
      '完成多卡多机推理并行策略设计',
      '建立推理性能基准与成本模型',
    ],
    mustSkills: ['算法优化能力', '编程能力', '分布式系统能力'],
    plusSkills: ['深度学习建模', '云原生与运维'],
    scenarios: ['大模型云服务', '私有化部署', '端云协同推理'],
    tasks: ['推理性能优化', '模型量化与蒸馏', '分布式训练调度', '长上下文与记忆优化'],
    directSkills: ['算法优化能力', '编程能力'],
  },
  {
    name: '算力调度工程师',
    category: 'AI基础设施',
    cluster: '基础设施',
    firstSeen: '2022-08',
    salary: [30, 58],
    definition: '负责智算集群的资源编排与调度优化，提升 GPU 集群利用率与作业稳定性。',
    coreDuties: ['设计集群调度与配额策略', '优化作业排队与抢占机制', '保障大规模训练作业稳定运行'],
    mustSkills: ['分布式系统能力', '云原生与运维', '系统架构设计'],
    plusSkills: ['编程能力'],
    scenarios: ['智算中心', '国产算力集群', '混合云调度'],
    tasks: ['算力资源调度', '分布式训练调度', '边缘算力调度'],
    directSkills: ['分布式系统能力', '云原生与运维'],
  },
];

/* ---------------- 辅助常量 ---------------- */

export const CITIES = ['北京', '上海', '深圳', '杭州', '广州', '成都', '南京', '西安', '合肥', '武汉'];
export const DEGREES = ['大专', '本科', '硕士', '博士'];
export const EXPERIENCE = ['应届', '1-3年', '3-5年', '5-10年', '10年以上'];
export const COMPANY_CATEGORIES = ['互联网大厂', '国有企业', '民营科技', '外资企业', '创业公司', '科研院所'];
export const SALARY_BANDS = ['10k以下', '10-20k', '20-30k', '30-50k', '50-70k', '70k以上'];

export const PAPER_VENUES = ['arXiv cs.CL', 'arXiv cs.LG', 'arXiv cs.CV', 'arXiv cs.AI', 'arXiv cs.RO', 'arXiv cs.DC'];
export const NEWS_OUTLETS = ['机器之心', '量子位', '36氪', 'InfoQ', '雷峰网', 'CSDN资讯', '虎嗅'];
export const COMPANIES = [
  '科大讯飞', '华为技术', '阿里云', '字节跳动', '腾讯', '百度', '商汤科技', '旷视科技',
  '智谱AI', '月之暗面', '美团', '京东', '小米', '蔚来', '大疆创新', '中兴通讯',
  '海康威视', '宁德时代', '国家电网', '中国移动', '第四范式', '云从科技',
];

/** 噪声样板话术（算法 §4.4.3） */
export const NOISE_PHRASES = [
  '沟通能力强', '抗压能力好', '工作认真负责', '有团队合作精神', '学习能力强',
  '具有良好的职业素养', '积极主动', '责任心强', '逻辑思维清晰', '能适应快节奏工作环境',
];
