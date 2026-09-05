# -*- coding: utf-8 -*-
"""pytest 根配置。

- 把 unit-tests 自身加入 sys.path，使测试文件可 ``import ut``；
- ``tmp``：模块级共享临时目录（str）——test_base_builder 的用例间需复用同一
  out_root（force 守卫故意复用已建边文件验证拒绝覆盖），故取模块级目录而非
  每用例独立的 tmp_path。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest


@pytest.fixture(scope="module")
def tmp(tmp_path_factory):
    return str(tmp_path_factory.mktemp("base_builder"))
