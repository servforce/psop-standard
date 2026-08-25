# 标准库模块规范文档

本文档集用于标准库模块的规范驱动开发。

本文档只约束标准库模块；视频等既有模块暂不纳入本轮重构。

## 功能范围

标准库模块包含以下功能：

1. 标准导入
   - 标准导入由后台完成，包含历史采集和周期性更新。
   - 采集范围限定为国家标准、行业标准、地方标准。
   - 国家标准包含强制性国家标准（GB）、推荐性国家标准（GB/T）、国家标准化指导性技术文件（GB/Z）。
   - 团体标准、企业标准、国际标准、国外标准暂不纳入采集范围。
   - 历史采集用于各标准来源的首次数据入库；每个网站使用独立采集脚本完成采集和存储。
   - 周期性更新用于项目运行后的持续同步，例如按固定时间间隔检查外部网站更新。
   - 周期性更新关注新增标准、已有标准元数据变化、文件变化、状态变化和无变化复查结果。
   - 国家标准和行业标准状态变化包括即将实施转为现行、现行转为废止；地方标准状态包括现行、有更新版、废止，其中有更新版按有效标准处理，有更新版转废止计入失效。
   - 历史采集和周期性更新都会进行入库决策；只有存在可下载 PDF 且需要下载或重新下载的标准才进入解析和索引流程。不可下载、仅在线阅读或只有非文件元数据变化的标准不触发解析和索引。
   - 前端只展示导入结果，不提供手动上传、手动解析、手动索引等操作。
   - 导入结果包括当前有效标准数量、最近更新时间、当前周期状态、新增现行标准数、失效标准数、失败标准数。
   - 新增现行标准数包括网站上新增的现行标准，以及由即将实施转为现行的标准。
   - 失效标准数指由现行转为废止的标准；地方标准中由现行或有更新版转为废止的标准也计入失效。
   - 后台会为有效标准生成检索向量，并生成首页 Embedding Atlas 所需的二维投影数据。

2. 标准目录管理
   - 默认展示最近一个月的有效标准。
   - 支持按关键词搜索数据库中的全部有效标准。
   - 标准列表展示来源标签。
   - 支持进入标准详情页。
   - 标准列表中每条标准展示序号、标准号、标准名称、来源、分类、发布日期、实施日期。

3. 标准详情与产物查看
   - 查看标准基础信息。
   - 查看标准来源信息。
   - 支持点击链接跳转到标准官方网站。
   - 查看解析后的 4 个 Markdown 产物。
   - 支持复制 Markdown 内容。

4. 标准检索分析
   - 支持输入检索或分析内容。
   - 使用 PostgreSQL + pgvector 进行标准级语义检索。
   - 展示检索结果。
   - 支持从检索结果进入标准详情页。
   - 支持查看检索历史，并从历史记录回填当时的检索文本、检索结果和检索时间。

5. 多来源采集状态展示
   - 标准数据来自多个来源，标准目录支持按来源查看。
   - 来源包括全部、国家标准、行业标准、地方标准。
   - 国家标准来源下支持区分强制性国家标准（GB）、推荐性国家标准（GB/T）、国家标准化指导性技术文件（GB/Z）。
   - 按来源查看时默认展示最近一个月的有效标准，也支持关键词搜索。
   - 来源级展示以有效标准条数为主；是否展示来源级索引时间、更新数量、失败数量等统计信息待后续页面设计时确定。

6. 首页标准可视化
   - 首页默认使用 Embedding Atlas 展示所有有效标准。
   - 每个标准显示为一个散点，并根据 embedding 内容在前端聚类和生成标签。
   - 点击散点可进入对应标准详情页。

## 文档目录

1. [前端页面与线框图规范](./01-standard-library-ui.md)
2. [架构设计规范](./02-standard-library-architecture.md)
3. [数据库设计规范](./03-standard-library-database.md)

## 资源目录

```text
docs/assets/wireframes/
```

## 标准库后台流水线运行顺序

本节用于说明历史采集和周期更新的执行边界。当前设计不是“Web 项目启动后默认消费所有解析和索引任务”，而是把采集、解析、索引拆成可追踪的任务阶段。

### 一、历史采集顺序

历史采集用于首次灌入或大批量补齐标准库数据，建议在项目外侧单独运行脚本。

1. 运行历史采集脚本。

```powershell
python tools/standard-collector/scripts/collect_national_pdfs.py
```

可小范围验证：

```powershell
python tools/standard-collector/scripts/collect_national_pdfs.py --max-pages 2 --max-items 60
```

行业标准、地方标准使用 SACInfo 采集脚本写入同一套新标准库表：

```powershell
python tools/standard-collector/scripts/collect_sacinfo_standards.py --source industry
python tools/standard-collector/scripts/collect_sacinfo_standards.py --source local
```

可小范围验证：

```powershell
python tools/standard-collector/scripts/collect_sacinfo_standards.py --source industry --category YD:通信 --max-pages 1 --max-items 20
python tools/standard-collector/scripts/collect_sacinfo_standards.py --source local --category 山西省 --max-pages 1 --max-items 20
```

地方标准市级分类可用 `省份|城市` 表达：

```powershell
python tools/standard-collector/scripts/collect_sacinfo_standards.py --source local --category "山西省|太原市" --max-pages 1 --max-items 20
```

历史采集成功后会写入新标准库：

```text
standards
standard_sync_jobs
standard_sync_items
```

如果某条标准存在可下载 PDF，采集线会上传 PDF 到 MinIO，写入：

```text
standards.source_pdf_bucket
standards.source_pdf_object_key
standards.source_pdf_hash
standards.source_pdf_size_bytes
```

同时只创建解析任务，不直接解析：

```text
standard_processing_jobs.job_type = 'materialize'
standard_processing_jobs.status = 'pending'
```

行业/地方脚本同样遵守这个边界：发现可下载 PDF 时上传到 `STANDARD_LIBRARY_OBJECT_STORE_BUCKET`，写入 `source_pdf_*` 字段并创建 `materialize pending`；仅在线阅读或不可下载时只保存元数据和 `online_url`，材料化/索引状态标记为 skipped。

2. 运行历史解析/索引处理脚本。

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --watch
```

这个脚本会按顺序消费新库任务：

```text
materialize pending
-> 读取 standards.source_pdf_object_key
-> 生成 standard_body.md / standard_structure.md / standard_logic.md / standard_overview.md
-> 写 MinIO
-> 更新 standards.materialize_status = 'materialized'
-> 创建 index pending job
-> 读取 standard_overview.md
-> 生成 embedding
-> 写 standard_indexes
-> 更新 standards.index_status = 'indexed'
```

也可以分阶段运行：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --materialize-only --watch
python tools/standard-collector/scripts/process_standard_library_jobs.py --index-only --watch
```

只处理一条任务用于验收：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --limit 1
```

3. 历史批处理完成后生成 Atlas 投影。

当 materialize/index pending job 已经处理完，显式运行一次 Atlas：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --atlas-only
```

也可以让处理脚本在本轮批处理结束后自动刷新一次 Atlas：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --atlas
```

Atlas 投影会读取当前有效且已索引的标准：

```text
standards.materialize_status = 'materialized'
standards.index_status = 'indexed'
standard_indexes.index_kind = 'overview'
standard_indexes.embedding_model = STANDARD_EMBEDDING_MODEL
standard_indexes.embedding_dimensions = STANDARD_EMBEDDING_DIMENSIONS
```

然后写入：

```text
standard_atlas_projections
standard_atlas_points
```

### 二、周期更新顺序

周期更新用于项目运行后的持续同步，由 Web 项目启动后的 scheduler 控制。和历史采集不同，周期更新应当自动消费自己产生的解析和索引任务，形成一条项目内自动链路。

默认配置为：

```text
STANDARD_UPDATE_SCHEDULER_ENABLED=false
```

需要启用周期更新时，在 `.env` 中设置：

```text
STANDARD_UPDATE_SCHEDULER_ENABLED=true
```

项目启动后，周期更新会按配置间隔运行国家标准更新检查：

```text
StandardUpdateScheduler
-> national: standard_update_service.run_national_update(...)
-> industry/local: collect_sacinfo_standards.py 的共享采集逻辑
-> 只消费本轮 sync_job 创建的 materialize/index processing jobs
-> 如有新索引，刷新 Atlas
```

当前项目内自动 scheduler 已接国家、行业、地方三类来源。国家标准默认跟随 scheduler 运行；行业/地方默认关闭，需要来源级配置显式开启：

```text
STANDARD_UPDATE_SCHEDULER_ENABLED=true
STANDARD_UPDATE_INDUSTRY_ENABLED=true
STANDARD_UPDATE_INDUSTRY_CATEGORIES=YD:通信,JT:交通

STANDARD_UPDATE_LOCAL_ENABLED=true
STANDARD_UPDATE_LOCAL_CATEGORIES=山西省,北京市
```

默认 `STANDARD_UPDATE_SACINFO_REQUIRE_CATEGORIES=true`。如果开启行业/地方但不填分类，scheduler 会跳过该来源，避免项目启动后大范围扫描行业和地方站点。确实要全量扫描时，才把它改成 `false`，并配合：

```text
STANDARD_UPDATE_SACINFO_MAX_PAGES=1
STANDARD_UPDATE_SACINFO_MAX_ITEMS=50
```

周期更新发现新增标准或 PDF 文件变化时，会写入新标准库，并创建解析任务：

```text
standards
standard_sync_jobs
standard_sync_items
standard_processing_jobs(job_type='materialize', status='pending')
```

随后周期更新流程会立即消费这条解析任务：

```text
run materialize job
-> 读取 standards.source_pdf_object_key
-> 生成 4 个 Markdown
-> 写 MinIO
-> 更新 standards.materialize_status = 'materialized'
-> 创建 standard_processing_jobs(job_type='index', status='pending')
```

解析成功后，周期更新流程会继续消费该标准的索引任务：

```text
run index job
-> 读取 standard_overview.md
-> 生成 embedding
-> 写 standard_indexes
-> 更新 standards.index_status = 'indexed'
```

如果本轮周期更新成功索引了新增标准，周期更新流程会在结尾自动刷新一次 Atlas：

```text
run atlas projection
-> 读取当前有效标准 + standard_indexes.embedding
-> 写 standard_atlas_projections
-> 写 standard_atlas_points
-> 将新投影标记为 is_current = true
-> 将旧投影标记为 is_current = false
```

因此周期更新的自动链路是：

```text
周期更新采集成功
-> 自动排解析任务
-> 自动执行该解析任务
-> 解析成功后自动排索引任务
-> 自动执行该索引任务
-> 本轮有新增索引时自动刷新 Atlas
```

外部 job processor 仍然可以用于处理历史采集任务，或者用于补跑周期更新中因为服务中断、限流、模型错误而遗留的 pending/failed 任务：

```powershell
python tools/standard-collector/scripts/process_standard_library_jobs.py --watch
```

可选方式：显式开启 Web 内部 processing worker，让 Web 项目启动后消费所有 pending 解析/索引任务。

```text
STANDARD_LIBRARY_PROCESSING_WORKER_ENABLED=true
```

默认值保持关闭：

```text
STANDARD_LIBRARY_PROCESSING_WORKER_ENABLED=false
```

默认关闭这个通用 worker，是为了避免 Web 项目启动后吞掉历史采集积压任务。周期更新自己的新增标准不依赖这个通用 worker，而是在周期更新流程内同步推进解析和索引。

### 三、关键触发关系

当前实际触发关系如下：

```text
历史采集成功
-> 自动创建 materialize pending job
-> 不直接解析

历史解析成功
-> 自动创建 index pending job
-> 不直接索引

历史外部 job processor 或显式开启的 worker
-> 消费 materialize/index pending job
-> 执行解析和索引

历史 Atlas
-> 在历史解析/索引批处理完成后手动运行 --atlas-only 或 --atlas
-> 生成 standard_atlas_projections / standard_atlas_points

周期更新采集成功
-> 自动创建 materialize pending job
-> 周期更新流程立即执行解析
-> 解析成功后自动创建 index pending job
-> 周期更新流程立即执行索引
-> 本轮有新增索引时自动刷新 Atlas
```

因此不要把历史采集和周期更新混为一件事。历史批处理强调外部可控、可限流、可重跑；周期更新强调项目运行后自动同步、自动解析、自动索引、自动刷新 Atlas。`process_standard_library_jobs.py` 主要服务历史批处理和补跑积压任务；`StandardLibraryProcessingWorker` 是显式 opt-in 的通用消费者，默认关闭。
