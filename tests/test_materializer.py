from pathlib import Path

from app.models.entities import Standard
from app.services.standards import (
    MaterializedStandard,
    build_four_markdowns,
    build_standard_search_content,
    validate_generated_markdown,
)


class FakeMarkdownGenerator:
    def generate(
        self,
        *,
        standard_name: str,
        source_pdf: str,
        pdf_path: Path,
        progress=None,
        timeout_seconds=None,
    ) -> MaterializedStandard:
        def front(role: str, extra: str = "") -> str:
            return f"""---
standard_name: "{standard_name}"
standard_number: "GB/T 1-2024"
source_pdf: "{source_pdf}"
document_role: "{role}"
standard_id: "demo"
schema_version: "simple-1.0"
{extra}---
"""

        body = front("standard_body") + f"\n# {standard_name}\n\n1 范围\n本文件规定了作业要求。\n2 技术要求\n应检查设备状态。"
        structure = front("standard_structure") + "\n# 章节结构\n\n| 章节 | 内容 |\n| --- | --- |\n| 2 技术要求 | 检查设备状态 |"
        logic = front("standard_logic") + "\n# 逻辑关系\n\n技术要求承接范围并形成执行要求。"
        overview = front("standard_overview") + f"""
# 标准检索概要

## 1. 标准基本信息

- 标准名称：{standard_name}
- 标准编号：GB/T 1-2024
- 来源 PDF：{source_pdf}
- 标准对象：作业设备
- 标准主题：设备状态检查

## 2. 适用画像

- 适用对象：作业设备
- 适用场景：设备状态检查、作业检查
- 适用工序：状态确认
- 相关风险：设备状态异常
- 管理要求：作业前检查
- 质量要求：检查记录完整

## 3. 典型检索命中描述

- 当视频分析涉及设备状态检查时，应优先匹配本标准。
- 当查询涉及作业前检查时，应优先匹配本标准。

## 4. 边界说明

- 与作业检查无关的内容通常不优先匹配本标准。

## 5. 检索摘要
根据是否涉及设备状态检查进行判断。
"""
        return MaterializedStandard(
            standard_id="demo",
            markdown={"body": body, "structure": structure, "logic": logic, "overview": overview},
        )


def test_build_four_markdowns_contains_all_kinds():
    result = build_four_markdowns(
        standard_name="示例标准",
        source_pdf="示例标准.pdf",
        pdf_path=Path("示例标准.pdf"),
        generator=FakeMarkdownGenerator(),
    )
    assert set(result.markdown) == {"overview", "structure", "logic", "body"}
    assert "示例标准" in result.markdown["overview"]
    assert "技术要求" in result.markdown["structure"]


def test_validate_generated_markdown_requires_simple_overview_sections():
    markdown = """---
standard_name: "示例标准"
standard_number: "GB/T 1-2024"
source_pdf: "示例标准.pdf"
document_role: "standard_overview"
standard_id: "demo"
schema_version: "simple-1.0"
---

# 标准检索概要

## 1. 标准基本信息

- 标准名称：示例标准
"""
    try:
        validate_generated_markdown("overview", markdown, standard_name="示例标准")
    except ValueError as exc:
        assert "适用画像" in str(exc)
        assert "检索摘要" in str(exc)
    else:
        raise AssertionError("overview validation should require simple search sections")


def test_build_standard_search_content_uses_overview_body_without_front_matter():
    overview = FakeMarkdownGenerator().generate(
        standard_name="示例标准",
        source_pdf="示例标准.pdf",
        pdf_path=Path("示例标准.pdf"),
    ).markdown["overview"]
    standard = Standard(id="demo", name="示例标准", code="GB/T 1-2024", source_pdf_object_key="standards/demo/source.pdf")
    content = build_standard_search_content(standard=standard, overview_markdown=overview)
    assert "document_role" not in content
    assert "适用画像" in content
    assert "设备状态检查" in content
