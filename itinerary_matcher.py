"""T2-3 同邮件发票与行程单匹配；不提取文件、不调用 AI、不写表。

金额相等层优先于容差层；无金额候选时可用明确原名尾缀兜底。
以发票为主记录逐轮生成拟关联，统一检查一对一关系冲突。
冲突时唯一明确尾缀优先；确认配对后，剩余一票一单直接成组。
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

    # 只有确认配对才减少数量；b 类、未知类别、来源异常和待核项仍计数。
    pending: list[InvoiceRecord] = []
    suffixes: dict[str, str] = {}
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
        pending.append(record)
        suffixes[record.record_id] = _name_suffix(files[seq].original_name, "发票")

    matched: set[str] = set()
    blocked_itineraries: set[str] = set()
    unresolved: dict[str, str] = {}

    def confirm(record: InvoiceRecord, item: _Itinerary) -> None:
        seq = record.source.source_file_seq[0]
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

    while pending:
        remaining = [item for item in available
                     if item.file_seq not in matched and item.file_seq not in blocked_itineraries]
        single_pair = (len(mail.records) - len(matched) == 1
                       and len(mail.itineraries) - len(matched) == 1)
        proposals: dict[str, list[tuple[InvoiceRecord, _Itinerary]]] = defaultdict(list)
        # 本轮歧义只暂存；确认其他关系后可能在下一轮解决。
        unresolved = {}
        for record in pending:
            if not remaining:
                unresolved[record.record_id] = "缺行程单或无可用行程单，需人工核对"
                continue
            if single_pair:
                proposals[remaining[0].file_seq].append((record, remaining[0]))
                continue
            equal: list[_Itinerary] = []
            tolerance: list[_Itinerary] = []
            for item in remaining:
                level = _amount_level(record.fields.total_amount, item.record.match_amount)
                if level == 1:
                    equal.append(item)
                elif level == 2:
                    tolerance.append(item)
            candidates = equal or tolerance
            if len(candidates) == 1:
                proposals[candidates[0].file_seq].append((record, candidates[0]))
                continue
            suffix = suffixes[record.record_id]
            name_candidates = [item for item in (candidates or remaining)
                               if suffix and suffix == item.suffix]
            if len(name_candidates) == 1:
                proposals[name_candidates[0].file_seq].append((record, name_candidates[0]))
                continue
            candidate_ids = "、".join(item.record.record_id
                                     for item in (name_candidates or candidates))
            unresolved[record.record_id] = (
                f"候选无法唯一确定（{candidate_ids}），需人工核对"
                if candidate_ids else "金额与原名尾缀均未命中，需人工核对")

        winners: list[tuple[InvoiceRecord, _Itinerary]] = []
        settled: set[str] = set()
        for group in proposals.values():
            if len(group) == 1:
                winners.append(group[0])
                continue
            explicit = [(record, item) for record, item in group
                        if suffixes[record.record_id] and suffixes[record.record_id] == item.suffix]
            if len(explicit) == 1:
                winners.append(explicit[0])
                # 其他发票不写冲突待核，留在下一轮寻找剩余附件。
                continue
            target = group[0][1]
            related = "、".join(record.record_id for record, _ in group)
            reason = (f"多张发票拟关联同一行程单 {target.record.record_id}（{related}），"
                      "一对一关系冲突且无唯一明确尾缀，需人工核对")
            blocked_itineraries.add(target.file_seq)
            for record, _ in group:
                report(record.record_id, record.source.source_file_seq[0], reason, record)
                settled.add(record.record_id)

        # 本轮所有候选与冲突已检查完毕，再统一确认，不按遍历顺序抢先占用。
        for record, item in sorted(winners, key=lambda pair: pair[0].record_id):
            confirm(record, item)
            settled.add(record.record_id)
        pending = [record for record in pending if record.record_id not in settled]
        if not winners:
            break

    for record in pending:
        report(record.record_id, record.source.source_file_seq[0],
               unresolved[record.record_id], record)

    for item in available:
        if item.file_seq not in matched:
            report(item.record.record_id, item.file_seq, "行程单未匹配汇入，保留候选供人工核对")
    return alerts
