# Standard Collector

`tools/standard-collector/scripts/collect_national_pdfs.py` 是独立的历史 PDF 采集脚本。

它默认会：

- 读取项目根目录 `.env`
- 发现国家标准列表
- 下载 PDF
- 上传到 `STANDARD_LIBRARY_OBJECT_STORE_BUCKET`，默认 `octopus-standard-library`
- 写入新标准库 `STANDARD_LIBRARY_DATABASE_URL` 指向的数据库，默认 `octopus_standard_library`
- 写入新库 `standards`
- 写入新库 `standard_sync_jobs`
- 写入新库 `standard_sync_items`
- 为可下载 PDF 创建新库 `standard_processing_jobs(job_type='materialize', status='pending')`
- 自动跳过已经入库的标准

注意：历史采集不再写旧业务库里的 `app.models.entities.Standard`。后续解析、索引、Atlas 也必须使用新标准库脚本 `process_standard_library_jobs.py`，不要再使用旧脚本 `materialize_and_index_standards.py` 处理这批数据。

`--resume` 已经不再需要了，脚本默认就是断点续跑式的行为。

## 一、`.env` 里的默认参数

这些参数建议长期放在 `.env` 里，不需要每次手敲：

- `STANDARD_COLLECTOR_REQUEST_INTERVAL_SECONDS`
  - 发现列表或处理单条标准之间的默认间隔
- `STANDARD_COLLECTOR_MAX_RETRIES`
  - 单条标准下载 / 上传失败后的默认重试次数
- `STANDARD_COLLECTOR_RETRY_BACKOFF_SECONDS`
  - 每次重试前的默认等待秒数
- `STANDARD_COLLECTOR_LOG_FILE`
  - 默认日志文件路径

相关的共享配置仍然来自现有环境变量：

- `OPENSTD_SOURCE_URL`
- `OPENSTD_CRAWL_SCOPE`
- `OPENSTD_ALLOWED_STATUSES`
- `OPENSTD_DOWNLOAD_TIMEOUT_SECONDS`
- `OPENSTD_IMPORTER_TOOL_DIR`
- `STANDARD_WORKDIR`
- `STANDARD_LIBRARY_OBJECT_STORE_BUCKET`

## 二、标准分类字段规范

当前标准主表只保留两级分类：

- `standard_type`
  - 一级分类
  - 取值示例：`national`、`industry`、`local`、`international`
- `standard_category`
  - 二级分类
  - 具体含义由 `standard_type` 决定

建议约定如下：

- `standard_type = national`
  - `standard_category = mandatory`
  - `standard_category = recommended`
  - `standard_category = guidance`
  - `standard_category = national` 作为兜底
- `standard_type = industry`
  - `standard_category` 存行业领域名称，比如 `档案`、`兵工`、`交通`、`机械`
- `standard_type = local`
  - `standard_category` 存地区名称，比如 `内蒙古自治区`、`北京市`
- `standard_type = international`
  - `standard_category` 存国际组织或体系名称，比如 `ISO`、`IEC`

这套规则的目的很简单：

- 先只解决“二级分类”
- 不把国家标准专用后缀写死进二级字段
- 后面加行业、地方、国际标准时，不需要改表结构

## 二、保留的命令行参数

### 1. 命令行覆盖参数

- `--request-interval`
- `--max-retries`
- `--retry-backoff-seconds`
- `--log-file`

这类参数用于临时覆盖 `.env` 默认值。比如你想做一次更快的测试，或者临时把日志写到别的文件。

### 2. 运行模式参数

- `--dry-run`
- `--retry-failed`
- `--failed-limit`

这类参数决定脚本这次是“只看不写”、“只重试失败项”，还是正常全量执行。

### 3. 调试范围参数

- `--max-pages`
- `--max-items`

这类参数只用于缩小采集范围，方便联调和小批量验证。
不传就是全量采集。

## 三、参数怎么搭配

### 1. 正常全量采集

适合第一次跑历史数据，或者后面做定期更新。

```powershell
python tools/standard-collector/scripts/collect_national_pdfs.py
```

如果你想临时调慢一点，可以加覆盖参数：

```powershell
python tools/standard-collector/scripts/collect_national_pdfs.py --request-interval 8
```

### 2. 小范围联调

适合先看流程是否通、数据库是否写对、MinIO 是否正常。

```powershell
python tools/standard-collector/scripts/collect_national_pdfs.py --max-pages 1 --max-items 20
```

如果还想只看结果不落库，用：

```powershell
python tools/standard-collector/scripts/collect_national_pdfs.py --dry-run --max-pages 1 --max-items 20
```

### 3. 失败项重试

适合前一次跑中断、下载失败、上传失败后，重新拉失败项。

```powershell
python tools/standard-collector/scripts/collect_national_pdfs.py --retry-failed
```

只重试前 N 条失败项：

```powershell
python tools/standard-collector/scripts/collect_national_pdfs.py --retry-failed --failed-limit 200
```

### 4. 需要更快的本地测试

适合确认逻辑，不适合长期跑大任务。

```powershell
python tools/standard-collector/scripts/collect_national_pdfs.py --request-interval 1 --max-retries 1 --retry-backoff-seconds 1 --max-pages 1 --max-items 10
```

## 四、重试逻辑

- 单条标准在同一次运行里会按 `--max-retries` 重试
- 两次重试之间等待 `--retry-backoff-seconds`
- 下载失败、上传失败、无效 PDF 都会记录到 `standard_sync_items`
- 下一次运行时，`--retry-failed` 只会捞出历史失败项重新处理

***
所以全量下载脚本和错误重试也算是很鲁棒了：
因为 standard_sync_items 表里面其实已经记录了每一次的下载状态，包括错误信息
而错误重试是对standard_sync_items中所有错误状态进行重试
==》 因此不会漏掉标准的
***

### 4.1 正常全量运行时，什么标准会被处理

正常全量运行时，脚本的判断依据是 `standards` 主表：

```text
官网发现了这条标准
-> 查 standards 主表
-> standards 里查不到
-> 认为还需要处理
```

这类记录可能包括：

- 从没见过的新标准
- 之前下载失败的标准
- 之前上传失败的标准
- 之前下载到的文件不是有效 PDF 的标准
- 之前官网详情页暂时没有可下载 PDF 的标准
- 之前脚本中断，还没来得及入库的标准

原因是这些标准都没有成功进入 `standards` 主表。

如果 `standards` 主表里已经能查到这条标准，脚本会认为它已经成功采集过：

```text
standards 里查得到
-> unchanged
-> 不下载
-> 不上传
-> 不新增 standard_sync_items
```

### 4.2 `--retry-failed` 和正常全量重跑的区别

`--retry-failed` 不重新扫描官网列表页，它只从 `standard_sync_items` 里找历史失败项重试。

当前只会重试这三类：

```text
download_failed
upload_failed
invalid_pdf
```

不会重试这些：

```text
not_downloadable / skipped
registered
new
unchanged
```

也就是说：

```text
运行 --retry-failed 是否重试历史失败：
看 standard_sync_items。

正常全量重跑是否再次处理：
看 standards 主表有没有这条标准。
没有就处理，有就 unchanged。
```

## 五、日志位置

默认日志文件：

```text
tools/standard-collector/logs/collect_national_pdfs.log
```

如果临时想换日志文件，就用 `--log-file` 覆盖。

### 6. Discovery timeout

For full historical collection, discovery can take a long time. Keep:

```text
STANDARD_COLLECTOR_DISCOVER_TIMEOUT_SECONDS=0
```

`0` means no parent-process timeout for the discovery phase. This does not change the per-PDF download timeout, which is still controlled by `OPENSTD_DOWNLOAD_TIMEOUT_SECONDS`.

### 7. Streaming mode

The collector now works in streaming mode by default:

- discover one page
- process that page immediately
- then continue to the next page

That means a long historical run will start writing PDFs and database rows before discovery finishes for the whole dataset.

脚本跑完后会额外输出一段 `summary`，里面会直接给出上传成功、下载失败、上传失败、跳过和总条数。

## 8. `effective_date` 回填脚本

如果你已经跑过一部分历史采集，但旧数据里的 `effective_date` 还是空的，可以单独跑这个回填脚本：

```powershell
python tools/standard-collector/scripts/backfill_standard_effective_dates.py
```

常用参数：

- `--limit 200`
  - 先小批量验证
- `--dry-run`
  - 只看会补哪些记录，不写库
- `--request-interval 3`
  - 控制每条详情页之间的请求间隔

## 9. 材料化和索引脚本

`tools/standard-collector/scripts/process_standard_library_jobs.py` 是新标准库历史标准的第二阶段处理脚本。

它不会重新爬官网，也不会下载新的 PDF。它只消费 `standards` 主表里已经有 PDF 的标准：

```text
standards.source_pdf_object_key != ''
standard_processing_jobs.job_type = 'materialize'
standard_processing_jobs.status = 'pending'
```

处理流程是：

```text
从 standard_processing_jobs 抢一条 materialize pending 任务
-> 读取新库 standards.source_pdf_object_key
-> 调材料化能力生成 4 个 markdown
-> 上传 markdown 到 STANDARD_LIBRARY_OBJECT_STORE_BUCKET
-> 创建 index pending 任务
-> 用 overview markdown 建 embedding 索引
-> 写 standard_indexes
-> 更新 standards.materialize_status / standards.index_status
```

历史 PDF 下载脚本可以继续运行：

```powershell
python tools/standard-collector/scripts/collect_national_pdfs.py
```

另开一个终端运行材料化和索引脚本：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --watch
```

这样下载脚本会持续把新 PDF 写入 `standards`，材料化索引脚本会持续消费已经写入 `standards` 的记录。

### 9.1 参数说明

- `--watch`
  - 没有待处理标准时不退出，等待下载脚本继续写入新标准。
- `--sleep-seconds`
  - `--watch` 模式下没有任务时等待多少秒，默认 `60`。
- `--limit`
  - 最多处理多少条标准，`0` 表示不限制。小批量测试时建议使用。
- `--materialize-only`
  - 只生成 4 个 markdown，不建立 embedding 索引。
- `--index-only`
  - 只给已经材料化完成的标准建立索引。
- `--atlas`
  - 材料化和索引处理完后，额外生成一次 Atlas 投影。
- `--atlas-only`
  - 只生成一次 Atlas 投影，不消费材料化和索引任务。
- `--log-file`
  - 日志文件路径。默认写入：

```text
tools/standard-collector/logs/process_standard_library_jobs.log
```

### 9.2 默认候选规则

脚本不再按旧库的状态字段直接扫描标准，而是消费新库 `standard_processing_jobs` 里的待处理任务。

历史采集下载 PDF 成功后会创建：

```text
job_type = materialize
status = pending
```

材料化成功后会自动创建：

```text
job_type = index
status = pending
```

默认完整模式会按这个顺序消费：

```text
materialize pending job
-> 生成 overview / structure / logic / body 4 个 markdown
-> 写入 MinIO bucket: STANDARD_LIBRARY_OBJECT_STORE_BUCKET
-> materialize_status = materialized
-> 创建 index pending job
-> 读取 overview markdown
-> 写入 standard_indexes
-> index_status = indexed
```

如果传入 `--materialize-only`，脚本只消费材料化任务，不消费索引任务。

如果传入 `--index-only`，脚本只消费已经排好的索引任务。

### 9.3 失败重试规则

失败任务会保留在 `standard_processing_jobs` 中：

```text
status = failed
stage = failed
```

对应标准的相关状态也会更新为失败，例如：

```text
materialize_status = failed
index_status = failed
```

当前脚本只消费 `pending` 任务，不会自动反复重试 `failed`，避免坏 PDF 或错误配置反复消耗模型/API。修复配置或数据后，需要重新排一个材料化/索引任务再处理。

### 9.4 常用命令组合

先小批量完整测试 1 条：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --limit 1
```

再测试 3 条：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --limit 3
```

下载脚本运行期间，持续处理已经下载好的标准：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --watch
```

没有任务时每 30 秒检查一次：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --watch --sleep-seconds 30
```

只生成 markdown，暂时不建索引：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --materialize-only --watch
```

后续只补索引：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --index-only --watch
```

继续处理待处理项，先限制 20 条：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --limit 20
```

只处理已经排队的索引任务：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --index-only --limit 20
```

### 9.5 运行前需要确认的配置

材料化阶段需要：

```text
STANDARD_LIBRARY_OBJECT_STORE_BUCKET
OBJECT_STORE_ENDPOINT
OBJECT_STORE_ACCESS_KEY
OBJECT_STORE_SECRET_KEY
QWEN_TEXT_API_KEY
QWEN_TEXT_BASE_URL
QWEN_TEXT_MODEL
QWEN_TEXT_MAX_INPUT_CHARS
QWEN_STANDARD_BODY_MAX_TOKENS
QWEN_STANDARD_STRUCTURE_MAX_TOKENS
QWEN_STANDARD_LOGIC_MAX_TOKENS
QWEN_STANDARD_OVERVIEW_MAX_TOKENS
QWEN_TEXT_TIMEOUT_SECONDS
STANDARD_WORKDIR
```

索引阶段还需要：

```text
STANDARD_LIBRARY_DATABASE_URL 使用 PostgreSQL
PostgreSQL 已启用 pgvector
STANDARD_EMBEDDING_API_KEY
STANDARD_EMBEDDING_BASE_URL
STANDARD_EMBEDDING_MODEL
STANDARD_EMBEDDING_DIMENSIONS
STANDARD_EMBEDDING_TIMEOUT_SECONDS
```

如果只想先生成 markdown，不建索引，可以使用：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --materialize-only --watch
```

### 9.6 状态流转

材料化成功：

```text
standard_processing_jobs.job_type = materialize
standard_processing_jobs.status: pending -> running -> completed
standards.materialize_status: pending -> materializing -> materialized
standards.index_status: pending
```

索引成功：

```text
standard_processing_jobs.job_type = index
standard_processing_jobs.status: pending -> running -> completed
standards.index_status: pending -> indexing -> indexed
```

材料化失败：

```text
materialize_status = failed
materialize_error = 错误信息
```

索引失败：

```text
materialize_status = materialized
index_status = failed
index_error = 错误信息
```

脚本抢到任务后会先把任务状态改成 `running`，同时把标准加工状态改成 `materializing` 或 `indexing`，再开始慢任务处理，用来避免重复处理同一条标准。

## 10. 国家标准周期更新脚本

`tools/standard-collector/scripts/sync_national_updates.py` 是国家标准周期更新的第一版脚本，和历史全量 PDF 采集脚本分开使用。

它只处理两类变化：

```text
1. 新增标准
2. 状态变化
```

它暂时不处理：

```text
PDF 内容变化
metadata_fingerprint
行业标准、地方标准、国际标准
```

### 10.1 默认配置

这些参数放在 `.env` 尾部：

```text
STANDARD_UPDATE_INTERVAL_SECONDS=1800
STANDARD_UPDATE_NATIONAL_ENABLED=true
STANDARD_UPDATE_INDUSTRY_ENABLED=false
STANDARD_UPDATE_LOCAL_ENABLED=false
STANDARD_UPDATE_INDUSTRY_CATEGORIES=
STANDARD_UPDATE_LOCAL_CATEGORIES=
STANDARD_UPDATE_SACINFO_REQUIRE_CATEGORIES=true
STANDARD_UPDATE_SACINFO_STATUS=
STANDARD_UPDATE_SACINFO_PAGE_SIZE=50
STANDARD_UPDATE_SACINFO_MAX_PAGES=1
STANDARD_UPDATE_SACINFO_MAX_ITEMS=50
STANDARD_UPDATE_SACINFO_DOWNLOAD_PDFS=true
STANDARD_UPDATE_SACINFO_PROCESSING_LIMIT=0
STANDARD_UPDATE_SACINFO_REFRESH_ATLAS=true
STANDARD_UPDATE_REQUEST_INTERVAL_SECONDS=3
STANDARD_UPDATE_MAX_RETRIES=2
STANDARD_UPDATE_RETRY_BACKOFF_SECONDS=3
STANDARD_UPDATE_MAX_PAGES_SAFETY=0
STANDARD_UPDATE_KNOWN_PAGE_STOP_COUNT=2
STANDARD_UPDATE_CHECK_UPCOMING=true
STANDARD_UPDATE_UPCOMING_LIMIT=0
STANDARD_UPDATE_ACTIVE_CHECK_LIMIT=0
STANDARD_UPDATE_NEW_MATERIALIZE_LIMIT=0
STANDARD_UPDATE_LOG_FILE=./tools/standard-collector/logs/sync_national_updates.log
```

参数含义：

```text
STANDARD_UPDATE_INTERVAL_SECONDS
  --watch 模式下每轮间隔秒数。当前默认 1800 秒，也就是 30 分钟。

STANDARD_UPDATE_NATIONAL_ENABLED
  scheduler 是否运行国家标准周期更新。默认 true。

STANDARD_UPDATE_INDUSTRY_ENABLED
  scheduler 是否运行行业标准周期更新。默认 false。

STANDARD_UPDATE_LOCAL_ENABLED
  scheduler 是否运行地方标准周期更新。默认 false。

STANDARD_UPDATE_INDUSTRY_CATEGORIES
  行业标准周期更新分类，逗号分隔。示例：YD:通信,JT:交通。

STANDARD_UPDATE_LOCAL_CATEGORIES
  地方标准周期更新分类，逗号分隔。示例：山西省,北京市。

STANDARD_UPDATE_SACINFO_REQUIRE_CATEGORIES
  行业/地方周期更新是否要求显式配置分类。默认 true，避免启动后大范围扫描。

STANDARD_UPDATE_SACINFO_STATUS
  行业/地方周期更新官网状态过滤。空值表示不过滤。

STANDARD_UPDATE_SACINFO_PAGE_SIZE
  行业/地方周期更新每页条数。

STANDARD_UPDATE_SACINFO_MAX_PAGES
  行业/地方周期更新每个分类最多扫描页数。默认 1。

STANDARD_UPDATE_SACINFO_MAX_ITEMS
  行业/地方周期更新每轮最多处理条数。默认 50，0 表示不限制。

STANDARD_UPDATE_SACINFO_DOWNLOAD_PDFS
  行业/地方周期更新发现可下载 PDF 时是否下载并写入 MinIO。

STANDARD_UPDATE_SACINFO_PROCESSING_LIMIT
  行业/地方周期更新后最多自动消费多少个本轮创建的解析/索引任务。0 表示不限制。

STANDARD_UPDATE_SACINFO_REFRESH_ATLAS
  行业/地方周期更新产生新索引后是否自动刷新 Atlas。

STANDARD_UPDATE_REQUEST_INTERVAL_SECONDS
  访问官网列表页/详情页之间的间隔秒数。

STANDARD_UPDATE_MAX_RETRIES
  新增标准下载或上传失败时的单条重试次数。

STANDARD_UPDATE_RETRY_BACKOFF_SECONDS
  重试退避基准秒数。第 n 次失败后等待 retry_backoff_seconds * n。

STANDARD_UPDATE_MAX_PAGES_SAFETY
  新增扫描的安全页数上限。0 表示不限制页数，负数表示不扫最近新增。

STANDARD_UPDATE_KNOWN_PAGE_STOP_COUNT
  按发布日期倒序扫描时，连续多少页没有新增就停止。默认 2，避免只凭固定 N 页漏掉 N+1 页新增。

STANDARD_UPDATE_CHECK_UPCOMING
  是否核验即将实施且已到实施日期的标准。

STANDARD_UPDATE_UPCOMING_LIMIT
  每轮最多核验多少条 upcoming 标准。0 表示不限制，负数表示禁用 upcoming 核验。

STANDARD_UPDATE_ACTIVE_CHECK_LIMIT
  每轮最多轮转核验多少条 active 标准，用来发现现行 -> 废止。0 表示不限制，负数表示禁用 active 核验。

STANDARD_UPDATE_NEW_MATERIALIZE_LIMIT
  每轮最多对多少条新增标准立即材料化并建立索引。0 表示不限制。

STANDARD_UPDATE_LOG_FILE
  周期更新脚本日志文件。
```

### 10.2 新增标准扫描逻辑

脚本不是固定只扫前 N 页，而是按发布日期倒序一直扫描到“已知边界”：

```text
第 1 页开始扫描
  有新增 -> 下载、入库、材料化、索引，继续下一页
  没有新增 -> known_page_count + 1

连续 STANDARD_UPDATE_KNOWN_PAGE_STOP_COUNT 页都没有新增
  停止新增扫描

如果 STANDARD_UPDATE_MAX_PAGES_SAFETY > 0，达到该页数
  安全停止
```

新增判断仍然看 `standards` 主表：

```text
code
standard_id
external_id
```

三者都查不到才认为是新增。

### 10.3 状态变化逻辑

状态变化只更新 `standards` 状态，不重新材料化，不重新索引：

```text
即将实施 -> 现行
现行 -> 废止
```

即将实施到期核验：

```text
从 standards 查 source_status='upcoming' 且 effective_date <= 今天
访问 detail_url
如果官网状态变化，就更新 source_status/source_status_raw
```

现行标准轮转核验：

```text
从 standards 查 source_status='active'
按 last_status_checked_at 最早排序
STANDARD_UPDATE_ACTIVE_CHECK_LIMIT=0 时检查全部；为正数时每轮检查对应条数
如果官网状态变成废止，就更新 standards 状态
```

### 10.4 运行方式

单次运行：

```powershell
python tools/standard-collector/scripts/sync_national_updates.py
```

常驻运行，每 30 分钟一轮：

```powershell
python tools/standard-collector/scripts/sync_national_updates.py --watch
```

调试时只扫很少页、少量状态：

```powershell
python tools/standard-collector/scripts/sync_national_updates.py --max-pages-safety 3 --active-check-limit 10
```

只做状态核验，不扫新增：

```powershell
python tools/standard-collector/scripts/sync_national_updates.py --max-pages-safety -1 --check-upcoming --active-check-limit 0
```

只扫新增，不做状态核验：

```powershell
python tools/standard-collector/scripts/sync_national_updates.py --active-check-limit -1 --no-check-upcoming
```

### 10.5 和材料化索引的关系

新增标准会在这个周期更新脚本里继续尝试材料化和索引：

```text
新增 -> 下载 PDF -> 上传 MinIO -> 写 standards -> 生成 4 个 markdown -> 建 overview embedding
```

默认会对本轮所有新增标准继续尝试材料化和索引：

```text
STANDARD_UPDATE_NEW_MATERIALIZE_LIMIT=0
```

`0` 表示不限制。如果你后面想控制每轮最多立即处理多少条新增标准，可以把它改成正数。

如果本轮新增标准数量超过这个正数限制，超出的新增标准会保留：

```text
materialize_status = pending
index_status = pending
```

后续可以由材料化索引脚本继续消费：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --watch
```

### 10.6 防重入

周期更新脚本会使用 PostgreSQL advisory lock。上一轮还没结束时，下一轮启动会直接跳过，避免两个更新任务同时运行。

### 10.7 命令行参数完整说明

周期更新脚本路径：

```bash
tools/standard-collector/scripts/sync_national_updates.py
```

默认运行一轮：

```bash
python tools/standard-collector/scripts/sync_national_updates.py
```

常驻运行，每 30 分钟一轮：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --watch
```

参数总览：

```text
--watch
--interval-seconds
--request-interval
--max-retries
--retry-backoff-seconds
--max-pages-safety
--known-page-stop-count
--check-upcoming
--no-check-upcoming
--upcoming-limit
--active-check-limit
--new-materialize-limit
--log-file
```

#### `--watch`

是否常驻运行。

不加 `--watch`：

```bash
python tools/standard-collector/scripts/sync_national_updates.py
```

含义：

```text
只执行一轮周期更新
执行完就退出
```

加 `--watch`：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --watch
```

含义：

```text
常驻运行
每隔 STANDARD_UPDATE_INTERVAL_SECONDS 秒执行一轮
当前默认是 1800 秒，也就是 30 分钟
```

注意：如果上一轮还没跑完，下一轮不会强行中断它。脚本内部用了 PostgreSQL advisory lock，避免两个更新任务并发执行。

#### `--interval-seconds`

只在 `--watch` 模式下生效。

默认来自 `.env`：

```env
STANDARD_UPDATE_INTERVAL_SECONDS=1800
```

含义：

```text
两轮周期更新之间等待多少秒
```

每 30 分钟一轮：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --watch --interval-seconds 1800
```

每 10 分钟一轮：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --watch --interval-seconds 600
```

每 1 小时一轮：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --watch --interval-seconds 3600
```

#### `--request-interval`

默认来自 `.env`：

```env
STANDARD_UPDATE_REQUEST_INTERVAL_SECONDS=3
```

含义：

```text
访问官网列表页/详情页之间等待多少秒
```

主要是为了避免请求太密集。

示例：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --request-interval 5
```

表示每次访问官网之间尽量间隔 5 秒。

#### `--max-retries`

默认来自 `.env`：

```env
STANDARD_UPDATE_MAX_RETRIES=2
```

含义：

```text
新增标准下载 PDF / 上传 MinIO 失败时，单条标准最多重试几次
```

示例：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --max-retries 3
```

表示每条新增标准最多尝试 3 次。

注意：这个重试主要用于新增标准的下载/上传阶段。状态核验失败会记录失败项，不会反复重试同一条直到成功。

#### `--retry-backoff-seconds`

默认来自 `.env`：

```env
STANDARD_UPDATE_RETRY_BACKOFF_SECONDS=3
```

含义：

```text
新增标准下载/上传失败后，下一次重试前等待的基准秒数
```

等待时间大致是：

```text
第 1 次失败后等待 3 秒
第 2 次失败后等待 6 秒
第 3 次失败后等待 9 秒
```

也就是：

```text
retry_backoff_seconds * attempt
```

示例：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --max-retries 3 --retry-backoff-seconds 5
```

#### `--max-pages-safety`

默认来自 `.env`：

```env
STANDARD_UPDATE_MAX_PAGES_SAFETY=0
```

含义：

```text
新增标准扫描的安全页数上限
```

当前语义是：

```text
0    = 不限制页数
正数 = 最多扫描这么多页
负数 = 禁用新增扫描
```

默认是：

```text
0，不限制页数
```

脚本不会无限扫，因为它还有另一个停止条件：

```text
连续 STANDARD_UPDATE_KNOWN_PAGE_STOP_COUNT 页没有新增标准，就停止
```

调试时只扫 3 页：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --max-pages-safety 3
```

只做状态核验，不扫新增：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --max-pages-safety -1
```

#### `--known-page-stop-count`

默认来自 `.env`：

```env
STANDARD_UPDATE_KNOWN_PAGE_STOP_COUNT=2
```

含义：

```text
按发布日期倒序扫描官网列表时，连续多少页没有发现新增标准，就停止新增扫描
```

默认是：

```text
连续 2 页没有新增，就停止
```

这样是为了解决不能只凭固定前 N 页扫描的问题。

现在逻辑是：

```text
从第 1 页开始扫
如果有新增，继续下一页
如果没有新增，known_page_count + 1
连续 2 页都没有新增，认为已经追到历史边界，停止
```

示例：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --known-page-stop-count 3
```

表示连续 3 页没有新增才停止，更保守，但会扫更多页。

#### `--check-upcoming`

默认来自 `.env`：

```env
STANDARD_UPDATE_CHECK_UPCOMING=true
```

含义：

```text
开启 upcoming 到期核验
```

它会查数据库里：

```text
source_status = upcoming
effective_date <= 今天
```

然后访问官网详情页，看是否已经从：

```text
即将实施 -> 现行
```

如果状态变了，就更新 `standards.source_status/source_status_raw`。

默认是开启的。

显式开启：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --check-upcoming
```

#### `--no-check-upcoming`

关闭 upcoming 到期核验。

示例：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --no-check-upcoming
```

含义：

```text
本轮不检查 upcoming 是否到期变成现行
```

如果只想扫新增标准，可以这样跑：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --active-check-limit -1 --no-check-upcoming
```

#### `--upcoming-limit`

默认来自 `.env`：

```env
STANDARD_UPDATE_UPCOMING_LIMIT=0
```

含义：

```text
每轮最多核验多少条到期 upcoming 标准
```

当前语义是：

```text
0    = 不限制，符合条件的 upcoming 都核验
正数 = 最多核验这么多条
负数 = 禁用 upcoming 核验
```

默认是：

```text
0，不限制
```

只核验 50 条：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --upcoming-limit 50
```

禁用 upcoming 核验：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --upcoming-limit -1
```

如果只是想关闭 upcoming，更推荐用：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --no-check-upcoming
```

#### `--active-check-limit`

默认来自 `.env`：

```env
STANDARD_UPDATE_ACTIVE_CHECK_LIMIT=0
```

含义：

```text
每轮最多核验多少条 active 标准
```

active 核验用于发现：

```text
现行 -> 废止
```

当前语义是：

```text
0    = 不限制，所有 active 都核验
正数 = 最多核验这么多条
负数 = 禁用 active 核验
```

默认是：

```text
0，不限制
```

调试时只核验 10 条 active：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --active-check-limit 10
```

不检查 active 状态：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --active-check-limit -1
```

#### `--new-materialize-limit`

默认来自 `.env`：

```env
STANDARD_UPDATE_NEW_MATERIALIZE_LIMIT=0
```

含义：

```text
每轮最多对多少条新增标准立即材料化并建立索引
```

当前语义是：

```text
0    = 不限制，本轮所有新增标准都立即材料化和索引
正数 = 最多立即材料化/索引这么多条
```

默认是：

```text
0，不限制
```

新增标准流程是：

```text
发现新增
-> 下载 PDF
-> 上传 MinIO
-> 写 standards
-> 生成 4 个 markdown
-> 建 overview embedding 索引
```

如果担心本轮新增很多，导致脚本跑很久，可以临时限制：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --new-materialize-limit 3
```

这样超过 3 条的新增标准会保留：

```text
materialize_status = pending
index_status = pending
```

后续可以用材料化索引脚本继续处理：

```bash
python tools/standard-collector/scripts/process_standard_library_jobs.py --watch
```

#### `--log-file`

默认来自 `.env`：

```env
STANDARD_UPDATE_LOG_FILE=./tools/standard-collector/logs/sync_national_updates.log
```

含义：

```text
日志文件路径
```

默认日志会追加写入，不覆盖。

自定义日志路径：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --log-file tools/standard-collector/logs/update_test.log
```

#### 常用组合

完整跑一轮，默认不限制：

```bash
python tools/standard-collector/scripts/sync_national_updates.py
```

常驻 30 分钟更新：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --watch
```

调试，只扫最多 3 页，只核验 10 条 active，只材料化 1 条新增：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --max-pages-safety 3 --active-check-limit 10 --new-materialize-limit 1
```

只扫新增，不核验状态：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --active-check-limit -1 --no-check-upcoming
```

只核验状态，不扫新增：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --max-pages-safety -1 --check-upcoming --active-check-limit 0
```

只核验 active 是否废止，不扫新增、不查 upcoming：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --max-pages-safety -1 --no-check-upcoming --active-check-limit 0
```

只核验到期 upcoming，不扫新增、不查 active：

```bash
python tools/standard-collector/scripts/sync_national_updates.py --max-pages-safety -1 --check-upcoming --active-check-limit -1
```

最重要的参数语义：

```text
0    = 不限制
正数 = 限制数量，适合调试
负数 = 禁用对应流程
--watch = 常驻周期运行
默认周期 = 1800 秒 = 30 分钟
```
