"""依赖管理模块 - 统一管理模块间依赖

通过延迟导入避免循环依赖，集中管理所有跨模块引用。
"""

import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def get_db():
    import db
    return db


def get_llm_judge():
    from llm_judge import LLMJudge
    return LLMJudge


def get_text_cleaner():
    from text_cleaner import clean_announcement_text
    return clean_announcement_text


def get_ann_detail():
    import ann_detail
    return ann_detail


def get_eastmoney_api():
    import eastmoney_api
    return eastmoney_api


def get_cninfo_api():
    import cninfo_api
    return cninfo_api


def get_daily_summary():
    import daily_summary
    return daily_summary
