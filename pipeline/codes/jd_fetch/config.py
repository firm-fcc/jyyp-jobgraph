# -*- coding: utf-8 -*-
"""读取项目 config.yaml 中的 MySQL 配置，提供连接辅助。"""
import os
import sys

import yaml
import pymysql

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
CONFIG_KEY = "jobDescription_TC"


def load_config(path=None):
    """path 缺省读本目录 config.yaml（远端 job51）；可传入其他 yaml（如 config_origin.yaml 指向本地库）。"""
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    section = cfg.get(CONFIG_KEY, cfg if isinstance(cfg, dict) and "ip" in cfg else {})
    # 兼容顶层直接是 mysql 配置的情况
    if "type" not in section and isinstance(cfg, dict) and cfg.get("type") == "mysql":
        section = cfg
    return section


def mysql_params(section=None):
    s = section or load_config()
    return {
        "host": s["ip"],
        "port": int(s.get("port", 3306)),
        "user": s["username"],
        "password": s["password"],
        "database": s["db_name"],
        "charset": "utf8mb4",
        "connect_timeout": 15,
    }


def get_tables(section=None):
    s = section or load_config()
    return list(s.get("tables", []))


def connect(section=None):
    return pymysql.connect(**mysql_params(section))
