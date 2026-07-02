#!/usr/bin/env python3
"""自选股公告仪表盘 - Web 界面

启动: python3 scripts/dashboard.py
访问: http://localhost:5001
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, Response, send_from_directory
import db
import csv
import io
import html
import re
from datetime import datetime

app = Flask(__name__)

# Static frontend directory
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'static', 'dashboard')


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(os.path.join(FRONTEND_DIR, 'assets'), filename)


@app.route("/api/stocks")
def api_stocks():
    data = db.get_stock_overview()
    for item in data:
        item["stock_name"] = html.escape(item.get("stock_name", ""))
        item["stock_code"] = html.escape(item.get("stock_code", ""))
    return jsonify(data)


_RE_STOCK_CODE = re.compile(r"^[0-9]{4,6}$")

@app.route("/api/announcements/<stock_code>")
def api_announcements(stock_code):
    if not _RE_STOCK_CODE.match(stock_code):
        return jsonify({"error": "股票代码格式错误"}), 400
    data = db.get_announcements_for_stock(stock_code, days=30)
    for item in data:
        item["title"] = html.escape(item.get("title") or "")
        item["summary"] = html.escape(item.get("summary") or "")
        item["clean_text"] = html.escape(item.get("clean_text") or "")
        item["ann_type_category"] = html.escape(item.get("ann_type_category") or "")
        item["ann_type_tag"] = html.escape(item.get("ann_type_tag") or "")
    return jsonify(data)


@app.route("/api/export/csv")
def export_csv():
    data = db.get_all_valuable_announcements(days=30)
    output = io.StringIO()
    output.write('\ufeff')  # BOM for Excel
    writer = csv.writer(output)
    writer.writerow(["股票代码", "股票名称", "公告标题", "公告日期", "大类", "小类", "摘要", "链接"])
    for ann in data:
        writer.writerow([
            ann["stock_code"], ann["stock_name"], ann["title"],
            ann["ann_date"], ann["ann_type_category"], ann["ann_type_tag"],
            ann["summary"], ann["url"],
        ])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=announcements_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


# SPA fallback - serve index.html for all non-API routes
@app.route("/<path:path>")
def spa_fallback(path):
    file_path = os.path.join(FRONTEND_DIR, path)
    if os.path.isfile(file_path):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, 'index.html')


if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("PORT", 5001))
    print(f"自选股公告仪表盘启动: http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
