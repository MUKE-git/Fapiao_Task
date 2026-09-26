"""
T2-2 数据底座 · 静态 Schema 层（由新建而来，契约 = §3 数据模型 / 字段表.txt）。

本文件只定义「结构」与「枚举」与「序列化」：
- 4 实体 dataclass：邮件 / 文件 / 发票 / 行程单（字段、类型、默认值对齐《字段表.txt》）
- 枚举常量：file_type / pre_read_type / kind / status
- 嵌套结构：records.fields / records.source / records.matched_itinerary / dedup_skipped
- 序列化函数：mail_to_dict → 契约 §5.3 task_debug.json 顶层结构

职责边界（T2-2 底座思想）：
- 本文件 = 静态层，不含组装逻辑 / AI 调用 / 匹配算法 / pipeline 编排。
- 组装逻辑（文件序号生成、files/records 构造、kind 判定、1:1 配对）见 records_builder.py。
- 字段运行时校验（20 位票号、金额 >0、日期格式）属 T2-6 字段校验差异化，不在本层。
- 不碰 invoice_pipeline.py（T2-1 重写）/ ai_client.py（§7 冻结）/ T2-3 多对多匹配。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Union


# ---------------------------------------------------------------------------
# 枚举常量（契约取值，字符串形式便于 JSON 序列化）
# ---------------------------------------------------------------------------

# 文件物理格式
FILE_TYPE_PDF = "PDF"
FILE_TYPE_XML = "XML"
FILE_TYPE_OFD = "OFD"
FILE_TYPE_IMAGE = "图片"
FILE_TYPE_OTHER = "其他"
FILE_TYPES = (FILE_TYPE_PDF, FILE_TYPE_XML, FILE_TYPE_OFD, FILE_TYPE_IMAGE, FILE_TYPE_OTHER)

# 预读凭证角色
PRE_READ_INVOICE = "Invoice"
PRE_READ_ITINERARY = "Itinerary"
PRE_READ_UNKNOWN = "Unknown"
PRE_READ_TYPES = (PRE_READ_INVOICE, PRE_READ_ITINERARY, PRE_READ_UNKNOWN)

# 发票行程类型
KIND_A = "a类"   # 交通类，需行程单
KIND_B = "b类"   # 非交通类，不需行程单
KINDS = (KIND_A, KIND_B)

# 记录处理结论
STATUS_COMPLETE = "complete"
STATUS_MISSING_VOUCHER = "缺凭证"
STATUS_MANUAL_REVIEW = "人工待核"
STATUSES = (STATUS_COMPLETE, STATUS_MISSING_VOUCHER, STATUS_MANUAL_REVIEW)

# 可空字段占位（对齐字段表默认值）
NOT_FOUND = "Not Found"


# ---------------------------------------------------------------------------
# 4 实体 dataclass
# ---------------------------------------------------------------------------

@dataclass
class FileRecord:
    """文件实体：邮件内每个附件/凭证文件。

    字段对齐《字段表.txt》「文件」实体。
    file_seq 为邮件内唯一两位数字串（01-99），与 mail_id 拼成全局定位键 Msg{n}-{m}。
    """
    file_seq: str                                  # char(2) 01-99，邮件内 UNIQUE
    file_type: str = FILE_TYPE_OTHER                # PDF/XML/OFD/图片/其他
    original_name: str = ""                         # 附件原始名（varchar 100）
    pre_read_type: str = PRE_READ_UNKNOWN           # Invoice/Itinerary/Unknown
    pre_read_invoice_no: str = NOT_FOUND            # 可空，20 位数字串
    pre_read_amount: Union[float, str] = NOT_FOUND  # 可空，decimal(16,2)


@dataclass
class SourceMeta:
    """来源元数据（行级溯源）：记录挂来源文件序号，便于人工待核定位。

    契约 §5.3 / D2：每条记录挂 来源邮件(主题+Date头) + 来源文件。
    邮件维度（subject/mail_date）挂顶层 MailRecord，本对象只记文件序号。
    """
    source_file_seq: List[str] = field(default_factory=list)


@dataclass
class MatchedItinerary:
    """行程单匹配汇入信息（D6：只记 文件序号 + 匹配金额，用于追源）。

    匹配方式字段不保留（D6）；仅 a 类发票匹配成功后填，b 类不填。
    多对多匹配引擎属 T2-3，本对象只承载命中结果。
    """
    file_seq: str = ""                                       # 行程单文件序号
    match_amount: Union[float, str] = NOT_FOUND              # 行程单票面金额


@dataclass
class InvoiceFields:
    """发票业务字段（嵌套在 record.fields）。

    对齐《字段表.txt》「发票」实体 + 行程单汇入字段。
    travel_time/origin_dest：所有者是行程单，a 类匹配成功后汇入；b 类不写（留空）。
    """
    invoice_date: str = NOT_FOUND                           # date YYYY-MM-DD
    invoice_number: str = NOT_FOUND                         # varchar(20) 20位纯数字 UNIQUE
    total_amount: Union[float, str] = NOT_FOUND             # decimal(16,2)
    invoice_type: str = NOT_FOUND                           # varchar(20) 可空
    receipt_type: str = NOT_FOUND                           # varchar(50) 二级类目
    seller: str = NOT_FOUND                                 # varchar(50) trim+去逗号
    travel_time: str = NOT_FOUND                            # datetime YYYY-MM-DD HH:mm（a类汇入）
    origin_dest: str = NOT_FOUND                           # varchar(100) A→B（a类汇入）


@dataclass
class InvoiceRecord:
    """发票实体：一张发票一条记录（D1：一发票一笔）。

    record_id = Msg{n}-{m}，全局定位键（mail_id + 文件序号）。
    matched_itinerary 仅 a 类匹配成功后填；b 类/未匹配 a 类为 None（序列化时省略）。
    """
    record_id: str = ""
    kind: str = KIND_B                                       # a类/b类
    fields: InvoiceFields = field(default_factory=InvoiceFields)
    source: SourceMeta = field(default_factory=SourceMeta)
    matched_itinerary: Optional[MatchedItinerary] = None
    status: str = STATUS_COMPLETE


@dataclass
class DedupSkipped:
    """去重跳过明细：记录被去重掉的重复文件。

    D 约束：MD5 仅处理期内存在，不存 files[] 或 dedup_skipped[]。
    故本对象只含 filename + reason，不含 md5 字段。
    判重/仲裁完整规则属 T2-4，本对象只承载结果。
    """
    filename: str = ""
    reason: str = ""


@dataclass
class MailRecord:
    """邮件实体：一封未读邮件一个 mail 对象。

    顶层 = 契约 §5.3 task_debug.json 每封邮件一个条目。
    mail_date 为 datetime YYYY-MM-DD HH:mm（分钟精度，无秒）。
    """
    mail_id: str = ""                                       # Msg{n} 业务序号
    mail_uid: str = ""                                      # IMAP UID 纯数字，追源锚点
    subject: str = ""                                       # 邮件主题
    mail_date: str = ""                                     # YYYY-MM-DD HH:mm
    files: List[FileRecord] = field(default_factory=list)
    records: List[InvoiceRecord] = field(default_factory=list)
    dedup_skipped: List[DedupSkipped] = field(default_factory=list)
    error_reason: str = ""


# ---------------------------------------------------------------------------
# 序列化（dataclass → 契约 §5.3 JSON dict）
# ---------------------------------------------------------------------------

def _file_to_dict(f: FileRecord) -> dict:
    """文件条目：按契约 §5.3 files[] 字段顺序输出。"""
    return {
        "file_seq": f.file_seq,
        "file_type": f.file_type,
        "original_name": f.original_name,
        "pre_read_type": f.pre_read_type,
        "pre_read_invoice_no": f.pre_read_invoice_no,
        "pre_read_amount": f.pre_read_amount,
    }


def _record_to_dict(r: InvoiceRecord) -> dict:
    """发票记录：按契约 §5.3 records[] 结构输出。

    - fields 中 travel_time/origin_dest 为 NOT_FOUND/空 时省略（b 类不写 / a 类未汇入）。
    - matched_itinerary 为 None 时省略（b 类 / 未匹配 a 类）。
    """
    fields: dict = {
        "invoice_date": r.fields.invoice_date,
        "invoice_number": r.fields.invoice_number,
        "total_amount": r.fields.total_amount,
        "invoice_type": r.fields.invoice_type,
        "receipt_type": r.fields.receipt_type,
        "seller": r.fields.seller,
    }
    # travel_time/origin_dest 仅在有值时输出（a 类已汇入），否则省略 key
    if r.fields.travel_time and r.fields.travel_time != NOT_FOUND:
        fields["travel_time"] = r.fields.travel_time
    if r.fields.origin_dest and r.fields.origin_dest != NOT_FOUND:
        fields["origin_dest"] = r.fields.origin_dest

    out: dict = {
        "record_id": r.record_id,
        "kind": r.kind,
        "fields": fields,
        "source": {"source_file_seq": list(r.source.source_file_seq)},
    }
    if r.matched_itinerary is not None:
        out["matched_itinerary"] = {
            "file_seq": r.matched_itinerary.file_seq,
            "match_amount": r.matched_itinerary.match_amount,
        }
    out["status"] = r.status
    return out


def mail_to_dict(mail: MailRecord) -> dict:
    """邮件 → 契约 §5.3 task_debug.json 顶层条目 dict。

    输出顺序：mail_id / mail_uid / subject / mail_date / files / records / dedup_skipped / error_reason。
    """
    return {
        "mail_id": mail.mail_id,
        "mail_uid": mail.mail_uid,
        "subject": mail.subject,
        "mail_date": mail.mail_date,
        "files": [_file_to_dict(f) for f in mail.files],
        "records": [_record_to_dict(r) for r in mail.records],
        "dedup_skipped": [
            {"filename": d.filename, "reason": d.reason} for d in mail.dedup_skipped
        ],
        "error_reason": mail.error_reason,
    }
