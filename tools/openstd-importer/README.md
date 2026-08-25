# OpenSTD Importer

国家标准全文公开系统定制采集工具。

第一版默认采集国家标准下三个可下载分类：

- 强制性国家标准
- 推荐性国家标准
- 指导性技术文件

工具负责发现官网标准条目、读取详情页下载入口、下载并校验 PDF 临时文件。正式入库、MinIO 上传、任务状态和断点续跑由主项目 `app/services/openstd_crawl.py` 负责。

## Setup

```powershell
pip install -r requirements.txt
```

## Commands

发现三个分类的列表：

```powershell
python tools/openstd-importer/scripts/openstd_importer.py discover --scope all_national_standards --max-pages 1 --output-json
```

只发现某一个分类：

```powershell
python tools/openstd-importer/scripts/openstd_importer.py discover --scope mandatory_national --max-pages 1 --output-json
python tools/openstd-importer/scripts/openstd_importer.py discover --scope recommended_national --max-pages 1 --output-json
python tools/openstd-importer/scripts/openstd_importer.py discover --scope gbz_guidance --max-pages 1 --output-json
```

检查详情页：

```powershell
python tools/openstd-importer/scripts/openstd_importer.py inspect --detail-url "https://openstd.samr.gov.cn/..." --output-json
```

下载详情页 PDF：

```powershell
python tools/openstd-importer/scripts/openstd_importer.py download --detail-url "https://openstd.samr.gov.cn/..." --output-dir work/standards/openstd_tmp --output-json
```

## Scope

- 默认只保留状态为 `现行`、`即将实施` 的标准。
- 状态包含 `废止` 的标准不会进入采集结果。
- 只采集官网明确提供下载入口的 PDF。
- 不绕过验证码、登录、权限或题录限制。
- 不把在线阅读页面打印成 PDF 当作标准原件。
- 下载文件只作为临时产物，主项目校验后上传 MinIO。
