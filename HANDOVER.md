# Stock Watcher 交接文档

> 本文档记录项目最新状态、本轮改动、关键设计决策和待办事项，供下一个 Coding Agent 快速上手。

---

## 项目概述

东方财富自选股公告追踪系统。自动获取自选股列表，拉取 A 股（巨潮资讯网）+ 港股（东方财富）公告，通过正则 + LLM 双重过滤筛选有价值的公告，下载 PDF 提取全文，清洗模板套话，LLM 生成摘要，最终通过 Flask Web 仪表盘展示。

---

## 本轮关键改动（2026-06-06）

### 1. 公告分类体系：大类 + 小类双层结构

**背景**：用户要求参照万得金融终端（Wind）的分类标准，A股分8大类64小类，港股分7大类41小类。

**实现**：
- [llm_judge.py](file:///home/administrator/.openclaw/workspace/skills/stock-watcher/scripts/llm_judge.py#L52-L83) 中新增结构化字典：
  - `A_CATEGORY_MAP`：8大类 -> 64小类
  - `HK_CATEGORY_MAP`：7大类 -> 41小类
- LLM Prompt 要求同时返回 `judge`（有无价值）、`category`（大类）、`type`（小类）
- 解析逻辑：优先用 LLM 返回的 `category`，若缺失则用 `_get_category(market, subtype)` 从代码映射中反查兜底

**A股8大类**：招股类、财务报告类、重大事项类、交易提示类、配股类、增发类、股权股本类、一般公告类
**港股7大类**：业绩快报、财务报告、上市文件、股权股本、公告及通函、一般公告、债券及结构性产品

### 2. 数据库新增 `ann_type_category` 字段

- [db.py](file:///home/administrator/.openclaw/workspace/skills/stock-watcher/scripts/db.py#L97) 迁移逻辑新增该字段
- INSERT / UPSERT / 查询均包含该字段
- 旧数据该字段为空，新抓取的数据会同时写入大类和小类

### 3. 仪表盘标签显示 `大类 / 小类`

- [dashboard.py](file:///home/administrator/.openclaw/workspace/skills/stock-watcher/scripts/dashboard.py#L135-L143) 前端渲染逻辑更新
- 摘要右侧标签显示格式：`股权股本类 / 回购`
- 旧数据无大类时只显示小类（如 `回购`）

### 4. 摘要 Prompt 优化

- [daily_summary.py](file:///home/administrator/.openclaw/workspace/skills/stock-watcher/scripts/daily_summary.py#L179-L188) 的 `build_summary_prompt` 中，类型信息现在显示为 `大类-小类`（如 `股权股本类-回购`）
- 帮助 LLM 更精准理解公告上下文

---

## 数据流

```
东方财富 myfavor API → 自选股代码 + 名称 + market
    ↓
  ┌─ A 股 (market=0/1) ──→ 巨潮资讯网 cninfo.com.cn ──→ 公告列表
  └─ 港股 (market=116) ──→ 东方财富 np-anotice-stock ──→ 公告列表
    ↓
入库（标题+日期+URL，全文为空）
    ↓
对每条公告逐条处理：
  1. should_skip_content()   → 正则跳过（14类模式）
  2. LLM 标题价值判断       → 返回 {judge, category, type}
     → 无价值：跳过，写入 status=filtered
     → 有价值：继续
  3. 下载 PDF → pdfplumber 提取 → full_text
  4. 判断文档类型：
     - 普通公告：全文清洗 → clean_text
     - 超长文档（>5000字+通函/会议等关键词）：只提取目录 → clean_text (≤2000字)
  5. prune_empty() → 删除全文为空的 filtered 记录
  6. LLM 批量摘要（每1条一批，定期报告跳过）→ summary
    ↓
SQLite 持久化（.stock-watcher-state/announcements.db）
    ↓
Flask 仪表盘展示（localhost:5001）/ --digest 输出（agent 转发）
```

---

## 数据库结构

**路径**: `.stock-watcher-state/announcements.db`

| 字段 | 说明 | 来源 |
|------|------|------|
| `ann_id` | SHA256 唯一标识 | 自动生成 |
| `stock_code` | 股票代码 | API |
| `stock_name` | 中文名称 | API |
| `title` | 公告标题 | API |
| `ann_date` | 公告日期 | API |
| `ann_type` | A/H 类型 | API |
| `url` | 公告/PDF 链接 | API |
| `art_code` | 文章编码 | API |
| `notice_id` | 通知日期（兼容旧数据） | API |
| `full_text` | 原始正文（PDF提取） | pdfplumber |
| `clean_text` | 清洗后正文 | text_cleaner |
| `attach_url` | 原始PDF链接 | API/pdf.dfcfw.com |
| `status` | valuable / filtered | 代码判断 |
| `ann_type_tag` | **小类**标签（如"回购"） | LLM 返回 |
| `ann_type_category` | **大类**标签（如"股权股本类"） | LLM 返回，缺失时代码兜底 |
| `summary` | LLM 单条摘要 | daily_summary.py |
| `first_seen_at` | 首次入库时间 | 自动生成 |

**索引**: stock_code, ann_date, first_seen_at, ann_type

---

## 关键文件与入口

```
stock-watcher/
  scripts/
    stock_watcher.py      # 主入口：CLI解析 + 流程编排
    eastmoney_api.py      # 自选股获取 + 港股公告列表
    cninfo_api.py         # 巨潮资讯网 A股公告列表
    ann_detail.py         # PDF下载 + 正文提取 + 正则过滤 + LLM判断入口 + TOC提取
    llm_judge.py          # LLM标题价值判断（OpenAI兼容API）
    text_cleaner.py       # 正文清洗（14类正则规则）
    daily_summary.py      # 批量摘要生成 + 每日日报
    db.py                 # SQLite建表/迁移/CRUD/统计
    dashboard.py          # Flask Web仪表盘（端口5001）
    refresh_cookie.py     # Cookie自动续签（Playwright）
```

### 代码关键入口

| 功能 | 文件 | 函数/位置 |
|------|------|----------|
| 主流程 | stock_watcher.py | `run()` |
| A股/港股分流 | stock_watcher.py | ~L284-295 |
| 正则跳过判断 | ann_detail.py | `should_skip_content()` ~L122 |
| LLM标题判断 | ann_detail.py | `judge_result = llm_judge.judge(...)` ~L235 |
| LLM返回解析 | llm_judge.py | `LLMJudge.judge()` ~L145 |
| 分类映射兜底 | llm_judge.py | `_get_category()` ~L76 |
| TOC提取 | ann_detail.py | `_extract_toc_only()` ~L83 |
| 正文清洗 | text_cleaner.py | `clean_announcement_text()` ~L27 |
| 批量摘要 | daily_summary.py | `generate_summaries()` ~L266 |
| 摘要Prompt构建 | daily_summary.py | `build_summary_prompt()` ~L163 |
| 数据库清理 | db.py | `prune_empty()` ~L343 |
| 仪表盘API | dashboard.py | `/api/announcements/<stock_code>` ~L174 |

---

## 核心设计决策（不要改动除非明确需求）

### 1. LLM 一次调用返回三个字段

不是分步判断（先 judge 再 category 再 type），而是一次 Prompt 要求 LLM 返回 `{"judge": true, "category": "...", "type": "..."}`。原因是：
- 减少 API 调用次数，降低成本
- 分类信息本身就能辅助价值判断（如"季度报告"天然有价值）
- 代码有 `_get_category()` 兜底，LLM 漏传 category 也能正确映射

### 2. 定期报告跳过 LLM 摘要

`SKIP_LLM_TYPES = {"业绩预告", "业绩快报", "季度报告", "半年报告", "年度报告", "补充更正"}`

这些公告正文极长且内容标准化，LLM 摘要性价比极低。直接写固定摘要 `【{类型}】{标题}`。

### 3. 批量摘要每批 1 条

推理模型输出较长，BATCH_SIZE=1 + max_tokens=10000 能避免 JSON 截断。每批只处理 1 条公告。

### 4. 正文清洗只移套话，不删内容

text_cleaner.py 的 14 条规则全部是移除"免责声明""特此公告""页码水印"等模板文字，不会删除实质性内容。清洗后字数通常会减少 20%-40%。

### 5. 空值保护 UPSERT

数据库 INSERT ... ON CONFLICT 使用 `CASE WHEN excluded.xxx != '' THEN excluded.xxx ELSE announcements.xxx END`，确保新数据为空时不覆盖旧数据。

---

## 待办 / 已知问题

### 高优先级

1. **旧数据无大类信息**
   - 现状：数据库中已有的公告 `ann_type_category` 为空
   - 影响：仪表盘旧公告只显示小类标签
   - 方案：运行批量脚本从现有 `ann_type_tag` 反查大类回填，或删除数据库重新抓取

2. **正文区域未横向铺满网页**
   - 用户反馈：点击公告展开的正文区域没有铺满整个网页
   - 位置：dashboard.py 中 `.ann-text` 的 CSS
   - 建议：检查 `.ann-text` 的 `width`、`max-width`、`margin` 设置，可能需要设为 `width: 100%` 或调整父容器

### 中优先级

3. **港股数据源单一**
   - 当前港股仅通过东方财富 API 获取，可考虑接入港交所披露易（HKEXnews）作为补充

4. **TOC 提取回退策略**
   - 超长文档找不到"目录"标记时，默认返回开头 2000 字。对某些非标准格式文档可能不够理想

5. **LLM 模型可迁移性**
   - 当前使用 DeepSeek V4 Flash（OpenCode Go）。如更换模型，需确认支持 `response_format: {"type": "json_object"}`

### 低优先级

6. **Cookie 过期**
   - `cookie.txt` 会过期（几天到几周）
   - 优先尝试 `python scripts/refresh_cookie.py` 自动续签
   - 失败则手动从浏览器复制：`copy(document.cookie)` 粘贴到 cookie.txt

7. **摘要质量优化**
   - 部分摘要过于精简，丢失重要信息。可考虑：
     - 增大 max_tokens
     - 在 Prompt 中明确要求"不要省略关键数字"
     - 对特定类型（如回购、资产重组）增加专用提取模板

---

## Next Agent Prompt

```
你是 stock-watcher 项目的开发助手。项目是一个东方财富自选股公告追踪系统，用 Python 编写。

**在你开始编码前，必读文件：**
1. /home/administrator/.openclaw/workspace/skills/stock-watcher/README.md — 完整功能文档
2. /home/administrator/.openclaw/workspace/skills/stock-watcher/HANDOVER.md — 本文档（项目状态和关键决策）
3. /home/administrator/.openclaw/workspace/skills/stock-watcher/config.json — 运行时配置

**核心代码文件位置：**
- /home/administrator/.openclaw/workspace/skills/stock-watcher/scripts/stock_watcher.py — 主入口
- /home/administrator/.openclaw/workspace/skills/stock-watcher/scripts/llm_judge.py — LLM判断+分类映射
- /home/administrator/.openclaw/workspace/skills/stock-watcher/scripts/db.py — 数据库
- /home/administrator/.openclaw/workspace/skills/stock-watcher/scripts/daily_summary.py — 摘要生成
- /home/administrator/.openclaw/workspace/skills/stock-watcher/scripts/dashboard.py — Web仪表盘
- /home/administrator/.openclaw/workspace/skills/stock-watcher/scripts/ann_detail.py — PDF下载+正文提取

**重要约束：**
- 数据库是 SQLite，路径 .stock-watcher-state/announcements.db
- LLM API Key 在 .env 文件中（LLM_API_KEY=...），不要硬编码到代码里
- 任何数据库字段变更都要在 db.py 的 _migrate_schema() 中添加迁移逻辑
- 公告分类体系参考万得金融终端，A股8大类64小类，港股7大类41小类，映射字典在 llm_judge.py
- 默认不创建新文件，优先修改现有文件
- 不要添加不必要的注释、类型注解或错误处理

**当前已知问题（按优先级）：**
1. 旧数据 ann_type_category 为空，需要批量回填或重新抓取
2. 仪表盘正文区域未横向铺满网页
3. 部分摘要过于精简，丢失重要信息

**用户习惯：**
- 用户是专业投资者，关注财务数字（金额、比例、股数）
- 对回购、资产重组、股份增减持等类型要求提取详细信息
- 仪表盘标签希望显示 "大类 / 小类" 格式
```
