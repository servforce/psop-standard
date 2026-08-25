from __future__ import annotations

import json
import hashlib
import re
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Protocol

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.standard_config import standard_settings as settings
from app.models.entities import Standard, StandardProcessingJob, StandardSearchQuery, StandardSearchResult
from app.services.audit import finish_call, logged_call_with_session
from app.services.storage import StorageService, storage_service


MARKDOWN_KINDS = {"overview", "structure", "logic", "body"}
MARKDOWN_FILENAMES = {
    "body": "standard_body.md",
    "structure": "standard_structure.md",
    "logic": "standard_logic.md",
    "overview": "standard_overview.md",
}
ROLE_BY_KIND = {
    "body": "standard_body",
    "structure": "standard_structure",
    "logic": "standard_logic",
    "overview": "standard_overview",
}
FRONT_MATTER_RE = re.compile(r"\A\s*---\s*\n(?P<front>.*?)\n---\s*(?:\n|$)", re.DOTALL)
STANDARD_SEARCH_INDEX_KIND = "overview"
CURRENT_EFFECTIVE_STANDARD_SOURCE_STATUS = "active"
CURRENT_EFFECTIVE_STANDARD_MATERIALIZE_STATUS = "materialized"
CURRENT_EFFECTIVE_STANDARD_INDEX_STATUS = "indexed"
MATERIALIZE_LENGTH_ERROR_STATUS = "length_error"
LENGTH_FINISH_REASONS = {"length", "max_tokens", "token_limit", "output_token_limit", "max_output_tokens"}


class MaterializeLengthError(RuntimeError):
    """Raised when a standard materialization step hits an input or output length limit."""


def standard_markdown_object_key(standard_id: str, kind: str) -> str:
    return f"standards/{standard_id}/markdown/{MARKDOWN_FILENAMES[kind]}"


@dataclass(frozen=True, slots=True)
class MaterializedStandard:
    standard_id: str
    markdown: dict[str, str]


class MarkdownGenerator(Protocol):
    def generate(
        self,
        *,
        standard_name: str,
        source_pdf: str,
        pdf_path: Path,
        progress: Callable[[str, int, str], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> MaterializedStandard:
        ...


def standard_id_from_name(name: str) -> str:
    stem = Path(name).stem
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()
    if ascii_part:
        return ascii_part[:80]
    return "standard_" + hashlib.sha1(stem.encode("utf-8")).hexdigest()[:18]


def safe_filename(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name)
    return name or "standard.pdf"


PDF_FONT_CHAR_MAP = str.maketrans(
    {
        "犃": "A",
        "犅": "B",
        "犆": "C",
        "犇": "D",
        "犈": "E",
        "犉": "F",
        "犌": "G",
        "犎": "H",
        "犐": "I",
        "犑": "J",
        "犓": "K",
        "犔": "L",
        "犕": "M",
        "犖": "N",
        "犗": "O",
        "犘": "P",
        "犙": "Q",
        "犚": "R",
        "犛": "S",
        "犜": "T",
        "犝": "U",
        "犞": "V",
        "犠": "W",
        "犡": "X",
        "犢": "Y",
        "犣": "Z",
        "犪": "a",
        "犫": "b",
        "犮": "c",
        "犱": "d",
        "犲": "e",
        "犳": "f",
        "犵": "g",
        "犺": "h",
        "犻": "i",
        "犼": "j",
        "犽": "k",
        "犾": "l",
        "犿": "m",
        "狀": "n",
        "狅": "o",
        "狆": "p",
        "狇": "q",
        "狉": "r",
        "狊": "s",
        "狋": "t",
        "狌": "u",
        "狏": "v",
        "狑": "w",
        "狓": "x",
        "狔": "y",
        "狕": "z",
    }
)


def extract_pdf_text(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少依赖 pypdf，无法本地抽取 PDF 原文。请先安装 requirements.txt 中的 pypdf。") from exc

    reader = PdfReader(str(pdf_path))
    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = normalize_extracted_pdf_text(page.extract_text() or "")
        if text.strip():
            pages.append(f"<!-- page {index} -->\n{text.strip()}")
    extracted = "\n\n".join(pages).strip()
    if len(extracted) < 50:
        raise ValueError("PDF 本地文本抽取结果过短，可能是扫描版或文字层不可读。")
    return extracted


def normalize_extracted_pdf_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.translate(PDF_FONT_CHAR_MAP)
    normalized = re.sub(r"[\ue000-\uf8ff]", "-", normalized)
    normalized = normalized.replace("\u00a0", " ").replace("\u3000", " ")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"(?m)^\s*书\s*$", "", normalized)
    return normalized.strip()


def build_four_markdowns(
    *,
    standard_name: str,
    source_pdf: str,
    pdf_path: Path,
    generator: MarkdownGenerator | None = None,
    progress: Callable[[str, int, str], None] | None = None,
    timeout_seconds: float | None = None,
) -> MaterializedStandard:
    qwen_generator = generator or QwenMarkdownGenerator.from_settings()
    return qwen_generator.generate(
        standard_name=standard_name,
        source_pdf=source_pdf,
        pdf_path=pdf_path,
        progress=progress,
        timeout_seconds=timeout_seconds,
    )


class QwenMarkdownGenerator:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float,
        top_p: float,
        max_tokens: int,
        body_max_tokens: int,
        structure_max_tokens: int,
        logic_max_tokens: int,
        overview_max_tokens: int,
        timeout_seconds: float,
        max_input_chars: int,
        file_upload_purpose: str,
        http_client: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.max_tokens_by_kind = {
            "body": body_max_tokens,
            "structure": structure_max_tokens,
            "logic": logic_max_tokens,
            "overview": overview_max_tokens,
        }
        self.timeout_seconds = timeout_seconds
        self.max_input_chars = max_input_chars
        self.file_upload_purpose = file_upload_purpose
        self.http_client = http_client

    @classmethod
    def from_settings(cls) -> "QwenMarkdownGenerator":
        if not settings.qwen_text_api_key:
            raise ValueError(
                "缺少 Qwen 文本模型 API key：请在 .env 中配置 QWEN_TEXT_API_KEY。"
            )
        return cls(
            api_key=settings.qwen_text_api_key,
            base_url=settings.qwen_text_base_url,
            model=settings.qwen_text_model,
            temperature=settings.qwen_text_temperature,
            top_p=settings.qwen_text_top_p,
            max_tokens=settings.qwen_text_max_tokens,
            body_max_tokens=settings.qwen_standard_body_max_tokens,
            structure_max_tokens=settings.qwen_standard_structure_max_tokens,
            logic_max_tokens=settings.qwen_standard_logic_max_tokens,
            overview_max_tokens=settings.qwen_standard_overview_max_tokens,
            timeout_seconds=settings.qwen_text_timeout_seconds,
            max_input_chars=settings.qwen_text_max_input_chars,
            file_upload_purpose=settings.qwen_text_file_upload_purpose,
        )

    def generate(
        self,
        *,
        standard_name: str,
        source_pdf: str,
        pdf_path: Path,
        progress: Callable[[str, int, str], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> MaterializedStandard:
        effective_timeout_seconds = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        standard_id = standard_id_from_name(source_pdf)
        standard_number = guess_standard_number(source_pdf, standard_name)

        emit_progress(progress, "extracting_pdf_text", 12, "正在本地抽取 PDF 原文文本")
        pdf_text = extract_pdf_text(pdf_path)

        emit_progress(progress, "generating_body", 25, "正在调用 qwen3.7-plus 根据 PDF 原文文本排版 standard_body.md")
        body = self._generate_body(
            standard_name=standard_name,
            standard_number=standard_number,
            source_pdf=source_pdf,
            pdf_text=pdf_text,
            timeout_seconds=effective_timeout_seconds,
        )
        body = ensure_markdown_front_matter(
            body,
            standard_name=standard_name,
            standard_number=standard_number,
            source_pdf=source_pdf,
            document_role="standard_body",
        )
        validate_generated_markdown("body", body, standard_name=standard_name)

        emit_progress(progress, "generating_structure", 46, "正在基于 standard_body.md 生成 standard_structure.md")
        structure = self._generate_from_body(
            kind="structure",
            standard_name=standard_name,
            standard_number=standard_number,
            source_pdf=source_pdf,
            standard_body=body,
            timeout_seconds=effective_timeout_seconds,
        )
        structure = ensure_markdown_front_matter(
            structure,
            standard_name=standard_name,
            standard_number=standard_number,
            source_pdf=source_pdf,
            document_role="standard_structure",
        )
        validate_generated_markdown("structure", structure, standard_name=standard_name)

        emit_progress(progress, "generating_logic", 62, "正在基于 standard_body.md 生成 standard_logic.md")
        logic = self._generate_from_body(
            kind="logic",
            standard_name=standard_name,
            standard_number=standard_number,
            source_pdf=source_pdf,
            standard_body=body,
            timeout_seconds=effective_timeout_seconds,
        )
        logic = ensure_markdown_front_matter(
            logic,
            standard_name=standard_name,
            standard_number=standard_number,
            source_pdf=source_pdf,
            document_role="standard_logic",
        )
        validate_generated_markdown("logic", logic, standard_name=standard_name)

        emit_progress(progress, "generating_overview", 78, "正在基于 standard_body.md 生成 standard_overview.md")
        overview = self._generate_from_body(
            kind="overview",
            standard_name=standard_name,
            standard_number=standard_number,
            source_pdf=source_pdf,
            standard_body=body,
            timeout_seconds=effective_timeout_seconds,
        )
        overview = ensure_markdown_front_matter(
            overview,
            standard_name=standard_name,
            standard_number=standard_number,
            source_pdf=source_pdf,
            document_role="standard_overview",
            extra={"standard_id": standard_id, "schema_version": "simple-1.0"},
        )
        validate_generated_markdown("overview", overview, standard_name=standard_name)
        emit_progress(progress, "validating_markdown", 88, "四个 Markdown 已生成并通过基础校验")

        return MaterializedStandard(
            standard_id=standard_id,
            markdown={"body": body, "structure": structure, "logic": logic, "overview": overview},
        )

    def _generate_body(
        self,
        *,
        standard_name: str,
        standard_number: str,
        source_pdf: str,
        pdf_text: str,
        timeout_seconds: float | None = None,
    ) -> str:
        body_context = require_text_within_input_limit(
            kind="body",
            source_label="pdf_text",
            text=pdf_text,
            max_chars=self.max_input_chars,
        )
        prompt = f"""请基于下面提供的 PDF 原文抽取文本，生成 `standard_body.md`。

固定要求：
- 只输出目标 Markdown 文件内容，不要解释，不要使用代码块包裹。
- 顶部必须包含 YAML front matter，字段为 standard_name、standard_number、source_pdf、document_role。
- document_role 必须是 "standard_body"。
- `standard_body.md` 的具体正文格式由你根据 PDF 原文抽取文本决定。
- 生成完整的标准正文整理稿。
- 保留 PDF 首页中的标准基本信息。
- 保留前言和正文内容。
- 不要输出“目次”“目录”“Contents”“标准章节目录”等目录部分；目录只应由 `standard_structure.md` 生成和承载。
- 如果 PDF 原文中存在只列章节编号和章节标题的目录/目次段落，只将其作为理解章节顺序的参考，不要写入 `standard_body.md`。
- 保留章节编号、条款编号、术语编号、表格标题、表格内容、注释、来源说明。
- 表格尽量整理为 Markdown 表格。
- 删除明显的页眉、页脚、目录点线、孤立页码等 PDF 排版噪声。
- 不需要标注内容来自 PDF 第几页。
- 不要把正文改写成摘要。
- 不要加入你自己的解释、建议或扩展内容。
- 如果某处内容无法识别，可以在 Markdown 中直接标注需要核对原文。
- 只能根据下方“PDF 原文抽取文本”整理，不要根据文件名、标准常识或训练知识补写正文。
- 不要添加 PDF 原文抽取文本中不存在的章节、条款、引用标准、术语或附录。
- 抽取文本中可能存在全角字符、异常断行或少量字体映射噪声；只做排版清理和明显字符修复，不要改变条款含义。

建议 front matter：
---
standard_name: "{yaml_escape(standard_name)}"
standard_number: "{yaml_escape(standard_number or '待模型识别')}"
source_pdf: "{yaml_escape(source_pdf)}"
document_role: "standard_body"
---

源 PDF 文件名：{source_pdf}
标准名称参考：{standard_name}
标准编号参考：{standard_number or "请从 PDF 中识别"}

PDF 原文抽取文本：
{body_context}
"""
        return self._chat(
            prompt,
            kind="body",
            max_tokens=self.max_tokens_by_kind["body"],
            timeout_seconds=timeout_seconds,
        )

    def _upload_pdf_for_model(self, *, pdf_path: Path, source_pdf: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": (source_pdf, pdf_path.read_bytes(), "application/pdf")}
        data = {"purpose": self.file_upload_purpose}
        url = self._files_url()
        if self.http_client is not None:
            response = self.http_client.post(url, headers=headers, files=files, data=data, timeout=self.timeout_seconds)
        else:
            with httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
                response = client.post(url, headers=headers, files=files, data=data)
        response.raise_for_status()
        payload = response.json()
        file_id = payload.get("id") or payload.get("file_id")
        if not file_id:
            raise ValueError(f"Qwen file upload response does not contain file id: {payload}")
        return f"fileid://{file_id}"

    def _generate_from_body(
        self,
        *,
        kind: str,
        standard_name: str,
        standard_number: str,
        source_pdf: str,
        standard_body: str,
        timeout_seconds: float | None = None,
    ) -> str:
        if kind == "structure":
            task = """生成 `standard_structure.md`。

目标定位：
`standard_structure.md` 只描述标准 PDF 自身的静态架构，用来回答“这份标准由哪些部分组成、章节层级如何、每个部分在文档架构中承担什么作用”。
它不是写作模板，也不分析章节之间的逻辑关系。

要求：
- 只基于 standard_body.md 提取标准架构，不要引入 standard_body.md 中不存在的章节或内容。
- 只写静态架构信息，不要写章节之间的逻辑关系、前后依赖、风险控制逻辑、流程推理、输入映射规则或生成约束；这些属于 standard_logic.md。
- 不要写“后续生成时应填入的内容”“可复用信息槽位”“可复用表格模板”“建议输出文档骨架”等写作模板内容。
- 不要把章节条款改写成摘要，重点是目录、层级、作用和板块划分。
- 不需要 PDF 页码。
- 正文标题使用 `# 标准架构`。

正文必须包含以下 5 个部分：

## 1. 标准文档身份
用表格列出标准名称、标准编号、英文名称、标准类型、适用对象、主要内容范围等身份信息。没有识别到的字段可以省略，不要编造。

## 2. 标准章节目录
按标准原文顺序列出主章节目录，保留章节编号和原章节名称。

## 3. 标准章节层级
用树形结构展示章节层级，保留主章节和可识别的二级章节。

## 4. 各章节作用
用表格说明每个主章节在标准自身架构中的作用。字段建议为：章节、章节作用。
这里只说明该章节在文档结构中的定位，不写章节间承接关系。

## 5. 内容板块划分
将标准章节归并为若干内容板块，用表格说明板块、对应章节和主要内容。
板块划分应体现文档静态组成，不写后续生成建议。"""
        elif kind == "logic":
            task = """生成 `standard_logic.md`。

目标定位：
`standard_logic.md` 描述标准内部的逻辑关系，用来回答“这份标准是按什么逻辑展开的、主章节之间如何承接、主要章节内部如何组织”。
它不是标准目录，也不是条款摘要。

要求：
- 只基于 standard_body.md 提取逻辑关系，不要编造 standard_body.md 中不存在的内容。
- 不要重复 standard_structure.md 的静态目录、章节层级和内容板块。
- 不要把正文条款逐条改写成摘要；重点是抽象“关系”和“逻辑”。
- 保持高层概括，但必须包含主要章节的章节内部关系。
- 不需要 PDF 页码。
- 正文标题使用 `# 标准逻辑`。

正文必须包含以下 5 个部分：

## 1. 总体逻辑
用一段话说明本标准整体按什么逻辑组织，并给出一个简短的“逻辑链”。

## 2. 章节间主线关系
说明主章节之间的先后关系和承接关系。可以使用流程图式文本和表格，字段建议为：逻辑阶段、对应章节、承接关系。
这一部分只写章节之间的主线，不要展开每章内部细节。

## 3. 章节内部关系
用表格说明主要章节内部是如何组织的。字段建议为：章节、内部组织方式、关键关系。
重点说明每章内部的展开顺序、分类维度、条件约束、流程分支、检查判定或其他内在组织方式。

## 4. 核心逻辑关系摘要
用表格概括标准中的关键逻辑关系，例如前置条件关系、风险关口关系、流程分支关系、操作约束关系、检查判定关系等。字段建议为：逻辑关系、关系说明。

## 5. 逻辑一句话概括
用一句话概括该标准最核心的内部逻辑。"""
        elif kind == "overview":
            task = """生成 `standard_overview.md`。

这是后续标准向量检索使用的核心文件，需要面向视频分析结果文本和用户检索文本进行语义匹配。

必须生成以下固定结构，标题名称和顺序不要改变：

## 1. 标准基本信息
用短列表列出标准名称、标准编号、来源 PDF、标准对象、标准主题。没有识别到的字段可以写“未识别”，不要编造。

## 2. 适用画像
概括本标准适用的对象、设备、产品、作业、工序、场景、风险点、管理要求和质量要求。这一部分是向量检索的核心，请写得具体。

## 3. 典型检索命中描述
用若干条说明：当查询或视频分析文本出现哪些对象、工序、风险、场景、管理要求时，应优先匹配本标准。

## 4. 边界说明
简要说明本标准通常不优先适用的场景。无法识别明确边界时写“未识别明确边界”，不要编造。

## 5. 检索摘要
用一段话概括本标准最核心的检索判断依据。

注意：
- 不写典型用户问题。
- 不写 PDF 页码。
- 不写详细章节目录。
- 不写旧目录标记。
- 不写 JSON。
- 不写复杂匹配决策规则。
- 不重复标准正文条款。
- 面向检索输入的语义匹配，而不是面向普通用户问答。"""
        else:
            raise ValueError(f"Unsupported generated markdown kind: {kind}")

        document_role = ROLE_BY_KIND[kind]
        body_context = require_text_within_input_limit(
            kind=kind,
            source_label="standard_body.md",
            text=standard_body,
            max_chars=self.max_input_chars,
        )
        standard_id = standard_id_from_name(source_pdf)
        prompt = f"""请基于下面的 `standard_body.md` 内容，{task}

固定要求：
- 只输出目标 Markdown 文件内容，不要解释，不要使用代码块包裹。
- 顶部必须包含 YAML front matter。
- document_role 必须是 "{document_role}"。

建议 front matter：
---
standard_id: "{yaml_escape(standard_id)}"
standard_name: "{yaml_escape(standard_name)}"
standard_number: "{yaml_escape(standard_number or '待模型识别')}"
source_pdf: "{yaml_escape(source_pdf)}"
document_role: "{document_role}"
schema_version: "simple-1.0"
---

标准 ID：{standard_id}
标准名称参考：{standard_name}
标准编号参考：{standard_number or "请从 standard_body.md 中识别"}

standard_body.md：
{body_context}
"""
        return self._chat(
            prompt,
            kind=kind,
            max_tokens=self.max_tokens_by_kind[kind],
            timeout_seconds=timeout_seconds,
        )

    def _chat(
        self,
        prompt: str,
        *,
        kind: str,
        max_tokens: int,
        file_references: list[str] | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        messages = [
            {
                "role": "system",
                "content": "你是严谨的国家标准 Markdown 整理助手。严格遵守用户格式要求，只输出目标 Markdown。",
            }
        ]
        for reference in file_references or []:
            messages.append({"role": "system", "content": reference})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": max_tokens,
            "enable_thinking": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = self._chat_completions_url()
        with logged_call_with_session(
            interface_type="model",
            tool_or_endpoint="qwen.chat.completions.standard_markdown",
            request={
                "model": self.model,
                "endpoint": url,
                "markdown_kind": kind,
                "input_chars": sum(len(str(item.get("content", ""))) for item in messages),
                "max_tokens": max_tokens,
            },
        ) as (audit_session, call_id):
            effective_timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
            if self.http_client is not None:
                response = self.http_client.post(url, headers=headers, json=payload, timeout=effective_timeout)
            else:
                with httpx.Client(timeout=effective_timeout, trust_env=False) as client:
                    response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            content = extract_chat_content(data)
            finish_reason = extract_finish_reason(data)
            if is_length_finish_reason(finish_reason):
                raise MaterializeLengthError(
                    f"{MARKDOWN_FILENAMES.get(kind, kind)} output length error: "
                    f"finish_reason={finish_reason}, max_tokens={max_tokens}"
                )
            finish_call(
                audit_session,
                call_id,
                {
                    "model": self.model,
                    "markdown_kind": kind,
                    "finish_reason": finish_reason,
                    "usage": data.get("usage", {}),
                    "output_chars": len(content),
                },
            )
            return clean_model_markdown(content)

    def _chat_completions_url(self) -> str:
        return f"{self._api_root()}/chat/completions"

    def _files_url(self) -> str:
        return f"{self._api_root()}/files"

    def _api_root(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url[: -len("/chat/completions")]
        return self.base_url


def emit_progress(progress: Callable[[str, int, str], None] | None, stage: str, percent: int, message: str) -> None:
    if progress is not None:
        progress(stage, percent, message)


def guess_standard_number(*parts: str) -> str:
    combined = "\n".join(part for part in parts if part)
    patterns = [
        r"\b(?:GB|GB/T|GB/Z|GJB|DL/T|JGJ|JT/T|JTG|NB/T|SY/T|AQ|AQ/T|HG/T|JB/T|YY|YY/T)\s*[0-9][0-9A-Z./]*\s*[-—]\s*\d{4}\b",
        r"\b[A-Z]{1,6}(?:/[A-Z])?\s*[0-9][0-9A-Z./]*\s*[-—]\s*\d{4}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).replace(" - ", "-").replace(" — ", "—").strip()
    return ""


def clip_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return text[:max_chars].rstrip() + f"\n\n[系统提示：由于输入长度限制，后续 {omitted} 个字符未传入本次模型调用。]"


def require_text_within_input_limit(*, kind: str, source_label: str, text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    raise MaterializeLengthError(
        f"{MARKDOWN_FILENAMES.get(kind, kind)} input length error: "
        f"{source_label} chars {len(text)} exceeds QWEN_TEXT_MAX_INPUT_CHARS={max_chars}"
    )


def extract_finish_reason(data: dict[str, Any]) -> str:
    try:
        reason = data["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError):
        return ""
    return str(reason or "")


def is_length_finish_reason(reason: str) -> bool:
    normalized = str(reason or "").strip().lower()
    return normalized in LENGTH_FINISH_REASONS


def extract_chat_content(data: dict[str, Any]) -> str:
    try:
        message = data["choices"][0].get("message") or {}
        content = message.get("content", "")
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Qwen response does not contain choices[0].message.content") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def clean_model_markdown(markdown: str) -> str:
    content = (markdown or "").replace("\ufeff", "").strip()
    fence_match = re.match(r"\A```(?:markdown|md)?\s*\n(?P<body>.*)\n```\s*\Z", content, re.DOTALL | re.IGNORECASE)
    if fence_match:
        content = fence_match.group("body").strip()
    return content


def yaml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def parse_front_matter(markdown: str) -> tuple[dict[str, str], str]:
    normalized = markdown.replace("\r\n", "\n")
    match = FRONT_MATTER_RE.match(normalized)
    if not match:
        return {}, normalized
    front: dict[str, str] = {}
    for raw_line in match.group("front").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        front[key.strip()] = value.strip().strip('"').strip("'")
    return front, normalized[match.end() :]


def ensure_markdown_front_matter(
    markdown: str,
    *,
    standard_name: str,
    standard_number: str,
    source_pdf: str,
    document_role: str,
    extra: dict[str, str] | None = None,
) -> str:
    cleaned = clean_model_markdown(markdown)
    existing, body = parse_front_matter(cleaned)
    merged = {
        "standard_name": existing.get("standard_name") or standard_name,
        "standard_number": existing.get("standard_number") or standard_number or "待模型识别",
        "source_pdf": existing.get("source_pdf") or source_pdf,
        "document_role": document_role,
    }
    if extra:
        merged.update({key: existing.get(key) or value for key, value in extra.items()})
    front_lines = ["---"] + [f'{key}: "{yaml_escape(value)}"' for key, value in merged.items()] + ["---"]
    return "\n".join(front_lines) + "\n\n" + body.lstrip()


def validate_generated_markdown(kind: str, markdown: str, *, standard_name: str) -> None:
    if not markdown.strip():
        raise ValueError(f"{MARKDOWN_FILENAMES[kind]} 内容为空。")
    front, body = parse_front_matter(markdown)
    expected_role = ROLE_BY_KIND[kind]
    if not front:
        raise ValueError(f"{MARKDOWN_FILENAMES[kind]} 缺少 YAML front matter。")
    if front.get("document_role") != expected_role:
        raise ValueError(f"{MARKDOWN_FILENAMES[kind]} document_role 应为 {expected_role}。")
    if not body.strip():
        raise ValueError(f"{MARKDOWN_FILENAMES[kind]} 正文内容为空。")
    if kind == "body":
        if standard_name not in markdown:
            raise ValueError("standard_body.md 未包含标准名称。")
        has_heading = re.search(r"(?m)^(#\s+.+|\d+(?:\.\d+)*\s+\S+)", body)
        if not has_heading:
            raise ValueError("standard_body.md 未包含可识别的正文标题。")
    if kind == "overview":
        required = ("标准基本信息", "适用画像", "典型检索命中描述", "边界说明", "检索摘要")
        missing = [item for item in required if item not in markdown]
        if missing:
            raise ValueError(f"standard_overview.md 缺少检索概要部分：{', '.join(missing)}。")


def build_standard_search_content(*, standard: Standard, overview_markdown: str) -> str:
    front, body = parse_front_matter(overview_markdown)
    parts = [
        f"标准名称：{front.get('standard_name') or standard.name}",
        f"标准编号：{front.get('standard_number') or standard.code or '未识别'}",
        f"来源 PDF：{front.get('source_pdf') or Path(standard.source_pdf_object_key).name}",
        "",
        clean_overview_body_for_search(body),
    ]
    content = "\n".join(part for part in parts if part is not None).strip()
    return re.sub(r"\n{3,}", "\n\n", content)


def clean_overview_body_for_search(body: str) -> str:
    cleaned = body.replace("\r\n", "\n")
    cleaned = re.sub(r"(?m)^#\s*标准检索概要\s*$", "", cleaned)
    cleaned = re.sub(r"(?m)^```(?:json|JSON)?\s*$", "", cleaned)
    cleaned = re.sub(r"(?m)^```\s*$", "", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(format(float(value), ".10g") for value in values) + "]"


def is_postgresql_database() -> bool:
    return settings.database_url.startswith("postgresql")


class StandardEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int,
        timeout_seconds: float,
        http_client: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client

    @classmethod
    def from_settings(cls) -> "StandardEmbeddingClient":
        if not settings.standard_embedding_api_key:
            raise ValueError("缺少标准向量化 API key：请在 .env 中配置 STANDARD_EMBEDDING_API_KEY 或 QWEN_TEXT_API_KEY。")
        return cls(
            api_key=settings.standard_embedding_api_key,
            base_url=settings.standard_embedding_base_url,
            model=settings.standard_embedding_model,
            dimensions=settings.standard_embedding_dimensions,
            timeout_seconds=settings.standard_embedding_timeout_seconds,
        )

    def embed(self, text_value: str) -> list[float]:
        payload = {
            "model": self.model,
            "input": text_value,
            "dimensions": self.dimensions,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = self._embeddings_url()
        with logged_call_with_session(
            interface_type="model",
            tool_or_endpoint="embedding.standard_search",
            request={
                "model": self.model,
                "endpoint": url,
                "input_chars": len(text_value),
                "dimensions": self.dimensions,
            },
        ) as (audit_session, call_id):
            if self.http_client is not None:
                response = self.http_client.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
            else:
                with httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
                    response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            embedding = extract_embedding(data)
            finish_call(
                audit_session,
                call_id,
                {
                    "model": self.model,
                    "usage": data.get("usage", {}),
                    "embedding_dimensions": len(embedding),
                },
            )
            if len(embedding) != self.dimensions:
                raise ValueError(f"向量维度不匹配：期望 {self.dimensions}，实际 {len(embedding)}。")
            return embedding

    def _embeddings_url(self) -> str:
        return f"{self._api_root()}/embeddings"

    def _api_root(self) -> str:
        if self.base_url.endswith("/embeddings"):
            return self.base_url[: -len("/embeddings")]
        return self.base_url


def extract_embedding(payload: dict[str, Any]) -> list[float]:
    data = payload.get("data")
    if isinstance(data, list) and data:
        embedding = data[0].get("embedding") if isinstance(data[0], dict) else None
        if isinstance(embedding, list):
            return [float(value) for value in embedding]
    output = payload.get("output")
    if isinstance(output, dict):
        embeddings = output.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            embedding = embeddings[0].get("embedding") if isinstance(embeddings[0], dict) else None
            if isinstance(embedding, list):
                return [float(value) for value in embedding]
    raise ValueError("向量化接口响应中没有 embedding。")


class StandardService:
    def __init__(self, storage: StorageService = storage_service) -> None:
        self.storage = storage
        self._progress_lock = RLock()
        self._materialize_progress: dict[str, dict[str, Any]] = {}

    def get_materialize_progress(self, standard_id: str) -> dict[str, Any]:
        with self._progress_lock:
            progress = self._materialize_progress.get(standard_id)
            if progress:
                return dict(progress)
        return {
            "standard_id": standard_id,
            "status": "idle",
            "stage": "idle",
            "progress_percent": 0,
            "message": "尚未开始解析。",
            "error": "",
            "updated_at": None,
        }

    def _update_materialize_progress(
        self,
        standard_id: str,
        *,
        stage: str,
        progress_percent: int,
        message: str,
        status: str = "processing",
        error: str = "",
    ) -> None:
        payload = {
            "standard_id": standard_id,
            "status": status,
            "stage": stage,
            "progress_percent": max(0, min(100, int(progress_percent))),
            "message": message,
            "error": error,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._progress_lock:
            self._materialize_progress[standard_id] = payload

    def _set_materialize_progress(
        self,
        session: Session,
        standard_id: str,
        job_id: str | None,
        *,
        stage: str,
        progress_percent: int,
        message: str,
        status: str = "processing",
        job_status: str | None = None,
        error: str = "",
    ) -> None:
        self._update_materialize_progress(
            standard_id,
            stage=stage,
            progress_percent=progress_percent,
            message=message,
            status=status,
            error=error,
        )
        self._update_materialize_job(
            session,
            job_id,
            stage=stage,
            progress_percent=progress_percent,
            message=message,
            status=job_status or ("failed" if status == "failed" else "completed" if status == "completed" else "running"),
            error=error,
        )

    def _update_materialize_job(
        self,
        session: Session,
        job_id: str | None,
        *,
        stage: str,
        progress_percent: int,
        message: str,
        status: str = "running",
        error: str = "",
    ) -> None:
        if not job_id:
            return
        job = session.get(StandardProcessingJob, job_id)
        if job is None:
            return
        job.status = status
        job.stage = stage
        job.progress_percent = max(0, min(100, int(progress_percent)))
        job.message = message
        job.error_message = error
        job.updated_at = datetime.now(timezone.utc)
        if status == "running" and job.started_at is None:
            job.started_at = datetime.now(timezone.utc)
        if status in {"completed", "failed"}:
            job.completed_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()

    def _progress_callback(
        self,
        standard_id: str,
        *,
        session: Session | None = None,
        job_id: str | None = None,
    ) -> Callable[[str, int, str], None]:
        def update(stage: str, progress_percent: int, message: str) -> None:
            if session is None:
                self._update_materialize_progress(
                    standard_id,
                    stage=stage,
                    progress_percent=progress_percent,
                    message=message,
                )
                return
            self._set_materialize_progress(
                session,
                standard_id,
                job_id,
                stage=stage,
                progress_percent=progress_percent,
                message=message,
            )

        return update

    def current_effective_standard_statement(
        self,
        *,
        limit: int = 0,
        offset: int = 0,
    ):
        statement = (
            select(Standard)
            .where(
                Standard.source_status == CURRENT_EFFECTIVE_STANDARD_SOURCE_STATUS,
                Standard.materialize_status == CURRENT_EFFECTIVE_STANDARD_MATERIALIZE_STATUS,
                Standard.index_status == CURRENT_EFFECTIVE_STANDARD_INDEX_STATUS,
            )
            .order_by(Standard.updated_at.desc(), Standard.created_at.desc(), Standard.id.asc())
        )
        if offset > 0:
            statement = statement.offset(offset)
        if limit > 0:
            statement = statement.limit(limit)
        return statement

    def current_effective_standards(
        self,
        session: Session,
        *,
        limit: int = 0,
        offset: int = 0,
    ) -> list[Standard]:
        statement = self.current_effective_standard_statement(limit=limit, offset=offset)
        return session.scalars(statement).all()

    def upload_pdf(self, session: Session, *, pdf_path: Path, filename: str, media_type: str) -> dict:
        safe_name = safe_filename(filename)
        if Path(safe_name).suffix.lower() != ".pdf":
            raise ValueError(f"Only PDF files are supported: {filename}")
        standard_id = standard_id_from_name(safe_name)
        object_key = f"standards/{standard_id}/source/{safe_name}"
        stored = self.storage.upload_file(
            object_key=object_key,
            path=pdf_path,
            media_type=media_type or "application/pdf",
            bucket=settings.object_store_standard_bucket,
        )
        standard = session.get(Standard, standard_id)
        if standard is None:
            standard = Standard(
                id=standard_id,
                name=Path(safe_name).stem,
                source_pdf_bucket=stored.bucket,
                source_pdf_object_key=stored.object_key,
                source_pdf_hash=stored.checksum,
                source_pdf_size_bytes=stored.size_bytes,
                source_status="active",
                materialize_status="not_started",
                index_status="not_indexed",
                fingerprint=stored.checksum,
                last_synced_at=datetime.now(timezone.utc),
            )
        else:
            standard.name = Path(safe_name).stem
            standard.source_pdf_bucket = stored.bucket
            standard.source_pdf_object_key = stored.object_key
            standard.source_pdf_hash = stored.checksum
            standard.source_pdf_size_bytes = stored.size_bytes
            standard.materialize_status = "not_started"
            standard.materialize_error = ""
            standard.index_status = "not_indexed"
            standard.indexed_at = None
            standard.index_error = ""
            standard.fingerprint = stored.checksum
            standard.last_synced_at = datetime.now(timezone.utc)
        session.add(standard)
        session.commit()
        return {
            "standard_id": standard.id,
            "name": standard.name,
            "status": standard.materialize_status,
            "bucket": stored.bucket,
            "object_key": stored.object_key,
            "size_bytes": stored.size_bytes,
        }

    def materialize(
        self,
        session: Session,
        standard_id: str,
        *,
        job_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        standard = session.get(Standard, standard_id)
        if standard is None:
            raise ValueError(f"Standard not found: {standard_id}")
        standard.materialize_status = "processing"
        standard.materialize_error = ""
        session.add(standard)
        session.commit()
        self._set_materialize_progress(
            session,
            standard_id,
            job_id,
            stage="starting",
            progress_percent=1,
            message="准备解析标准 PDF。",
        )
        try:
            workdir = Path(settings.standard_workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f"{standard_id}_", dir=str(workdir)) as tmp:
                pdf_path = Path(tmp) / Path(standard.source_pdf_object_key).name
                self._set_materialize_progress(
                    session,
                    standard_id,
                    job_id,
                    stage="downloading_pdf",
                    progress_percent=5,
                    message="正在从对象存储读取源 PDF。",
                )
                self.storage.download_file(
                    bucket=standard.source_pdf_bucket or settings.object_store_standard_bucket,
                    object_key=standard.source_pdf_object_key,
                    path=pdf_path,
                )
                self._set_materialize_progress(
                    session,
                    standard_id,
                    job_id,
                    stage="extracting_pdf_text",
                    progress_percent=10,
                    message="已读取源 PDF，准备本地抽取 PDF 原文。",
                )
                materialized = build_four_markdowns(
                    standard_name=standard.name,
                    source_pdf=Path(standard.source_pdf_object_key).name,
                    pdf_path=pdf_path,
                    progress=self._progress_callback(standard_id, session=session, job_id=job_id),
                    timeout_seconds=timeout_seconds,
                )
            if set(materialized.markdown) != MARKDOWN_KINDS:
                missing = MARKDOWN_KINDS - set(materialized.markdown)
                extra = set(materialized.markdown) - MARKDOWN_KINDS
                raise ValueError(f"Markdown 生成结果不完整，缺少: {sorted(missing)}，多余: {sorted(extra)}")

            self._set_materialize_progress(
                session,
                standard_id,
                job_id,
                stage="uploading_artifacts",
                progress_percent=92,
                message="正在保存四个 Markdown 文件。",
            )
            artifacts = {}
            for kind, markdown in materialized.markdown.items():
                object_key = standard_markdown_object_key(standard_id, kind)
                stored = self.storage.upload_bytes(
                    object_key=object_key,
                    content=markdown.encode("utf-8"),
                    media_type="text/markdown; charset=utf-8",
                    bucket=standard.source_pdf_bucket or settings.object_store_standard_bucket,
                )
                artifacts[kind] = stored.object_key
                setattr(standard, f"{kind}_md_object_key", stored.object_key)
            standard.materialize_status = "materialized"
            standard.materialize_error = ""
            standard.materialized_at = datetime.now(timezone.utc)
            standard.index_status = "not_indexed"
            standard.indexed_at = None
            standard.index_error = ""
            session.add(standard)
            session.commit()
            self._set_materialize_progress(
                session,
                standard_id,
                job_id,
                stage="completed",
                progress_percent=100,
                message="四个 Markdown 文件已生成并保存完成。",
                status="completed",
                job_status="completed",
            )
            return {"standard_id": standard_id, "status": standard.materialize_status, "artifacts": artifacts}
        except Exception as exc:
            failure_status = MATERIALIZE_LENGTH_ERROR_STATUS if isinstance(exc, MaterializeLengthError) else "failed"
            standard.materialize_status = failure_status
            standard.materialize_error = str(exc)
            session.add(standard)
            session.commit()
            self._set_materialize_progress(
                session,
                standard_id,
                job_id,
                stage=failure_status,
                progress_percent=100,
                message="解析生成失败。",
                status=failure_status,
                job_status="failed",
                error=str(exc),
            )
            raise

    def get_markdown(self, session: Session, standard_id: str, kind: str) -> dict:
        if kind not in MARKDOWN_KINDS:
            raise ValueError(f"Unsupported markdown kind: {kind}")
        standard = session.get(Standard, standard_id)
        if standard is None:
            raise ValueError(f"Standard not found: {standard_id}")
        object_key = getattr(standard, f"{kind}_md_object_key", "") or standard_markdown_object_key(standard_id, kind)
        bucket = standard.source_pdf_bucket or settings.object_store_standard_bucket
        markdown = self.storage.get_bytes(bucket=bucket, object_key=object_key).decode("utf-8", errors="replace")
        return {
            "standard_id": standard_id,
            "kind": kind,
            "markdown": markdown,
            "artifact": {"bucket": bucket, "object_key": object_key},
        }

    def index_standard(self, session: Session, standard_id: str) -> dict:
        if not is_postgresql_database():
            raise ValueError("标准向量索引需要 PostgreSQL + pgvector，当前 DATABASE_URL 不是 PostgreSQL。")
        standard = session.get(Standard, standard_id)
        if standard is None:
            raise ValueError(f"Standard not found: {standard_id}")
        standard.index_status = "indexing"
        standard.index_error = ""
        session.add(standard)
        session.commit()
        try:
            overview = self.get_markdown(session, standard_id, "overview")["markdown"]
            search_content = build_standard_search_content(standard=standard, overview_markdown=overview)
            if not search_content.strip():
                raise ValueError("standard_overview.md 提取出的检索文本为空。")
            embedding = StandardEmbeddingClient.from_settings().embed(search_content)
            embedding_literal = vector_literal(embedding)
            search_hash = content_hash(search_content)
            session.execute(
                text(
                    """
                    DELETE FROM standard_indexes
                    WHERE standard_id = :standard_id AND index_kind = :index_kind
                    """
                ),
                {"standard_id": standard_id, "index_kind": STANDARD_SEARCH_INDEX_KIND},
            )
            session.execute(
                text(
                    """
                    INSERT INTO standard_indexes (
                        id,
                        standard_id,
                        index_kind,
                        content,
                        embedding,
                        embedding_model,
                        embedding_dimensions,
                        content_hash,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        CAST(:id AS uuid),
                        :standard_id,
                        :index_kind,
                        :content,
                        CAST(:embedding AS vector),
                        :embedding_model,
                        :embedding_dimensions,
                        :content_hash,
                        now(),
                        now()
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "standard_id": standard_id,
                    "index_kind": STANDARD_SEARCH_INDEX_KIND,
                    "content": search_content,
                    "embedding": embedding_literal,
                    "embedding_model": settings.standard_embedding_model,
                    "embedding_dimensions": settings.standard_embedding_dimensions,
                    "content_hash": search_hash,
                },
            )
            standard.index_status = "indexed"
            standard.indexed_at = datetime.now(timezone.utc)
            standard.index_error = ""
            session.add(standard)
            session.commit()
            return {
                "standard_id": standard_id,
                "status": "indexed",
                "index_kind": STANDARD_SEARCH_INDEX_KIND,
                "embedding_model": settings.standard_embedding_model,
                "embedding_dimensions": settings.standard_embedding_dimensions,
                "content_hash": search_hash,
                "content_chars": len(search_content),
            }
        except Exception as exc:
            session.rollback()
            standard = session.get(Standard, standard_id)
            if standard is not None:
                standard.index_status = "failed"
                standard.index_error = str(exc)
                session.add(standard)
                session.commit()
            raise

    def rebuild_search_index(self, session: Session) -> dict:
        if not is_postgresql_database():
            raise ValueError("标准向量索引需要 PostgreSQL + pgvector，当前 DATABASE_URL 不是 PostgreSQL。")
        standards = self.current_effective_standards(session)
        indexed = []
        failed = []
        for standard in standards:
            try:
                indexed.append(self.index_standard(session, standard.id))
            except Exception as exc:
                failed.append({"standard_id": standard.id, "standard_name": standard.name, "reason": str(exc)})
        return {
            "status": "completed" if not failed else "completed_with_errors",
            "indexed_count": len(indexed),
            "failed_count": len(failed),
            "indexed": indexed,
            "failed": failed,
            "embedding_model": settings.standard_embedding_model,
            "embedding_dimensions": settings.standard_embedding_dimensions,
        }

    def search(self, session: Session, query: str, limit: int = 5) -> dict:
        clean_query = query.strip()
        if not clean_query:
            return {"query": query, "mode": "pgvector_overview", "matches": [], "excluded": []}
        if not is_postgresql_database():
            return {
                "query": query,
                "mode": "pgvector_overview",
                "matches": [],
                "excluded": [],
                "message": "标准向量检索需要 PostgreSQL + pgvector，当前 DATABASE_URL 不是 PostgreSQL。",
            }
        final_limit = max(1, min(limit, 20))
        embedding = StandardEmbeddingClient.from_settings().embed(clean_query)
        query_embedding = vector_literal(embedding)
        rows = session.execute(
            text(
                """
                SELECT
                    i.id AS index_id,
                    s.id AS standard_id,
                    s.name AS standard_name,
                    s.code AS standard_number,
                    s.publish_date AS publish_date,
                    s.effective_date AS effective_date,
                    s.source_status AS source_status,
                    s.materialized_at AS materialized_at,
                    s.indexed_at AS indexed_at,
                    s.last_synced_at AS last_synced_at,
                    i.index_kind AS index_kind,
                    i.content AS content,
                    i.embedding_model AS embedding_model,
                    i.embedding_dimensions AS embedding_dimensions,
                    1 - (i.embedding <=> CAST(:query_embedding AS vector)) AS score
                FROM standard_indexes i
                JOIN standards s ON s.id = i.standard_id
                WHERE s.index_status = :current_index_status
                  AND s.materialize_status = :current_materialize_status
                  AND s.source_status = :current_source_status
                  AND i.index_kind = :index_kind
                  AND i.embedding_model = :embedding_model
                  AND i.embedding_dimensions = :embedding_dimensions
                ORDER BY i.embedding <=> CAST(:query_embedding AS vector)
                LIMIT :limit
                """
            ),
            {
                "query_embedding": query_embedding,
                "index_kind": STANDARD_SEARCH_INDEX_KIND,
                "embedding_model": settings.standard_embedding_model,
                "embedding_dimensions": settings.standard_embedding_dimensions,
                "current_index_status": CURRENT_EFFECTIVE_STANDARD_INDEX_STATUS,
                "current_materialize_status": CURRENT_EFFECTIVE_STANDARD_MATERIALIZE_STATUS,
                "current_source_status": CURRENT_EFFECTIVE_STANDARD_SOURCE_STATUS,
                "limit": final_limit,
            },
        ).mappings().all()
        matches = []
        for row in rows:
            score = float(row["score"] or 0.0)
            if score < settings.standard_vector_search_min_score:
                continue
            decision = "应返回" if score >= 0.78 else "可作为候选返回"
            matches.append(
                {
                    "standard_id": row["standard_id"],
                    "index_id": str(row["index_id"] or ""),
                    "standard_name": row["standard_name"],
                    "standard_number": row["standard_number"] or "",
                    "publish_date": row["publish_date"] or "",
                    "effective_date": row["effective_date"] or "",
                    "source_status": row["source_status"] or "",
                    "materialized_at": row["materialized_at"].isoformat() if row["materialized_at"] else None,
                    "indexed_at": row["indexed_at"].isoformat() if row["indexed_at"] else None,
                    "last_synced_at": row["last_synced_at"].isoformat() if row["last_synced_at"] else None,
                    "decision": decision,
                    "match_level": "strong" if decision == "应返回" else "weak",
                    "score": max(0.0, min(1.0, score)),
                    "reason": "pgvector 根据 standard_overview.md 检索文本相似度召回。",
                    "evidence": [str(row["content"] or "")[:500]],
                    "index_kind": row["index_kind"],
                    "embedding_model": row["embedding_model"],
                    "embedding_dimensions": row["embedding_dimensions"],
                }
            )
        return {
            "query": query,
            "mode": "pgvector_overview",
            "embedding_model": settings.standard_embedding_model,
            "embedding_dimensions": settings.standard_embedding_dimensions,
            "matches": matches,
            "excluded": [],
        }

    def save_standard_matches(
        self,
        session: Session,
        *,
        matches: list[dict],
        search_id: str,
        query_text: str = "",
        caller_type: str = "web",
        limit: int = 5,
        mode: str = "pgvector_overview",
        duration_ms: int = 0,
        status: str = "success",
        error_message: str = "",
    ) -> int:
        session.add(
            StandardSearchQuery(
                id=search_id,
                query_text=query_text,
                query_hash=content_hash(query_text.strip()) if query_text.strip() else "",
                caller_type=caller_type,
                video_id="",
                limit=limit,
                mode=mode,
                embedding_model=settings.standard_embedding_model,
                embedding_dimensions=settings.standard_embedding_dimensions,
                result_count=len(matches),
                duration_ms=duration_ms,
                status=status,
                error_message=error_message,
            )
        )
        saved_count = 0
        for rank, item in enumerate(matches, start=1):
            session.add(
                StandardSearchResult(
                    id=uuid.uuid4().hex,
                    query_id=search_id,
                    standard_id=item["standard_id"],
                    index_id=item.get("index_id") or "",
                    rank=rank,
                    score=item["score"],
                    match_level=item.get("match_level") or item.get("decision") or "",
                    reason=item.get("reason") or "pgvector overview search",
                    evidence=json.dumps(item.get("evidence") or [], ensure_ascii=False),
                )
            )
            saved_count += 1
        session.commit()
        return saved_count

standard_service = StandardService()
