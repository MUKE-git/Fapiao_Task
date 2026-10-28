"""T2-3 同邮件发票与行程单匹配；不提取文件、不调用 AI、不写表。

金额相等层优先于容差层；无金额候选时可用明确原名尾缀兜底。
先计算全部拟配对，再撤回争用同一行程单的关系，避免遍历顺序决定结果。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
import unicodedata

from data_model import (
    InvoiceRecord, ItineraryRecord, KIND_A, MailRecord, MatchedItinerary, NOT_FOUND,
    STATUS_MANUAL_REVIEW, normalize_amount,
)
from processor_contracts import DocumentResult

_TOLERANCE = Decimal("0.5")
_EXTENSIONS = (".pdf", ".xml", ".ofd", ".png", ".jpg", ".jpeg", ".tif",
               ".tiff", ".bmp", ".webp")


def _name_suffix(original_name: str, role_word: str) -> str:
    """只采信角色词后非空、完全相等的尾缀；不借存盘名或模糊相似度。"""
    stem = unicodedata.normalize("NFKC", original_name).strip().casefold()
    for extension in _EXTENSIONS:
        if stem.endswith(extension):
            stem = stem[:-len(extension)]
            break
    position = stem.rfind(role_word)
    if position < 0:
        return ""
    return stem[position + len(role_word):].strip(" \t\r\n_-—–+()[]{}")


def _usable_amount(value: object) -> Decimal | None:
    amount = normalize_amount(value)
    if (not isinstance(amount, Decimal) or amount <= 0
            or amount.as_tuple().exponent < -4):
        return None
    return amount


def _amount_level(left: object, right: object) -> int:
    """0=非候选，1=按原精度相等，2=仅原始差值在容差内。"""
    a, b = _usable_amount(left), _usable_amount(right)
    if a is None or b is None:
        return 0
    places_a = max(0, -a.as_tuple().exponent)
    places_b = max(0, -b.as_tuple().exponent)
    # 匹配不依赖调用方的 Decimal 上下文；加法/量化所需精度保留充分余量。
    with localcontext() as context:
        context.prec = max(28, *(len(x.as_tuple().digits)
                                 + abs(x.as_tuple().exponent) + 8 for x in (a, b)))
        if places_a > places_b:
            equal = a.quantize(Decimal(1).scaleb(-places_b), rounding=ROUND_HALF_UP) == b
        elif places_b > places_a:
            equal = b.quantize(Decimal(1).scaleb(-places_a), rounding=ROUND_HALF_UP) == a
        else:
            equal = a == b
        if equal:
            return 1
        return 2 if abs(a - b) <= _TOLERANCE else 0


@dataclass(frozen=True)
class _Itinerary:
    file_seq: str
    record: ItineraryRecord
    suffix: str


def match_itineraries(mail: MailRecord,
                      itineraries: list[DocumentResult]) -> list[dict]:
    """原位更新匹配所属字段，返回主流程兼容的核查提示。

    mail.itineraries 是已持久化事实源；非空处理结果入参须与其对应。
    直接调用者可用空处理结果列表匹配已有完整 MailRecord，不重建候选。
    所有原始金额、全部行程单、发票业务字段及其他阶段的待核状态保留。
    """
    alerts: list[dict] = []
    files = {item.file_seq: item for item in mail.files}
    file_counts = Counter(item.file_seq for item in mail.files)
    record_counts = Counter(item.record_id for item in mail.records)
    itinerary_counts = Counter(item.record_id for item in mail.itineraries)
    evidence: dict[str, list[DocumentResult]] = defaultdict(list)
    for item in itineraries:
        evidence[item.file_seq].append(item)

    def report(record_id: str, file_seq: str, reason: str,
               record: InvoiceRecord | None = None) -> None:
        if record is not None:
            record.status = STATUS_MANUAL_REVIEW
        flag = f"T2-3 {record_id} {reason}"
        source = files.get(file_seq)
        alerts.append({"mail_subject": mail.subject, "mail_date": mail.mail_date,
                       "mail_date_header": "", "file_seq": file_seq,
                       "filename": source.original_name if source else "",
                       "flags": [flag]})
        if flag not in mail.error_reason.split(";"):
            mail.error_reason = (mail.error_reason + ";" + flag).strip(";")

    available: list[_Itinerary] = []
    prefix = f"{mail.mail_id}-"
    for item in sorted(mail.itineraries, key=lambda value: value.record_id):
        seq = item.record_id[len(prefix):] if item.record_id.startswith(prefix) else ""
        if not seq or file_counts[seq] != 1 or itinerary_counts[item.record_id] != 1:
            report(item.record_id, seq, "行程单来源缺失或重复，需人工核对")
            continue
        if itineraries:
            supplied = evidence[seq]
            if len(supplied) != 1 or supplied[0].role != "Itinerary":
                report(item.record_id, seq, "行程单处理结果来源不唯一或类型不符，需人工核对")
                continue
            fields = supplied[0].fields
            amount = fields.get("match_amount", fields.get("total_amount", NOT_FOUND))
            if (normalize_amount(amount) != normalize_amount(item.match_amount)
                    or str(fields.get("travel_time") or NOT_FOUND) != (item.travel_time or NOT_FOUND)
                    or str(fields.get("origin_dest") or NOT_FOUND) != (item.origin_dest or NOT_FOUND)):
                report(item.record_id, seq, "行程单处理结果与已保存字段不一致，需人工核对")
                continue
        available.append(_Itinerary(seq, item,
                                    _name_suffix(files[seq].original_name, "行程单")))

    # 不在移除 b 类、无效候选或已拟配对项后重新计算单对单例外。
    single_pair = len(mail.records) == 1 and len(mail.itineraries) == 1
    proposals: list[tuple[InvoiceRecord, _Itinerary]] = []
    for record in sorted(mail.records, key=lambda value: value.record_id):
        record.matched_itinerary = None
        record.fields.travel_time = NOT_FOUND if record.kind == KIND_A else ""
        record.fields.origin_dest = NOT_FOUND if record.kind == KIND_A else ""
        if record.kind != KIND_A:
            continue
        seq = record.source.source_file_seq[0] if record.source.source_file_seq else ""
        if (record.record_id != prefix + seq or file_counts[seq] != 1
                or record_counts[record.record_id] != 1
                or any(file_counts[source] != 1 for source in record.source.source_file_seq)):
            report(record.record_id, seq, "发票来源缺失或重复，需人工核对", record)
            continue
        if not available:
            report(record.record_id, seq, "缺行程单或无可用行程单，需人工核对", record)
            continue
        if single_pair:
            proposals.append((record, available[0]))
            continue
        equal: list[_Itinerary] = []
        tolerance: list[_Itinerary] = []
        for item in available:
            level = _amount_level(record.fields.total_amount, item.record.match_amount)
            if level == 1:
                equal.append(item)
            elif level == 2:
                tolerance.append(item)
        candidates = equal or tolerance
        if len(candidates) == 1:
            proposals.append((record, candidates[0]))
            continue
        suffix = _name_suffix(files[seq].original_name, "发票")
        name_candidates = [item for item in (candidates or available)
                           if suffix and suffix == item.suffix]
        if len(name_candidates) == 1:
            proposals.append((record, name_candidates[0]))
            continue
        candidate_ids = "、".join(item.record.record_id
                                 for item in (name_candidates or candidates))
        reason = (f"候选无法唯一确定（{candidate_ids}），需人工核对"
                  if candidate_ids else "金额与原名尾缀均未命中，需人工核对")
        report(record.record_id, seq, reason, record)

    claims = Counter(item.file_seq for _, item in proposals)
    matched: set[str] = set()
    for record, item in proposals:
        seq = record.source.source_file_seq[0]
        if claims[item.file_seq] > 1:
            competing = "、".join(sorted(other.record_id for other, target in proposals
                                        if target.file_seq == item.file_seq))
            report(record.record_id, seq,
                   f"行程单 {item.record.record_id} 被多张发票竞争（{competing}），需人工核对",
                   record)
            continue
        record.matched_itinerary = MatchedItinerary(item.file_seq, item.record.match_amount)
        record.fields.travel_time = item.record.travel_time
        record.fields.origin_dest = item.record.origin_dest
        matched.add(item.file_seq)
        if (_usable_amount(record.fields.total_amount) is None
                or _usable_amount(item.record.match_amount) is None):
            report(record.record_id, seq, "已按数量或原名成组，金额缺失或格式/精度需核对", record)
        missing = [name for name, value in (("出行时间", item.record.travel_time),
                                          ("出发地/目的地", item.record.origin_dest))
                   if not value or value == NOT_FOUND]
        if missing:
            report(record.record_id, seq, "已成组但行程单缺少 " + "、".join(missing), record)

    for item in available:
        if item.file_seq not in matched:
            report(item.record.record_id, item.file_seq, "行程单未匹配汇入，保留候选供人工核对")
    return alerts
