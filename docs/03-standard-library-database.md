# 标准库数据库设计规范

本文档只约束标准库模块；视频等既有模块暂不纳入本轮重构。

标准库新建独立 PostgreSQL 逻辑数据库，保存结构化数据、任务记录、检索向量、检索历史和 Atlas 投影数据；使用 MinIO 保存 PDF 原件和 4 个 Markdown 产物。严格意义上，MinIO 是对象存储，不是关系数据库，但标准库的数据设计需要同时说明数据库表与对象存储的分工。

## 存储总览

### PostgreSQL

标准库逻辑数据库名为 `octopus_standard_library`。如果部署环境不方便创建独立 database，可以退化为在现有 PostgreSQL 实例中创建独立 schema `standard_library`，但表结构、字段、约束和索引仍以本文档为准。

本轮标准库重构不复用初稿中的标准库旧表结构。实现时以本文档新建表和迁移代码，视频等既有模块不进入该数据库，也不随本轮重构调整。

PostgreSQL 负责：

- 保存标准主数据。
- 保存采集、解析、索引、投影等后台任务状态。
- 保存语义检索向量。
- 保存语义检索历史和结果快照。
- 保存 Embedding Atlas 的投影版本和点位。

### MinIO

对象存储 bucket 建议使用 `standard-library`。

MinIO 负责保存：

- 标准 PDF 原件。
- `standard_body.md`。
- `standard_structure.md`。
- `standard_logic.md`。
- `standard_overview.md`。

数据库只保存 bucket、object_key、文件 hash、文件大小和读取状态，不把 PDF 或 Markdown 正文直接写入数据库。

## 表清单

标准库本期需要 10 张核心表：

| 表名 | 用途 |
| --- | --- |
| `standards` | 标准主表，保存标准基础信息、来源信息、文件信息、材料化状态和索引状态 |
| `standard_sources` | 标准来源配置与同步水位表，保存国家标准、行业标准、地方标准的采集入口和最近同步状态 |
| `standard_sync_jobs` | 采集任务总表，保存历史采集和周期更新任务的整体状态与统计 |
| `standard_sync_items` | 采集任务明细表，记录每次采集任务中每条标准的入库决策、文件决策和处理结果 |
| `standard_processing_jobs` | 加工任务表，保存解析、索引、投影等后台加工任务的生命周期 |
| `standard_indexes` | 标准向量索引表，保存 `standard_overview.md` 生成的 embedding |
| `standard_search_queries` | 语义检索记录表，保存用户每次检索的 query、时间、状态和统计 |
| `standard_search_results` | 语义检索结果快照表，保存某次检索返回给前端的结果快照 |
| `standard_atlas_projections` | Atlas 投影版本表，保存一次二维投影任务的版本、算法、状态和统计 |
| `standard_atlas_points` | Atlas 点位表，保存某个投影版本下每条标准的二维坐标 |

## 通用字段类型

除非单表另有说明，字段类型遵循以下规则：

| 字段模式 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | 主键 ID |
| `*_id` | `uuid` | 无 | 外键或关联 ID |
| `created_at` | `timestamptz` | `now()` | 创建时间 |
| `updated_at` | `timestamptz` | `now()` | 更新时间，由应用或触发器维护 |
| `*_at` | `timestamptz` | `null` | 事件发生时间 |
| `*_date` | `date` | `null` | 官网业务日期 |
| `*_count` | `integer` | `0` | 统计数量 |
| `*_bytes` | `bigint` | `null` | 文件大小 |
| `*_percent` | `numeric(5,2)` | `0` | 进度百分比，范围 0 到 100 |
| `*_hash` | `text` | `null` | hash 或 fingerprint 字符串 |
| `*_url` | `text` | `null` | URL |
| `*_object_key` | `text` | `null` | MinIO object key |
| `*_message` / `*_error` | `text` | `null` | 错误信息 |
| `*_payload` | `jsonb` | `null` | 扩展快照或来源原始数据 |

## 枚举值定义

本期可以使用 PostgreSQL enum，也可以使用 `text` + check constraint。无论实现方式如何，允许值必须与下表保持一致。

### 来源与状态

| 字段 | 允许值 | 含义 |
| --- | --- | --- |
| `source` | `national`、`industry`、`local` | 国家标准、行业标准、地方标准 |
| `official_status` | `upcoming`、`current`、`updated_available`、`abolished` | 即将实施、现行、有更新版、废止 |
| `file_access_type` | `downloadable`、`online_only`、`unavailable` | 可下载、仅在线阅读、不可获取全文 |
| `materialize_status` | `pending`、`materializing`、`materialized`、`failed`、`skipped` | 待材料化、材料化中、已材料化、材料化失败、跳过 |
| `index_status` | `pending`、`indexing`、`indexed`、`failed`、`skipped` | 待索引、索引中、已索引、索引失败、跳过 |

### 任务

| 字段 | 允许值 | 含义 |
| --- | --- | --- |
| `standard_sync_jobs.job_type` | `historical_collect`、`scheduled_update` | 历史采集、周期更新 |
| `standard_processing_jobs.job_type` | `materialize`、`index`、`atlas_projection` | 材料化、向量索引、Atlas 投影 |
| `trigger_type` | `schedule`、`system`、`admin` | 定时触发、系统自动触发、后台管理触发 |
| `status` | `pending`、`running`、`completed`、`failed`、`cancelled` | 待执行、执行中、已完成、失败、已取消 |
| `metadata_action` | `new`、`changed`、`unchanged` | 新增、元数据变化、无变化 |
| `file_decision` | `download`、`redownload`、`no_download`、`online_only`、`unavailable`、`skip` | 下载、重新下载、不下载、仅在线阅读、不可获取、跳过 |
| `file_result` | `success`、`failed`、`skipped` | 成功、失败、跳过 |

### 检索与投影

| 字段 | 允许值 | 含义 |
| --- | --- | --- |
| `search_mode` | `semantic` | 语义检索 |
| `standard_search_queries.status` | `success`、`failed` | 检索成功、检索失败 |
| `index_kind` | `overview` | 标准级概要索引 |
| `standard_atlas_projections.status` | `pending`、`running`、`completed`、`failed` | 待投影、投影中、已完成、失败 |
| `algorithm` | `umap`、`tsne`、`pca` | Atlas 降维算法 |
| `distance_metric` | `cosine`、`l2` | 距离度量 |

## 1. `standards`

标准主表。它是标准库最核心的数据表，目录、详情、语义检索、Atlas、有效标准统计都以它为基础。

有效标准不单独建表，通过查询条件判断：

```text
有效标准 = 官网状态有效 AND materialize_status = materialized AND index_status = indexed
```

其中官网状态有效指：

- 国家标准、行业标准：`official_status = current`。
- 地方标准：`official_status = current` 或 `official_status = updated_available`。

| 字段 | 含义 |
| --- | --- |
| `id` | 标准内部 ID，主键 |
| `code` | 标准号，例如 `GB/T 12345-2026` |
| `code_normalized` | 标准号规范化值，用于精确检索、去重和唯一约束 |
| `name` | 标准名称 |
| `source` | 来源，取值为 `national`、`industry`、`local` |
| `source_label` | 来源展示名，例如 `国家标准`、`行业标准`、`地方标准` |
| `category` | 来源下分类；国家标准为 `GB`、`GB/T`、`GB/Z`，行业标准为行业名称，地方标准为地区 |
| `category_label` | 分类展示名，例如 `推荐性国家标准`、`通信`、`山西省 / 太原市` |
| `standard_org` | 发布组织或归口组织，若官网可获得则保存 |
| `official_status` | 标准化后的官网状态，建议取值 `upcoming`、`current`、`updated_available`、`abolished` |
| `official_status_raw` | 官网原始状态文本，例如 `即将实施`、`现行`、`有更新版`、`废止` |
| `publish_date` | 发布日期 |
| `effective_date` | 实施日期 |
| `abolish_date` | 废止日期，官网未提供时为空 |
| `source_site` | 来源网站标识，例如 `openstd`、`hbba`、`dbba` |
| `external_id` | 官网侧唯一 ID，例如国家标准 `hcno`、行业/地方标准 `pk` |
| `detail_url` | 官网详情页链接，用于详情页来源 logo 跳转 |
| `pdf_url` | PDF 下载链接，若只支持在线阅读则为空 |
| `online_url` | 在线阅读链接，若有则保存 |
| `file_access_type` | 文件访问类型，建议取值 `downloadable`、`online_only`、`unavailable` |
| `source_pdf_bucket` | PDF 原件所在 MinIO bucket |
| `source_pdf_object_key` | PDF 原件 object key |
| `source_pdf_hash` | PDF 文件 hash，用于判断文件变化 |
| `source_pdf_size_bytes` | PDF 文件大小 |
| `metadata_fingerprint` | 标准元数据指纹，用于周期更新判断元数据是否变化 |
| `file_fingerprint` | 文件侧指纹，用于判断是否需要重新下载和重新解析 |
| `body_md_object_key` | `standard_body.md` 的 object key |
| `structure_md_object_key` | `standard_structure.md` 的 object key |
| `logic_md_object_key` | `standard_logic.md` 的 object key |
| `overview_md_object_key` | `standard_overview.md` 的 object key，索引线只读取这个产物 |
| `materialize_status` | 材料化状态，建议取值 `pending`、`materializing`、`materialized`、`failed`、`skipped` |
| `materialize_error` | 最近一次材料化失败原因 |
| `materialized_at` | 最近一次材料化成功时间 |
| `index_status` | 索引状态，建议取值 `pending`、`indexing`、`indexed`、`failed`、`skipped` |
| `index_error` | 最近一次索引失败原因 |
| `indexed_at` | 最近一次索引成功时间 |
| `first_seen_at` | 第一次采集到该标准的时间 |
| `last_seen_at` | 最近一次在来源网站列表或详情中看到该标准的时间 |
| `last_checked_at` | 最近一次复查该标准的时间 |
| `created_at` | 入库时间 |
| `updated_at` | 记录更新时间 |

## 2. `standard_sources`

标准来源配置与同步水位表。它用于管理国家标准、行业标准、地方标准的采集入口、启停状态和最近同步状态。

| 字段 | 含义 |
| --- | --- |
| `id` | 来源配置 ID，主键 |
| `source` | 来源，取值为 `national`、`industry`、`local` |
| `source_label` | 来源展示名 |
| `entry_url` | 来源网站入口 |
| `enabled` | 是否启用该来源采集 |
| `historical_collect_enabled` | 是否允许历史采集 |
| `scheduled_update_enabled` | 是否允许周期更新 |
| `schedule_cron` | 周期更新计划表达式或配置文本 |
| `last_historical_job_id` | 最近一次历史采集任务 ID |
| `last_update_job_id` | 最近一次周期更新任务 ID |
| `last_success_at` | 最近一次成功完成同步的时间 |
| `scan_watermark` | 增量扫描水位，例如最近发布日期、备案时间或来源自定义游标 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

## 3. `standard_sync_jobs`

采集任务总表。历史采集和周期更新都写入这里，用于任务监控、首页摘要、失败排查和历史追踪。

| 字段 | 含义 |
| --- | --- |
| `id` | 采集任务 ID，主键 |
| `job_type` | 任务类型，取值 `historical_collect` 或 `scheduled_update` |
| `source` | 本次采集来源，取值 `national`、`industry`、`local` |
| `trigger_type` | 触发方式，例如 `schedule`、`system`、`admin` |
| `status` | 任务状态，建议取值 `pending`、`running`、`completed`、`failed`、`cancelled` |
| `stage` | 当前阶段，例如扫描列表、读取详情、下载文件、写入数据库 |
| `progress_percent` | 进度百分比 |
| `process_id` | 执行该任务的进程 ID |
| `heartbeat_at` | 最近心跳时间 |
| `started_at` | 开始时间 |
| `finished_at` | 结束时间 |
| `duration_ms` | 执行耗时 |
| `scanned_pages` | 已扫描页数 |
| `discovered_count` | 已发现标准数 |
| `processed_count` | 已处理标准数 |
| `need_download_count` | 需要下载或重新下载的标准数 |
| `downloaded_count` | 下载成功数 |
| `download_failed_count` | 下载失败数 |
| `new_active_count` | 本轮新增现行标准数，包含即将实施转现行 |
| `expired_count` | 本轮失效标准数 |
| `failed_count` | 本轮失败标准数 |
| `error_message` | 任务级错误摘要 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

## 4. `standard_sync_items`

采集任务明细表。它记录一次采集任务中每条标准的处理过程，用于解释某条标准为什么新增、变化、跳过、下载失败或不可下载。

| 字段 | 含义 |
| --- | --- |
| `id` | 明细 ID，主键 |
| `job_id` | 所属 `standard_sync_jobs.id` |
| `standard_id` | 对应 `standards.id`，入库前失败时可为空 |
| `code` | 本次采集到的标准号 |
| `name` | 本次采集到的标准名称 |
| `source` | 来源 |
| `category` | 来源下分类 |
| `external_id` | 官网侧唯一 ID |
| `detail_url` | 官网详情页链接 |
| `official_status_before` | 处理前官网状态 |
| `official_status_after` | 处理后官网状态 |
| `metadata_action` | 元数据动作，建议取值 `new`、`changed`、`unchanged` |
| `status_change_type` | 状态变化类型，例如即将实施转现行、现行转废止、有更新版转废止 |
| `file_decision` | 文件处理决策，建议取值 `download`、`redownload`、`no_download`、`online_only`、`unavailable`、`skip` |
| `file_result` | 文件处理结果，建议取值 `success`、`failed`、`skipped` |
| `source_pdf_bucket` | 本次下载成功后的 PDF bucket |
| `source_pdf_object_key` | 本次下载成功后的 PDF object key |
| `source_pdf_hash` | 本次下载成功后的 PDF hash |
| `source_pdf_size_bytes` | 本次下载成功后的 PDF 大小 |
| `online_url` | 在线阅读地址 |
| `retry_count` | 单条标准处理重试次数 |
| `error_message` | 单条标准失败原因 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

## 5. `standard_processing_jobs`

加工任务表。它保存采集之后的后台加工任务，包括 PDF 材料化、向量索引和 Atlas 投影任务。标准主表保存当前加工状态，本表保存每一次加工任务的执行记录。

| 字段 | 含义 |
| --- | --- |
| `id` | 加工任务 ID，主键 |
| `job_type` | 加工任务类型，建议取值 `materialize`、`index`、`atlas_projection` |
| `standard_id` | 对应 `standards.id`；投影任务面向全量有效标准时可为空 |
| `projection_id` | 对应 `standard_atlas_projections.id`，仅投影任务使用 |
| `source_sync_job_id` | 触发该加工任务的采集任务 ID |
| `source_sync_item_id` | 触发该加工任务的采集明细 ID |
| `status` | 任务状态，建议取值 `pending`、`running`、`completed`、`failed`、`cancelled` |
| `stage` | 当前阶段，例如读取 PDF、生成 Markdown、生成 embedding、计算投影 |
| `progress_percent` | 进度百分比 |
| `priority` | 任务优先级 |
| `retry_count` | 已重试次数 |
| `max_retries` | 最大重试次数 |
| `process_id` | 执行进程 ID |
| `heartbeat_at` | 最近心跳时间 |
| `started_at` | 开始时间 |
| `finished_at` | 结束时间 |
| `duration_ms` | 执行耗时 |
| `error_message` | 任务失败原因 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

## 6. `standard_indexes`

标准向量索引表。当前只保存标准级 `overview` 索引，用于语义检索和 Atlas 投影输入。

| 字段 | 含义 |
| --- | --- |
| `id` | 索引 ID，主键 |
| `standard_id` | 对应 `standards.id` |
| `index_kind` | 索引类型，本期固定为 `overview` |
| `content` | 用于生成 embedding 的检索文本 |
| `content_hash` | 检索文本 hash，用于判断是否需要重建索引 |
| `embedding` | pgvector 向量字段 |
| `embedding_model` | 生成该向量的 Embedding 模型 |
| `embedding_dimensions` | 向量维度 |
| `schema_version` | overview 内容结构版本 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

## 7. `standard_search_queries`

语义检索记录表。每次成功或失败的语义检索都写入一条记录，用于检索历史浮层和历史结果回填。

| 字段 | 含义 |
| --- | --- |
| `id` | 检索记录 ID，主键 |
| `query_text` | 用户输入的检索文本 |
| `searched_at` | 本次检索发生时间 |
| `last_reused_at` | 最近一次从历史记录中被点击复用的时间 |
| `sort_at` | 历史列表排序时间；新检索取 `searched_at`，复用历史时更新 |
| `caller` | 调用来源，例如前端检索页 |
| `limit` | 本次检索请求的返回数量 |
| `search_mode` | 检索模式，本期可固定为 `semantic` |
| `embedding_model` | query embedding 使用的模型 |
| `embedding_dimensions` | query embedding 维度 |
| `result_count` | 本次返回结果数 |
| `latency_ms` | 检索耗时 |
| `status` | 检索状态，建议取值 `success`、`failed` |
| `error_message` | 检索失败原因 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

## 8. `standard_search_results`

语义检索结果快照表。它保存某次检索当时返回给前端的结果，历史回填时直接读取快照，不重新检索，也不按当前标准库状态重新补齐。

| 字段 | 含义 |
| --- | --- |
| `id` | 结果快照 ID，主键 |
| `query_id` | 对应 `standard_search_queries.id` |
| `standard_id` | 当时命中的标准 ID |
| `index_id` | 当时命中的 `standard_indexes.id` |
| `rank` | 当次结果排序名次 |
| `score` | 相似度得分 |
| `match_level` | 命中等级，例如高相关、中相关、低相关 |
| `reason` | 命中说明 |
| `evidence` | 证据片段或概要摘录 |
| `snapshot_code` | 当时展示的标准号 |
| `snapshot_name` | 当时展示的标准名称 |
| `snapshot_source` | 当时展示的来源 |
| `snapshot_category` | 当时展示的分类 |
| `snapshot_publish_date` | 当时展示的发布日期 |
| `snapshot_effective_date` | 当时展示的实施日期 |
| `snapshot_detail_url` | 当时展示的官网详情链接 |
| `snapshot_payload` | 可选 JSON 快照，保存前端展示需要的扩展字段 |
| `created_at` | 创建时间 |

## 9. `standard_atlas_projections`

Atlas 投影版本表。每次投影任务生成一个版本，完成后再切换为当前版本，避免前端读到半成品。

| 字段 | 含义 |
| --- | --- |
| `id` | 投影版本 ID，主键 |
| `version` | 投影版本号或版本标识 |
| `algorithm` | 降维算法，例如 `umap`、`tsne`、`pca` |
| `distance_metric` | 距离度量，例如 `cosine` |
| `color_by` | 默认着色维度，例如 `source` |
| `embedding_model` | 本次投影使用的 embedding 模型 |
| `embedding_dimensions` | 本次投影使用的 embedding 维度 |
| `effective_standard_count` | 本次投影时的有效标准总数 |
| `projected_count` | 已生成坐标的标准数量 |
| `missing_count` | 缺失投影坐标的有效标准数量 |
| `input_hash` | 本次投影输入集合 hash，用于判断是否需要重算 |
| `status` | 投影状态，建议取值 `pending`、`running`、`completed`、`failed` |
| `is_current` | 是否为当前前端读取版本 |
| `started_at` | 开始时间 |
| `completed_at` | 完成时间 |
| `error_message` | 投影失败原因 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

## 10. `standard_atlas_points`

Atlas 点位表。它保存某个投影版本下每条标准的二维坐标和分组字段，只服务首页散点图读取。

| 字段 | 含义 |
| --- | --- |
| `id` | 点位 ID，主键 |
| `projection_id` | 对应 `standard_atlas_projections.id` |
| `standard_id` | 对应 `standards.id` |
| `x` | 二维投影 x 坐标 |
| `y` | 二维投影 y 坐标 |
| `color_key` | 着色分组键，例如来源、行业或地区 |
| `source` | 来源快照 |
| `category` | 分类快照 |
| `created_at` | 创建时间 |

## 字段类型与默认值

以下类型使用 PostgreSQL 表达。`not null` 表示实现时必须写入；`null` 表示允许为空。

### `standards`

| 字段 | 类型 | 默认值 | 空值 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | not null |
| `code` | `text` | 无 | not null |
| `code_normalized` | `text` | 无 | not null |
| `name` | `text` | 无 | not null |
| `source` | `text` | 无 | not null |
| `source_label` | `text` | 无 | not null |
| `category` | `text` | 无 | not null |
| `category_label` | `text` | 无 | not null |
| `standard_org` | `text` | null | null |
| `official_status` | `text` | 无 | not null |
| `official_status_raw` | `text` | null | null |
| `publish_date` | `date` | null | null |
| `effective_date` | `date` | null | null |
| `abolish_date` | `date` | null | null |
| `source_site` | `text` | 无 | not null |
| `external_id` | `text` | null | null |
| `detail_url` | `text` | null | null |
| `pdf_url` | `text` | null | null |
| `online_url` | `text` | null | null |
| `file_access_type` | `text` | `unavailable` | not null |
| `source_pdf_bucket` | `text` | null | null |
| `source_pdf_object_key` | `text` | null | null |
| `source_pdf_hash` | `text` | null | null |
| `source_pdf_size_bytes` | `bigint` | null | null |
| `metadata_fingerprint` | `text` | null | null |
| `file_fingerprint` | `text` | null | null |
| `body_md_object_key` | `text` | null | null |
| `structure_md_object_key` | `text` | null | null |
| `logic_md_object_key` | `text` | null | null |
| `overview_md_object_key` | `text` | null | null |
| `materialize_status` | `text` | `pending` | not null |
| `materialize_error` | `text` | null | null |
| `materialized_at` | `timestamptz` | null | null |
| `index_status` | `text` | `pending` | not null |
| `index_error` | `text` | null | null |
| `indexed_at` | `timestamptz` | null | null |
| `first_seen_at` | `timestamptz` | `now()` | not null |
| `last_seen_at` | `timestamptz` | null | null |
| `last_checked_at` | `timestamptz` | null | null |
| `created_at` | `timestamptz` | `now()` | not null |
| `updated_at` | `timestamptz` | `now()` | not null |

### `standard_sources`

| 字段 | 类型 | 默认值 | 空值 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | not null |
| `source` | `text` | 无 | not null |
| `source_label` | `text` | 无 | not null |
| `entry_url` | `text` | 无 | not null |
| `enabled` | `boolean` | `true` | not null |
| `historical_collect_enabled` | `boolean` | `true` | not null |
| `scheduled_update_enabled` | `boolean` | `true` | not null |
| `schedule_cron` | `text` | null | null |
| `last_historical_job_id` | `uuid` | null | null |
| `last_update_job_id` | `uuid` | null | null |
| `last_success_at` | `timestamptz` | null | null |
| `scan_watermark` | `jsonb` | `'{}'::jsonb` | not null |
| `created_at` | `timestamptz` | `now()` | not null |
| `updated_at` | `timestamptz` | `now()` | not null |

### `standard_sync_jobs`

| 字段 | 类型 | 默认值 | 空值 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | not null |
| `job_type` | `text` | 无 | not null |
| `source` | `text` | 无 | not null |
| `trigger_type` | `text` | `system` | not null |
| `status` | `text` | `pending` | not null |
| `stage` | `text` | null | null |
| `progress_percent` | `numeric(5,2)` | `0` | not null |
| `process_id` | `integer` | null | null |
| `heartbeat_at` | `timestamptz` | null | null |
| `started_at` | `timestamptz` | null | null |
| `finished_at` | `timestamptz` | null | null |
| `duration_ms` | `integer` | null | null |
| `scanned_pages` | `integer` | `0` | not null |
| `discovered_count` | `integer` | `0` | not null |
| `processed_count` | `integer` | `0` | not null |
| `need_download_count` | `integer` | `0` | not null |
| `downloaded_count` | `integer` | `0` | not null |
| `download_failed_count` | `integer` | `0` | not null |
| `new_active_count` | `integer` | `0` | not null |
| `expired_count` | `integer` | `0` | not null |
| `failed_count` | `integer` | `0` | not null |
| `error_message` | `text` | null | null |
| `created_at` | `timestamptz` | `now()` | not null |
| `updated_at` | `timestamptz` | `now()` | not null |

### `standard_sync_items`

| 字段 | 类型 | 默认值 | 空值 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | not null |
| `job_id` | `uuid` | 无 | not null |
| `standard_id` | `uuid` | null | null |
| `code` | `text` | null | null |
| `name` | `text` | null | null |
| `source` | `text` | 无 | not null |
| `category` | `text` | null | null |
| `external_id` | `text` | null | null |
| `detail_url` | `text` | null | null |
| `official_status_before` | `text` | null | null |
| `official_status_after` | `text` | null | null |
| `metadata_action` | `text` | null | null |
| `status_change_type` | `text` | null | null |
| `file_decision` | `text` | null | null |
| `file_result` | `text` | null | null |
| `source_pdf_bucket` | `text` | null | null |
| `source_pdf_object_key` | `text` | null | null |
| `source_pdf_hash` | `text` | null | null |
| `source_pdf_size_bytes` | `bigint` | null | null |
| `online_url` | `text` | null | null |
| `retry_count` | `integer` | `0` | not null |
| `error_message` | `text` | null | null |
| `created_at` | `timestamptz` | `now()` | not null |
| `updated_at` | `timestamptz` | `now()` | not null |

### `standard_processing_jobs`

| 字段 | 类型 | 默认值 | 空值 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | not null |
| `job_type` | `text` | 无 | not null |
| `standard_id` | `uuid` | null | null |
| `projection_id` | `uuid` | null | null |
| `source_sync_job_id` | `uuid` | null | null |
| `source_sync_item_id` | `uuid` | null | null |
| `status` | `text` | `pending` | not null |
| `stage` | `text` | null | null |
| `progress_percent` | `numeric(5,2)` | `0` | not null |
| `priority` | `integer` | `100` | not null |
| `retry_count` | `integer` | `0` | not null |
| `max_retries` | `integer` | `3` | not null |
| `process_id` | `integer` | null | null |
| `heartbeat_at` | `timestamptz` | null | null |
| `started_at` | `timestamptz` | null | null |
| `finished_at` | `timestamptz` | null | null |
| `duration_ms` | `integer` | null | null |
| `error_message` | `text` | null | null |
| `created_at` | `timestamptz` | `now()` | not null |
| `updated_at` | `timestamptz` | `now()` | not null |

### `standard_indexes`

| 字段 | 类型 | 默认值 | 空值 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | not null |
| `standard_id` | `uuid` | 无 | not null |
| `index_kind` | `text` | `overview` | not null |
| `content` | `text` | 无 | not null |
| `content_hash` | `text` | 无 | not null |
| `embedding` | `vector(<EMBEDDING_DIMENSIONS>)` | 无 | not null |
| `embedding_model` | `text` | 无 | not null |
| `embedding_dimensions` | `integer` | 无 | not null |
| `schema_version` | `text` | 无 | not null |
| `created_at` | `timestamptz` | `now()` | not null |
| `updated_at` | `timestamptz` | `now()` | not null |

### `standard_search_queries`

| 字段 | 类型 | 默认值 | 空值 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | not null |
| `query_text` | `text` | 无 | not null |
| `searched_at` | `timestamptz` | `now()` | not null |
| `last_reused_at` | `timestamptz` | null | null |
| `sort_at` | `timestamptz` | `now()` | not null |
| `caller` | `text` | `frontend` | not null |
| `limit` | `integer` | `20` | not null |
| `search_mode` | `text` | `semantic` | not null |
| `embedding_model` | `text` | null | null |
| `embedding_dimensions` | `integer` | null | null |
| `result_count` | `integer` | `0` | not null |
| `latency_ms` | `integer` | null | null |
| `status` | `text` | 无 | not null |
| `error_message` | `text` | null | null |
| `created_at` | `timestamptz` | `now()` | not null |
| `updated_at` | `timestamptz` | `now()` | not null |

### `standard_search_results`

| 字段 | 类型 | 默认值 | 空值 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | not null |
| `query_id` | `uuid` | 无 | not null |
| `standard_id` | `uuid` | 无 | not null |
| `index_id` | `uuid` | null | null |
| `rank` | `integer` | 无 | not null |
| `score` | `numeric(8,6)` | 无 | not null |
| `match_level` | `text` | null | null |
| `reason` | `text` | null | null |
| `evidence` | `text` | null | null |
| `snapshot_code` | `text` | 无 | not null |
| `snapshot_name` | `text` | 无 | not null |
| `snapshot_source` | `text` | 无 | not null |
| `snapshot_category` | `text` | null | null |
| `snapshot_publish_date` | `date` | null | null |
| `snapshot_effective_date` | `date` | null | null |
| `snapshot_detail_url` | `text` | null | null |
| `snapshot_payload` | `jsonb` | null | null |
| `created_at` | `timestamptz` | `now()` | not null |

### `standard_atlas_projections`

| 字段 | 类型 | 默认值 | 空值 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | not null |
| `version` | `text` | 无 | not null |
| `algorithm` | `text` | `umap` | not null |
| `distance_metric` | `text` | `cosine` | not null |
| `color_by` | `text` | `source` | not null |
| `embedding_model` | `text` | 无 | not null |
| `embedding_dimensions` | `integer` | 无 | not null |
| `effective_standard_count` | `integer` | `0` | not null |
| `projected_count` | `integer` | `0` | not null |
| `missing_count` | `integer` | `0` | not null |
| `input_hash` | `text` | null | null |
| `status` | `text` | `pending` | not null |
| `is_current` | `boolean` | `false` | not null |
| `started_at` | `timestamptz` | null | null |
| `completed_at` | `timestamptz` | null | null |
| `error_message` | `text` | null | null |
| `created_at` | `timestamptz` | `now()` | not null |
| `updated_at` | `timestamptz` | `now()` | not null |

### `standard_atlas_points`

| 字段 | 类型 | 默认值 | 空值 |
| --- | --- | --- | --- |
| `id` | `uuid` | `gen_random_uuid()` | not null |
| `projection_id` | `uuid` | 无 | not null |
| `standard_id` | `uuid` | 无 | not null |
| `x` | `double precision` | 无 | not null |
| `y` | `double precision` | 无 | not null |
| `color_key` | `text` | null | null |
| `source` | `text` | 无 | not null |
| `category` | `text` | null | null |
| `created_at` | `timestamptz` | `now()` | not null |

## 约束

### 主键与外键

| 表名 | 约束 |
| --- | --- |
| 全部表 | `id` 为主键 |
| `standard_sources.last_historical_job_id` | 外键到 `standard_sync_jobs.id`，允许为空 |
| `standard_sources.last_update_job_id` | 外键到 `standard_sync_jobs.id`，允许为空 |
| `standard_sync_items.job_id` | 外键到 `standard_sync_jobs.id`，级联删除 |
| `standard_sync_items.standard_id` | 外键到 `standards.id`，允许为空 |
| `standard_processing_jobs.standard_id` | 外键到 `standards.id`，允许为空 |
| `standard_processing_jobs.projection_id` | 外键到 `standard_atlas_projections.id`，允许为空 |
| `standard_processing_jobs.source_sync_job_id` | 外键到 `standard_sync_jobs.id`，允许为空 |
| `standard_processing_jobs.source_sync_item_id` | 外键到 `standard_sync_items.id`，允许为空 |
| `standard_indexes.standard_id` | 外键到 `standards.id`，级联删除 |
| `standard_search_results.query_id` | 外键到 `standard_search_queries.id`，级联删除 |
| `standard_search_results.standard_id` | 外键到 `standards.id`，保留历史快照时不级联删除 |
| `standard_search_results.index_id` | 外键到 `standard_indexes.id`，允许为空 |
| `standard_atlas_points.projection_id` | 外键到 `standard_atlas_projections.id`，级联删除 |
| `standard_atlas_points.standard_id` | 外键到 `standards.id`，级联删除 |

历史检索结果需要保留“当时看到的结果”。因此即使标准后续失效或字段变化，也不应更新 `standard_search_results` 的快照字段。若未来允许物理删除标准，需要先决定历史快照是否继续保留；默认策略是不物理删除标准，只更新官网状态。

### 唯一约束

| 表名 | 约束 | 说明 |
| --- | --- | --- |
| `standard_sources` | `unique(source)` | 每个来源只有一条配置 |
| `standards` | `unique(source, external_id)` where `external_id is not null` | 官网 ID 存在时，以来源 + 官网 ID 判断同一标准 |
| `standards` | `unique(source, code_normalized, category)` | 官网 ID 缺失或变化时，以来源 + 标准号 + 分类兜底 |
| `standard_sync_items` | `unique(job_id, source, external_id)` where `external_id is not null` | 同一采集任务内同一官网记录不重复写明细 |
| `standard_indexes` | `unique(standard_id, index_kind, embedding_model, embedding_dimensions)` | 同一标准同一模型维度下只有一个 overview 索引 |
| `standard_search_results` | `unique(query_id, rank)` | 同一次检索内排序名次唯一 |
| `standard_atlas_projections` | `unique(version)` | 投影版本唯一 |
| `standard_atlas_points` | `unique(projection_id, standard_id)` | 同一投影版本中一条标准只有一个点 |

### Check 约束

| 表名 | 约束 |
| --- | --- |
| `standards` | `source`、`official_status`、`file_access_type`、`materialize_status`、`index_status` 必须属于枚举值定义 |
| `standard_sync_jobs` | `job_type`、`source`、`trigger_type`、`status` 必须属于枚举值定义 |
| `standard_sync_jobs` | `progress_percent` 范围为 0 到 100 |
| `standard_sync_items` | `source`、`metadata_action`、`file_decision`、`file_result` 使用枚举值定义；允许业务空值的字段可为空 |
| `standard_processing_jobs` | `job_type`、`status` 必须属于枚举值定义 |
| `standard_processing_jobs` | `progress_percent` 范围为 0 到 100 |
| `standard_indexes` | `index_kind = 'overview'` |
| `standard_indexes` | `embedding_dimensions > 0` |
| `standard_search_queries` | `limit > 0` |
| `standard_search_queries` | `search_mode = 'semantic'` |
| `standard_search_results` | `rank > 0` |
| `standard_search_results` | `score >= 0` |
| `standard_atlas_projections` | `status`、`algorithm`、`distance_metric` 必须属于枚举值定义 |
| `standard_atlas_points` | `x`、`y` 不能为 NaN |

## 查询索引

### 目录与详情

| 索引 | 建议定义 | 用途 |
| --- | --- | --- |
| `idx_standards_effective_recent` | `(publish_date desc, id)`，partial 过滤有效标准 | 默认展示最近一个月有效标准 |
| `idx_standards_effective_source_recent` | `(source, publish_date desc, id)`，partial 过滤有效标准 | 来源筛选 + 最近有效标准 |
| `idx_standards_code_normalized` | `(code_normalized)`，partial 过滤有效标准 | 标准号精确检索 |
| `idx_standards_name_trgm` | `gin(name gin_trgm_ops)`，partial 过滤有效标准 | 标准名称关键词检索 |
| `idx_standards_detail_url` | `(detail_url)` | 来源链接查重和排查 |

有效标准 partial 过滤条件为：

```sql
(
  (
    source in ('national', 'industry')
    and official_status = 'current'
  )
  or (
    source = 'local'
    and official_status in ('current', 'updated_available')
  )
)
and materialize_status = 'materialized'
and index_status = 'indexed'
```

### 采集与任务

| 索引 | 建议定义 | 用途 |
| --- | --- | --- |
| `idx_standard_sources_enabled` | `(enabled, source)` | 调度器读取启用来源 |
| `idx_sync_jobs_status_priority` | `(status, created_at)` | 调度器扫描待执行采集任务 |
| `idx_sync_jobs_latest_update` | `(job_type, source, created_at desc)` | 首页摘要读取最近周期更新 |
| `idx_sync_items_job` | `(job_id, created_at)` | 读取采集任务明细 |
| `idx_sync_items_standard` | `(standard_id, created_at desc)` | 查看某条标准的采集历史 |
| `idx_processing_jobs_status_priority` | `(status, priority, created_at)` | 调度器扫描待执行加工任务 |
| `idx_processing_jobs_standard` | `(standard_id, job_type, created_at desc)` | 查看单条标准的解析、索引历史 |
| `idx_processing_jobs_heartbeat` | `(status, heartbeat_at)` | 心跳超时检查 |

### 语义检索

| 索引 | 建议定义 | 用途 |
| --- | --- | --- |
| `idx_standard_indexes_standard` | `(standard_id)` | 与标准主表关联 |
| `idx_standard_indexes_model` | `(index_kind, embedding_model, embedding_dimensions)` | 过滤当前可用索引 |
| `idx_standard_indexes_embedding_hnsw` | `hnsw (embedding vector_cosine_ops)` | pgvector 相似度检索 |
| `idx_search_queries_sort` | `(sort_at desc, id desc)` | 检索历史浮层倒序 |
| `idx_search_results_query_rank` | `(query_id, rank)` | 读取历史结果快照 |

### Atlas

| 索引 | 建议定义 | 用途 |
| --- | --- | --- |
| `idx_atlas_projections_current` | `(is_current, completed_at desc)` where `status = 'completed'` | 读取当前投影版本 |
| `idx_atlas_points_projection` | `(projection_id, standard_id)` | 读取整张散点图 |
| `idx_atlas_points_color_key` | `(projection_id, color_key)` | 生成图例和分类统计 |

## pgvector 规则

PostgreSQL 数据库必须启用 `vector` 扩展。`standard_indexes.embedding` 使用 `vector(<EMBEDDING_DIMENSIONS>)` 类型，维度由当前 Embedding 模型配置决定。

本期向量索引使用 HNSW，距离度量使用 cosine：

```sql
create index idx_standard_indexes_embedding_hnsw
on standard_indexes
using hnsw (embedding vector_cosine_ops);
```

模型或维度变化时，不允许把不同模型或不同维度的向量混在同一次检索中。语义检索和 Atlas 投影都必须同时过滤 `embedding_model`、`embedding_dimensions` 和 `index_kind = 'overview'`。旧模型向量可以保留，但默认不参与当前检索；需要切换模型时，通过后台重建索引任务重新生成。

## 对象存储结构

MinIO 中的 object key 建议保持稳定，便于重新材料化时覆盖同一标准的产物。

```text
standard-library/
  pdf/{source}/{standard_id}.pdf
  markdown/{standard_id}/standard_body.md
  markdown/{standard_id}/standard_structure.md
  markdown/{standard_id}/standard_logic.md
  markdown/{standard_id}/standard_overview.md
```

对象存储不负责判断标准是否有效。有效标准判断永远以 PostgreSQL 中的官网状态、材料化状态和索引状态为准。

## 关系说明

| 关系 | 说明 |
| --- | --- |
| `standards` 1 对多 `standard_sync_items` | 一条标准可能在多次采集任务中被发现、复查或更新 |
| `standard_sync_jobs` 1 对多 `standard_sync_items` | 一个采集任务包含多条标准处理明细 |
| `standards` 1 对多 `standard_processing_jobs` | 一条标准可能多次材料化或重建索引 |
| `standards` 1 对 1 `standard_indexes` | 本期每条标准最多一个 `overview` 索引 |
| `standard_search_queries` 1 对多 `standard_search_results` | 一次语义检索保存多条结果快照 |
| `standard_atlas_projections` 1 对多 `standard_atlas_points` | 一个投影版本包含多条标准点位 |
| `standards` 1 对多 `standard_atlas_points` | 一条标准可存在于多个历史投影版本中 |

## 后续细化项

本文件当前已经确定新标准库数据库、表边界、字段含义、字段类型、默认值、枚举值、主要约束、查询索引和 pgvector 使用规则。

后续实现时还需要继续补充：

- Alembic migration 或建表 SQL。
- `EMBEDDING_DIMENSIONS` 的具体数值，该值跟随最终选定的 Embedding 模型。
- 是否使用 PostgreSQL enum，或使用 `text` + check constraint。
- 从现有初稿标准库结构切换到新数据库的执行步骤；本轮默认新建 `octopus_standard_library`，不要求兼容旧标准库表结构。
