#!/usr/bin/env python3
"""LLM 标题价值判断模块 - 在下载 PDF 前筛选低价值公告

通过 OpenAI 兼容 API 判断公告标题是否包含实质性内容。
作为正则模式（SKIP_CONTENT_PATTERNS）的补充，捕获遗漏的低价值公告。
"""

import json
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 15

_ENV_PATH = None


def _get_env_path() -> str:
    global _ENV_PATH
    if _ENV_PATH is None:
        _ENV_PATH = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        )
    return _ENV_PATH


def _load_env_key(key: str) -> Optional[str]:
    path = _get_env_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    val = v.strip().strip("\"'")
                    return val if val else None
    except Exception as e:
        logger.debug("读取 .env 失败: %s", e)
    return None

SYSTEM_PROMPT = """请判断以下上市公司公告标题是否需要下载PDF全文。

需要下载的类型（有价值）：季度报告、年度报告、业绩预告、收购资产公告、重大合同、人事变动、股权激励、关联交易、回购股份方案及进展公告、投资者关系活动记录表

不需要下载的类型（无价值）：仅程序性的董事会决议公告、股东大会通知、薪酬管理制度、担保额度公告、会计政策变更、债券付息公告、法律意见书、保荐机构核查意见、分红实施公告、变更会计师事务所

特别注意：董事会决议公告如果标题中没有明确提及具体议案（如收购、回购等），属于程序性公告，不需要下载。

用JSON格式回答：{"judge": true} 或 {"judge": false}"""


class LLMJudge:
    """LLM 标题价值判断器"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = DEFAULT_MODEL,
        enabled: bool = True,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = 2,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.enabled = enabled
        self.timeout = timeout
        self.retries = retries

        self._chat_url = f"{self.base_url}/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # 统计信息
        self.stats = {"total": 0, "valuable": 0, "skip": 0, "error": 0}

    def judge(self, title: str, stock_name: str = "") -> bool:
        """判断公告标题是否有价值

        Args:
            title: 公告标题
            stock_name: 股票名称（可选）

        Returns:
            True = 有价值（需要下载PDF），False = 无价值（跳过）
        """
        if not self.enabled:
            return True

        self.stats["total"] += 1

        user_msg = f"标题：{title}"
        if stock_name:
            user_msg += f"\n股票：{stock_name}"

        for attempt in range(self.retries + 1):
            try:
                resp = requests.post(
                    self._chat_url,
                    headers=self._headers,
                    json={
                        "model": self.model,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_msg},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 1024,
                    },
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                message = data.get("choices", [{}])[0].get("message", {})
                content = (message.get("content") or "").strip()
                reasoning = (message.get("reasoning_content") or "").strip()

                # 优先解析 JSON content
                if content:
                    try:
                        parsed = json.loads(content)
                        is_valuable = parsed.get("judge", True)
                    except Exception:
                        is_valuable = True
                else:
                    # reasoning 模型可能 content 为空，从 reasoning 中提取
                    combined = reasoning.lower()
                    if "true" in combined and "false" not in combined:
                        is_valuable = True
                    elif "false" in combined:
                        is_valuable = False
                    else:
                        is_valuable = True

                if is_valuable:
                    self.stats["valuable"] += 1
                    logger.debug("LLM判断: 有价值 [%s] %s", stock_name, title[:40])
                else:
                    self.stats["skip"] += 1
                    logger.info("LLM跳过: 无价值 [%s] %s", stock_name, title[:60])

                return is_valuable

            except Exception as e:
                logger.warning(
                    "LLM 调用失败 (attempt %d/%d): %s", attempt + 1, self.retries + 1, e
                )
                if attempt < self.retries:
                    time.sleep((attempt + 1) * 2)
                continue

        self.stats["error"] += 1
        logger.warning("LLM 调用全部失败，默认视为有价值: [%s] %s", stock_name, title[:40])
        return True

    def report(self) -> str:
        """返回 LLM 判断统计信息"""
        total = self.stats["total"]
        if total == 0:
            return "LLM 未进行任何判断"
        skip_pct = self.stats["skip"] / total * 100
        return (
            f"LLM 判断: 共 {total} 条, "
            f"有价值 {self.stats['valuable']} 条, "
            f"跳过 {self.stats['skip']} 条 ({skip_pct:.1f}%), "
            f"失败 {self.stats['error']} 条"
        )

    @classmethod
    def from_config(cls, config: dict) -> "LLMJudge":
        """从配置字典 + .env 文件创建 LLMJudge 实例

        api_key 优先从 .env 文件读取（LLM_API_KEY 变量），
        config.json 中不再存储敏感信息。
        """
        llm_cfg = config.get("llm", {})
        if not llm_cfg.get("enabled", False):
            return cls(api_key="", enabled=False)

        api_key = _load_env_key("LLM_API_KEY")
        if not api_key:
            logger.warning(
                "LLM 已启用但 .env 中未配置 LLM_API_KEY，已自动禁用"
            )
            return cls(api_key="", enabled=False)

        return cls(
            api_key=api_key,
            base_url=llm_cfg.get("base_url", "https://api.openai.com/v1"),
            model=llm_cfg.get("model", DEFAULT_MODEL),
            enabled=True,
            timeout=llm_cfg.get("timeout", DEFAULT_TIMEOUT),
            retries=llm_cfg.get("retries", 2),
        )
