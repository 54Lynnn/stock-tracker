# 自选股公告监控系统 (stock-watcher)

## 项目概述

自动监控东方财富自选股的最新公告，通过正则表达式与LLM双重过滤筛选有价值的公告，下载PDF提取全文，经正文清洗后由LLM生成摘要，提供Web仪表盘查看所有公告详情。支持A股（巨潮资讯网）与港股（东方财富）双数据源，增量抓取，SQLite持久化存储。

> 通过 ClawHub 安装：`clawhub install stock-watcher-pro`

## 系统架构

完整数据流如下：

```
东方财富 myfavor API --> 自选股列表（股票代码 + 名称 + 市场类型）
    |
    +--> A股 (market=0/1) ---> 东方财富公告API（默认）---> 公告列表
    |                        └-- 可选：巨潮资讯网公告API（--source cninfo）
    |
    +--> 港股 (market=116) ---> 东方财富公告API ---> 公告列表
    |
    v
  正则过滤（SKIP_CONTENT_PATTERNS，14类模式，零成本）
    |
    v
  LLM标题价值判断（可选，同时返回公告类型分类，JSON模式输出）
    |
    v
  PDF下载 + pdfplumber全文提取（失败回退pdftotext）
    |
    v
  超长文档TOC提取（通函/会议资料等只提取目录）
    |
    v
  正文清洗（text_cleaner.py，14类清洗规则，移除模板套话）
    |
    v
  SQLite入库（UPSERT，status=valuable/filtered，ann_type_tag=小类标签，ann_type_category=大类标签）
    |
    v
  LLM批量摘要生成（定期报告跳过LLM直接写固定摘要，其他每1条一批JSON并发调用，workers=20）
    |
    v
  Agent digest 输出 / Flask 仪表盘展示（端口5001）
```

## 文件结构

```
stock-watcher/
  SKILL.md                        # Skill定义文件（Agent调用入口）
  HANDOVER.md                     # 项目交接文档
  README.md                       # 本文档
  config.json                     # 全局配置（通知、LLM、抓取间隔）
  cookie.txt                      # 东方财富登录Cookie（不进git）
  .env                            # LLM API Key（不进git）
  .gitignore                      # 排除敏感文件和状态目录
  run.sh                          # 每日运行脚本（抓取 + 摘要 + digest）
  dashboard.sh                    # 仪表盘模式（抓取 + 摘要 + 启动Web）
  scripts/
    stock_watcher.py              # 主入口脚本（CLI参数解析 + 流程编排）
    eastmoney_api.py              # 东方财富API封装（自选股列表 + 港股公告）
    cninfo_api.py                 # 巨潮资讯网API封装（A股公告列表）
    ann_detail.py                 # 公告正文抓取 + PDF下载 + 正则跳过规则 + TOC提取
    llm_judge.py                  # LLM标题价值判断（OpenAI兼容API）
    text_cleaner.py               # 正文清洗（14类正则规则，移除模板套话）
    daily_summary.py              # 每日摘要生成 + 每日日报推送
    db.py                         # SQLite数据库模块（建表/迁移/CRUD/统计）
    dashboard.py                  # Flask Web仪表盘（默认端口5001，支持PORT环境变量）
    refresh_cookie.py             # Cookie自动续签工具（Playwright）
    backfill_category.py          # 旧数据大类回填（从tag模糊反查category）
    reclassify_all.py             # 全量公告重新分类（清空tag/category后逐条重调LLM）
    setup.sh                      # 环境一键配置脚本
  logs/                           # 运行日志（自动创建，按天轮转）
  .stock-watcher-state/
    announcements.db              # SQLite公告数据库（自动创建和管理）
```

## 核心模块详解

### 1. stock_watcher.py - 主入口

主入口脚本负责参数解析、流程编排和通知推送。

**支持的命令行参数：**

| 参数 | 说明 |
|------|------|
| `--group`, `-g` | 只追踪指定分组（模糊匹配，如"持仓"、"hk"、"自选"） |
| `--source` | 数据来源：`eastmoney`（默认，东方财富全部）或 `cninfo`（巨潮A股+东方财富港股，可选） |
| `--days` | 抓取最近N天的公告（默认取config.json中的fetch_interval_days，值为7） |
| `--fetch-content` | 下载PDF并提取全文存入数据库，同时经过LLM再筛选 |
| `--dry-run` | 试运行，不更新数据库状态 |
| `--force` | 强制重新抓取所有公告（忽略已存在的ann_id） |
| `--list` | 列出历史公告记录 |
| `--list-groups` | 列出所有可用的东方财富自选股分组 |
| `--stats` | 查看数据库统计信息（总量、含正文数、追踪股票数、来源分布） |
| `--stock` | 配合`--list`使用，筛选指定股票代码的公告 |
| `--clean` | 清洗已获取的公告正文（移除模板套话） |
| `--prune` | 清理无正文的空记录（status=filtered且full_text为空） |

**执行流程：**

1. 初始化日志（控制台 + 按天轮转文件，保留30天）
2. 加载config.json配置
3. 根据参数执行对应子命令（stats/list/groups/clean/prune）
4. 获取自选股列表（myfavor API -> Cookie解析 -> config.json，三级回退）
5. 按数据来源分流：默认全部走东方财富（A股+港股），`--source cninfo` 时 A股走巨潮、港股走东方财富
6. 增量检测新公告（比对ann_id）
7. 可选：下载PDF提取全文（fetch_all_contents）
8. 判断status（有全文=valuable，无全文=filtered）
9. UPSERT入库
10. 可选：通知推送（terminal/webhook）

### 2. 数据采集层

#### cninfo_api.py（巨潮资讯网 - A股公告）

巨潮资讯网是证监会指定的上市公司信息披露平台，覆盖沪深京全部A股。

**API端点：**
- 公告列表查询：`POST https://www.cninfo.com.cn/new/hisAnnouncement/query`
- PDF链接基础：`https://static.cninfo.com.cn/{adjunctUrl}`

**请求参数：**
- `searchkey`：股票代码
- `seDate`：日期范围，格式 `YYYY-MM-DD~YYYY-MM-DD`
- `pageNum`/`pageSize`：分页参数
- `column`：固定为`szse`
- `tabName`：固定为`fulltext`

**限制说明：**
- 默认每页30条，最多翻页20页
- 股票间请求间隔默认0.5秒
- 返回的PDF链接需要拼接`https://static.cninfo.com.cn/`前缀

#### eastmoney_api.py（东方财富 - 自选股列表 + 港股公告）

**自选股列表获取（三级回退）：**

1. **myavor API（优先）**：
   - 分组列表：`GET https://myfavor.eastmoney.com/v4/webouter/ggdefstkindexinfos`
   - 分组内股票：`GET https://myfavor.eastmoney.com/v4/webouter/gstkinfos`
   - 需要Cookie中的登录态

2. **Cookie解析（兜底）**：从Cookie的`selfSelectStocks`字段正则解析

3. **config.json手动配置**：`stocks`数组，每项含`code`、`market`、`name`

**公告列表获取：**
- API端点：`GET https://np-anotice-stock.eastmoney.com/api/security/ann`
- 港股参数：`ann_type=H`，A股参数：`ann_type=A`
- 返回公告标题、日期、PDF链接（art_code）

**market字段说明：**

| market值 | 市场 | 代码规则 |
|----------|------|---------|
| `0` | 深市 | 00xxxx（主板/中小板）、30xxxx（创业板） |
| `1` | 沪市 | 60xxxx（主板）、688xxx（科创板） |
| `116` | 港股 | 4-5位数字 |

系统会自动过滤掉指数、ETF、债券、权证等非个股标的，仅保留有公告价值的真实个股。

### 3. 过滤层

系统采用三级过滤架构，在下载PDF之前尽可能多地拦截低价值公告，节省网络和计算资源。

#### 正则过滤（ann_detail.py - SKIP_CONTENT_PATTERNS）

零成本拦截已知低价值公告类型，共14类正则模式：

| 序号 | 匹配模式 | 跳过类型 |
|------|---------|---------|
| 1 | `公司章程` | 公司章程（纯法律模板） |
| 2 | `信用评级` 或 `跟踪评级` | 信用/跟踪评级报告 |
| 3 | `募集说明书` | 债券募集说明书 |
| 4 | `付息公告` | 债券付息公告 |
| 5 | `上市公告`、`上市的公告`、`摘牌` | 债券上市/摘牌公告 |
| 6 | `发行结果公告`、`票面利率`、`簿记建档`、`更名公告`、`发行完毕` | 债券程序性公告 |
| 7 | `董事会报告` | 董事会报告（程序性文件） |
| 8 | `法律意见书`、`法律意见` | 股东会法律意见书 |
| 9 | `股东会决议公告`、`股东会表决结果`、`投票表决结果` | 股东会决议/表决结果 |
| 10 | `薪酬` | 薪酬管理制度 |
| 11 | `周年会通告` | 股东周年会通告 |
| 12 | `担保额度` | 担保额度公告 |
| 13 | `召开情况` | 业绩说明会召开情况 |

> 注意：董事会决议公告未被正则跳过，保留给LLM判断（因可能包含收购、回购等实质决策内容）。

#### LLM标题判断（llm_judge.py）

通过OpenAI兼容API（当前使用DeepSeek V4 Flash）判断正则无法覆盖的边界情况。

**核心机制：**
- 同时返回价值判断（judge: true/false）+ 大类（category）+ 小类（type）
- 代码内置 `A_CATEGORY_MAP`（8大类64小类）和 `HK_CATEGORY_MAP`（7大类41小类）做兜底映射
- 使用`response_format: {"type": "json_object"}`确保结构化输出
- 支持reasoning模型（content为空时从reasoning_content中提取结论）
- 失败时默认视为有价值（fail-open策略）
- 支持重试（默认2次），指数退避

**有价值（保留下载PDF）的类型：** 季度报告、年度报告、业绩预告、收购资产公告、重大合同、人事变动、股权激励、关联交易、回购股份方案及进展公告、投资者关系活动记录表

**无价值（跳过下载）的类型：** 程序性董事会决议、股东大会通知、薪酬管理制度、担保额度公告、会计政策变更、债券付息公告、法律意见书、保荐机构核查意见、分红实施公告、变更会计师事务所

**A股公告分类体系（64种，参考万得金融终端）：**

| 大类 | 子分类 |
|------|--------|
| 招股类 | 申报稿、申报反馈、招股说明书、发行定价、发行结果、上市公告书 |
| 财务报告类 | 业绩预告、业绩快报、季度报告、半年报告、年度报告、补充更正 |
| 重大事项类 | 利润分配、股份增减持、资金投向、资产重组、收购兼并、重大合同、股权激励、关联交易、借贷担保、委托理财、违纪违规、政策影响、人事变动 |
| 交易提示类 | 停牌提示、交易异动、澄清公告、风险提示、特别处理、终止上市、恢复上市、暂停上市 |
| 配股类 | 配股预案、配股说明书、配股获准、配股发行、配股上市 |
| 增发类 | 增发预案、增发说明书、增发获准、增发发行、增发上市 |
| 股权股本类 | 权益变动、股本变动、质押冻结、质押式回购、回购股权、约定购回、股权分置改革 |
| 一般公告类 | 董事会公告、股东大会、权证公告、中介公告、法律纠纷、机构调研公告、其他补充更正、公司资料变更、融资融券、员工持股、产销经营快报、个股其他公告、ESG报告、函件 |

**A股64种公告类型详情（提取重点 + Prompt行为）：**

| # | 大类 | 公告类型 | 提取重点 | Prompt 行为 |
|---|------|---------|---------|------------|
| 1 | 招股 | 申报稿 | 默认（核心内容+关键数字） | LLM 摘要 |
| 2 | 招股 | 申报反馈 | 默认 | LLM 摘要 |
| 3 | 招股 | 招股说明书 | 默认 | LLM 摘要 |
| 4 | 招股 | 发行定价 | 默认 | LLM 摘要 |
| 5 | 招股 | 发行结果 | 默认 | LLM 摘要 |
| 6 | 招股 | 上市公告书 | 默认 | LLM 摘要 |
| 7 | 财务报告 | 业绩预告 | 预计营收/利润、同比增减、变动原因 | 跳过 LLM，固定摘要 |
| 8 | 财务报告 | 业绩快报 | 实际营收/利润、同比增减、与预告差异 | 跳过 LLM，固定摘要 |
| 9 | 财务报告 | 季度报告 | — | 跳过 LLM，固定摘要 |
| 10 | 财务报告 | 半年报告 | — | 跳过 LLM，固定摘要 |
| 11 | 财务报告 | 年度报告 | — | 跳过 LLM，固定摘要 |
| 12 | 财务报告 | 补充更正 | — | 跳过 LLM，固定摘要 |
| 13 | 重大事项 | 利润分配 | 每股分红金额、分红总额、股权登记日 | LLM 摘要（专用重点） |
| 14 | 重大事项 | 股份增减持 | 增减持主体、股数、金额、变动后比例 | LLM 摘要（专用重点） |
| 15 | 重大事项 | 资金投向 | 投资项目、金额、预期收益、资金来源 | LLM 摘要（专用重点） |
| 16 | 重大事项 | 资产重组 | 重组标的、金额、对手、方式 | LLM 摘要（专用重点） |
| 17 | 重大事项 | 收购兼并 | 交易标的、金额、对手、目的 | LLM 摘要（专用重点） |
| 18 | 重大事项 | 重大合同 | 合同金额、对手方、期限、内容 | LLM 摘要（专用重点） |
| 19 | 重大事项 | 股权激励 | 授予/行权价格、人数、限售期、解锁条件 | LLM 摘要（专用重点） |
| 20 | 重大事项 | 关联交易 | 交易对方、金额、定价依据、目的 | LLM 摘要（专用重点） |
| 21 | 重大事项 | 借贷担保 | 担保金额、对象、被担保方财务 | LLM 摘要（专用重点） |
| 22 | 重大事项 | 委托理财 | 理财金额、产品类型、收益率、期限 | LLM 摘要（专用重点） |
| 23 | 重大事项 | 违纪违规 | 违规主体、事项、处罚措施 | LLM 摘要（专用重点） |
| 24 | 重大事项 | 政策影响 | 政策内容、公司影响、应对措施 | LLM 摘要（专用重点） |
| 25 | 重大事项 | 人事变动 | 具体人名、原职务、新职务、原因 | LLM 摘要（专用重点） |
| 26 | 交易提示 | 停牌提示 | 停牌原因、预计时间、复牌条件 | LLM 摘要（专用重点） |
| 27 | 交易提示 | 交易异动 | 异动类型、原因、是否需核查 | LLM 摘要（专用重点） |
| 28 | 交易提示 | 澄清公告 | 澄清事项、事实情况、对股价影响 | LLM 摘要（专用重点） |
| 29 | 交易提示 | 风险提示 | 风险类型、内容、可能影响 | LLM 摘要（专用重点） |
| 30 | 交易提示 | 特别处理 | ST/ST*原因、风险警示、后果 | LLM 摘要（专用重点） |
| 31 | 交易提示 | 终止上市 | 终止原因、退市整理期、投资者保护 | LLM 摘要（专用重点） |
| 32 | 交易提示 | 恢复上市 | 恢复原因、复牌条件、公司现状 | LLM 摘要（默认重点） |
| 33 | 交易提示 | 暂停上市 | 暂停原因、后续安排、风险提示 | LLM 摘要（默认重点） |
| 34 | 配股 | 配股预案 | 默认 | LLM 摘要（默认重点） |
| 35 | 配股 | 配股说明书 | 默认 | LLM 摘要（默认重点） |
| 36 | 配股 | 配股获准 | 默认 | LLM 摘要（默认重点） |
| 37 | 配股 | 配股发行 | 默认 | LLM 摘要（默认重点） |
| 38 | 配股 | 配股上市 | 默认 | LLM 摘要（默认重点） |
| 39 | 增发 | 增发预案 | 增发股数、价格、总额、发行对象 | LLM 摘要（专用重点） |
| 40 | 增发 | 增发说明书 | 默认 | LLM 摘要（默认重点） |
| 41 | 增发 | 增发获准 | 默认 | LLM 摘要（默认重点） |
| 42 | 增发 | 增发发行 | 默认 | LLM 摘要（默认重点） |
| 43 | 增发 | 增发上市 | 默认 | LLM 摘要（默认重点） |
| 44 | 股权股本 | 权益变动 | 变动主体、股数、变动后比例 | LLM 摘要（专用重点） |
| 45 | 股权股本 | 股本变动 | 默认 | LLM 摘要（默认重点） |
| 46 | 股权股本 | 质押冻结 | 主体、股数、占持股比例、质权人 | LLM 摘要（专用重点） |
| 47 | 股权股本 | 质押式回购 | 默认 | LLM 摘要（默认重点） |
| 48 | 股权股本 | 回购股权 | 累计金额、股数、占比、计划进度 | LLM 摘要（专用重点） |
| 49 | 股权股本 | 约定购回 | 默认 | LLM 摘要（默认重点） |
| 50 | 股权股本 | 股权分置改革 | 默认 | LLM 摘要（默认重点） |
| 51 | 一般公告 | 董事会公告 | 议案内容、表决结果、重大决议 | LLM 摘要（专用重点） |
| 52 | 一般公告 | 股东大会 | 默认 | LLM 摘要（默认重点） |
| 53 | 一般公告 | 权证公告 | 默认 | LLM 摘要（默认重点） |
| 54 | 一般公告 | 中介公告 | 默认 | LLM 摘要（默认重点） |
| 55 | 一般公告 | 法律纠纷 | 原告被告、金额、进展、判决 | LLM 摘要（专用重点） |
| 56 | 一般公告 | 机构调研公告 | 调研机构、时间、关注要点 | LLM 摘要（专用重点） |
| 57 | 一般公告 | 其他补充更正 | 默认 | LLM 摘要（默认重点） |
| 58 | 一般公告 | 公司资料变更 | 默认 | LLM 摘要（默认重点） |
| 59 | 一般公告 | 融资融券 | 默认 | LLM 摘要（默认重点） |
| 60 | 一般公告 | 员工持股 | 持股规模、参与人数、股票来源 | LLM 摘要（专用重点） |
| 61 | 一般公告 | 产销经营快报 | 产销量、同比变化、经营亮点 | LLM 摘要（专用重点） |
| 62 | 一般公告 | 个股其他公告 | 默认 | LLM 摘要（默认重点） |
| 63 | 一般公告 | ESG报告 | 环境/社会/治理指标、评级变化 | LLM 摘要（专用重点） |
| 64 | 一般公告 | 函件 | 默认 | LLM 摘要（默认重点） |

统计：跳过 LLM（固定摘要）6种 / LLM 摘要（专用重点）28种 / LLM 摘要（默认重点）30种

**港股公告分类体系（41种）：**

| 大类 | 子分类 |
|------|--------|
| 业绩快报 | 业绩预告、季度业绩、中期业绩、末期业绩、业绩发布会 |
| 财务报告 | 环境及管治报告、季度报告、中期报告、年度报告 |
| 上市文件 | 预览资料、发售以供认购、招股说明书、公开招股、供股、资本化发行、介绍上市、发售现有证券、聆讯资料、其它上市文件 |
| 股权股本 | 权益变动、证券/股本、交易披露、翌日报表、月报表 |
| 公告及通函 | 重大事项、新上市、会议/表决、关联交易、须公布的交易、公司变动、财务资料、杂项 |
| 一般公告 | 交易安排、创业板资料、监管者公告、委任代表表格、宪章文件 |
| 债券及结构性产品 | 权证公告、权证上市、债务证券公告、其它 |

**港股41种公告类型详情（提取重点 + Prompt行为）：**

| # | 大类 | 公告类型 | 提取重点 | Prompt 行为 |
|---|------|---------|---------|------------|
| 1 | 业绩快报 | 业绩预告 | 预计营收/利润、同比增减、变动原因 | 跳过 LLM，固定摘要 |
| 2 | 业绩快报 | 季度业绩 | 实际营收/利润、同比增减、环比变化 | 跳过 LLM，固定摘要 |
| 3 | 业绩快报 | 中期业绩 | 上半年营收/利润、同比增减、中期分红 | 跳过 LLM，固定摘要 |
| 4 | 业绩快报 | 末期业绩 | 全年营收/利润、同比增减、末期分红 | 跳过 LLM，固定摘要 |
| 5 | 业绩快报 | 业绩发布会 | 发布会时间、管理层关键指引、展望 | LLM 摘要（默认重点） |
| 6 | 财务报告 | 环境及管治报告 | ESG评级、关键环境指标、治理改进 | 跳过 LLM，固定摘要 |
| 7 | 财务报告 | 季度报告 | — | 跳过 LLM，固定摘要 |
| 8 | 财务报告 | 中期报告 | — | 跳过 LLM，固定摘要 |
| 9 | 财务报告 | 年度报告 | — | 跳过 LLM，固定摘要 |
| 10 | 上市文件 | 预览资料 | 招股时间表、发行规模、定价范围 | LLM 摘要（默认重点） |
| 11 | 上市文件 | 发售以供认购 | 发售价格、发售数量、集资额、认购安排、对股价影响 | LLM 摘要（专用重点） |
| 12 | 上市文件 | 招股说明书 | 发行主体、发行规模、定价、资金用途、业务概要 | LLM 摘要（默认重点） |
| 13 | 上市文件 | 公开招股 | 招股价格区间、招股数量、集资额、上市日期、保荐人 | LLM 摘要（专用重点） |
| 14 | 上市文件 | 供股 | 供股比例（几供几）、供股价格、募集资金总额、供股目的及资金用途、对现有股东的影响 | LLM 摘要（专用重点） |
| 15 | 上市文件 | 资本化发行 | 发行方式、发行对象、对股本结构的影响、是否涉及大股东 | LLM 摘要（专用重点） |
| 16 | 上市文件 | 介绍上市 | 介绍上市方式、不涉及公开发售的原因、上市日期、保荐人 | LLM 摘要（专用重点） |
| 17 | 上市文件 | 发售现有证券 | 出售股东身份、发售股数、售价、较市价折让幅度、出售原因 | LLM 摘要（专用重点） |
| 18 | 上市文件 | 聆讯资料 | 聆讯结果、聆讯日期、后续时间安排 | LLM 摘要（默认重点） |
| 19 | 上市文件 | 其它上市文件 | 默认 | LLM 摘要（默认重点） |
| 20 | 股权股本 | 权益变动 | 变动主体、变动前持股比例、变动股数、变动后比例、变动方式 | LLM 摘要（专用重点） |
| 21 | 股权股本 | 证券/股本 | 变动原因、变动前后股本结构、新增股份性质 | LLM 摘要（默认重点） |
| 22 | 股权股本 | 交易披露 | 交易类型、交易对手、交易金额、对股权结构的影响 | LLM 摘要（默认重点） |
| 23 | 股权股本 | 翌日报表 | 买入/卖出股数、成交价格、涉及股东名称及身份 | LLM 摘要（专用重点） |
| 24 | 股权股本 | 月报表 | 月度股份变动汇总、各增减持主体及股数、变动后持股比例 | LLM 摘要（专用重点） |
| 25 | 公告及通函 | 重大事项 | 事项内容、对公司及股价的影响 | LLM 摘要（默认重点） |
| 26 | 公告及通函 | 新上市 | 新上市公司名称、主营业务及行业、发行规模及定价、集资用途、上市后表现预期 | LLM 摘要（专用重点） |
| 27 | 公告及通函 | 会议/表决 | 会议类型、议案内容、表决结果 | LLM 摘要（默认重点） |
| 28 | 公告及通函 | 关联交易 | 交易对方（关联方关系）、交易金额、定价依据、交易目的 | LLM 摘要（专用重点） |
| 29 | 公告及通函 | 须公布的交易 | 交易性质（收购/出售/合营）、交易金额、交易对手、是否构成重大交易、对财务状况的影响 | LLM 摘要（专用重点） |
| 30 | 公告及通函 | 公司变动 | 变动内容（董事/秘书/注册地等）、生效日期 | LLM 摘要（默认重点） |
| 31 | 公告及通函 | 财务资料 | 关键财务数据、同比变化、重大财务事项 | LLM 摘要（默认重点） |
| 32 | 公告及通函 | 杂项 | 默认 | LLM 摘要（默认重点） |
| 33 | 一般公告 | 交易安排 | 关键日期（除净日、过户截止日、派息日）、每股派息金额 | LLM 摘要（专用重点） |
| 34 | 一般公告 | 创业板资料 | 创业板上市/转板相关信息 | LLM 摘要（默认重点） |
| 35 | 一般公告 | 监管者公告 | 监管机构名称、监管事项、处罚/警告措施、对公司经营的影响 | LLM 摘要（专用重点） |
| 36 | 一般公告 | 委任代表表格 | 委任代表信息、授权范围 | LLM 摘要（默认重点） |
| 37 | 一般公告 | 宪章文件 | 章程修订内容、修订原因 | LLM 摘要（默认重点） |
| 38 | 债券及结构性产品 | 权证公告 | 权证标的证券、行权价格、到期日、杠杆比率、实际杠杆 | LLM 摘要（专用重点） |
| 39 | 债券及结构性产品 | 权证上市 | 权证条款（标的/行权价/到期日）、上市日期、初始价格 | LLM 摘要（专用重点） |
| 40 | 债券及结构性产品 | 债务证券公告 | 债券规模、票面利率、期限、信用评级、担保情况、募集资金用途 | LLM 摘要（专用重点） |
| 41 | 债券及结构性产品 | 其它 | 默认 | LLM 摘要（默认重点） |

港股统计：跳过 LLM（固定摘要）9种 / LLM 摘要（专用重点）17种 / LLM 摘要（默认重点）15种

market字段决定使用哪套分类体系（A股用market=0/1，港股用market=116）。

### 4. PDF处理层（ann_detail.py）

**获取策略（优先级）：**

1. **东方财富内容API（优先）**：调用 `https://np-cnotice-stock.eastmoney.com/api/content/ann`，返回纯文本正文
2. **PDF下载回退**：API失败时下载PDF并提取文本
   - 东方财富PDF：`https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf`
   - 巨潮PDF（可选数据源）：`https://static.cninfo.com.cn/{adjunctUrl}`
3. **提取工具**：pdfplumber（主）-> pdftotext（备）

**超长文档TOC提取：**

对于通函、海外市场公告、股东会会议资料、发行公告等超长文档（full_text > 5000字），只提取目录部分（最多2000字），`attach_url`保留原始PDF链接。策略如下：

1. 查找"目录"标记，截取到其后的条目列表末尾
2. 没有目录标记时，保留开头2000字（通常包含提案摘要）
3. 最终不超过2000字

**批量保存机制：** 每处理 `batch_size` 条公告后自动调用 `save_batch` 回调函数保存进度到数据库，防止中断丢失。默认 `batch_size=10`。

### 5. 正文清洗（text_cleaner.py）

对PDF提取的原始文本进行模板套话清理，共14类清洗规则按顺序执行：

| 序号 | 清洗对象 | 说明 |
|------|---------|------|
| 1 | 股票代码表头行 | 如"证券代码:600961 证券简称:株冶集团 公告编号:2026-021" |
| 2 | 纯公司名行 | 独立的公司全称行（非段落开头） |
| 3 | 董事会/监事会免责声明 | "本公司及董事会全体成员保证..."等10个变体 |
| 4 | "特此公告"结语 + 日期落款 | 支持阿拉伯数字和中文数字日期 |
| 5 | 股票信息行 | 如"股票简称:申万宏源 股票代码:000166" |
| 6 | "重要内容提示"标记 | 保留下方内容，只移除标题 |
| 7 | PDF页脚/分页噪声 | 页码（"第1页 共3页"）、水印文字 |
| 8 | 声明板块 | 年报/公告PDF中固定的声明文本 |
| 9 | 投资者关系活动记录表头 | 证券代码行 + "投资者关系活动记录表" |
| 10 | 地址/联系信息模板 | 住所、邮编、联系电话、传真 |
| 11 | 募集说明书评级/担保字段 | 信用评级、增信措施、承销商信息等 |
| 12 | 目录页 | 年报/股东会资料中的模板目录 |
| 13 | H股表格模板文字 | 翌日披露报表、表格类别等繁体模板 |
| 14 | 多余空白行 | 保留最多一个连续空行 |

清洗后的文本存储在`clean_text`字段，原始文本保留在`full_text`字段。

### 6. 摘要生成（daily_summary.py）

**两阶段处理：**

**第一阶段 - 单条摘要生成：**
- 查询所有`summary`字段为空且`clean_text`非空的公告
- 定期报告类（业绩预告、业绩快报、季度报告、半年报告、年度报告、补充更正）跳过LLM，直接写固定摘要：`【{类型}】{标题}`
- 其他类型公告：每1条一批（`BATCH_SIZE=1`），20并发（`workers=20`），构建包含股票名、标题、正文（前3000字）的prompt，以JSON模式调用LLM生成摘要
- 摘要要求包含关键数字（金额、比例、股数等），不同类型公告有不同侧重点

**第二阶段 - Digest 输出（可选，--digest参数）：**
- 查询过去24小时有价值公告的 summary
- 格式化为编号列表输出到 stdout（供 agent 读取转发）
- 不再调用 LLM 做汇总，直接输出已有摘要

**LLM调用方式：**
- 使用DeepSeek JSON Mode
- prompt中明确指定JSON输出格式
- 解析失败时有正则回退（逐行匹配ann_id + summary格式）

### 7. 数据库（db.py）

**存储方式：** SQLite，文件路径 `.stock-watcher-state/announcements.db`

**SQLite配置：**
- WAL模式（并发读写优化）
- 外键约束开启

**announcements表结构：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `ann_id` | TEXT PRIMARY KEY | SHA256唯一标识（stock_code + art_code + notice_id + title） |
| `stock_code` | TEXT NOT NULL | 股票代码 |
| `stock_name` | TEXT | 股票中文名称 |
| `title` | TEXT | 公告标题 |
| `ann_date` | TEXT | 公告日期 |
| `ann_type` | TEXT | 公告类型（A/H） |
| `url` | TEXT | 公告页面或PDF链接 |
| `art_code` | TEXT | 文章编码（东方财富） |
| `notice_id` | TEXT | 实际存储公告日期（兼容旧数据，非ID） |
| `full_text` | TEXT | 原始正文（PDF提取） |
| `clean_text` | TEXT | 清洗后正文（移除模板套话） |
| `attach_url` | TEXT | 原始PDF附件链接 |
| `status` | TEXT | 状态：`valuable`（有价值）/ `filtered`（已过滤） |
| `ann_type_tag` | TEXT | 小类标签（如"回购"、"季度报告"） |
| `ann_type_category` | TEXT | 大类标签（如"股权股本类"、"财务报告类"） |
| `summary` | TEXT | LLM生成的单条摘要 |
| `first_seen_at` | TEXT | 首次入库时间（自动填充，localtime） |

**索引：** stock_code、ann_date、first_seen_at、ann_type

**UPSERT逻辑：**
- INSERT ... ON CONFLICT(ann_id) DO UPDATE SET
- 空值保护：`CASE WHEN excluded.xxx != '' THEN excluded.xxx ELSE announcements.xxx END`
- 即新数据为空时不覆盖旧数据

**数据库迁移：** 自动检测并新增字段（full_text、clean_text、attach_url、summary、status、ann_type_tag、ann_type_category），兼容旧版数据库。

**JSON迁移：** 如果`.stock-watcher-state/seen_announcements.json`存在且数据库为空，自动从JSON迁移记录。

### 8. Web仪表盘（dashboard.py）

基于Flask的轻量Web应用，默认监听端口5001，支持通过 `PORT` 环境变量自定义端口。

**功能：**
- 股票列表表格：显示股票名称、代码，以及7天/15天/30天/全部的有价值公告数与总数比例
- 点击展开：懒加载该股票的公告详情（标题、日期、摘要、清洗后正文、原文链接）
- 类型标签：摘要右侧显示 `大类 / 小类` 标签（如 `股权股本类 / 回购`），旧数据无大类时只显示小类
- 响应式设计，支持移动端

**API端点：**

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 仪表盘首页（内嵌HTML模板） |
| `/api/stocks` | GET | 返回所有股票的概览统计（JSON） |
| `/api/announcements/<stock_code>` | GET | 返回指定股票最近30天有价值的公告列表（JSON） |

**数据查询：**
- 股票概览：按stock_code分组统计，包含7天/15天/30天/全部维度的valuable和total计数
- 公告详情：只返回status='valuable'的公告，包含clean_text和summary

## Token消耗分析

| 操作 | Token消耗 | 说明 |
|------|----------|------|
| LLM标题判断 | 每条约100 token（输入+输出） | 每条公告标题独立调用，返回judge+type |
| LLM摘要生成 | 每条约1500 token输入，约500 token输出 | 正文截取前3000字，BATCH_SIZE=1 |
| 定期报告跳过LLM | 0 token | 业绩预告/快报/季报/半年报/年报/补充更正直接写固定摘要 |
| Digest 输出 | 0 token | 直接输出已有 summary，不调用 LLM |
| 没有新公告时 | 0 token | 正则过滤和增量检测均不消耗API |

**成本优化策略：**
- 三级过滤架构：正则零成本拦截 -> LLM精筛 -> 才下载PDF
- 定期报告类公告跳过LLM摘要
- 每1条一批（`BATCH_SIZE=1`），20并发（`workers=20`）批量调用LLM，速度快且不超时
- 增量抓取：已存在的ann_id直接跳过
- 超长文档只提取目录，减少后续摘要的token输入

## 配置说明

### config.json

```json
{
  "notify": {
    "type": "terminal",        // 通知方式：terminal（终端输出）或 webhook（飞书/钉钉）
    "webhook_url": ""          // Webhook URL（type=webhook时必填）
  },
  "fetch_interval_days": 7,    // 默认抓取天数
  "stocks": [],                // 手动指定自选股列表（仅在API和Cookie均失败时使用）
  "llm": {
    "enabled": true,           // 是否启用LLM标题判断
    "base_url": "https://opencode.ai/zen/go/v1",  // OpenAI兼容API地址
    "model": "deepseek-v4-flash",                  // 模型名称
    "timeout": 60,             // 请求超时（秒）
    "retries": 2               // 失败重试次数
  }
}
```

### .env文件

位于项目根目录，存放敏感的LLM API Key，不纳入git版本控制：

```
LLM_API_KEY=sk-your-api-key-here
```

### cookie.txt

东方财富网页版登录后的Cookie，用于调用myfavor API获取自选股列表和公告数据。

**获取方法：**
1. 浏览器打开 `https://quote.eastmoney.com/zixuan/lite.html` 并登录
2. 按 F12 -> Console -> 输入 `copy(document.cookie)` 回车
3. 粘贴到 `cookie.txt`

**过期处理：**
- 优先尝试自动续签：`python3 scripts/refresh_cookie.py`（需要playwright）
- 自动续签失败时，手动从浏览器复制覆盖

## 使用方法

### 首次运行

```bash
# 1. 一键配置环境（安装依赖、创建目录、设置crontab）
bash scripts/setup.sh

# 2. 配置Cookie（手动或自动续签）
python3 scripts/refresh_cookie.py
# 或手动写入 cookie.txt

# 3. 配置LLM（可选，在.env中填入API Key）
echo "LLM_API_KEY=sk-xxx" > .env

# 4. 完整流程测试（默认东方财富数据源）
python3 scripts/stock_watcher.py --group test --days 15 --fetch-content

# 可选：使用巨潮资讯网作为A股数据源
python3 scripts/stock_watcher.py --source cninfo --group mygroup --days 15 --fetch-content

# 5. 生成摘要
python3 scripts/daily_summary.py --group mygroup --digest
```

### 日常运行

```bash
# 完整流程（默认东方财富数据源）
python3 scripts/stock_watcher.py --group mygroup --days 15 --fetch-content

# 可选：使用巨潮资讯网作为A股数据源
python3 scripts/stock_watcher.py --source cninfo --group mygroup --days 15 --fetch-content

# 只拉公告列表（不下载PDF）
python3 scripts/stock_watcher.py --group mygroup --days 15

# 补抓缺少全文的公告
python3 scripts/stock_watcher.py --fetch-content

# 手动清洗正文
python3 scripts/stock_watcher.py --clean

# 查看统计
python3 scripts/stock_watcher.py --stats

# 查看公告历史
python3 scripts/stock_watcher.py --list --stock 600519 --days 30

# 列出所有可用分组
python3 scripts/stock_watcher.py --list-groups
```

### 查看仪表盘

```bash
python3 scripts/dashboard.py
# 浏览器访问 http://localhost:5001

# 自定义端口（如5001被占用）
PORT=5003 python3 scripts/dashboard.py
```

### 一键仪表盘模式

```bash
bash dashboard.sh mygroup 15 eastmoney
# 自动执行：抓取 → 摘要 → 启动仪表盘
```

### 数据修复（可选）

当公告分类标签出现不精确或需要全量重分类时：

```bash
# 预览影响范围
python3 scripts/reclassify_all.py --dry-run

# 执行全量重分类（清空所有tag/category后逐条重调LLM）
python3 scripts/reclassify_all.py

# 仅批量回填大类（从现有小类tag反查category，不调用LLM）
python3 scripts/backfill_category.py
```

### 定时任务（agent cronjob）

推荐通过 agent 配置定时任务，每天运行 `run.sh`：

```bash
# 每天早上8点运行一次
openclaw cron add \
  --name stock-watcher \
  --cron "0 0 * * *" \
  --message "运行股票公告扫描：cd {{SKILL_DIR}} && bash run.sh mygroup 15 eastmoney"
```

也可以直接使用 `run.sh` 脚本手动执行抓取+摘要：

```bash
bash run.sh mygroup 15 eastmoney
```

## 依赖

- Python 3.9+
- requests - HTTP请求库
- pdfplumber - PDF文本提取
- flask - Web仪表盘框架
- sqlite3 - 数据库（Python内置）
- playwright（可选） - Cookie自动续签

安装命令：

```bash
pip install requests pdfplumber flask
# 可选：Cookie自动续签
pip install playwright && playwright install chromium
```
