---
name: "stock-watcher"
description: "Tracks latest announcements of self-selected stocks from EastMoney (东方财富). Invoke when user wants to monitor their zixuan (自选股) stock announcements or set up automated tracking via cron."
---

# Stock Watcher - 东方财富自选股公告追踪

自动追踪东方财富网页版自选板块中所有自选股的最新公告，支持定时运行（如每天两次）并通过多种方式通知用户。

## 功能特性

- 自动从 [自选股页面](https://quote.eastmoney.com/zixuan/lite.html) 获取自选股列表
- 支持按分组筛选（如 `--group 持仓` 只追踪指定分组）
- 抓取每只自选股的最新公告
- **三级过滤**：正则 → LLM 标题价值判断 → prune_empty 清理
- **SQLite 数据库**：公告状态和全文持久化，支持按股票/日期查询历史公告
- 支持定时任务（每天 9:00 和 15:00 各运行一次）
- 支持多种通知方式：飞书/钉钉 Webhook、终端输出

## 自选股获取原理

脚本从 `https://quote.eastmoney.com/zixuan/lite.html` 使用的 `myfavor.eastmoney.com` API 获取自选股，三级回退：

1. **myfavor API**（优先）：调用 `myfavor.eastmoney.com/v4/webouter` 接口获取分组及股票列表，只需 Cookie 中带登录态即可
2. **Cookie 解析**（兜底）：从 Cookie 的 `selfSelectStocks` 字段解析
3. **config.json 手动配置**：如果以上都失败，从配置文件加载

## 使用前提

1. **Python 3.8+** 环境
2. 东方财富网页版登录后的 **Cookie**（用于调用 myfavor API）
3. 安装依赖：`pip install requests pdfplumber`

## 获取 Cookie

1. 用浏览器打开并登录 [https://quote.eastmoney.com/zixuan/lite.html](https://quote.eastmoney.com/zixuan/lite.html)
2. 确保页面右上角显示你的用户名（已登录状态）
3. 按 F12 → Console → 输入 `copy(document.cookie)` 回车
4. Ctrl+V 粘贴到 `cookie.txt` 文件
5. **注意**：Cookie 会过期（几天到几周），过期后需重新获取

### Cookie 过期自动续签

如果 Cookie 过期，优先尝试自动续签（无需手动操作）：

```bash
python scripts/refresh_cookie.py
```

脚本会通过 Playwright 浏览器访问东财页面，尝试续签 Cookie 并自动验证有效性。
> 依赖：需要安装 `playwright` 库（`pip install playwright && playwright install chromium`）

如果自动续签失败，说明服务器端 session 已失效，再按上述步骤手动复制 Cookie。

## LLM 配置（可选）

要启用 LLM 标题价值判断，需要：

1. 在 `.env` 文件中配置 API Key：
   ```
   LLM_API_KEY=sk-your-api-key
   ```
2. 在 `config.json` 中配置 LLM 参数：
   ```json
   {
     "llm": {
       "enabled": true,
       "base_url": "https://opencode.ai/zen/go/v1",
       "model": "deepseek-v4-flash",
       "timeout": 15,
       "retries": 2
     }
   }
   ```

> 未配置 API Key 时，LLM 功能自动禁用，仅使用正则过滤，不影响正常运行。

**支持的模型：** 任何兼容 OpenAI 接口且支持 `response_format: {"type": "json_object"}` 的模型均可使用。

## 使用方法

### 手动运行

```bash
# 完整流程（推荐）
python scripts/stock_watcher.py --source cninfo --group 持仓 --days 15 --fetch-content

# 基础运行 - 追踪所有自选股最近7天公告
python scripts/stock_watcher.py

# 只追踪指定分组（如"持仓"）
python scripts/stock_watcher.py --group 持仓

# 列出所有可用分组
python scripts/stock_watcher.py --list-groups

# 查看数据库统计
python scripts/stock_watcher.py --stats

# 查看最近公告历史
python scripts/stock_watcher.py --list

# 查看某只股票的历史公告
python scripts/stock_watcher.py --list --stock 600519 --days 30
```

公告状态存储在 SQLite 数据库中（`.stock-watcher-state/announcements.db`），支持按股票代码、日期范围查询。

### 设置定时任务（每天两次）

编辑 crontab：

```bash
crontab -e
```

添加以下两行（分别在北京时间 9:00 和 15:00 运行）：

```cron
0 1 * * * cd /path/to/stock-watcher && python scripts/stock_watcher.py --source cninfo --group 持仓 --days 15 >> logs/stock_watcher.log 2>&1
0 7 * * * cd /path/to/stock-watcher && python scripts/stock_watcher.py --source cninfo --group 持仓 --days 15 >> logs/stock_watcher.log 2>&1
```

> **注意**：服务器通常使用 UTC 时间，北京时间 UTC+8，所以 9:00 = UTC 1:00，15:00 = UTC 7:00。

## 通知配置

编辑 `config.json`：

```json
{
  "notify": {
    "type": "webhook",
    "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/your-webhook-id"
  },
  "fetch_interval_days": 7
}
```

支持的通知类型：
- `terminal`：终端输出（默认）
- `webhook`：飞书/钉钉机器人 Webhook

## 文件结构

```
.trae/skills/stock-watcher/
  SKILL.md                        # 本 Skill 定义
  config.json                     # 通知 + LLM 配置
  cookie.txt                      # 东方财富 Cookie（登录态）
  .env                            # LLM API Key（敏感信息，不进 git）
  .gitignore                      # 排除 .env
  scripts/
    stock_watcher.py              # 主脚本入口
    eastmoney_api.py              # 东方财富 API 封装
    cninfo_api.py                 # 巨潮资讯网公告获取
    ann_detail.py                 # 公告正文抓取 + 提取目录
    llm_judge.py                  # LLM 标题价值判断
    text_cleaner.py               # 公告正文清洗（移除模板套话）
    db.py                         # SQLite 数据库模块
    refresh_cookie.py             # Cookie 自动续签工具
    setup.sh                      # 环境配置脚本
  logs/                           # 运行日志
.trae/skills/.stock-watcher-state/
  announcements.db                # SQLite 公告数据库（自动管理）
```

## 公告正文提取策略

### 三级过滤流程

```
公告标题 → 正则过滤（SKIP_CONTENT_PATTERNS，零成本）
         → LLM 标题价值判断（可选，Semantic 判断）
         → 下载 PDF + 提取全文（高成本）
```

### 全文提取 vs 目录提取

对不同类型公告采用不同的提取策略：

| 文档类型 | 策略 | 说明 |
|---------|------|------|
| 普通公告（<5000字） | 全文提取 + 清洗 | 短公告，完整正文存入 `clean_text` |
| 股东会通函 | **只提取目录** | `clean_text` 仅存目录/提案列表，`attach_url` 保留 PDF 链接 |
| 海外市场/监管公告 | **只提取目录** | 同上 |
| 股东会会议资料 | **只提取目录** | 同上 |

### 给 LLM 的提示

当分析 `announcements.db` 中的公告时：

1. **优先使用 `clean_text`**：已去除模板套话，内容更精炼
2. **长文档仅有目录时**：`clean_text` 只有目录/提案列表（最多 2000 字），完整正文在 `full_text`，原始 PDF 在 `attach_url`
3. **`full_text` 有但 `clean_text` 为空**：该文档被跳过或无法提取，可通过 `attach_url` 查看原始 PDF
4. **`stock_code` 5 位数为港股**（如 02628），6 位数为 A 股（如 600519）

## 输出示例

```
[2026-06-02 09:00] === 自选股公告追踪报告 ===
[2026-06-02 09:00] 共扫描 15 只自选股
[2026-06-02 09:00] 发现 3 条新公告：
[2026-06-02 09:00] ─────────────────────────────
[2026-06-02 09:00] 1. 贵州茅台 (600519)
[2026-06-02 09:00]    标题：贵州茅台关于召开2025年度股东大会的通知
[2026-06-02 09:00]    时间：2026-06-01
[2026-06-02 09:00]    链接：https://...
[2026-06-02 09:00] ─────────────────────────────
[2026-06-02 09:00] 2. 宁德时代 (300750)
[2026-06-02 09:00]    标题：宁德时代2026年第一季度报告
[2026-06-02 09:00]    时间：2026-05-31
[2026-06-02 09:00]    链接：https://...
```
