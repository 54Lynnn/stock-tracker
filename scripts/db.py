#!/usr/bin/env python3
"""SQLite 状态存储模块 - 替代 seen_announcements.json

数据库表:
  announcements: 存储已抓取公告的完整信息
  - ann_id (TEXT PRIMARY KEY): MD5 唯一标识
  - stock_code, stock_name: 股票信息
  - title, ann_date, ann_type: 公告摘要
  - url, art_code, notice_id: 完整数据
  - full_text: 公告全文（原始，来自 PDF 提取）
  - clean_text: 公告全文（经清洗，移除模板套话）
  - attach_url: PDF 附件链接
  - first_seen_at: 首次发现时间戳
"""

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(SKILL_DIR, ".stock-watcher-state")
DB_PATH = os.path.join(STATE_DIR, "announcements.db")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS announcements (
    ann_id      TEXT PRIMARY KEY,
    stock_code  TEXT NOT NULL,
    stock_name  TEXT,
    title       TEXT,
    ann_date    TEXT,
    ann_type    TEXT,
    url         TEXT,
    art_code    TEXT,
    notice_id   TEXT,    -- 注意：实际存储的是公告日期（兼容旧数据，非 ID）
    full_text   TEXT,
    clean_text  TEXT,
    attach_url  TEXT,
    first_seen_at TEXT DEFAULT (datetime('now', 'localtime'))
)
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_stock_code ON announcements(stock_code)",
    "CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(ann_date)",
    "CREATE INDEX IF NOT EXISTS idx_first_seen ON announcements(first_seen_at)",
    "CREATE INDEX IF NOT EXISTS idx_ann_type ON announcements(ann_type)",
]

INSERT_SQL = """
INSERT OR REPLACE INTO announcements
    (ann_id, stock_code, stock_name, title, ann_date, ann_type,
     url, art_code, notice_id, full_text, clean_text, attach_url)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

UPDATE_CONTENT_SQL = """
UPDATE announcements SET full_text = ?, clean_text = ?, attach_url = ? WHERE ann_id = ?
"""

UPDATE_CLEAN_SQL = """
UPDATE announcements SET clean_text = ? WHERE ann_id = ?
"""


def _get_conn() -> sqlite3.Connection:
    os.makedirs(STATE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate_schema(conn: sqlite3.Connection):
    for col, col_def in [("full_text", "TEXT"), ("clean_text", "TEXT"), ("attach_url", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE announcements ADD COLUMN {col} {col_def}")
            logger.info("数据库迁移: 新增字段 %s", col)
        except sqlite3.OperationalError:
            pass


def init_db():
    conn = _get_conn()
    try:
        conn.execute(CREATE_TABLE_SQL)
        _migrate_schema(conn)
        for idx in CREATE_INDEXES_SQL:
            conn.execute(idx)
        conn.commit()

        old_json = os.path.join(STATE_DIR, "seen_announcements.json")
        if os.path.exists(old_json):
            _migrate_from_json(conn, old_json)
    finally:
        conn.close()


def _migrate_from_json(conn: sqlite3.Connection, json_path: str):
    cursor = conn.execute("SELECT COUNT(*) FROM announcements")
    if cursor.fetchone()[0] > 0:
        logger.info("数据库已有数据，跳过 JSON 迁移")
        return

    try:
        with open(json_path, "r") as f:
            hashes = json.load(f)
        if not hashes:
            return

        for h in hashes:
            conn.execute(
                INSERT_SQL,
                (h, "", "", "", "", "", "", "", "", "", ""),
            )
        conn.commit()
        backup = json_path + ".bak"
        os.rename(json_path, backup)
        logger.info("已从 %s 迁移 %d 条记录到 SQLite", json_path, len(hashes))
        logger.info("原 JSON 文件已备份为 %s", backup)
    except Exception as e:
        logger.warning("JSON 迁移失败: %s", e)


def make_ann_id(ann: dict) -> str:
    raw = f"{ann['stock_code']}_{ann.get('art_code', '')}_{ann.get('notice_id', '')}_{ann['title']}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_seen_ids() -> set:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT ann_id FROM announcements").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def record_announcements(announcements: list[dict]):
    if not announcements:
        return
    conn = _get_conn()
    try:
        count = 0
        for ann in announcements:
            ann_id = make_ann_id(ann)
            conn.execute(
                INSERT_SQL,
                (
                    ann_id,
                    ann["stock_code"],
                    ann["stock_name"],
                    ann["title"],
                    ann["ann_date"],
                    ann.get("ann_type", ""),
                    ann["url"],
                    ann.get("art_code", ""),
                    ann.get("notice_id", ""),
                    ann.get("full_text", ""),
                    ann.get("clean_text", ""),
                    ann.get("attach_url", ""),
                ),
            )
            count += 1
        conn.commit()
        logger.info("已记录 %d 条公告到数据库", count)
    finally:
        conn.close()


def update_content(announcements: list[dict]):
    conn = _get_conn()
    try:
        count = 0
        for ann in announcements:
            full_text = ann.get("full_text", "")
            clean_text = ann.get("clean_text", "")
            attach_url = ann.get("attach_url", "")
            if not full_text and not attach_url:
                continue
            ann_id = make_ann_id(ann)
            conn.execute(UPDATE_CONTENT_SQL, (full_text, clean_text, attach_url, ann_id))
            count += 1
        conn.commit()
        if count:
            logger.info("已更新 %d 条公告正文", count)
    finally:
        conn.close()


def update_clean_text(announcements: list[dict]):
    """批量更新清洗后的文本"""
    conn = _get_conn()
    try:
        count = 0
        for ann in announcements:
            clean_text = ann.get("clean_text", "")
            if not clean_text:
                continue
            ann_id = make_ann_id(ann)
            conn.execute(UPDATE_CLEAN_SQL, (clean_text, ann_id))
            count += 1
        conn.commit()
        if count:
            logger.info("已清洗 %d 条公告", count)
    finally:
        conn.close()


def get_records_needing_clean() -> list[dict]:
    """获取有原始全文但尚无清洗文本的记录"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT ann_id, stock_code, stock_name, title, art_code, notice_id, url, ann_date, full_text "
            "FROM announcements WHERE full_text IS NOT NULL AND full_text != '' "
            "AND (clean_text IS NULL OR clean_text = '')"
        ).fetchall()
        return [
            {
                "ann_id": r[0], "stock_code": r[1], "stock_name": r[2],
                "title": r[3], "art_code": r[4], "notice_id": r[5],
                "url": r[6], "ann_date": r[7], "full_text": r[8],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_pending_content() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT ann_id, stock_code, stock_name, title, art_code, notice_id, url, ann_date "
            "FROM announcements WHERE (full_text IS NULL OR full_text = '') AND art_code != ''"
        ).fetchall()
        return [
            {
                "ann_id": r[0], "stock_code": r[1], "stock_name": r[2],
                "title": r[3], "art_code": r[4], "notice_id": r[5],
                "url": r[6], "ann_date": r[7],
            }
            for r in rows
        ]
    finally:
        conn.close()


def prune_empty():
    """删除 full_text 和 clean_text 同时为空的无效记录"""
    conn = _get_conn()
    try:
        deleted = conn.execute(
            "DELETE FROM announcements WHERE (full_text IS NULL OR full_text = '')"
        ).rowcount
        conn.commit()
        if deleted:
            logger.info("已清理 %d 条无正文的空记录", deleted)
        return deleted
    finally:
        conn.close()


def _count_by_source(keyword: str) -> int:
    conn = _get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM announcements WHERE url LIKE ?", (f"%{keyword}%",)
        ).fetchone()[0]
    finally:
        conn.close()


def get_stats() -> dict:
    conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        with_text = conn.execute(
            "SELECT COUNT(*) FROM announcements WHERE full_text IS NOT NULL AND full_text != ''"
        ).fetchone()[0]
        stocks = conn.execute(
            "SELECT COUNT(DISTINCT stock_code) FROM announcements WHERE stock_code != ''"
        ).fetchone()[0]
        latest = conn.execute(
            "SELECT MAX(first_seen_at) FROM announcements"
        ).fetchone()[0] or "无"
        return {
            "total": total,
            "with_content": with_text,
            "stocks_tracked": stocks,
            "latest_update": latest,
        }
    finally:
        conn.close()


def list_announcements(stock_code: str = None, days: int = None, limit: int = 100) -> list[dict]:
    conn = _get_conn()
    try:
        sql = (
            "SELECT ann_id, stock_code, stock_name, title, ann_date, ann_type, "
            "url, art_code, full_text, attach_url, first_seen_at FROM announcements"
        )
        conditions = []
        params = []

        if stock_code:
            conditions.append("stock_code = ?")
            params.append(stock_code)

        if days:
            conditions.append("ann_date >= date('now', ? || ' days')")
            params.append(f"-{days}")

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        sql += " ORDER BY first_seen_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "ann_id": r[0], "stock_code": r[1], "stock_name": r[2],
                "title": r[3], "ann_date": r[4], "ann_type": r[5],
                "url": r[6], "art_code": r[7],
                "full_text": r[8], "attach_url": r[9],
                "first_seen_at": r[10],
            }
            for r in rows
        ]
    finally:
        conn.close()


init_db()
