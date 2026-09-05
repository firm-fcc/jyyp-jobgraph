# -*- coding: utf-8 -*-
"""extractor 的 LLM 调用封装（论文信号分类用；自持拷贝，与 builder/llm.py 同源）。

API key 读取、批量 JSON 输出、重试退避、输出截断自动升级 max_tokens。
use_thinking 由 config.USE_THINKING 控制（True=禁用推理，提速降本）。
多 key（settings llm.api_keys_parallel > 1）：请求级线程安全轮转，重试换下一个
key（429/限速分散到各账号）；显式传 api_key 参数则固定使用该 key。
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request

import config


class ResourceExhaustedError(RuntimeError):
    """全部启用 key 余额耗尽（HTTP 402）——资源性故障，熔断信号。

    2026-09-02 用户裁定：402 类故障不得走"降级保信号"路径（会绕过全部守门造成未审实体
    出生，2025-06 窗实证 34 条）。调用方的保守降级 except 必须单独放行本类型并中止运行。
    """


class KeyRing:
    """线程安全 key 轮转（round-robin）。单 key 时恒返回该 key。"""

    def __init__(self, keys):
        self._keys = list(keys)
        self._i = 0
        self._lock = threading.Lock()

    def __len__(self):
        return len(self._keys)

    def next(self):
        with self._lock:
            k = self._keys[self._i % len(self._keys)]
            self._i += 1
            return k


_RING = None


def _probe_key(key):
    """单 key 可用性探测（极小请求、快速失败）→ (ok, err)。

    401/403 立即判死；429/5xx/网络错重试一次（瞬时抖动不误杀）；返回choices即视为有效。
    """
    body_obj = {
        "model": config.DEFAULT_MODEL,
        "messages": [{"role": "user", "content": '只输出 JSON：{"ok": true}'}],
        "max_tokens": 200,
        "temperature": 0,
    }
    if config.USE_THINKING:
        body_obj["thinking"] = {"type": "disabled"}
    data = json.dumps(body_obj).encode("utf-8")
    err = ""
    for attempt in range(2):
        req = urllib.request.Request(
            config.API_URL, data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=min(config.TIMEOUT, 30)) as r:
                resp = json.loads(r.read().decode("utf-8"))
            return bool(resp.get("choices")), ""
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            if e.code in (429, 500, 502, 503) and attempt == 0:
                time.sleep(2)
                continue
            return False, err
        except Exception as e:
            err = str(e)[:60]
            if attempt == 0:
                time.sleep(1)
                continue
    return False, err


def key_ring():
    """进程级 KeyRing（惰性；key 文件/开关变更后需重启进程生效）。

    首次构建时按 llm.key_probe 预检：并行探测启用 key，不可用者剔除并打警告
    （尾 4 位标识），本进程内不再轮转到——把"运行中途撞坏 key"提前到启动时排除。
    全部不可用则中止并列出各 key 失败原因。"""
    global _RING
    if _RING is None:
        keys = config.active_api_keys()
        if not keys:
            raise RuntimeError("未找到 API key（codes/api-key.txt 或环境变量）")
        if config.KEY_PROBE and len(keys) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=len(keys)) as ex:
                results = list(ex.map(_probe_key, keys))
            dead = [(k, e) for k, (ok, e) in zip(keys, results) if not ok]
            keys = [k for k, (ok, _) in zip(keys, results) if ok]
            for k, e in dead:
                print(f"[llm] 预检剔除 key ...{k[-4:]}（{e}；本进程不使用，重启后重测）",
                      flush=True)
            if not keys:
                detail = "；".join(f"...{k[-4:]}: {e}" for k, e in dead)
                raise RuntimeError(f"预检失败：全部启用 key 不可用（{detail}）")
        _RING = KeyRing(keys)
    return _RING


def call_llm(prompt, parse_json=True, max_tokens=None, api_key=None):
    """调用 LLM，返回解析后的 JSON（若 parse_json）或原始文本。

    截断处理：deepseek-v4-flash 为推理模型，推理 token 计入 max_tokens；
    若输出因 finish_reason=length 被截断，自动升级 max_tokens 重试（至多 MAX_TOKENS_CAP）。
    重试/截断升级时换下一个轮转 key（多 key 下限速错开账号）。
    """
    max_tokens = max_tokens or config.BATCH_MAX_TOKENS
    key = api_key  # 显式传入：整次调用（含重试）固定用该 key

    def _request(mt, k):
        body_obj = {
            "model": config.DEFAULT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": mt,
            "temperature": 0,
        }
        if config.USE_THINKING:
            body_obj["thinking"] = {"type": "disabled"}
        req = urllib.request.Request(
            config.API_URL, data=json.dumps(body_obj).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {k}"},
        )
        return req

    last_err = None
    keys_402 = set()          # 余额耗尽的 key（熔断判定：启用 key 全部 402）
    attempt = 0
    # 402 轮转需能触及环上全部 key：次数上限取 RETRIES 与 2×环大小 的较大者
    max_attempts = max(config.RETRIES, 2 * len(key_ring() if api_key is None else [api_key]))
    while attempt < max_attempts:
        if key is None:
            key = key_ring().next()
        try:
            with urllib.request.urlopen(_request(max_tokens, key), timeout=config.TIMEOUT) as r:
                resp = json.loads(r.read().decode("utf-8"))
            choice = resp["choices"][0]
            content = choice["message"].get("content", "") or ""
            finish = choice.get("finish_reason")

            # 输出截断：升级 max_tokens 重试，避免推理/长输出把预算耗尽
            if finish == "length" and content:
                if max_tokens >= config.MAX_TOKENS_CAP:
                    last_err = f"finish_reason=length（已达 max_tokens 上限 {config.MAX_TOKENS_CAP}）"
                    break
                max_tokens = min(max_tokens * 2, config.MAX_TOKENS_CAP)
                last_err = f"finish_reason=length，升级 max_tokens={max_tokens} 重试"
                if api_key is None:
                    key = None          # 轮转模式：重试换下一个 key
                continue

            if not parse_json:
                return content
            return _extract_json(content)
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code == 402:
                # 余额耗尽：先轮转其他 key（可能仍有余额）；全部耗尽则熔断
                keys_402.add(key)
                ring_n = len(key_ring()) if api_key is None else 1
                if len(keys_402) >= ring_n:
                    raise ResourceExhaustedError(
                        "全部启用 key 不可用（402 余额耗尽）——熔断：资源性故障不降级保信号，"
                        "请充值后重跑")
                key = None              # 换下一个 key 再试（不占瞬时重试次数）
                continue
            if e.code in (429, 500, 502, 503):
                time.sleep(8 * (attempt + 1) + 5)
                if api_key is None:
                    key = None          # 限速/服务错：换账号重试
                attempt += 1
                continue
            raise
        except Exception as e:
            last_err = str(e)
            time.sleep(6 * (attempt + 1))
            if api_key is None:
                key = None
            attempt += 1
    raise RuntimeError(f"LLM 调用失败（{max_attempts} 次重试后）：{last_err}")


def _extract_json(text):
    """从模型输出中提取 JSON（容忍 markdown 围栏、首尾杂讯、多对象）。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()
    # 尝试直接解析
    try:
        return json.loads(text)
    except Exception:
        pass
    # 用括号平衡找第一个完整 JSON 结构（数组优先，其次对象）
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
    raise ValueError(f"响应中未找到合法 JSON: {text[:150]!r}")
