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
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    EasyOcrOptions,
    PdfPipelineOptions,
    RapidOcrOptions,
    TesseractCliOcrOptions,
    TesseractOcrOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption

_OCR_OPTIONS = {
    "rapidocr": RapidOcrOptions,
    "easyocr": EasyOcrOptions,
    "tesseract": TesseractOcrOptions,
    "tesseract_cli": TesseractCliOcrOptions,
}


class ModelDownloadError(RuntimeError):
    """Raised when Docling cannot fetch its models (e.g. network policy)."""


def _pipeline_options() -> PdfPipelineOptions:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options.do_cell_matching = True

    artifacts = os.getenv("BILL_EXTRACTOR_ARTIFACTS")
    if artifacts:
        pipeline_options.artifacts_path = artifacts

    engine = os.getenv("BILL_EXTRACTOR_OCR", "auto").lower()
    ocr_cls = _OCR_OPTIONS.get(engine)
    if ocr_cls is not None:
        pipeline_options.ocr_options = ocr_cls(force_full_page_ocr=True)
    return pipeline_options


@lru_cache(maxsize=1)
def _build_converter() -> DocumentConverter:
    """Build (and cache) a DocumentConverter with OCR + table structure on.

    The converter and its underlying models are expensive to load, so we cache
    a single instance for the life of the process.
    """
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
    """Run Docling on ``source`` and return ``(document, markdown, tables)``.

    ``tables`` is a list of tables, each a list of row dicts.
    """
    source = str(source)
    converter = _build_converter()
    try:
        result = converter.convert(source)
    except Exception as exc:  # surface model-download failures clearly
        if _looks_like_download_failure(exc):
            raise ModelDownloadError(
                "Docling could not download its models. The layout/table models "
                "are hosted on Hugging Face and OCR models on Hugging Face / "
                "ModelScope; this environment may block those hosts.\n"
                "Fix: run this where huggingface.co is reachable, or pre-download "
                "models with `docling-tools models download -o ./docling-models` "
                "and set BILL_EXTRACTOR_ARTIFACTS=./docling-models.\n"
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


def _looks_like_download_failure(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    needles = ("download", "huggingface", "modelscope", "403", "connection",
               "resolve host", "max retries", "httperror", "host_not_allowed")
    return any(n in text for n in needles)
