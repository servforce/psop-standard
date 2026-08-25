// Standard library service frontend. Generated from app.js split points; keep service-specific API calls here.

const state = {
  videos: [],
  standards: [],
  activeStandards: [],
  selectedStandardId: null,
  selectedStandardDetail: null,
  activeStandardMarkdownKind: null,
  standardSearchText: "",
  standardSortOrder: "desc",
  standardLatestUploadActive: false,
  standardMarkdownView: "rendered",
  activeStandardView: "directory",
  activeStandardSearchView: "home",
  lastStandardSearchAt: null,
  selectedVideoId: null,
  activeVideoView: "directory",
  activeVideoTab: "transcript",
  videoSearchText: "",
  videoSortOrder: "desc",
  videoLatestUploadActive: false,
  videoMarkdownView: "rendered",
  activeParseMode: null,
  videoStatusPoll: null,
  wireframeJobPoll: null,
  standardMaterializePoll: null,
  openstdCrawlPoll: null,
  openstdCrawlJob: null,
  standardUpdatePoll: null,
  latestStandardUpdate: null,
  lastStandardUpdateTerminalKey: "",
  standardUpdateSchedulerEnabled: false,
};


function bindElement(id, eventName, handler) {
  document.getElementById(id)?.addEventListener(eventName, handler);
}

function setActiveView(viewId) {
  if (!viewId || !document.getElementById(viewId)) return;
  document.querySelectorAll(".sidebar button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewId);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === viewId);
  });
}

async function loadRuntimeConfig() {
  try {
    const config = await fetchJson("/api/config");
    state.standardUpdateSchedulerEnabled = Boolean(config?.standard_update_scheduler_enabled);
  } catch {
    state.standardUpdateSchedulerEnabled = false;
  }
}

async function copyTextResult({ textId, buttonSelector, emptyTexts }) {
  const textEl = document.getElementById(textId);
  const button = document.querySelector(buttonSelector);
  const label = button?.querySelector("span");
  const text = textEl?.textContent || "";
  if (!text || emptyTexts.some((emptyText) => text === emptyText || text.startsWith(emptyText))) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    if (button) button.classList.add("copied");
    if (label) label.textContent = "已复制";
  } finally {
    if (button) setTimeout(() => {
      button.classList.remove("copied");
      if (label) label.textContent = "复制";
    }, 1200);
  }
}

function setStandardMarkdownContent(markdown, options = {}) {
  const rawTarget = document.getElementById("standardMarkdown");
  const renderedTarget = document.getElementById("standardMarkdownRendered");
  if (rawTarget) rawTarget.textContent = markdown;
  if (!renderedTarget) {
    applyStandardMarkdownView();
    return;
  }
  if (options.placeholder) {
    renderedTarget.innerHTML = `<div class="empty">${escapeHtml(markdown)}</div>`;
    applyStandardMarkdownView();
    return;
  }
  renderedTarget.innerHTML = renderMarkdownDocument(markdown);
  applyStandardMarkdownView();
}

function renderMarkdownDocument(markdown) {
  const lines = normalizeMarkdownEntities(stripMarkdownFrontmatter(String(markdown || ""))).replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    const trimmed = trimMarkdownLine(line);
    if (isMarkdownBlankLine(line)) {
      index += 1;
      continue;
    }
    if (/^```/.test(trimmed)) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index].trim())) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(`<pre class="markdown-code-block"><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = Math.min(6, heading[1].length);
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }
    if (isMarkdownTableStart(lines, index)) {
      const tableLines = [];
      while (index < lines.length && isMarkdownTableLine(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      blocks.push(renderMarkdownTable(tableLines));
      continue;
    }
    if (isMarkdownOutlineStart(lines, index)) {
      const outlineLines = [];
      while (index < lines.length && !isMarkdownBlankLine(lines[index]) && parseMarkdownOutlineLine(lines[index])) {
        outlineLines.push(lines[index]);
        index += 1;
      }
      blocks.push(renderMarkdownPlainBlock(outlineLines));
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const listLines = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        listLines.push(lines[index]);
        index += 1;
      }
      if (hasIndentedListLines(listLines)) {
        blocks.push(renderMarkdownPlainBlock(listLines));
        continue;
      }
      const items = listLines.map((item) => item.replace(/^\s*[-*+]\s+/, ""));
      blocks.push(`<ul>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const listLines = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        listLines.push(lines[index]);
        index += 1;
      }
      if (hasIndentedListLines(listLines)) {
        blocks.push(renderMarkdownPlainBlock(listLines));
        continue;
      }
      const items = listLines.map((item) => item.replace(/^\s*\d+[.)]\s+/, ""));
      blocks.push(`<ol>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ol>`);
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push("<hr />");
      index += 1;
      continue;
    }
    const paragraph = [];
    while (index < lines.length && !isMarkdownBlankLine(lines[index]) && !isMarkdownBlockStart(lines, index)) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(`<p>${paragraph.map((item) => renderInlineMarkdown(item)).join("<br />")}</p>`);
  }
  return blocks.join("") || "<div class='empty'>暂无内容。</div>";
}

function stripMarkdownFrontmatter(markdown) {
  if (!markdown.startsWith("---")) return markdown;
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  for (let index = 1; index < lines.length; index += 1) {
    if (lines[index].trim() === "---") return lines.slice(index + 1).join("\n").trimStart();
  }
  return markdown;
}

function trimMarkdownLine(line) {
  return normalizeMarkdownEntities(line)
    .replace(/[\u00a0\u2002\u2003\u3000]/g, " ")
    .trim();
}

function isMarkdownBlankLine(line) {
  return trimMarkdownLine(line) === "";
}

function isMarkdownBlockStart(lines, index) {
  const line = lines[index] || "";
  const trimmed = line.trim();
  return /^```/.test(trimmed)
    || /^(#{1,6})\s+/.test(trimmed)
    || isMarkdownTableStart(lines, index)
    || isMarkdownOutlineStart(lines, index)
    || /^\s*[-*+]\s+/.test(line)
    || /^\s*\d+[.)]\s+/.test(line)
    || /^(-{3,}|\*{3,}|_{3,})$/.test(trimmed);
}

function isMarkdownOutlineStart(lines, index) {
  if (!parseMarkdownOutlineLine(lines[index] || "")) return false;
  return Boolean(parseMarkdownOutlineLine(lines[index + 1] || ""));
}

function parseMarkdownOutlineLine(line) {
  const text = String(line || "");
  const numbered = text.match(/^(\s*)(\d+(?:\.\d+)*)(?:[、.．]|\s+)(.+)$/);
  if (numbered) {
    return {
      depth: Math.max(0, numbered[2].split(".").length - 1),
      marker: numbered[2],
      text: numbered[3].trim(),
    };
  }
  const cnNumbered = text.match(/^(\s*)((?:第[一二三四五六七八九十百千万\d]+[章节篇])|(?:[一二三四五六七八九十]+、)|(?:（[一二三四五六七八九十\d]+）))\s*(.+)$/);
  if (cnNumbered) {
    const leadingDepth = Math.floor(cnNumbered[1].replace(/\t/g, "  ").length / 2);
    return {
      depth: Math.max(0, leadingDepth),
      marker: cnNumbered[2],
      text: cnNumbered[3].trim(),
    };
  }
  return null;
}

function renderMarkdownOutline(outlineLines) {
  return `
    <div class="markdown-outline">
      ${outlineLines.map((line) => {
        const item = parseMarkdownOutlineLine(line);
        if (!item) return "";
        const depth = Math.max(0, Math.min(5, item.depth));
        return `
          <div class="markdown-outline-row" style="--outline-depth:${depth}">
            <span class="markdown-outline-marker">${escapeHtml(item.marker)}</span>
            <span class="markdown-outline-text">${renderInlineMarkdown(item.text)}</span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function hasIndentedListLines(lines) {
  return lines.some((line) => /^\s{2,}[-*+\d]/.test(line));
}

function renderMarkdownPlainBlock(lines) {
  return `
    <div class="markdown-plain-block">
      ${lines.map((line) => `<div class="markdown-plain-line">${renderInlineMarkdown(line)}</div>`).join("")}
    </div>
  `;
}

function isMarkdownTableStart(lines, index) {
  return isMarkdownTableLine(lines[index]) && isMarkdownSeparatorLine(lines[index + 1] || "");
}

function isMarkdownTableLine(line) {
  const trimmed = String(line || "").trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.includes("|");
}

function isMarkdownSeparatorLine(line) {
  if (!isMarkdownTableLine(line)) return false;
  return splitMarkdownTableRow(line).every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function splitMarkdownTableRow(line) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderMarkdownTable(tableLines) {
  const headers = splitMarkdownTableRow(tableLines[0]);
  const rows = tableLines.slice(2).map(splitMarkdownTableRow).filter((row) => row.some(Boolean));
  const keyValue = headers.length === 2 && /字段|项目|名称/.test(headers[0]) && /内容|说明|取值/.test(headers[1]);
  const tableClass = keyValue ? "markdown-table markdown-kv-table" : "markdown-table";
  return `
    <div class="markdown-table-wrap">
      <table class="${tableClass}">
        <thead>
          <tr>${headers.map((header) => `<th>${renderInlineMarkdown(header)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows.map((row) => renderMarkdownTableRow(row, headers.length, keyValue)).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderMarkdownTableRow(row, columnCount, keyValue) {
  const normalized = [...row];
  while (normalized.length < columnCount) normalized.push("");
  const fieldClass = keyValue ? standardFieldRowClass(normalized[0]) : "";
  const rowClass = fieldClass ? ` class="${fieldClass}"` : "";
  return `<tr${rowClass}>${normalized.slice(0, columnCount).map((cell, index) => {
    const className = keyValue ? (index === 0 ? " class=\"markdown-kv-field\"" : " class=\"markdown-kv-value\"") : "";
    return `<td${className}>${renderMarkdownTableCell(cell, normalized[0], index, keyValue)}</td>`;
  }).join("")}</tr>`;
}

function renderMarkdownTableCell(cell, field, index, keyValue) {
  return renderInlineMarkdown(cell);
}

function standardFieldRowClass(field) {
  const text = String(field || "");
  if (/标准名称|名称/.test(text)) return "markdown-kv-name-row";
  if (/标准编号|编号|标准号/.test(text)) return "markdown-kv-code-row";
  if (/来源|PDF|文件/.test(text)) return "markdown-kv-source-row";
  if (/对象|适用/.test(text)) return "markdown-kv-object-row";
  if (/主题|风险|要求/.test(text)) return "markdown-kv-topic-row";
  return "";
}

function renderInlineMarkdown(value, options = {}) {
  let html = escapeHtml(normalizeMarkdownEntities(value));
  html = html.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (match, alt, url) => {
    const safeUrl = safeMarkdownUrl(url);
    if (!safeUrl) return match;
    return `<img class="markdown-image" src="${escapeHtml(safeUrl)}" alt="${alt}" loading="lazy" />`;
  });
  html = html.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (match, text, url) => {
    const safeUrl = safeMarkdownUrl(url);
    if (!safeUrl) return match;
    return `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${text}</a>`;
  });
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  if (options.emphasis !== false) {
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  }
  return html;
}

function normalizeMarkdownEntities(value) {
  return String(value ?? "")
    .replace(/&emsp;?/gi, "\u2003")
    .replace(/&#8195;/gi, "\u2003")
    .replace(/&ensp;?/gi, "\u2002")
    .replace(/&#8194;/gi, "\u2002")
    .replace(/&nbsp;?/gi, " ")
    .replace(/&#160;/gi, " ");
}

function safeMarkdownUrl(url) {
  const decoded = String(url || "")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, "\"")
    .trim();
  if (!decoded) return "";
  if (/^(https?:|data:image\/|\/|\.\/|\.\.\/|#)/i.test(decoded)) return decoded;
  return "";
}

function standardMarkdownLabel(kind) {
  return {
    overview: "standard_overview.md",
    structure: "standard_structure.md",
    logic: "standard_logic.md",
    body: "standard_body.md",
  }[kind] || `${kind}.md`;
}

function sourcePdfFilename(standard) {
  const objectKey = standard?.source_pdf_object_key || "";
  const filename = objectKey.replace(/\\/g, "/").split("/").filter(Boolean).pop();
  return filename || `${standard?.name || "标准"}.pdf`;
}

window.copyActiveStandardMarkdown = async () => {
  await copyTextResult({
    textId: "standardMarkdown",
    buttonSelector: ".standard-markdown-copy-button",
    emptyTexts: [
      "正在加载 standard_overview.md...",
      "正在加载 standard_structure.md...",
      "正在加载 standard_logic.md...",
      "正在加载 standard_body.md...",
      "还未解析",
    ],
  });
};

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatTimestamp(seconds) {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatDateTime(value) {
  if (!value) return "未知时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatBeijingDateTime(value) {
  if (!value) return "未知时间";
  const date = parseBackendDateAsUtc(value);
  if (Number.isNaN(date.getTime())) return value;
  const beijing = new Date(date.getTime() + 8 * 60 * 60 * 1000 + 30 * 1000);
  return [
    beijing.getUTCFullYear(),
    String(beijing.getUTCMonth() + 1).padStart(2, "0"),
    String(beijing.getUTCDate()).padStart(2, "0"),
  ].join("-") + ` ${String(beijing.getUTCHours()).padStart(2, "0")}:${String(beijing.getUTCMinutes()).padStart(2, "0")}`;
}

function parseBackendDateAsUtc(value) {
  const text = String(value ?? "").trim();
  if (!text) return new Date(NaN);
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(text)) return new Date(text);
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?/);
  if (!match) return new Date(text);
  const [, year, month, day, hour, minute, second = "0", fraction = "0"] = match;
  const millisecond = Number(fraction.slice(0, 3).padEnd(3, "0"));
  return new Date(Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
    millisecond,
  ));
}

function truncateText(value, maxLength) {
  const text = String(value ?? "");
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`;
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" }[ch]));
}

function domId(value) {
  return String(value ?? "").replace(/[^A-Za-z0-9_-]+/g, "_") || "item";
}

function compactDetails(items, separator = " · ") {
  return items.filter(Boolean).join(separator);
}

function escapeJsString(value) {
  return String(value ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

const standardLibraryState = {
  summary: null,
  atlas: null,
  catalog: {
    query: "",
    source: "",
    page: 1,
    pageSize: 10,
    data: null,
  },
  activeHomeTab: "atlas",
  activePage: "home",
  previousPage: "home",
  searchResult: null,
  historyOpen: false,
  history: null,
  detail: null,
  detailReturnPage: "home",
  detailMarkdownKind: "overview",
  detailMarkdownView: "rendered",
};

function prepareStandardWorkbenchLayout() {
  document.querySelector('.sidebar button[data-view="standards"]')?.remove();
  const standardWorkbenchButton = document.querySelector('.sidebar button[data-view="standardSearch"]');
  if (standardWorkbenchButton) standardWorkbenchButton.textContent = "标准库";
  const section = document.getElementById("standardSearch");
  if (!section) return;
  section.innerHTML = `
    <div class="standard-library-app" data-standard-library-page="home">
      <div class="standard-library-page standard-library-home-page" data-standard-library-panel="home">
        <div class="standard-library-searchbar">
          <div class="standard-library-search-input">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m16.5 16.5 4 4" /></svg>
            <input id="standardSearchText" type="search" placeholder="输入作业场景、对象、工序、风险点，检索相关标准" />
          </div>
          <button id="searchStandards" class="standard-library-search-button" type="button">检索</button>
        </div>
        <div id="standardLibrarySummary" class="standard-library-summary muted">正在读取标准库摘要。</div>
        <div class="standard-library-tabs" role="tablist" aria-label="标准库首页视图">
          <button id="standardLibraryAtlasTab" class="result-tab active" type="button" data-standard-library-tab="atlas">标准可视化</button>
          <button id="standardLibraryLatestTab" class="result-tab" type="button" data-standard-library-tab="latest">最新标准列表</button>
        </div>
        <section id="standardLibraryAtlasPanel" class="standard-library-tab-panel">
          <div id="standardLibraryAtlas" class="standard-library-atlas">
            <div class="empty">正在读取 Atlas。</div>
          </div>
        </section>
        <section id="standardLibraryLatestPanel" class="standard-library-tab-panel hidden">
          <div class="standard-library-catalog-tools">
            <div class="standard-library-search-input compact">
              <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m16.5 16.5 4 4" /></svg>
              <input id="standardLibraryCatalogQuery" type="search" placeholder="标准号或标准名称，检索范围为全部有效标准" />
            </div>
            <button id="standardLibraryCatalogSearch" class="secondary" type="button">检索全部</button>
            <select id="standardLibrarySourceFilter" aria-label="标准来源">
              <option value="">全部来源</option>
              <option value="national">国家标准</option>
              <option value="industry">行业标准</option>
              <option value="local">地方标准</option>
            </select>
          </div>
          <div id="standardLibraryCatalog" class="standard-library-table-wrap">
            <div class="empty">正在读取最新标准列表。</div>
          </div>
        </section>
      </div>

      <div class="standard-library-page standard-library-result-page hidden" data-standard-library-panel="result">
        <button class="standard-library-round-back" type="button" onclick="backToStandardLibraryHome()" aria-label="返回">‹</button>
        <div class="standard-library-searchbar">
          <div class="standard-library-search-input">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m16.5 16.5 4 4" /></svg>
            <input id="standardSearchResultText" type="search" placeholder="输入作业场景、对象、工序、风险点，检索相关标准" />
          </div>
          <button id="searchStandardsFromResult" class="standard-library-search-button" type="button">检索</button>
        </div>
        <div class="standard-library-result-head">
          <div>
            <h2>标准检索结果</h2>
            <div id="standardLibrarySearchMeta" class="muted">暂无检索结果。</div>
          </div>
          <div class="standard-library-history-anchor">
            <button id="standardLibraryHistoryButton" class="secondary" type="button">检索历史</button>
            <div id="standardLibraryHistoryPopover" class="standard-library-history-popover hidden"></div>
          </div>
        </div>
        <div id="standardSearchResult" class="standard-library-table-wrap">
          <div class="empty">暂无检索结果。</div>
        </div>
      </div>

      <div class="standard-library-page standard-library-detail-page hidden" data-standard-library-panel="detail">
        <button class="standard-library-round-back" type="button" onclick="backFromStandardLibraryDetail()" aria-label="返回">‹</button>
        <div id="standardDetail" class="standard-library-detail">
          <div class="empty">请选择标准查看详情。</div>
        </div>
      </div>
    </div>
  `;

  document.getElementById("searchStandards")?.addEventListener("click", () => searchStandards("standardSearchText"));
  document.getElementById("searchStandardsFromResult")?.addEventListener("click", () => searchStandards("standardSearchResultText"));
  document.getElementById("standardSearchText")?.addEventListener("keydown", standardLibrarySearchKeydown);
  document.getElementById("standardSearchResultText")?.addEventListener("keydown", standardLibrarySearchKeydown);
  document.getElementById("standardLibraryCatalogQuery")?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    applyStandardLibraryCatalogSearch();
  });
  document.getElementById("standardLibraryCatalogSearch")?.addEventListener("click", applyStandardLibraryCatalogSearch);
  document.getElementById("standardLibrarySourceFilter")?.addEventListener("change", () => {
    standardLibraryState.catalog.source = document.getElementById("standardLibrarySourceFilter")?.value || "";
    standardLibraryState.catalog.page = 1;
    loadStandardLibraryCatalog();
  });
  document.querySelectorAll("[data-standard-library-tab]").forEach((button) => {
    button.addEventListener("click", () => setStandardLibraryHomeTab(button.dataset.standardLibraryTab));
  });
  document.getElementById("standardLibraryHistoryButton")?.addEventListener("click", toggleStandardLibraryHistory);
  document.addEventListener("click", closeStandardLibraryHistoryOnOutsideClick);
}

function standardLibrarySearchKeydown(event) {
  if (event.key !== "Enter") return;
  event.preventDefault();
  searchStandards(event.target.id);
}

async function loadStandardLibraryHome() {
  await Promise.all([
    loadStandardLibrarySummary(),
    loadStandardLibraryAtlas(),
    loadStandardLibraryCatalog(),
  ]);
}

async function loadActiveStandards() {
  await loadStandardLibrarySummary();
  await loadStandardLibraryCatalog();
  return standardLibraryState.catalog.data?.items || [];
}

async function loadStandards() {
  await loadStandardLibraryCatalog();
  return standardLibraryState.catalog.data?.items || [];
}

async function loadLatestStandardUpdate() {
  await loadStandardLibrarySummary();
  return standardLibraryState.summary;
}

function startStandardUpdatePolling() {
  return null;
}

async function loadStandardLibrarySummary() {
  const target = document.getElementById("standardLibrarySummary");
  try {
    standardLibraryState.summary = await fetchJson("/api/standard-library/summary");
    renderStandardLibrarySummary();
  } catch (error) {
    if (target) target.innerHTML = `<span class="error">读取标准库摘要失败：${escapeHtml(error.message)}</span>`;
  }
}

function renderStandardLibrarySummary() {
  const target = document.getElementById("standardLibrarySummary");
  const summary = standardLibraryState.summary;
  if (!target || !summary) return;
  const updateTime = summary.latest_update_at ? formatBeijingDateTime(summary.latest_update_at) : "暂无";
  const status = standardLibraryCycleStatusLabel(summary.cycle_status);
  const running = summary.cycle_status === "running";
  const counts = running
    ? "周期更新 · 进行中"
    : `新增 ${Number(summary.new_active_count || 0)} · 失效 ${Number(summary.expired_count || 0)} · 失败 ${Number(summary.failed_count || 0)}`;
  target.textContent = `当前有效标准 ${Number(summary.effective_standard_count || 0)} 条　最近周期更新时间：${updateTime}　周期状态：${status}　${counts}`;
}

function standardLibraryCycleStatusLabel(status) {
  return {
    not_started: "未开始",
    running: "进行中",
    completed: "已完成",
    completed_with_failures: "已完成，有失败项",
    failed: "失败",
  }[status] || status || "未知";
}

async function loadStandardLibraryAtlas() {
  const target = document.getElementById("standardLibraryAtlas");
  try {
    standardLibraryState.atlas = await fetchJson("/api/standard-library/atlas");
    renderStandardLibraryAtlas();
  } catch (error) {
    if (target) target.innerHTML = `<div class="error">读取 Atlas 失败：${escapeHtml(error.message)}</div>`;
  }
}

function renderStandardLibraryAtlas() {
  const target = document.getElementById("standardLibraryAtlas");
  const atlas = standardLibraryState.atlas;
  if (!target || !atlas) return;
  if (atlas.status !== "ready" || !atlas.data?.ids?.length) {
    const message = atlas.status === "generating" ? "Atlas 正在生成中。" : "Atlas 暂无数据。";
    target.innerHTML = `
      <div class="standard-library-atlas-empty">
        <strong>${escapeHtml(message)}</strong>
        <span>当前有效标准 ${Number(atlas.effective_standard_count || 0)} 条，已投影 ${Number(atlas.projected_count || 0)} 条。</span>
      </div>
    `;
    return;
  }
  const data = atlas.data;
  const bounds = standardLibraryAtlasBounds(data.x, data.y);
  const points = data.ids.map((id, index) => {
    const left = standardLibraryNormalizePoint(data.x[index], bounds.minX, bounds.maxX);
    const top = 100 - standardLibraryNormalizePoint(data.y[index], bounds.minY, bounds.maxY);
    const category = Number(data.category[index] || 0);
    const color = atlas.categories?.[category]?.color || "#2f80ed";
    const name = data.names[index] || "";
    const code = data.codes[index] || "";
    return `
      <button class="standard-library-atlas-point" style="left:${left}%; top:${top}%; --point-color:${escapeHtml(color)}"
        title="${escapeHtml(compactDetails([code, name]))}"
        onclick="showStandard('${escapeJsString(id)}')"
        aria-label="${escapeHtml(compactDetails([code, name]))}">
      </button>
    `;
  }).join("");
  target.innerHTML = `
    <div class="standard-library-atlas-meta">
      <span>投影版本 ${escapeHtml(atlas.version || "-")}</span>
      <span>有效 ${Number(atlas.effective_standard_count || 0)}</span>
      <span>已投影 ${Number(atlas.projected_count || 0)}</span>
      <span>更新 ${escapeHtml(formatBeijingDateTime(atlas.updated_at))}</span>
    </div>
    <div class="standard-library-atlas-canvas">${points}</div>
    <div class="standard-library-atlas-legend">
      ${(atlas.categories || []).map((item) => `
        <span><i style="background:${escapeHtml(item.color)}"></i>${escapeHtml(item.name)} ${Number(item.count || 0)}</span>
      `).join("")}
    </div>
  `;
}

function standardLibraryAtlasBounds(xs = [], ys = []) {
  const cleanX = xs.map(Number).filter(Number.isFinite);
  const cleanY = ys.map(Number).filter(Number.isFinite);
  return {
    minX: Math.min(...cleanX),
    maxX: Math.max(...cleanX),
    minY: Math.min(...cleanY),
    maxY: Math.max(...cleanY),
  };
}

function standardLibraryNormalizePoint(value, min, max) {
  if (!Number.isFinite(Number(value)) || !Number.isFinite(min) || !Number.isFinite(max) || min === max) return 50;
  return 6 + ((Number(value) - min) / (max - min)) * 88;
}

async function loadStandardLibraryCatalog() {
  const target = document.getElementById("standardLibraryCatalog");
  const catalog = standardLibraryState.catalog;
  const params = new URLSearchParams({
    page: String(catalog.page),
    page_size: String(catalog.pageSize),
  });
  if (catalog.query) params.set("query", catalog.query);
  if (catalog.source) params.set("source", catalog.source);
  try {
    catalog.data = await fetchJson(`/api/standard-library/catalog?${params}`);
    renderStandardLibraryCatalog();
  } catch (error) {
    if (target) target.innerHTML = `<div class="error">读取标准目录失败：${escapeHtml(error.message)}</div>`;
  }
}

function applyStandardLibraryCatalogSearch() {
  standardLibraryState.catalog.query = document.getElementById("standardLibraryCatalogQuery")?.value.trim() || "";
  standardLibraryState.catalog.page = 1;
  loadStandardLibraryCatalog();
}

function renderStandardLibraryCatalog() {
  const target = document.getElementById("standardLibraryCatalog");
  const data = standardLibraryState.catalog.data;
  if (!target || !data) return;
  target.innerHTML = `
    ${renderStandardLibraryTable(data.items || [], { showScore: false })}
    ${renderStandardLibraryPagination(data)}
  `;
}

function renderStandardLibraryPagination(data) {
  const page = Number(data.page || 1);
  const totalPages = Number(data.total_pages || 0);
  const total = Number(data.total || 0);
  return `
    <div class="standard-library-pagination">
      <div>
        每页显示
        <select onchange="setStandardLibraryPageSize(this.value)">
          ${[10, 25, 50].map((size) => `<option value="${size}" ${Number(data.page_size || 10) === size ? "selected" : ""}>${size}</option>`).join("")}
        </select>
        条，共 ${total} 条标准，${totalPages ? `${page} / ${totalPages}` : "0 / 0"}
      </div>
      <div class="standard-library-page-buttons">
        <button class="secondary small" type="button" onclick="setStandardLibraryCatalogPage(${page - 1})" ${page <= 1 ? "disabled" : ""}>上一页</button>
        <button class="secondary small" type="button" onclick="setStandardLibraryCatalogPage(${page + 1})" ${!totalPages || page >= totalPages ? "disabled" : ""}>下一页</button>
      </div>
    </div>
  `;
}

window.setStandardLibraryCatalogPage = (page) => {
  const totalPages = Number(standardLibraryState.catalog.data?.total_pages || 0);
  standardLibraryState.catalog.page = Math.max(1, totalPages ? Math.min(totalPages, Number(page || 1)) : 1);
  loadStandardLibraryCatalog();
};

window.setStandardLibraryPageSize = (value) => {
  standardLibraryState.catalog.pageSize = Math.max(1, Math.min(50, Number(value || 10)));
  standardLibraryState.catalog.page = 1;
  loadStandardLibraryCatalog();
};

function setStandardLibraryHomeTab(tab) {
  standardLibraryState.activeHomeTab = tab === "latest" ? "latest" : "atlas";
  document.querySelectorAll("[data-standard-library-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.standardLibraryTab === standardLibraryState.activeHomeTab);
  });
  document.getElementById("standardLibraryAtlasPanel")?.classList.toggle("hidden", standardLibraryState.activeHomeTab !== "atlas");
  document.getElementById("standardLibraryLatestPanel")?.classList.toggle("hidden", standardLibraryState.activeHomeTab !== "latest");
}

async function searchStandards(sourceInputId = "standardSearchText") {
  const query = document.getElementById(sourceInputId)?.value.trim() || "";
  if (!query) return alert("请输入检索文本");
  syncStandardSearchInputs(query);
  setStandardSearchWorkspaceView("result");
  state.lastStandardSearchAt = new Date();
  standardLibraryState.searchResult = null;
  renderStandardSearchResult({ query, matches: [], result_count: 0, searched_at: state.lastStandardSearchAt.toISOString(), loading: true });
  try {
    const result = await fetchJson(`/api/standard-library/search?query=${encodeURIComponent(query)}&limit=20`, { method: "POST" });
    standardLibraryState.searchResult = result;
    renderStandardSearchResult(result);
  } catch (error) {
    const target = document.getElementById("standardSearchResult");
    if (target) target.innerHTML = `<div class="error">检索失败：${escapeHtml(error.message)}</div>`;
  }
}

function setStandardSearchWorkspaceView(view) {
  const next = view === "result" || view === "detail" ? view : "home";
  standardLibraryState.activePage = next;
  const app = document.querySelector(".standard-library-app");
  if (app) app.dataset.standardLibraryPage = next;
  document.querySelectorAll("[data-standard-library-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.standardLibraryPanel !== next);
  });
  document.getElementById("standardSearch")?.scrollIntoView({ block: "start" });
}

function syncStandardSearchInputs(query) {
  const value = String(query ?? "");
  const homeInput = document.getElementById("standardSearchText");
  const resultInput = document.getElementById("standardSearchResultText");
  if (homeInput) homeInput.value = value;
  if (resultInput) resultInput.value = value;
}

function renderStandardSearchResult(result) {
  const target = document.getElementById("standardSearchResult");
  const meta = document.getElementById("standardLibrarySearchMeta");
  if (!target) return "";
  const matches = result.matches || [];
  const searchedAt = result.searched_at || (state.lastStandardSearchAt ? state.lastStandardSearchAt.toISOString() : "");
  if (meta) {
    if (result.loading) {
      meta.textContent = "正在检索标准。";
    } else {
      meta.innerHTML = `共 <strong>${Number(result.result_count ?? matches.length)}</strong> 条检索结果，检索时间：${escapeHtml(formatBeijingDateTime(searchedAt))}`;
    }
  }
  target.innerHTML = result.loading
    ? "<div class='muted'>正在检索标准...</div>"
    : `${result.message ? `<div class="muted standard-library-result-message">${escapeHtml(result.message)}</div>` : ""}${renderStandardLibraryTable(matches, { showScore: true })}`;
  return target.innerHTML;
}

function renderStandardLibraryTable(items, options = {}) {
  const showScore = Boolean(options.showScore);
  if (!items.length) return "<div class='empty'>暂无标准。</div>";
  return `
    <table class="standard-library-table">
      <thead>
        <tr>
          <th>序号</th>
          <th>标准号</th>
          <th>标准名称</th>
          <th>来源</th>
          <th>分类</th>
          <th>发布日期</th>
          <th>实施日期</th>
          ${showScore ? "<th>相似度得分</th>" : ""}
        </tr>
      </thead>
      <tbody>
        ${items.map((item, index) => {
          const standardId = item.standard_id || item.id || "";
          const rank = item.rank || index + 1;
          return `
            <tr class="standard-library-click-row" onclick="showStandard('${escapeJsString(standardId)}')">
              <td>${Number(rank)}</td>
              <td>${escapeHtml(item.code || "")}</td>
              <td>${escapeHtml(item.name || item.standard_name || "")}</td>
              <td>${escapeHtml(item.source_label || sourceLabel(item.source))}</td>
              <td>${escapeHtml(item.category_label || item.category || "")}</td>
              <td>${escapeHtml(item.publish_date || "")}</td>
              <td>${escapeHtml(item.effective_date || "")}</td>
              ${showScore ? `<td><span class="standard-library-score">${Number(item.score || 0).toFixed(3)}</span></td>` : ""}
            </tr>
          `;
        }).join("")}
      </tbody>
    </table>
  `;
}

function sourceLabel(source) {
  return {
    national: "国家标准",
    industry: "行业标准",
    local: "地方标准",
  }[source] || source || "";
}

async function toggleStandardLibraryHistory(event) {
  event?.stopPropagation();
  const popover = document.getElementById("standardLibraryHistoryPopover");
  if (!popover) return;
  standardLibraryState.historyOpen = popover.classList.contains("hidden");
  popover.classList.toggle("hidden", !standardLibraryState.historyOpen);
  if (standardLibraryState.historyOpen) await loadStandardSearchHistory();
}

function closeStandardLibraryHistoryOnOutsideClick(event) {
  const anchor = document.querySelector(".standard-library-history-anchor");
  if (!anchor || anchor.contains(event.target)) return;
  document.getElementById("standardLibraryHistoryPopover")?.classList.add("hidden");
  standardLibraryState.historyOpen = false;
}

async function loadStandardSearchHistory() {
  const target = document.getElementById("standardLibraryHistoryPopover");
  if (!target) return;
  target.innerHTML = "<div class='muted'>正在加载检索历史...</div>";
  try {
    standardLibraryState.history = await fetchJson("/api/standard-library/search/history?limit=10");
    target.innerHTML = renderStandardSearchHistory(standardLibraryState.history);
  } catch (error) {
    target.innerHTML = `<div class="error">加载检索历史失败：${escapeHtml(error.message)}</div>`;
  }
}

function renderStandardSearchHistory(history) {
  const items = Array.isArray(history) ? history : history.items || [];
  if (!items.length) return "<div class='muted'>暂无检索历史</div>";
  return `
    <div class="standard-library-history-list">
      ${items.map((item) => `
        <button class="standard-library-history-item" type="button" onclick="loadStandardLibraryHistorySnapshot('${escapeJsString(item.search_id)}')">
          <span>${escapeHtml(truncateText(item.query || "无检索文本", 90))}</span>
          <time>${escapeHtml(formatBeijingDateTime(item.searched_at))}</time>
        </button>
      `).join("")}
    </div>
  `;
}

window.loadStandardLibraryHistorySnapshot = async (searchId) => {
  const target = document.getElementById("standardLibraryHistoryPopover");
  try {
    const snapshot = await fetchJson(`/api/standard-library/search/history/${encodeURIComponent(searchId)}`);
    syncStandardSearchInputs(snapshot.query || "");
    standardLibraryState.searchResult = {
      ...snapshot,
      result_count: snapshot.result_count,
      searched_at: snapshot.searched_at,
      matches: snapshot.matches || [],
    };
    renderStandardSearchResult(standardLibraryState.searchResult);
    if (target) target.classList.add("hidden");
    standardLibraryState.historyOpen = false;
  } catch (error) {
    if (target) target.innerHTML = `<div class="error">读取历史快照失败：${escapeHtml(error.message)}</div>`;
  }
};

window.backToStandardLibraryHome = () => {
  setStandardSearchWorkspaceView("home");
};

window.showStandard = async (id) => {
  if (!id) return;
  standardLibraryState.detailReturnPage = standardLibraryState.activePage === "result" ? "result" : "home";
  setStandardSearchWorkspaceView("detail");
  const detailTarget = document.getElementById("standardDetail");
  if (detailTarget) detailTarget.innerHTML = "<div class='empty'>正在加载标准详情。</div>";
  try {
    const detail = await fetchJson(`/api/standard-library/${encodeURIComponent(id)}`);
    standardLibraryState.detail = detail;
    state.selectedStandardId = id;
    state.selectedStandardDetail = detail;
    standardLibraryState.detailMarkdownKind = "overview";
    state.activeStandardMarkdownKind = "overview";
    renderStandardDetail(detail);
    await loadMarkdown(id, "overview");
  } catch (error) {
    if (detailTarget) detailTarget.innerHTML = `<div class="error">加载标准详情失败：${escapeHtml(error.message)}</div>`;
  }
};

window.backFromStandardLibraryDetail = () => {
  setStandardSearchWorkspaceView(standardLibraryState.detailReturnPage || "home");
};

function renderStandardDetail(standard) {
  const target = document.getElementById("standardDetail");
  if (!target) return;
  const standardId = standard.standard_id || standard.id || "";
  const markdownLabels = {
    overview: "overview.md",
    structure: "structure.md",
    logic: "logic.md",
    body: "body.md",
  };
  const tabs = ["overview", "structure", "logic", "body"].map((kind) => {
    const available = Boolean(standard.markdown?.[kind]?.available);
    return `<button class="result-tab ${standardLibraryState.detailMarkdownKind === kind ? "active" : ""}" data-standard-kind="${kind}" type="button" onclick="loadMarkdown('${escapeJsString(standardId)}','${kind}')" ${available ? "" : ""}>${markdownLabels[kind]}</button>`;
  }).join("");
  const officialUrl = standard.detail_url || standard.online_url || standard.pdf_url || "";
  target.innerHTML = `
    <div class="standard-library-detail-head">
      <div>
        <h2>
          ${escapeHtml(standard.name || "未知标准")}
          ${officialUrl ? `<a class="standard-library-source-logo" href="${escapeHtml(officialUrl)}" target="_blank" rel="noopener noreferrer" title="打开官网链接">${escapeHtml(sourceShortLabel(standard.source))}</a>` : ""}
        </h2>
        <div class="standard-library-detail-meta">
          ${escapeHtml(compactDetails([
            standard.code,
            compactDetails([standard.source_label || sourceLabel(standard.source), standard.category_label || standard.category], " / "),
            standard.publish_date ? `发布时间 ${standard.publish_date}` : "",
            standard.effective_date ? `实施时间 ${standard.effective_date}` : "",
          ]))}
        </div>
      </div>
    </div>
    <div class="standard-library-detail-source">
      <span>官网状态：${escapeHtml(standard.official_status || "")}</span>
      ${officialUrl ? `<a href="${escapeHtml(officialUrl)}" target="_blank" rel="noopener noreferrer">官网链接</a>` : "<span>暂无官网链接</span>"}
    </div>
    <div class="result-tabs">${tabs}</div>
    <div class="standard-markdown-tab">
      <div class="markdown-result">
        <div class="video-markdown-toolbar">
          <div class="video-markdown-switch" aria-label="Markdown 显示模式">
            <button type="button" data-standard-markdown-view="rendered" onclick="setStandardMarkdownView('rendered')">视图</button>
            <button type="button" data-standard-markdown-view="source" onclick="setStandardMarkdownView('source')">源码</button>
          </div>
        </div>
        <button class="markdown-copy-button standard-markdown-copy-button" onclick="copyActiveStandardMarkdown()" aria-label="复制当前 Markdown">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="9" y="9" width="10" height="10" rx="2" />
            <rect x="5" y="5" width="10" height="10" rx="2" />
          </svg>
          <span>复制</span>
        </button>
        <div id="standardMarkdownRendered" class="standard-markdown-rendered">
          <div class="empty">正在加载 overview.md...</div>
        </div>
        <pre id="standardMarkdown" class="hidden">正在加载 overview.md...</pre>
      </div>
    </div>
  `;
  applyStandardMarkdownView();
}

function sourceShortLabel(source) {
  return {
    national: "GB",
    industry: "HB",
    local: "DB",
  }[source] || "STD";
}

window.loadMarkdown = async (id, kind) => {
  standardLibraryState.detailMarkdownKind = kind;
  state.activeStandardMarkdownKind = kind;
  document.querySelectorAll("[data-standard-kind]").forEach((button) => {
    button.classList.toggle("active", button.dataset.standardKind === kind);
  });
  const label = standardMarkdownLabel(kind);
  setStandardMarkdownContent(`正在加载 ${label}...`, { placeholder: true });
  const response = await fetch(`/api/standard-library/${encodeURIComponent(id)}/markdown/${encodeURIComponent(kind)}`).catch(() => null);
  if (!response || !response.ok) {
    setStandardMarkdownContent(`${label} 暂无数据。`, { placeholder: true });
    return;
  }
  const markdown = await response.text();
  setStandardMarkdownContent(markdown || `${label} 暂无数据。`, { placeholder: !markdown });
};

window.previewSearchMarkdown = async (standardId, kind, previewId) => {
  const preview = document.getElementById(previewId);
  if (!preview) return;
  preview.textContent = `正在加载 ${kind}.md...`;
  try {
    const markdown = await fetch(`/api/standard-library/${encodeURIComponent(standardId)}/markdown/${kind}`).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      return r.text();
    });
    preview.textContent = markdown || "暂无内容。";
    preview.dataset.loadedKind = kind;
  } catch (error) {
    preview.textContent = `加载失败：${error.message}`;
  }
};


async function bootstrapStandardApp() {
  await loadRuntimeConfig();
  prepareStandardWorkbenchLayout();
  setActiveView("standardSearch");
  await loadStandardLibraryHome();
}

bootstrapStandardApp().catch((error) => {
  console.error("failed to bootstrap standard library app", error);
});
