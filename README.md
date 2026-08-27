# PSOP Standard Library Service

PSOP Standard Library Service 是 PSOP 体系中的标准文档知识库服务，面向国家标准、行业标准、地方标准等技术标准资料的采集、存储、解析、索引和检索场景。项目负责从国家标准全文公开系统（https://openstd.samr.gov.cn/bzgk/std/）、行业标准信息服务平台（https://hbba.sacinfo.org.cn/）和地方标准信息服务平台（https://dbba.sacinfo.org.cn/）采集标准元数据和 PDF 文件，将原始文档整理为可阅读、可检索、可向量化的结构化内容，并通过 API 与前端页面提供标准目录浏览、标准详情查看、语义搜索、标准图谱和后台更新处理能力，帮助上层系统或用户更高效地管理和使用标准库资源。

标准库 API 与前端服务。

技术栈：FastAPI + PostgreSQL（pgvector）+ MinIO（S3 协议），前端为原生 HTML/CSS/JS。

## 启动

### Linux / WSL

```bash
cd /mnt/d/work/psop_standard
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
./run.sh
```

### Windows

```powershell
cd D:\work\psop_standard
python --version
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
.\run.ps1
```

打开：

```text
http://127.0.0.1:8091/
```

应用入口：`app.standard_main:app`

启动前需确保 PostgreSQL 已装 `pgvector`、`pgcrypto`、`pg_trgm` 扩展，并在 `.env` 中配好
`STANDARD_LIBRARY_DATABASE_URL`、MinIO 连接和 `MODEL_API_KEY`。

## 双接口说明

本项目目前同时包含两套标准库 API：

### `/api/standard-library` （新接口）

- **状态**：按 `docs/` 中的规范设计，功能正在迁移中，前端已切换到新接口
- **数据库**：`STANDARD_LIBRARY_DATABASE_URL` → `octopus_standard_library` 库
- **模型**：`app/models/standard_library.py` (10 张表)
- **功能**：核心检索、目录、图谱、markdown 读取
- **端点**：
  - `GET /api/standard-library/summary` - 库概览
  - `GET /api/standard-library/catalog` - 标准目录（分页/过滤）
  - `GET /api/standard-library/atlas` - 标准图谱（向量降维可视化）
  - `POST /api/standard-library/search` - 语义检索
  - `GET /api/standard-library/search/history` - 检索历史
  - `GET /api/standard-library/{id}` - 标准详情
  - `GET /api/standard-library/{id}/markdown/{kind}` - 标准文档（overview/structure/logic/body）

### `/api/standards` （旧接口）

- **状态**：单体时期的遗留接口，包含更多管理功能，后台任务和工具脚本仍在使用
- **数据库**：与新接口共用 `STANDARD_LIBRARY_DATABASE_URL`（原先独立的 `DATABASE_URL` 已移除）
- **模型**：`app/models/entities.py` (6 张表)
- **功能**：标准采集、解析、索引重建、OpenSTD 爬取、周期更新等管理功能
- **后续计划**：待后台任务迁移到新系统后逐步下线

**架构说明**：全项目只连一个数据库 `octopus_standard_library`。前端已切到新接口；后台工具和定时任务还在用旧
接口，所以两套暂时并存。注意旧模型（`entities.py`）与新模型（`standard_library.py`）对 `standards` 等同名表
的列定义不一致，旧接口的写操作在当前库上未经验证。拆分过程中新系统复用了旧系统的部分通用能力
（`app/services/standards.py` 里的 Qwen 生成器、embedding 客户端、提示词模板等），未来会抽成独立模块。

## 功能与实现

| 功能 | 实现方式 |
| --- | --- |
| **标准采集** | `tools/standard-collector/` 脚本抓取国标网站（openstd.samr.gov.cn、sacinfo）列表页与详情页，下载 PDF 存入 MinIO，元数据落库 |
| **PDF 解析（materialize）** | `pypdf` 提取正文 → 调 Qwen 生成四份 markdown（`overview` 检索摘要 / `structure` 章节结构 / `logic` 逻辑关系 / `body` 正文），提示词见 `app/templates/*.md`，产物存 MinIO |
| **向量索引（index）** | 用 `text-embedding-v4` 对 `overview` 文本做 embedding，写入 `standard_indexes` 表的 `vector(1024)` 列，建 HNSW 余弦索引 |
| **语义检索** | 查询文本 embedding 后与 `standard_indexes` 做 pgvector 余弦相似度检索，结果按分数排序并落 `standard_search_queries` / `standard_search_results` 留痕 |
| **标准目录** | 按标准号精确匹配或名称模糊匹配（`pg_trgm` GIN 索引），支持按来源（国标/行业/地方）过滤和分页 |
| **标准图谱** | 取全部 `overview` 向量，用自实现的降维算法投影到二维（`standard_library_atlas.py` 的 `project_embeddings`），结果缓存在 `standard_atlas_projections` / `standard_atlas_points` |
| **周期更新** | `StandardUpdateScheduler` 按 `STANDARD_UPDATE_INTERVAL_SECONDS` 轮询国标网站，比对 fingerprint 发现新增/更新/废止，自动排队 materialize + index |
| **后台任务** | `StandardLibraryProcessingWorker` 轮询 `standard_processing_jobs` 表消费任务；启动时 `standard_job_recovery.py` 把中断的任务标记为 failed |
| **对象存储** | `app/services/storage.py` 封装 boto3 S3 客户端，PDF 和 markdown 都存 MinIO，前端通过 `GET /api/objects/{key}` 代理读取 |

## 代码结构

### 入口与 API

| 文件 | 职责 |
| --- | --- |
| `app/standard_main.py` | FastAPI 应用入口。初始化两个库、挂载路由和静态资源、启动调度器与 worker、注册对象存储代理和健康检查 |
| `app/api/standard_library.py` | 新接口路由（`/api/standard-library`），薄封装，逻辑都在 service 层 |
| `app/api/standards.py` | 旧接口路由（`/api/standards`），部分端点已返回 410 废弃提示 |
| `app/api/config.py` | `GET /api/config`，给前端暴露运行时开关（如周期更新是否启用） |

### 配置

| 文件 | 职责 |
| --- | --- |
| `app/core/env.py` | `.env` 加载与类型转换工具（`env`/`env_bool`/`env_list`），处理 BOM |
| `app/core/standard_config.py` | 标准库全部配置项，导出 `standard_settings` 单例 |
| `app/core/storage_config.py` | MinIO/S3 连接配置，导出 `storage_settings` |

### 数据库与模型

| 文件 | 职责 |
| --- | --- |
| `app/db/standard_library.py` | 新库引擎与 session，建 pgvector 扩展、自定义 `Vector` 类型、创建索引 |
| `app/db/session.py` | 旧库引擎与 session，含旧库的建表和迁移语句 |
| `app/db/sql/init_standard_library.sql` | 新库完整建表 DDL，与 ORM 模型对应，可用于手工初始化 |
| `app/models/standard_library.py` | 新库 10 张表的 ORM 定义 |
| `app/models/entities.py` | 旧库 6 张表的 ORM 定义 |

### 业务服务（新系统）

| 文件 | 职责 |
| --- | --- |
| `app/services/standard_library.py` | 新接口核心服务。目录查询、语义检索、图谱读取、检索历史、markdown 代理 |
| `app/services/standard_library_materialize.py` | PDF → 四份 markdown。调 Qwen API 生成，存入 MinIO，更新 `standards` 表状态 |
| `app/services/standard_library_index.py` | markdown → 向量。调 embedding API，写 `standard_indexes` 表，建 HNSW 索引 |
| `app/services/standard_library_atlas.py` | 图谱降维与缓存。自实现的向量投影算法（非 t-SNE），结果存 `standard_atlas_projections/points` |
| `app/services/standard_library_collect.py` | 采集数据落库。从外部爬虫结果写入新库 `standards` / `standard_sync_jobs/items` |
| `app/services/standard_library_sacinfo_update.py` | sacinfo 增量更新逻辑（国标中心 API 爬虫） |

### 业务服务（旧系统 / 通用）

| 文件 | 职责 |
| --- | --- |
| `app/services/standards.py` | **新旧共用**。含 `QwenMarkdownGenerator` 生成四份 markdown、`StandardEmbeddingClient` 调 embedding API、提示词模板（502/513 行）、旧接口 service 逻辑 |
| `app/services/standard_update.py` | 周期更新抓取逻辑（openstd + sacinfo 增量比对），写旧库 |
| `app/services/openstd_crawl.py` | OpenSTD 国标网站全量/增量爬虫，结果落旧库 |
| `app/services/storage.py` | 对象存储封装，boto3 S3 协议，被新旧系统共用 |
| `app/services/standard_job_recovery.py` | 启动时把中断任务（status=running 但进程已死）标记为 failed |
| `app/services/audit.py` | 历史遗留的调用记录接口，现为空实现（stub） |

### 后台任务

| 文件 | 职责 |
| --- | --- |
| `app/jobs/standard_library_processing_worker.py` | 轮询 `standard_processing_jobs` 消费 materialize / index 任务，受 `STANDARD_LIBRARY_PROCESSING_WORKER_ENABLED` 控制 |
| `app/jobs/standard_update_scheduler.py` | 按固定间隔触发标准更新检查，受 `STANDARD_UPDATE_SCHEDULER_ENABLED` 控制 |
| `app/jobs/openstd_worker.py` | OpenSTD 爬取任务 worker（旧系统） |

### 提示词模板

| 文件 | 职责 |
| --- | --- |
| `app/templates/standard_overview.md` | 检索摘要模板。这份是向量检索的核心，决定 embedding 质量 |
| `app/templates/standard_structure.md` | 章节结构模板 |
| `app/templates/standard_logic.md` | 逻辑关系模板 |
| `app/templates/standard_body.md` | 正文提取模板 |

### 前端

| 文件 | 职责 |
| --- | --- |
| `static/standard.html` | 单页应用骨架 |
| `static/assets/standard-app.js` | 全部前端逻辑。状态管理、渲染、API 调用，只调 `/api/standard-library` 和 `/api/config` |
| `static/assets/style.css` | 样式表（含少量拆分前遗留的 `.video-*` 死规则） |

### 工具脚本

| 文件 | 职责 |
| --- | --- |
| `tools/standard-collector/scripts/collect_national_pdfs.py` | 全量采集国标 PDF |
| `tools/standard-collector/scripts/collect_sacinfo_standards.py` | 从 sacinfo 采集标准元数据 |
| `tools/standard-collector/scripts/sync_national_updates.py` | 增量同步国标更新 |
| `tools/standard-collector/scripts/materialize_and_index_standards.py` | 批量执行 materialize + index |
| `tools/standard-collector/scripts/process_standard_library_jobs.py` | 手动消费任务队列 |
| `tools/standard-collector/scripts/backfill_standard_effective_dates.py` | 回填实施日期字段 |
| `tools/openstd-importer/scripts/openstd_importer.py` | OpenSTD 数据导入 |

### 测试

```bash
source .venv/bin/activate
python -m pytest
```

`tests/` 下覆盖 materialize、index、collect、atlas 和 openstd 导入。

## 规范文档

`docs/` 下是标准库模块的规范驱动开发文档，新接口按这些规范实现：

| 文件 | 内容 |
| --- | --- |
| `docs/01-standard-library-ui.md` | 前端界面线框图 |
| `docs/02-standard-library-architecture.md` | 架构设计规范 |
| `docs/03-standard-library-database.md` | 数据库设计规范 |
