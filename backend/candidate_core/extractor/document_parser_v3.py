"""V3 document parsing with an explicit quality gate.

Fast parsing stays dependency-light. Complex/scanned PDFs are flagged for a
layout-aware fallback instead of silently feeding bad text to the skill model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParseQualityReport:
    passed: bool
    fallback_required: bool
    flags: tuple[str, ...]
    char_count: int
    nonempty_line_count: int
    readable_char_ratio: float
    empty_page_count: int = 0
    page_count: int = 0
    empty_page_ratio: float = 0.0


@dataclass(frozen=True)
class DocumentParseResult:
    path: str
    parser: str
    text: str
    quality: ParseQualityReport


def _readable_ratio(text: str) -> float:
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return 0.0
    readable = 0
    for char in chars:
        category = unicodedata.category(char)
        if category[0] in {"L", "N", "P", "S"} and char != "\ufffd":
            readable += 1
    return readable / len(chars)


def assess_text_quality(
    text: str,
    *,
    page_count: int = 0,
    empty_page_count: int = 0,
    empty_page_ratio_threshold: float = 0.50,
) -> ParseQualityReport:
    stripped = text.strip()
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    flags: list[str] = []
    char_count = len(stripped)
    ratio = _readable_ratio(stripped)
    empty_page_ratio = (
        empty_page_count / page_count if page_count > 0 else 0.0
    )

    if char_count < 80:
        flags.append("too_little_text")
    if len(lines) < 3:
        flags.append("too_few_nonempty_lines")
    if ratio < 0.90:
        flags.append("low_readable_char_ratio")
    if "\ufffd" in stripped:
        flags.append("replacement_character_present")
    if page_count and empty_page_ratio >= empty_page_ratio_threshold:
        flags.append("pdf_many_text_empty_pages")
    # Lots of isolated glyph lines often indicate broken layout/text extraction.
    if lines:
        short_lines = sum(len(re.sub(r"\s+", "", line)) <= 2 for line in lines)
        if short_lines / len(lines) > 0.45:
            flags.append("suspicious_fragmented_lines")

    fallback_required = bool(flags)
    return ParseQualityReport(
        passed=not flags,
        fallback_required=fallback_required,
        flags=tuple(flags),
        char_count=char_count,
        nonempty_line_count=len(lines),
        readable_char_ratio=ratio,
        empty_page_count=empty_page_count,
        page_count=page_count,
        empty_page_ratio=empty_page_ratio,
    )


def _parse_txt(path: Path) -> tuple[str, str, int, int]:
    return path.read_text(encoding="utf-8-sig"), "txt_utf8", 0, 0


def _parse_docx(path: Path) -> tuple[str, str, int, int]:
    """Read paragraphs and tables in the order they appear in the DOCX body."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    doc = Document(str(path))
    texts: list[str] = []
    body = doc.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, doc).text.strip()
            if text:
                texts.append(text)
        elif isinstance(child, CT_Tbl):
            table = Table(child, doc)
            for row in table.rows:
                values = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if values:
                    texts.append("\t".join(values))
    return "\n".join(texts), "python-docx_ordered", 0, 0


def _parse_pdf(path: Path) -> tuple[str, str, int, int]:
    import fitz

    doc = fitz.open(str(path))
    pages: list[str] = []
    empty = 0
    try:
        for page in doc:
            # block extraction with sort=True is safer for common multi-column resumes
            # than raw page.get_text() ordering while keeping the fast path lightweight.
            blocks = page.get_text("blocks", sort=True)
            block_texts = [
                str(block[4]).strip()
                for block in blocks
                if len(block) >= 5 and str(block[4]).strip()
            ]
            page_text = "\n".join(block_texts).strip()
            if page_text:
                pages.append(page_text)
            else:
                empty += 1
        page_count = len(doc)
    finally:
        doc.close()
    return "\n\n".join(pages), "pymupdf_blocks_sorted", page_count, empty


def parse_file_v3(path: str | Path) -> DocumentParseResult:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".txt":
        text, parser, page_count, empty = _parse_txt(path)
    elif suffix == ".docx":
        text, parser, page_count, empty = _parse_docx(path)
    elif suffix == ".pdf":
        text, parser, page_count, empty = _parse_pdf(path)
    else:
        raise ValueError(f"unsupported file type: {suffix}")
    quality = assess_text_quality(
        text, page_count=page_count, empty_page_count=empty
    )
    return DocumentParseResult(
        path=str(path),
        parser=parser,
        text=text,
        quality=quality,
    )
