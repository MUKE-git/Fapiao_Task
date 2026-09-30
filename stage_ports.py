"""T2 后续模块接入点；这里只规定主流程所需的最小调用形状。

建议文件名来自 01 docs/01 project/项目结构与任务边界.md。对应任务实现模块后，
主流程在下一轮运行时自动加载；缺席时明确标记待核，不假装环节已完成。
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Callable, Optional

from attachment_processor import CollectedFile
from config import RunConfig
from data_model import MailRecord
from processor_contracts import DocumentResult

Matcher = Callable[[MailRecord, list[DocumentResult]], list[dict]]
Arbitrator = Callable[[list[MailRecord]], list[MailRecord]]
Validator = Callable[[MailRecord], list[dict]]
DocumentHandler = Callable[[CollectedFile, RunConfig, str, str, str], DocumentResult]
VoucherMatcher = Callable[[MailRecord, list[DocumentResult]], list[DocumentResult]]


@dataclass(frozen=True)
class StagePorts:
    match_itineraries: Optional[Matcher] = None
    arbitrate_records: Optional[Arbitrator] = None
    validate_records: Optional[Validator] = None
    document_handlers: dict[str, DocumentHandler] | None = None
    combine_vouchers: Optional[VoucherMatcher] = None


def _optional(module_name: str, function_name: str):
    if not (Path(__file__).resolve().parent / f"{module_name}.py").is_file():
        return None
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise
    function = getattr(module, function_name)
    if not callable(function):
        raise TypeError(f"{module_name}.{function_name} 必须可调用")
    return function


def load_stage_ports() -> StagePorts:
    """T2-3/4/6 按各自任务实现后即可接入，不依赖 T1 快照。"""
    return StagePorts(
        match_itineraries=_optional("itinerary_matcher", "match_itineraries"),
        arbitrate_records=_optional("dedup_arbitrator", "arbitrate_records"),
        validate_records=_optional("field_validator", "validate_records"),
        document_handlers={
            file_type: handler for file_type, handler in (
                ("XML", _optional("xml_processor", "process_xml")),
                ("OFD", _optional("ofd_processor", "process_ofd")),
                ("图片", _optional("image_processor", "process_image")),
            ) if handler is not None
        },
        combine_vouchers=_optional("voucher_matcher", "combine_vouchers"),
    )
