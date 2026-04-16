"""
kb_renderer.py - Render ServiceNow KB articles as styled HTML pages.

Fetches extracted markdown text from GCS and converts it to a branded
HTML page for browser display.
"""

import logging
import re
from typing import Optional

import markdown
from google.cloud import storage

from config import (
    PROJECT_ID,
    GCS_BUCKET_NAME,
    GCS_SERVICENOW_KB_PREFIX,
)

logger = logging.getLogger(__name__)

# Regex to detect ServiceNow KB filenames: ServiceNow_KB_KB0018536.html
_SERVICENOW_KB_RE = re.compile(r"^ServiceNow_KB_(KB\d+)\.html$")


def is_servicenow_kb(filename: str) -> bool:
    """Check if a filename matches the ServiceNow KB pattern."""
    return bool(_SERVICENOW_KB_RE.match(filename))


def extract_kb_number(filename: str) -> Optional[str]:
    """Extract the KB number from a ServiceNow KB filename.

    E.g. 'ServiceNow_KB_KB0018536.html' -> 'KB0018536'
    """
    match = _SERVICENOW_KB_RE.match(filename)
    return match.group(1) if match else None


def render_kb_article(filename: str) -> Optional[str]:
    """Fetch and render a ServiceNow KB article as a styled HTML page.

    Looks for the extracted markdown text at:
      gs://{bucket}/servicenow_kb_extraction/extracted/{kb_number}_html.txt

    Returns the rendered HTML string, or None if the article was not found.
    """
    kb_number = extract_kb_number(filename)
    if not kb_number:
        return None

    # Fetch the extracted markdown text from GCS
    txt_blob_path = f"{GCS_SERVICENOW_KB_PREFIX}extracted/{kb_number}_html.txt"
    markdown_text = _fetch_blob_text(GCS_BUCKET_NAME, txt_blob_path)

    if not markdown_text:
        logger.warning(
            "No extracted text for %s at gs://%s/%s",
            kb_number,
            GCS_BUCKET_NAME,
            txt_blob_path,
        )
        return None

    logger.info(
        "Rendering ServiceNow KB article %s from gs://%s/%s",
        kb_number,
        GCS_BUCKET_NAME,
        txt_blob_path,
    )

    title = _extract_title(markdown_text) or kb_number
    return _render_html(title, markdown_text, kb_number)


# ── Private helpers ──


def _fetch_blob_text(bucket_name: str, blob_path: str) -> Optional[str]:
    """Fetch a text blob from GCS and return its contents."""
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)

    if not blob.exists():
        return None

    return blob.download_as_text(encoding="utf-8")


def _extract_title(text: str) -> Optional[str]:
    """Extract a title from the first bold heading or first line of markdown."""
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Match **Title** pattern (common in extracted KB text)
        bold_match = re.match(r"^\*\*(.+?)\*\*$", line)
        if bold_match:
            return bold_match.group(1)
        # Match # Heading pattern
        heading_match = re.match(r"^#+\s+(.+)$", line)
        if heading_match:
            return heading_match.group(1)
        # Use first non-empty line as fallback
        return line[:100]
    return None


def _preprocess_kb_markdown(text: str) -> str:
    """Pre-process extracted ServiceNow KB markdown before rendering.

    The ServiceNow KB extraction pipeline produces markdown-like text with
    several issues that need fixing before the Python ``markdown`` library
    can render it correctly:

    1. **Raw CSS at the bottom** — The original ServiceNow page CSS is
       appended as plain text.  We strip everything from the first CSS
       comment block (``/* …``) onwards.
    2. **Duplicate page-break sections** — The extractor sometimes repeats
       content after ``---`` separators.  We deduplicate by keeping only the
       first occurrence of each unique content block.
    3. **Pipe tables missing separator rows** — The ``tables`` markdown
       extension requires a ``|---|---|`` separator row after the header.
       The extracted text omits these, so we detect consecutive pipe-
       delimited lines and insert a separator after the first row of each
       table group.
    """

    # ── 1. Strip trailing CSS ──
    css_patterns = [
        re.compile(r"^\s*/\*"),  # /* comment
        re.compile(r"^\s*body\s*\{"),  # body { (no comment before it)
        re.compile(r"^\s*\.\w+[-\w]*\s*\{"),  # .class-name {
        re.compile(r"^\s*#\w+[-\w]*\s*\{"),  # #id-name {
    ]

    lines = text.split("\n")
    css_start_idx = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        for pat in css_patterns:
            if pat.match(stripped):
                lookahead = "\n".join(lines[i : i + 10])
                if re.search(r"[{};]\s*$", lookahead, re.MULTILINE) and re.search(
                    r":\s*[^|]+;", lookahead
                ):
                    css_start_idx = i
                    break
        if css_start_idx is not None:
            break

    if css_start_idx is not None:
        lines = lines[:css_start_idx]

    # ── 2. Remove duplicate page-break sections ──
    # The extractor inserts "---" at ServiceNow page breaks.  After each
    # "---" the first line is always a *fragment* (partial text from the
    # page-break boundary), followed by duplicate lines that repeat content
    # from just above the break.  A blank line separates the duplicates
    # from genuinely new content.  Strategy: drop everything before the
    # first blank line in each post-"---" section.
    joined = "\n".join(lines)
    sections = re.split(r"\n---+\n", joined)

    result_parts: list[str] = []
    if sections:
        result_parts.append(sections[0])

    for i in range(1, len(sections)):
        sec_lines = sections[i].split("\n")
        # Find the first blank line — everything before it is
        # the fragment + duplicate rows.
        blank_idx = None
        for j, sec_line in enumerate(sec_lines):
            if not sec_line.strip():
                blank_idx = j
                break
        if blank_idx is not None:
            remaining = "\n".join(sec_lines[blank_idx:])
            if remaining.strip():
                result_parts.append(remaining)
        # If there's no blank line at all, the entire section is a
        # duplicate fragment — drop it entirely.

    text = "\n\n".join(result_parts)

    # ── 3. Fix pipe tables — insert separator rows ──
    out_lines: list[str] = []
    pipe_group: list[str] = []

    def flush_pipe_group() -> None:
        if not pipe_group:
            return
        if len(pipe_group) >= 2:
            # Use the widest row to determine column count — title rows
            # may have fewer columns than the data rows below them.
            max_cols = max(row.count("|") - 1 for row in pipe_group)
            if max_cols < 1:
                max_cols = 1
            separator = "| " + " | ".join(["---"] * max_cols) + " |"

            for idx, row in enumerate(pipe_group):
                # Pad rows that have fewer columns than the widest row
                row_stripped = row.rstrip()
                row_cols = row_stripped.count("|") - 1
                if row_cols < max_cols:
                    row_stripped += " |" * (max_cols - row_cols)
                out_lines.append(row_stripped)
                # Insert separator right after the header (first row)
                if idx == 0:
                    out_lines.append(separator)
        else:
            out_lines.extend(pipe_group)
        pipe_group.clear()

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            pipe_group.append(raw_line)
        else:
            flush_pipe_group()
            out_lines.append(raw_line)

    flush_pipe_group()

    return "\n".join(out_lines)


def _render_html(title: str, markdown_text: str, kb_number: str) -> str:
    """Convert markdown to a styled HTML page with Hitachi branding."""
    # Pre-process the extracted markdown to fix tables, strip CSS, etc.
    cleaned = _preprocess_kb_markdown(markdown_text)

    # Convert markdown to HTML (no nl2br — it breaks pipe tables)
    html_body = markdown.markdown(
        cleaned,
        extensions=["tables", "fenced_code"],
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — Hitachi Digital</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont,
                         Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #f4f4f5;
            color: #212222;
            line-height: 1.7;
            -webkit-font-smoothing: antialiased;
        }}

        /* ── Header — matches UnifiedTopNav ── */
        .header {{
            position: sticky;
            top: 0;
            z-index: 50;
            height: 64px;
            background: rgba(255, 255, 255, 0.80);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border-bottom: 1px solid rgba(0, 0, 0, 0.08);
            display: flex;
            align-items: center;
            padding: 0 1.5rem;
            gap: 0.75rem;
        }}
        .header-logo {{
            height: 32px;
            width: auto;
        }}
        .header-divider {{
            width: 1px;
            height: 24px;
            background: rgba(0, 0, 0, 0.15);
        }}
        .header-brand {{
            font-weight: 700;
            font-size: 0.95rem;
            color: #cb2026;
            letter-spacing: 0.5px;
        }}
        .header-badge {{
            margin-left: auto;
            background: #f4f4f5;
            color: #71717a;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.8rem;
            font-weight: 500;
            border: 1px solid rgba(0, 0, 0, 0.08);
        }}

        .container {{
            max-width: 900px;
            margin: 2rem auto;
            padding: 0 1.5rem;
        }}

        .card {{
            background: #fff;
            border-radius: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04);
            padding: 2.5rem;
        }}

        .prose h1 {{ font-size: 1.75rem; font-weight: 700; margin: 1.5rem 0 1rem; color: #212222; }}
        .prose h2 {{ font-size: 1.4rem;  font-weight: 600; margin: 1.5rem 0 0.75rem; color: #212222; }}
        .prose h3 {{ font-size: 1.15rem; font-weight: 600; margin: 1.25rem 0 0.5rem; color: #212222; }}
        .prose p  {{ margin: 0.75rem 0; color: #374151; }}
        .prose ul, .prose ol {{ margin: 0.75rem 0; padding-left: 1.75rem; color: #374151; }}
        .prose li {{ margin: 0.35rem 0; }}
        .prose strong {{ color: #212222; font-weight: 600; }}
        .prose a {{ color: #ed1c24; text-decoration: none; }}
        .prose a:hover {{ text-decoration: underline; }}

        /* ── Table styling ── */
        .prose table {{
            width: 100%;
            border-collapse: collapse;
            margin: 1.25rem 0;
            font-size: 0.9rem;
            overflow: hidden;
            border-radius: 8px;
            border: 1px solid #e5e7eb;
        }}
        .prose thead th {{
            background: #f9fafb;
            padding: 0.65rem 0.75rem;
            text-align: left;
            font-weight: 600;
            font-size: 0.85rem;
            color: #374151;
            border-bottom: 2px solid #e5e7eb;
        }}
        .prose tbody td {{
            padding: 0.55rem 0.75rem;
            border-bottom: 1px solid #f3f4f6;
            color: #4b5563;
        }}
        .prose tbody tr:last-child td {{ border-bottom: none; }}
        .prose tbody tr:hover td {{ background: #f9fafb; }}

        .prose code {{
            background: #f3f4f6;
            padding: 0.15rem 0.4rem;
            border-radius: 4px;
            font-size: 0.9em;
        }}
        .prose pre {{
            background: #18181b;
            color: #e5e7eb;
            padding: 1rem;
            border-radius: 8px;
            overflow-x: auto;
            margin: 1rem 0;
        }}
        .prose pre code {{ background: none; padding: 0; color: inherit; }}
        .prose hr {{
            border: none;
            border-top: 1px solid #e5e7eb;
            margin: 1.5rem 0;
        }}

        .footer {{
            text-align: center;
            padding: 2rem 1rem;
            color: #a1a1aa;
            font-size: 0.8rem;
        }}

        @media (max-width: 640px) {{
            .card {{ padding: 1.25rem; }}
            .container {{ padding: 0 0.75rem; margin: 1rem auto; }}
            .header {{ padding: 0 1rem; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <img src="/hitachi-digital-red.png" alt="Hitachi Digital" class="header-logo"
             onerror="this.style.display='none'">
        <div class="header-divider"></div>
        <div class="header-brand">HD1D | HD1AI</div>
        <div class="header-badge">{kb_number}</div>
    </div>
    <div class="container">
        <div class="card prose">
            {html_body}
        </div>
    </div>
    <div class="footer">
        Hitachi Digital &mdash; Knowledge Base Article {kb_number}
    </div>
</body>
</html>"""
