---
name: "stock-watcher-pro"
version: "2.0.0"
description: "东方财富自选股公告追踪。三级过滤 + LLM分类摘要 + Web仪表盘，支持 agent 定时推送。"
metadata:
  {
    "openclaw":
      {
        "emoji": "📊",
        "requires": { "python": "3.9+" },
        "install":
          [
            {
              "id": "deps",
              "kind": "shell",
              "command": "cd {{SKILL_DIR}} && pip install requests pdfplumber flask",
              "label": "Install Python dependencies",
            },
          ],
      },
  }
---

# 📊 Stock Watcher - 东方财富自选股公告追踪

**双模式技能** — Agent 定时自动追踪 + 用户 Web 仪表盘查看。

告诉 agent 你想做什么，例如：

> "帮我看看持仓板块这两天有什么重要公告"
> "每天8点提醒我有价值的自选股公告"
> "帮我打开公告仪表盘看看"

## 模式一：Agent Run 模式

Agent 定时运行 `run.sh`，自动抓取公告、生成摘要，输出有价值公告的 digest 转发给用户。

告诉 agent 即可：

> "帮我设置 stock-watcher 定时任务，每天早上8点运行一次，追踪【xx】板块的最新公告，有重要公告通知我"

或者手动运行：

```bash
# 用法: bash run.sh [group] [days] [source]
bash run.sh mygroup 15 eastmoney
```

### 输出示例

**有公告时：**
```
DIGEST_TOTAL:3
1.
【000001平安银行】-【平安银行2026年第一季度报告】
【季度报告】...

2.
【600519贵州茅台】-【贵州茅台关于回购股份的进展公告】
累计回购金额XX亿元...
```

**无公告时：**
```
DIGEST_EMPTY:最近1天test板块无高价值公告
```

---

## 模式二：Dashboard 模式

用户手动运行，抓取公告 + 生成摘要 + 启动 Web 仪表盘，浏览器查看所有公告详情。

告诉 agent 即可：

> "帮我打开公告仪表盘，看看 mygroup 板块最近15天的情况"

或者手动运行：

```bash
# 用法: bash dashboard.sh [group] [days] [source]
bash dashboard.sh mygroup 15 eastmoney
```

脚本自动执行三步：
1. `stock_watcher.py --fetch-content` — 抓取公告 + 全文 + LLM 分类
2. `daily_summary.py` — 生成摘要
3. `dashboard.py` — 启动 Flask 仪表盘（默认端口 5001）

启动后浏览器访问 `http://localhost:5001`，按 Ctrl+C 停止。

**仪表盘功能：**
- 股票列表表格：7天/15天/30天/全部的有价值公告比例
- 点击展开：懒加载公告详情（标题、日期、摘要、正文、原文链接）
- 类型标签：`大类 / 小类`（如 `股权股本类 / 回购`）
- 搜索过滤，响应式设计

---

## 工具命令

不通过 `run.sh` / `dashboard.sh`，直接操作数据库或查看数据：

```bash
python3 scripts/stock_watcher.py --stats              # 数据库统计
python3 scripts/stock_watcher.py --list               # 最近公告
python3 scripts/stock_watcher.py --list --stock 600519 --days 30  # 指定股票
python3 scripts/stock_watcher.py --list-groups        # 可用分组
python3 scripts/stock_watcher.py --clean              # 清洗已有正文
python3 scripts/stock_watcher.py --prune              # 清理无正文的空记录
```

---

## 首次设置

### 1. 安装依赖

```bash
cd ~/.openclaw/workspace/skills/stock-watcher
pip install requests pdfplumber flask
```

### 2. 配置 Cookie

东方财富网页版登录后的 Cookie，用于获取自选股列表。

1. 浏览器打开 [https://quote.eastmoney.com/zixuan/lite.html](https://quote.eastmoney.com/zixuan/lite.html) 并登录
2. F12 → Console → 输入 `copy(document.cookie)` 回车
3. 粘贴到 `cookie.txt`

**自动续签（可选）：**
```bash
pip install playwright && playwright install chromium
python3 scripts/refresh_cookie.py
```

### 3. 配置 LLM（可选）

LLM 用于标题价值判断、公告分类和摘要生成。未配置时仅使用正则过滤。

```bash
# .env 文件
echo "LLM_API_KEY=sk-your-api-key" > .env
```

```json
// config.json
{
  "llm": {
    "enabled": true,
    "base_url": "https://opencode.ai/zen/go/v1",
    "model": "deepseek-v4-flash",
    "timeout": 60,
    "retries": 2
  }
}
```

### 4. 验证运行

```bash
python3 scripts/stock_watcher.py --group test --days 15 --fetch-content
python3 scripts/stock_watcher.py --stats
```

### 5. 配置定时任务

告诉 agent 即可，例如：

> "帮我设置 stock-watcher 定时任务，每天早上8点运行一次，追踪【xx】板块的最新公告，有重要公告通知我"

或者手动配置：

```bash
openclaw cron add \
  --name stock-watcher \
  --cron "0 1 * * *" \
  --message "运行股票公告扫描：cd {{SKILL_DIR}} && bash run.sh mygroup 15 eastmoney"
```

---

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `config.json` → `llm.enabled` | `true` | 是否启用 LLM |
| `config.json` → `llm.model` | `deepseek-v4-flash` | LLM 模型 |
| `.env` → `LLM_API_KEY` | （需设置） | LLM API Key |
| `cookie.txt` | （需设置） | 东方财富登录 Cookie |
| `--source` | `eastmoney` | 数据来源：`eastmoney`（A股+港股）或 `cninfo`（巨潮A股+东方财富港股） |
| 数据库 | `.stock-watcher-state/announcements.db` | 自动创建 |
| 日志 | `logs/stock_watcher_YYYYMMDD.log` | 按天轮转，保留30天 |

---

## 依赖

- Python 3.9+
- `requests` — HTTP 请求
- `pdfplumber` — PDF 文本提取
- `flask` — Web 仪表盘
- `sqlite3` — 数据库（Python 内置）
- `playwright`（可选） — Cookie 自动续签

## 注意事项

1. **Cookie 会过期**（几天到几周），过期后 `run.sh` 会尝试自动续签，失败则需手动更新
2. **LLM 可选** — 未配置 API Key 时自动禁用，仅使用正则过滤，不影响基本功能
3. **增量抓取** — 已存在的公告（通过 ann_id 去重）不会重复抓取
4. **超长文档** — 通函、会议资料等 >5000 字的文档只提取目录，减少 LLM token 消耗

---

完整技术文档、公告分类体系、Token 消耗分析等详见 [GitHub](https://github.com/54Lynnn/stock-watcher-pro)
