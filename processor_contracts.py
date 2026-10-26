"""形态处理器交给主循环的最小文件级结果契约。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentResult:
    file_seq: str
    role: str  # Invoice / Itinerary / Unknown
    fields: dict[str, Any] = field(default_factory=dict)
    source_file_seq: list[str] = field(default_factory=list)
    error_reason: str = ""
    mismatches: list[dict] = field(default_factory=list)
    manual_unknown: list[dict] = field(default_factory=list)
    # T3 additive contract. Each evidence item identifies file_seq, raw value,
    # normalized value and a location (XPath/page/region). A missing value has
    # no evidence item. The main flow preserves these in task_debug.json.
    field_evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    # A checked delivery copy of an OFD/image, never the original input.
    pdf_path: str = ""
