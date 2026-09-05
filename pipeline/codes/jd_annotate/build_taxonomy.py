# -*- coding: utf-8 -*-
"""技术栈体系构建：classify/TechStacks/techstacks.json。

v2.0（2026-08-20）：体系改为**人工确定的八类技术栈分类**（来源：temp/技术栈分类.docx
「三、八类技术栈分类」——基于 DevOps 周期表 / SFIA / ThoughtWorks 雷达 / 三层架构 /
UbiStack 等文献融合提炼，兼顾架构分层、功能角色、工程实践三维度）。
原 v1.0 的「LLM 500 样本归纳草稿 + 人工审定」流程（--induct）退役，本脚本只负责把
下方人工审定稿写成正式体系文件。

keywords 构成（同时是 annotate_jd.py 第 2/3 层标题/正文关键词规则的唯一词表）：
  1. docx 各类别「具体清单」中的技术条目（权威来源，原样收录）；
  2. 该类别「说明」范围之内的中文职能/技术同义词扩展（供 funtype part 规则种子与
     中文 JD 标题/正文匹配），如 类别1 说明含"移动端"→ android/ios/移动开发 等归 TS-01。
重叠口径（docx 原文即如此，多标签机制天然支持）：
  - Docker/Kubernetes 同时列于 类别5 与 类别7（运行基础设施 vs 部署编排工具链）；
  - Python 语言开发归 TS-02（docx 类别2 "Python（Django/Flask）"），数据科学生态
    （NumPy/Pandas）归 TS-08；
  - 可观测性拆分：Prometheus/Grafana/ELK 归 TS-05（类别5 基础设施），Datadog/Splunk
    等 AIOps/分析平台归 TS-08（类别8）；
  - Redis/Memcached 归 TS-04 中间件（docx 类别4 具体清单），Kafka/RocketMQ 等消息
    中间件同属 TS-04；Hadoop/Spark/Flink 等数据管道/数仓引擎归 TS-03（类别3 说明含
    "数据仓库"）。

用法：
  python build_taxonomy.py --finalize          # 写出正式体系文件
  python build_taxonomy.py --finalize --out X  # 写到指定路径（测试）
"""
import argparse
import json
import os

import common

# ---------------------------------------------------------------- 八类体系（人工审定，docx 为准）
# 每项：(code, name_zh, name_en, 说明, keywords)
# 说明列 = docx 「三、八类技术栈分类」表格的"说明"原文。
BASE_STACKS = [
    ("TS-01", "前端与用户体验", "Frontend & User Experience",
     "用户交互界面、呈现逻辑、移动端、Web UI、设计系统",
     ["html", "css", "javascript", "typescript", "es6", "react", "vue", "angular",
      "react native", "flutter", "uniapp", "webgl", "axe-core",
      "前端", "web前端", "小程序", "h5", "移动端", "移动开发", "移动互联", "app开发",
      "android", "ios", "安卓", "ui设计", "ux设计", "交互设计", "图形设计", "动画",
      "内容设计"]),
    ("TS-02", "后端开发与业务逻辑", "Backend & Business Logic",
     "服务端业务逻辑、API 实现、微服务、编程语言及框架",
     ["java", "j2ee", "spring", "springboot", "jvm", "python", "django", "flask",
      "fastapi", "c#", "csharp", ".net", "asp.net", "winform", "php", "laravel",
      "node", "node.js", "express", "ruby", "rails", "go语言", "go开发", "go工程师",
      "golang", "rust", "c++", "c语言", "vc", "mfc", "qt",
      "后端", "服务端", "微服务", "软件工程师", "软件研发", "软件开发"]),
    ("TS-03", "数据存储与管理", "Data Storage & Management",
     "关系型/非关系型数据库、数据仓库、对象存储、缓存、数据治理",
     ["mysql", "postgresql", "mongodb", "sql server", "sqlserver", "mariadb",
      "oracle", "elasticsearch", "iceberg", "hadoop", "spark", "flink", "hive",
      "hbase", "etl", "dba", "数据库", "数据仓库", "数仓", "数据中台", "数据开发",
      "数据治理", "数据建模", "数据管理"]),
    ("TS-04", "中间件与消息通信", "Middleware & Messaging",
     "消息队列、事件流、API 网关、RPC 框架、服务间通信",
     ["rabbitmq", "kafka", "rocketmq", "activemq", "pulsar", "mq", "消息队列",
      "redis", "memcached", "中间件", "api网关", "apisix", "webmethods",
      "api connect", "app connect", "ag-ui", "rpc", "dubbo", "服务注册", "服务发现",
      "zookeeper", "ldap"]),
    ("TS-05", "基础设施与云原生", "Infrastructure & Cloud Native",
     "操作系统、网络、服务器、容器、编排、IaaS/PaaS、可观测性基础设施",
     ["docker", "kubernetes", "k8s", "容器", "容器编排", "服务网格", "istio",
      "openshift", "rancher", "eks", "ecs", "aks", "gke", "aws", "azure", "阿里云",
      "云计算", "云原生", "云平台", "iaas", "paas", "虚拟化", "nginx", "apache",
      "linux", "windows server", "globus", "网格计算", "prometheus", "grafana",
      "elk", "可观测性", "运维", "sre", "系统运维", "自动化运维",
      "网络工程师", "数通", "交换机", "路由", "网优", "网规", "光通信", "传输网",
      "通信工程", "5g"]),
    ("TS-06", "安全与合规", "Security & Compliance",
     "身份认证、访问控制、加密、渗透测试、漏洞扫描、安全策略",
     ["零信任", "passkeys", "sonarqube", "snort", "vault", "guardium", "qradar",
      "maas360", "信息安全", "网络安全", "安全工程师", "安全服务", "渗透测试", "渗透",
      "漏洞", "等保", "攻防", "逆向", "身份认证", "访问控制", "加密", "密码学",
      "数据安全", "安全合规", "安全策略"]),
    ("TS-07", "DevOps 与自动化", "DevOps & Automation",
     "CI/CD、配置管理、基础设施即代码、发布管理、自动化测试工具",
     ["devops", "ci/cd", "cicd", "持续集成", "持续交付", "发布管理", "配置管理",
      "基础设施即代码", "git", "gitlab ci", "circleci", "travis", "jenkins",
      "maven", "gradle", "ansible", "terraform", "chef", "puppet", "spinnaker",
      "octopus", "harness", "codepipeline", "digital.ai", "dora", "版本控制",
      "docker", "kubernetes", "k8s", "自动化测试", "测试开发", "selenium", "appium",
      "jmeter", "自动化运维"]),
    ("TS-08", "AI/ML 与数据智能", "AI/ML & Data Intelligence",
     "机器学习框架、数据科学平台、AI 模型服务、上下文感知、智能分析",
     ["tensorflow", "keras", "pytorch", "numpy", "pandas", "机器学习", "深度学习",
      "神经网络", "算法", "nlp", "计算机视觉", "推荐系统", "人工智能", "ai", "大模型",
      "llm", "aigc", "rag", "提示词", "智能体", "slm", "adk", "数据挖掘", "数据科学",
      "数据分析", "数据分析师", "bi", "tableau", "powerbi", "报表", "智能分析",
      "aiops", "instana", "datadog", "splunk", "dynatrace", "new relic"]),
]


def finalize(args):
    detail = {}
    for code, name_zh, name_en, desc, kws in BASE_STACKS:
        detail[code] = {"code": code, "name_zh": name_zh, "name_en": name_en,
                        "description": desc, "keywords": kws, "aliases": []}
    result = {
        "system_name": "IT岗位技术栈体系",
        "version": "2.0",
        "date": "2026-08-20",
        "source": "人工确定的八类技术栈分类（temp/技术栈分类.docx「三、八类技术栈分类」，"
                  "DevOps周期表/SFIA/ThoughtWorks雷达/三层架构/UbiStack 文献融合提炼），"
                  "构建脚本 codes/jd_annotate/build_taxonomy.py",
        "total": len(detail),
        "meta": {
            "note": "多标签横向分组维度（赛题'按技术栈切换视图'）。v2.0 由原 25 栈（LLM 样本"
                    "归纳 + 人工审定）改为人工确定的八类体系。keywords 同时是标题/正文关键词"
                    "规则的唯一词表（中文按子串、ascii 按字母数字边界、大小写不敏感）；构成 ="
                    " docx 各类别具体清单（权威）+ 类别说明范围内的中文职能/技术同义扩展。"
                    "允许无栈职能（项目管理、产品经理、技术文员等）映射为空。",
            "overlap_note": "docx 原文即存在跨类条目，多标签机制天然支持：Docker/Kubernetes "
                            "同列类别5与类别7；Python 语言开发归 TS-02、NumPy/Pandas 数据科学"
                            "生态归 TS-08；Prometheus/Grafana/ELK（可观测性基础设施）归 TS-05、"
                            "Datadog/Splunk 等 AIOps/分析平台归 TS-08；Redis/Memcached/Kafka 等"
                            "消息与缓存中间件归 TS-04、Hadoop/Spark/Flink 数仓引擎归 TS-03。",
            "usage": "JD 归类 = 词库快路（标题/正文关键词命中即划分）+ LLM 兜底"
                     "（codes/jd_annotate/classify_stacks.py → output/jd_stack_cache.jsonl），"
                     "行级引擎见 annotate_jd.py 三层解析；泛化软件研发类（软件工程师等）归 TS-02。",
        },
        "detail": detail,
        "简明体系": [{"code": c, "name_zh": n, "name_en": e} for c, n, e, _, _ in BASE_STACKS],
    }
    out_path = args.out or common.TAXONOMY_PATH
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"体系已写出：{out_path}（{len(detail)} 类）")


def main():
    ap = argparse.ArgumentParser(description="技术栈体系构建（v2.0 八类，人工确定）")
    ap.add_argument("--finalize", action="store_true", help="写出正式体系文件（classify/TechStacks/）")
    ap.add_argument("--out", default="", help="输出路径（默认 classify/TechStacks/techstacks.json）")
    args = ap.parse_args()
    if args.finalize:
        finalize(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
