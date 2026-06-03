# Stock Watcher 交接文档

## 项目概述

东方财富自选股公告追踪工具。自动获取自选股列表，拉取 A 股（巨潮资讯网）+ 港股（东方财富）的公告，用 LLM 筛选有价值的公告标题，下载 PDF 提取正文，清洗模板套话，最终存入 SQLite 数据库供 LLM 分析。

---

## 数据流

```
东方财富 myfavor API → 自选股代码 + 名称
    ↓
  ┌─ A 股 (market=0/1) ──────────────────────┐
  │  巨潮资讯网 cninfo.com.cn                  │
  │  POST /new/hisAnnouncement/query           │
  │  → 公告列表 (title, date, PDF url)         │
  └──────────────────────────────────────────┘
  ┌─ 港股 (market=116) ──────────────────────┐
  │  东方财富 np-anotice-stock API            │
  │  ann_type='H'                             │
  │  → 公告列表 (title, date, PDF url)         │
  └──────────────────────────────────────────┘
    ↓
入库（此时只有标题+日期+URL，全文为空）
    ↓
对每条公告逐条处理:
  1. should_skip_content()  → 正则跳过 → prune_empty 自动删除
  2. LLM 标题价值判断         → LLM 判断无价值 → 跳过
  3. 下载 PDF → pdfplumber 提取文本 → full_text
  4. 判断文档类型:
     - 普通公告: 全文清洗 → clean_text
     - 超长参考文档: 只提取目录 → clean_text (≤2000字)
  5. prune_empty() → 删除全文为空的记录
```

---

## 数据库结构

**路径**: `.trae/skills/.stock-watcher-state/announcements.db`

| 字段 | 说明 | 来源 |
|------|------|------|
| `ann_id` | MD5 唯一标识 | 自动生成 |
| `stock_code` | 股票代码 | myfavor API |
| `stock_name` | 中文名称 | myfavor API（优先），无则显示代码 |
| `title` | 公告标题 | 公告列表 API |
| `ann_date` | 公告日期 | 公告列表 API |
| `ann_type` | 类型（A/H） | 公告列表 API |
| `url` | PDF 或公告页链接 | 公告列表 API |
| `art_code` | 文章编码 | 公告列表 API |
| `notice_id` | 通知日期（兼容旧数据） | 公告列表 API |
| `full_text` | 原始正文（PDF 提取） | pdfplumber |
| `clean_text` | 清洗后正文（或目录） | text_cleaner |
| `attach_url` | 原始 PDF 链接 | 同 url 或 pdf.dfcfw.com |
| `first_seen_at` | 首次入库时间 | 自动生成 |

---

## 关键文件

```
.trae/skills/stock-watcher/
  SKILL.md                        # Skill 定义
  HANDOVER.md                     # 本文档
  config.json                     # 通知 + LLM 配置
  cookie.txt                      # 东方财富 Cookie（登录态）
  .env                            # LLM API Key（敏感信息，不进 git）
  .gitignore                      # 排除 .env
  scripts/
    stock_watcher.py              # 主入口（CLI 参数解析 + 流程编排）
    eastmoney_api.py              # 自选股获取 + 港股公告列表 API
    cninfo_api.py                 # 巨潮资讯网 A 股公告列表 API
    ann_detail.py                 # 正文抓取 + 正则跳过规则 + TOC 提取
    llm_judge.py                  # LLM 标题价值判断（OpenAI 兼容 API）
    text_cleaner.py               # 正文清洗（14 类正则规则）
    db.py                         # SQLite 操作 + prune_empty
    setup.sh                      # 环境配置
  logs/
.trae/skills/.stock-watcher-state/
  announcements.db                # SQLite 公告数据库
```

---

## 过滤体系（三级过滤）

### 第一级：正则过滤（SKIP_CONTENT_PATTERNS）

定义在 [ann_detail.py](file:///home/administrator/.openclaw/workspace/skills/.trae/skills/stock-watcher/scripts/ann_detail.py#L32-L51)，零成本拦截已知低价值公告类型：

| 匹配关键词 | 跳过类型 |
|-----------|---------|
| "公司章程" | 公司章程 |
| "信用评级" 或 "跟踪评级" | 信用/跟踪评级报告 |
| "募集说明书" | 债券募集说明书 |
| "付息公告" | 债券付息公告 |
| "上市公告" 或 "摘牌" | 债券上市/摘牌 |
| "发行结果公告"、"票面利率"、"簿记建档"、"更名公告"、"发行完毕" | 债券程序性公告 |
| "董事会报告" | 董事会报告 |
| "法律意见书" 或 "法律意见" | 股东会法律意见书 |
| "股东会决议公告"、"表决结果"、"投票表决结果" | 股东会决议/表决 |
| "薪酬" | 薪酬管理制度 |
| "周年会通告" | 股东周年会通告 |
| "担保额度" | 担保额度公告 |
| "召开情况" | 业绩说明会召开情况 |

> **注意**: "决议公告" 只跳过了"股东会决议公告"，"董事会决议公告"保留由 LLM 判断（有实质人事/决策内容）。

### 第二级：LLM 标题价值判断

定义在 [llm_judge.py](file:///home/administrator/.openclaw/workspace/skills/.trae/skills/stock-watcher/scripts/llm_judge.py) 中，通过 OpenAI 兼容 API（DeepSeek V4 Flash）判断正则无法覆盖的边界情况。

**有价值（保留）：** 季度/年度报告、业绩预告、收购资产、重大合同、人事变动、股权激励、关联交易、回购方案及进展、投资者关系活动记录表

**无价值（跳过）：** 程序性董事会决议、股东大会通知、薪酬制度、担保额度、会计政策变更、债券付息公告、法律意见书、保荐机构核查意见、分红实施公告、变更会计师事务所

### 第三级：TOC 提取规则

定义在 [ann_detail.py](file:///home/administrator/.openclaw/workspace/skills/.trae/skills/stock-watcher/scripts/ann_detail.py#L75-L81)：

| 匹配关键词 | 文档类型 | 说明 |
|-----------|---------|------|
| "通函" | 股东会通函 | 提取目录+提案列表 |
| "海外市场公告"、"海外监管公告" | 港股海外公告 | 同上 |
| "股东会会议资料"、"股东大会会议资料"、"会议文件" | 会议资料 | 同上 |
| "发行公告" | 债券发行公告 | 同上 |

> 触发条件: 必须同时满足 `len(full_text) > 5000`。小于 5000 字的正常全文提取。

### 第四级：正文清洗（text_cleaner.py）

[text_cleaner.py](file:///home/administrator/.openclaw/workspace/skills/.trae/skills/stock-watcher/scripts/text_cleaner.py) 中 14 类按顺序执行：
1. 股票代码表头（支持"证券简称"和"股票简称"）
2. 纯公司名行
3. 董事会/监事会免责声明（10 个变体）
4. "特此公告"结语 + 日期落款（支持阿拉伯和中文数字）
5. 股票信息行
6. "重要内容提示"标记
7. PDF 噪声（页码、水印）
8. 声明板块
9. 投资者关系表头
10. 地址/联系信息
11. 募集说明书评级/担保字段
12. 目录页
13. H股表格模板文字（繁体）
14. 多余空白行

---

## 配置文件

### config.json（非敏感配置）
```json
{
  "notify": { "type": "terminal", "webhook_url": "" },
  "fetch_interval_days": 7,
  "llm": {
    "enabled": true,
    "base_url": "https://opencode.ai/zen/go/v1",
    "model": "deepseek-v4-flash",
    "timeout": 15,
    "retries": 2
  }
}
```

### .env（敏感信息，不进 git）
```
LLM_API_KEY=sk-your-api-key
```

---

## 运行命令

```bash
# 完整流程（推荐）
python scripts/stock_watcher.py --source cninfo --group 持仓 --days 15 --fetch-content

# 只拉公告列表（不下载 PDF）
python scripts/stock_watcher.py --source cninfo --group 持仓 --days 15

# 补抓缺少全文的公告（会经过 LLM 再筛一次）
python scripts/stock_watcher.py --fetch-content

# 手动清洗
python scripts/stock_watcher.py --clean

# 清理空记录
python scripts/stock_watcher.py --prune

# 查看统计
python scripts/stock_watcher.py --stats

# 查看公告列表
python scripts/stock_watcher.py --list --stock 600519 --days 30
```

---

## 待办/注意事项

### 1. Cookie 过期
`cookie.txt` 是东方财富网页版登录态，**会过期**（几天到几周）。过期后尝试以下方法：

**方法一：自动续签（优先尝试）**
```bash
python scripts/refresh_cookie.py
```
脚本会用 Playwright 浏览器访问东财页面，尝试续签 Cookie 并自动验证。如果成功，无需手动操作。

**方法二：手动复制**
如果自动续签失败，说明服务器端 session 已失效，需要手动操作：
- 浏览器打开 https://quote.eastmoney.com/zixuan/lite.html
- 登录 → F12 → Console → `copy(document.cookie)`
- 粘贴覆盖 cookie.txt

### 2. 公告来源判断逻辑
代码里判断 A 股/港股用的是 `market` 字段（API 返回），不是代码位数：
```python
a_stocks = [s for s in stocks if s.get("market") in ("0", "1")]    # A 股
hk_stocks = [s for s in stocks if s.get("market") == "116"]        # 港股
```

### 3. 股票名称来源
股票名称优先从东方财富 myfavor API 返回的 `name` 字段获取。API 没有返回名称时，用股票代码代替（如 `02259`）。不再依赖硬编码映射表。

### 4. TOC 提取的回退策略
`_extract_toc_only()` 在找不到"目录"标记时，会返回开头 2000 字。对某些没有目录结构的文档可能不够理想，可优化。

### 5. LLM 模型选择
当前使用 DeepSeek V4 Flash（OpenCode Go 订阅），价格 $0.14/$0.28 per 1M tokens。如需更换模型，修改 `config.json` 中的 `model` 和 `base_url`。注意 `response_format: {"type": "json_object"}` 是否被目标模型支持。

### 6. 港股公告
港股通过东方财富 API（ann_type='H'）获取，PDF 也是从 pdf.dfcfw.com 下载。后续可考虑接入港交所披露易作为补充。

### 7. 数据库手动维护
```sql
-- 按股票查
SELECT stock_code, stock_name, title, ann_date FROM announcements WHERE stock_code = '02259';

-- 统计字数
SELECT SUM(LENGTH(clean_text)) FROM announcements;

-- 修复错误股票名
UPDATE announcements SET stock_name = '紫金黄金国际' WHERE stock_code = '02259';
```

---

## 代码关键入口

- **主流程**: [stock_watcher.py](file:///home/administrator/.openclaw/workspace/skills/.trae/skills/stock-watcher/scripts/stock_watcher.py) `run()` 函数
- **分流逻辑**: stock_watcher.py 第 258~268 行（A 股/港股分流）
- **正则跳过判断**: [ann_detail.py](file:///home/administrator/.openclaw/workspace/skills/.trae/skills/stock-watcher/scripts/ann_detail.py#L122-L128) `should_skip_content()`
- **LLM 标题判断**: [llm_judge.py](file:///home/administrator/.openclaw/workspace/skills/.trae/skills/stock-watcher/scripts/llm_judge.py#L103-L155) `LLMJudge.judge()`
- **TOC 提取**: [ann_detail.py](file:///home/administrator/.openclaw/workspace/skills/.trae/skills/stock-watcher/scripts/ann_detail.py#L83-L113) `_extract_toc_only()`
- **正文清洗**: [text_cleaner.py](file:///home/administrator/.openclaw/workspace/skills/.trae/skills/stock-watcher/scripts/text_cleaner.py#L27-L127) `clean_announcement_text()`
- **数据库清理**: [db.py](file:///home/administrator/.openclaw/workspace/skills/.trae/skills/stock-watcher/scripts/db.py#L258-L272) `prune_empty()`
