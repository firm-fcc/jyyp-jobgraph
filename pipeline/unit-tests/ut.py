# -*- coding: utf-8 -*-
"""unit-tests 共享工具：包根路径、sys.path 确定性装配、跨包冲突模块名隔离。

仓库各 code 包采用平铺导入（包内模块直接 ``import config``），同名模块存在于
多个包（builder / extractor 各有 config.py、llm.py、prompts.py、paper_prompts.py）。
pytest 在同一进程内收集全部测试文件时，必须保证每个文件的被测依赖按其声明的
包目录解析，否则先收集的文件会把同名模块"锁"成错误的实现：

- ``setup(*pkgs)``：把 ``codes/<pkg>`` 逐个插入 sys.path 最前——**后声明的参数
  位于 sys.path 最前、优先解析**（与原先各文件多次 ``sys.path.insert(0, ...)``
  的最终顺序一致）；
- ``isolate()``：从 sys.modules 弹出跨包冲突名（config / llm / prompts /
  paper_prompts），使本文件随后的 import 按当前 sys.path 重新解析，不受先前
  测试文件遗留绑定的影响（已加载模块内部持有的引用不受影响）；
- ``fixture(name)``：unit-tests 内置夹具数据（news_delta.json 等）的绝对路径；
- ``path(pkg)``：``codes/<pkg>`` 绝对路径。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 跨包同名平铺模块（冲突名清单）
COLLIDING = ("config", "llm", "prompts", "paper_prompts")


def path(pkg):
    return os.path.join(ROOT, "codes", pkg)


def setup(*pkgs):
    """按参数顺序插入各包根到 sys.path 最前（后声明者优先解析）。幂等。"""
    for p in pkgs:
        d = path(p)
        if d in sys.path:
            sys.path.remove(d)
        sys.path.insert(0, d)


def isolate():
    """弹出跨包冲突模块名，令后续 import 按当前 sys.path 重新解析。"""
    for m in COLLIDING:
        sys.modules.pop(m, None)


def fixture(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
