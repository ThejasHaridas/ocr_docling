"""Thin wrapper around Docling to OCR an image and extract its tables.

Docling does the heavy lifting (OCR + layout + TableFormer table-structure
recognition). This module configures a ``DocumentConverter`` once and exposes a
single helper that returns the raw primitives the parser needs: the document's
markdown text and each detected table as a list-of-row-dicts.

Configuration via environment variables (all optional):
  * ``BILL_EXTRACTOR_OCR``       — OCR engine: ``auto`` (default), ``rapidocr``,
                                   ``easyocr``, ``tesseract`` or ``tesseract_cli``.
  * ``BILL_EXTRACTOR_ARTIFACTS`` — path to a local Docling model cache, for
                                   offline / air-gapped use (see README).
  * ``BILL_EXTRACTOR_CELL_MATCHING`` — ``1`` (default) or ``0`` to let the
                                   table model split cells OCR merged.
  * ``BILL_EXTRACTOR_BACKEND``   — ``auto`` (default: Docling, falling back to
                                   the text backend if models are unreachable),
                                   ``docling`` (force Docling), or ``text``
                                   (pypdfium2 text extraction, no models — good
                                   for digital/text PDFs and fully offline).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Docling (and its heavy torch stack) is imported lazily inside the functions
# that need it, so the offline text backend stays fast and dependency-light.

_OCR_ENGINE_NAMES = {"rapidocr", "easyocr", "tesseract", "tesseract_cli"}


class ModelDownloadError(RuntimeError):
    """Raised when Docling cannot fetch its models (e.g. network policy)."""


def _pipeline_options():
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        PdfPipelineOptions,
        RapidOcrOptions,
        TesseractCliOcrOptions,
        TesseractOcrOptions,
    )

    ocr_options = {
        "rapidocr": RapidOcrOptions,
        "easyocr": EasyOcrOptions,
        "tesseract": TesseractOcrOptions,
        "tesseract_cli": TesseractCliOcrOptions,
    }

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = True
    # ACCURATE TableFormer copes better with skewed photos and borderless
    # tables. Cell matching snaps table cells to OCR words; when one OCR word
    # spans several header columns ("HSNCODEGST%") turning it off lets the
    # table model split them (BILL_EXTRACTOR_CELL_MATCHING=0).
    try:
        from docling.datamodel.pipeline_options import TableFormerMode

        pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
    except ImportError:  # older Docling
        pass
    pipeline_options.table_structure_options.do_cell_matching = (
        os.getenv("BILL_EXTRACTOR_CELL_MATCHING", "1") != "0")

    artifacts = os.getenv("BILL_EXTRACTOR_ARTIFACTS")
    if artifacts:
        pipeline_options.artifacts_path = artifacts

    engine = os.getenv("BILL_EXTRACTOR_OCR", "auto").lower()
    ocr_cls = ocr_options.get(engine)
    if ocr_cls is not None:
        pipeline_options.ocr_options = ocr_cls(force_full_page_ocr=True)
    return pipeline_options


@lru_cache(maxsize=1)
def _build_converter():
    """Build (and cache) a DocumentConverter with OCR + table structure on.

    The converter and its underlying models are expensive to load, so we cache
    a single instance for the life of the process.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    fmt = PdfFormatOption(pipeline_options=_pipeline_options())
    return DocumentConverter(
        format_options={
            InputFormat.IMAGE: fmt,
            InputFormat.PDF: fmt,
        }
    )


def _table_to_rows(table: Any) -> List[Dict[str, Any]]:
    """Convert a Docling table to a list of row dicts keyed by column header."""
    try:
        df = table.export_to_dataframe()
    except Exception:
        return []
    # Stringify column names and replace NaN with None for clean JSON.
    df = df.where(df.notna(), None)
    df.columns = [str(c) for c in df.columns]
    return df.to_dict(orient="records")


def to_docling_document(source: str | Path) -> Tuple[Any, str, List[List[Dict[str, Any]]]]:
    """Extract ``(document, markdown, tables)`` from ``source``.

    Uses Docling by default (best quality: OCR + table structure). When
    ``BILL_EXTRACTOR_BACKEND=text`` — or when Docling's models can't be fetched
    in ``auto`` mode — falls back to plain pypdfium2 text extraction, which needs
    no models and works offline on digital/text PDFs (``tables`` is then empty
    and line items are parsed from the text).
    """
    source = str(source)
    backend = os.getenv("BILL_EXTRACTOR_BACKEND", "auto").lower()

    if backend == "text":
        return _text_backend(source)

    converter = _build_converter()
    try:
        result = converter.convert(source)
    except Exception as exc:  # surface / recover from model-download failures
        download_failure = _looks_like_download_failure(exc)
        if backend == "auto" and download_failure and _is_pdf(source):
            return _text_backend(source)
        if download_failure:
            raise ModelDownloadError(
                "Docling could not download its models. The layout/table models "
                "are hosted on Hugging Face and OCR models on Hugging Face / "
                "ModelScope; this environment may block those hosts.\n"
                "Fix: run this where huggingface.co is reachable, pre-download "
                "models with `docling-tools models download -o ./docling-models` "
                "and set BILL_EXTRACTOR_ARTIFACTS=./docling-models, or force the "
                "offline text backend with BILL_EXTRACTOR_BACKEND=text (digital "
                "PDFs only).\n"
                f"Original error: {exc}"
            ) from exc
        raise

    document = result.document
    try:
        markdown = document.export_to_markdown()
    except Exception:
        markdown = ""

    tables = [_table_to_rows(t) for t in getattr(document, "tables", [])]
    return document, markdown, tables


def _is_pdf(source: str) -> bool:
    return source.lower().endswith(".pdf")


def _text_backend(source: str) -> Tuple[Any, str, List[List[Dict[str, Any]]]]:
    """Model-free text extraction for digital PDFs via pypdfium2."""
    if not _is_pdf(source):
        raise ModelDownloadError(
            "The offline text backend only supports digital PDFs; "
            f"'{source}' needs Docling OCR (which requires model downloads)."
        )
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(source)
    pages = []
    for page in pdf:
        textpage = page.get_textpage()
        pages.append(textpage.get_text_range())
    markdown = "\n".join(pages)
    return None, markdown, []


def _looks_like_download_failure(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    needles = ("download", "huggingface", "modelscope", "403", "connection",
               "resolve host", "max retries", "httperror", "host_not_allowed")
    return any(n in text for n in needles)
