"""Build the frozen C01-C19 synthetic case inputs and draft oracles.

Only the 18 mandatory IDs are emitted. This initializes review material; it does
not turn a draft oracle into an approved business answer or run the product AI.
"""
from __future__ import annotations

import hashlib
import json
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases"
DATE_HEADER = "Tue, 29 Sep 2026 09:30:00 +0800"
FROM = "fixture-sender@example.test"
TO = "fixture-reviewer@example.test"
FONT_PATH = Path(r"C:\Windows\Fonts\simhei.ttf")
pdfmetrics.registerFont(TTFont("CaseSimHei", str(FONT_PATH)))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def invoice(name: str, number: str | None, amount: str | None,
            *, date: str | None = "2026-09-28", receipt: str = "办公用品",
            seller: str = "合成测试销售方", extra: list[str] | None = None) -> dict:
    return {"name": name, "role": "Invoice", "fields": {
        "invoice_date": date or "Not Found",
        "invoice_number": number or "Not Found",
        "total_amount": amount or "Not Found",
        "invoice_type": "电子发票（普通发票）",
        "receipt_type": receipt,
        "seller": seller,
    }, "extra": extra or []}


def itinerary(name: str, amount: str | None, *,
              travel: str = "2026-09-25 10:00",
              route: str) -> dict:
    return {"name": name, "role": "Itinerary", "fields": {
        "match_amount": amount or "Not Found",
        "travel_time": travel,
        "origin_dest": route,
    }, "extra": []}


def pdf_bytes(doc: dict) -> bytes:
    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4, invariant=1, pageCompression=0)
    page.setTitle("合成测试材料")
    page.setFont("CaseSimHei", 18)
    page.drawString(58, 775, "合成测试发票" if doc["role"] == "Invoice" else "合成测试行程单")
    page.setFont("CaseSimHei", 11)
    lines = ["仅供软件测试，不是真实报销凭证", f"样本文件：{doc['name']}"]
    fields = doc["fields"]
    if doc["role"] == "Invoice":
        labels = [("invoice_number", "发票号码"), ("invoice_date", "开票日期"),
                  ("total_amount", "价税合计"), ("invoice_type", "发票类型"),
                  ("receipt_type", "票面类型"), ("seller", "销售方")]
    else:
        labels = [("match_amount", "报销金额"), ("travel_time", "出行时间"),
                  ("origin_dest", "出发地/目的地")]
    lines += [f"{label}：{fields[key]}" for key, label in labels
              if fields.get(key) not in (None, "Not Found")]
    lines += doc.get("extra", [])
    for idx, line in enumerate(lines):
        page.drawString(58, 733 - idx * 27, line)
    page.showPage()
    page.save()
    return buffer.getvalue()


def zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, content in entries:
            info = ZipInfo(name, date_time=(2026, 9, 29, 9, 30, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue()


def eml_bytes(subject: str, attachments: list[tuple[str, bytes]]) -> bytes:
    mail = EmailMessage()
    mail["Subject"] = subject
    mail["From"] = FROM
    mail["To"] = TO
    mail["Date"] = DATE_HEADER
    mail["Message-ID"] = f"<case-{sha256(subject.encode())[:16]}@example.test>"
    mail.set_content("合成测试邮件。附件仅供固定用例审核。")
    for name, content in attachments:
        subtype = "pdf" if name.lower().endswith(".pdf") else "zip" if name.lower().endswith(".zip") else "octet-stream"
        mail.add_attachment(content, maintype="application", subtype=subtype, filename=name)
    return mail.as_bytes()


def fixture(name: str, doc: dict, *, corrupt: bool = False) -> dict:
    return {"name": name, "doc": doc, "corrupt": corrupt}


def pdf(name: str, doc: dict) -> dict:
    return fixture(name, doc)


def archive(name: str, entries: list[dict]) -> dict:
    return {"name": name, "zip_entries": entries}


def variant(code: str, subject: str | None, attachments: list[dict],
            expected: dict, note: str) -> dict:
    return {"code": code, "subject": subject, "attachments": attachments,
            "expected": expected, "note": note}


def exp(*, selected: int, files: int, records: int, itineraries: int,
        rows: int, matched: dict[str, str] | None = None,
        duplicate_skipped: int = 0, alerts: list[str] | None = None,
        field_checks: dict | None = None) -> dict:
    return {"unseen_total": 0 if selected == -1 else 1,
            "selected_mails": max(selected, 0), "file_count": files,
            "record_count": records, "itinerary_count": itineraries,
            "excel_rows": rows, "matched_by_invoice": matched or {},
            "duplicate_skipped": duplicate_skipped,
            "alerts_contain": alerts or [], "field_checks": field_checks or {}}


def case_def(code: str, title: str, stages: list[str], variants: list[dict],
             review_focus: str) -> dict:
    return {"id": code, "title": title, "priority": "必选",
            "stages": stages, "source_kind": "合成", "review_status": "待审核",
            "review_focus": review_focus, "variants": variants}


def all_cases() -> list[dict]:
    n = lambda x: f"9000000000000000{x:04d}"
    ordinary = lambda filename, x, amount="126.40": invoice(filename, n(x), amount)
    traffic = lambda filename, x, amount="53.93": invoice(
        filename, n(x), amount, receipt="客运服务费")

    c01 = case_def("C01", "无未读邮件", ["T2-1"], [
        variant("v1", None, [], exp(selected=-1, files=0, records=0,
                                    itineraries=0, rows=0),
                "INBOX 的 UNSEEN 搜索为空；没有邮件 FETCH 或附件处理。")],
        "检查邮箱状态、附件/PDF 调用次数和空输出。")
    c02 = case_def("C02", "主题不命中", ["T2-1"], [
        variant("v1", "普通会议通知 C02", [pdf("无关凭证.pdf", ordinary("无关凭证.pdf", 2))],
                exp(selected=0, files=0, records=0, itineraries=0, rows=0),
                "邮件未读且含 PDF，但主题没有发票/报销关键词；不得完整 FETCH 附件。")],
        "检查主题筛选在附件处理前发生。")
    c03 = case_def("C03", "命中主题但无 PDF", ["T2-1"], [
        variant("v1", "电子发票 C03", [],
                exp(selected=1, files=0, records=0, itineraries=0, rows=0,
                    alerts=["无有效附件"]),
                "命中主题，邮件没有任何附件。")],
        "检查零文件、零记录、零 Excel 行和无有效附件提示。")
    c04 = case_def("C04", "普通发票", ["T2-1", "T2-2"], [
        variant("v1", "电子发票 C04", [pdf("办公用品发票.pdf", ordinary("办公用品发票.pdf", 4))],
                exp(selected=1, files=1, records=1, itineraries=0, rows=1,
                    field_checks={"办公用品发票.pdf": {
                        "invoice_number": n(4), "invoice_date": "2026-09-28",
                        "total_amount": "126.40", "receipt_type": "办公用品"}}),
                "正文含完整的普通发票字段；输出一票一笔。")],
        "逐项核对 PDF 页 1 的字段和一行汇总表。")
    c05 = case_def("C05", "单张发票与单份行程单同封", ["T2-3"], [
        variant("v1", "电子发票及行程单 C05", [
            pdf("客运发票.pdf", traffic("客运发票.pdf", 5)),
            pdf("行程单.pdf", itinerary("行程单.pdf", "53.93",
                                       route="杭州东站→杭州萧山国际机场"))],
            exp(selected=1, files=2, records=1, itineraries=1, rows=1,
                matched={"客运发票.pdf": "行程单.pdf"}),
            "整封邮件恰好一票一单；行程字段只汇入发票行。")],
        "检查两个文件各自来源、matched_itinerary 与一行 Excel。")
    c06 = case_def("C06", "ZIP 根目录 PDF", ["T2-1", "T2-5"], [
        variant("v1", "电子发票 C06", [archive("报销附件.zip", [
            pdf("压缩包内发票.pdf", ordinary("压缩包内发票.pdf", 6))])],
            exp(selected=1, files=1, records=1, itineraries=0, rows=1),
            "PDF 在 ZIP 根目录；解包后保留原名和来源。")],
        "检查 ZIP 内容、登记文件和一行发票。")
    c07 = case_def("C07", "多附件及重复文件", ["T2-1"], [
        variant("v1", "电子发票 C07", [
            pdf("发票甲.pdf", ordinary("发票甲.pdf", 7, "53.93")),
            archive("汇总附件.zip", [
                pdf("发票甲.pdf", ordinary("发票甲.pdf", 7, "53.93")),
                pdf("发票乙.pdf", ordinary("发票乙.pdf", 70, "126.40"))])],
            exp(selected=1, files=2, records=2, itineraries=0, rows=2,
                duplicate_skipped=1),
            "ZIP 内甲与直接附件甲字节完全相同；乙不同。")],
        "检查同邮件 MD5 去重，区别于 T2-4 票号仲裁。")
    c08 = case_def("C08", "无类型提示的附件名", ["T2-1", "T2-5"], [
        variant("v1", "电子发票 C08", [pdf("附件01.pdf", ordinary("附件01.pdf", 8))],
                exp(selected=1, files=1, records=1, itineraries=0, rows=1),
                "原名不含发票/行程单；PDF 正文含发票字段。")],
        "检查角色判断依据，保留原名与内部名对应关系。")
    c09 = case_def("C09", "不可读 PDF", ["T2-1"], [
        variant("v1", "电子发票 C09", [fixture("损坏发票.pdf",
                {"name": "损坏发票.pdf", "role": "Unknown", "fields": {},
                 "error_reason": "PDF_READ_ERROR"}, corrupt=True)],
                exp(selected=1, files=1, records=0, itineraries=0, rows=0,
                    alerts=["PDF_READ_ERROR"]),
                "附件以 PDF 扩展名提交，但字节故意损坏；不能猜造字段。")],
        "检查错误可定位，文件登记保留且无虚构发票行。")
    c11 = case_def("C11", "金额或日期有歧义", ["T2-1", "T2-6"], [
        variant("v1", "电子发票 C11", [pdf("多金额多日期发票.pdf", invoice(
            "多金额多日期发票.pdf", n(11), "128.68", date="2026-10-03",
            extra=["单价：120.00", "税额：8.68", "行程日期：2026-10-01"]))],
            exp(selected=1, files=1, records=1, itineraries=0, rows=1,
                field_checks={"多金额多日期发票.pdf": {
                    "total_amount": "128.68", "invoice_date": "2026-10-03"}}),
            "单价、税额、价税合计及两种日期同时出现。")],
        "检查价税合计优先，开票日期不取行程日期。")
    c12 = case_def("C12", "关键字段缺失", ["T2-1", "T2-6"], [
        variant("partial", "电子发票 C12 部分缺失", [pdf("缺票号发票.pdf", invoice(
            "缺票号发票.pdf", None, "126.40"))],
            exp(selected=1, files=1, records=1, itineraries=0, rows=1,
                alerts=["缺少发票号码"], field_checks={"缺票号发票.pdf": {
                    "invoice_number": "Not Found", "total_amount": "126.40"}}),
            "只有票号缺失；保留 JSON 记录并写一行 Excel。"),
        variant("all_missing", "电子发票 C12 全缺", [pdf("三项全缺发票.pdf", invoice(
            "三项全缺发票.pdf", None, None, date=None))],
            exp(selected=1, files=1, records=1, itineraries=0, rows=0,
                alerts=["均缺失，未写入 Excel"],
                field_checks={"三项全缺发票.pdf": {
                    "invoice_number": "Not Found", "invoice_date": "Not Found",
                    "total_amount": "Not Found"}}),
            "票号、日期、金额三项全缺；JSON 仍留记录，Excel 零行。")],
        "两个变体分别检查部分缺失与三项全缺的入表边界。")
    c13 = case_def("C13", "交通类发票无行程单", ["T2-3", "T2-6"], [
        variant("v1", "电子发票 C13", [pdf("交通发票.pdf", traffic("交通发票.pdf", 13, "80.13"))],
                exp(selected=1, files=1, records=1, itineraries=0, rows=1,
                    alerts=["缺行程单"]),
                "a 类发票关键字段完整，同封没有行程单。")],
        "检查记录、Excel 行、空匹配关系和缺行程单提示。")

    c14 = case_def("C14", "T2-3 金额层级", ["T2-3"], [
        variant("v1", "电子发票及行程单 C14", [
            pdf("发票.pdf", traffic("发票.pdf", 14, "100.00")),
            pdf("行程单甲.pdf", itinerary("行程单甲.pdf", "100.0000", route="杭州东站→杭州宋城（之江路148号）")),
            pdf("行程单乙.pdf", itinerary("行程单乙.pdf", "100.40", route="杭州宋城（之江路148号）→杭州东站"))],
            exp(selected=1, files=3, records=1, itineraries=2, rows=1,
                matched={"发票.pdf": "行程单甲.pdf"}),
            "精度舍入后相等层优先于仅在 0.5 容差内的候选。")],
        "检查 100.0000 原精度及未匹配乙单保留。")
    c15 = case_def("C15", "T2-3 同层原名消歧", ["T2-3", "T2-5"], [
        variant("v1", "电子发票及行程单 C15", [
            pdf("发票B.pdf", traffic("发票B.pdf", 15, "100.00")),
            pdf("行程单A.pdf", itinerary("行程单A.pdf", "100.00", route="中国美术学院（南山校区）→杭州东站")),
            pdf("行程单B.pdf", itinerary("行程单B.pdf", "100.00", route="浙江省科技馆（西湖文化广场2号）→杭州宋城"))],
            exp(selected=1, files=3, records=1, itineraries=2, rows=1,
                matched={"发票B.pdf": "行程单B.pdf"}),
            "两份行程单都处在相等金额层，只能用原名尾缀 B 消歧。")],
        "检查完整解码原名，不使用内部存盘名。")

    def c16_variant(code: str, target_amount: str, inv_name: str,
                    target_name: str, should_match: bool) -> dict:
        return variant(code, f"电子发票及行程单 C16 {code}", [
            pdf(inv_name, traffic(inv_name, 16, "100.00")),
            pdf(target_name, itinerary(target_name, target_amount,
                                       route="杭州东站→杭州宋城")),
            pdf("行程单D.pdf", itinerary("行程单D.pdf", "200.00",
                                        route="杭州东站→杭州萧山国际机场"))],
            exp(selected=1, files=3, records=1, itineraries=2, rows=1,
                matched={inv_name: target_name} if should_match else {}),
            f"原始金额差值 {target_amount} - 100.00；"
            + ("唯一目标应成组。" if should_match else "原名也不对应，不自动成组。"))

    c16 = case_def("C16", "T2-3 容差边界与原名下一步", ["T2-3"], [
        c16_variant("d049", "100.49", "发票C.pdf", "行程单A.pdf", True),
        c16_variant("d050", "100.50", "发票C.pdf", "行程单A.pdf", True),
        c16_variant("d051_name", "100.51", "发票A.pdf", "行程单A.pdf", True),
        c16_variant("d051_no_name", "100.51", "发票C.pdf", "行程单A.pdf", False),
    ], "0.49/0.50 进入容差层；0.51 不入该层，但唯一同尾缀可兜底。")
    c17 = case_def("C17", "T2-3 缺金额时原名兜底", ["T2-3"], [
        variant("v1", "电子发票及行程单 C17", [
            pdf("发票A.pdf", traffic("发票A.pdf", 17, None)),
            pdf("行程单A.pdf", itinerary("行程单A.pdf", "500.00", route="杭州东站→千岛湖镇人民政府")),
            pdf("行程单B.pdf", itinerary("行程单B.pdf", "600.00", route="杭州萧山国际机场→千岛湖镇人民政府"))],
            exp(selected=1, files=3, records=1, itineraries=2, rows=1,
                matched={"发票A.pdf": "行程单A.pdf"},
                alerts=["金额缺失或格式/精度需核对"],
                field_checks={"发票A.pdf": {"total_amount": "Not Found"}}),
            "发票金额缺失，但完整原名尾缀 A 唯一对应；不代填金额。")],
        "检查配对关系与缺金额待核同时存在。")
    c18 = case_def("C18", "T2-3 多票竞争一单", ["T2-3"], [
        variant("v1", "电子发票及行程单 C18", [
            pdf("发票A.pdf", traffic("发票A.pdf", 181, "100.00")),
            pdf("发票B.pdf", traffic("发票B.pdf", 182, "100.00")),
            pdf("发票C.pdf", traffic("发票C.pdf", 183, "200.00")),
            pdf("行程单X.pdf", itinerary("行程单X.pdf", "100.00", route="杭州宋城→杭州萧山国际机场")),
            pdf("行程单C.pdf", itinerary("行程单C.pdf", "200.00", route="临安汽车东站→杭州东站"))],
            exp(selected=1, files=5, records=3, itineraries=2, rows=3,
                matched={"发票C.pdf": "行程单C.pdf"},
                alerts=["被多张发票竞争"]),
            "A/B 均先拟配 X，冲突撤回；C/C 独立配对。")],
        "检查竞争双方都无 matched_itinerary，独立组成功。")
    c19 = case_def("C19", "T2-3 无法唯一匹配", ["T2-3"], [
        variant("same_amount", "电子发票及行程单 C19 同金额", [
            pdf("发票C.pdf", traffic("发票C.pdf", 191, "100.00")),
            pdf("行程单A.pdf", itinerary("行程单A.pdf", "100.00", route="杭州萧山国际机场→杭州宋城")),
            pdf("行程单B.pdf", itinerary("行程单B.pdf", "100.00", route="杭州宋城→中国美术学院（南山校区）"))],
            exp(selected=1, files=3, records=1, itineraries=2, rows=1,
                alerts=["候选无法唯一确定"]),
            "两个同层金额候选，发票 C 与 A/B 均无尾缀对应。"),
        variant("no_evidence", "电子发票及行程单 C19 无证据", [
            pdf("发票C.pdf", traffic("发票C.pdf", 192, "100.00")),
            pdf("行程单A.pdf", itinerary("行程单A.pdf", "200.00", route="千岛湖镇人民政府→杭州东站")),
            pdf("行程单B.pdf", itinerary("行程单B.pdf", "300.00", route="临安汽车东站→杭州萧山国际机场"))],
            exp(selected=1, files=3, records=1, itineraries=2, rows=1,
                alerts=["金额与原名尾缀均未命中"]),
            "金额两层均不命中，发票 C 与 A/B 均无尾缀对应。")],
        "无唯一证据时不猜关系，全部行程单保留。")

    return [c01, c02, c03, c04, c05, c06, c07, c08, c09,
            c11, c12, c13, c14, c15, c16, c17, c18, c19]


def build_variant(case: dict, spec: dict, pdf_cache: dict[str, bytes]) -> dict:
    folder = CASES / case["id"] / "variants" / spec["code"]
    folder.mkdir(parents=True, exist_ok=False)
    state = {"folder": "INBOX", "uid": f"8{int(case['id'][1:]):03d}",
             "unseen_uids": [] if spec["subject"] is None else [f"8{int(case['id'][1:]):03d}"],
             "messages": 0 if spec["subject"] is None else 1,
             "unseen": 0 if spec["subject"] is None else 1,
             "subject": spec["subject"], "date_header": DATE_HEADER if spec["subject"] else None}
    write_json(folder / "imap_state.json", state)

    input_parts: list[tuple[str, bytes]] = []
    input_inventory: list[dict] = []
    extracted: list[dict] = []
    extracted_seen: set[str] = set()

    def payload(entry: dict) -> bytes:
        if entry.get("corrupt"):
            return b"%PDF-1.4\n%INTENTIONALLY_CORRUPT_FIXED_CASE\n"
        document = entry["doc"]
        key = json.dumps(document, ensure_ascii=False, sort_keys=True)
        if key not in pdf_cache:
            pdf_cache[key] = pdf_bytes(document)
        return pdf_cache[key]

    def save_doc(entry: dict, relpath: Path, content: bytes, source: str) -> None:
        target = folder / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        input_inventory.append({"path": relpath.as_posix(), "name": entry["name"],
                                "source": source, "bytes": len(content), "sha256": sha256(content)})
        if entry["name"] not in extracted_seen:
            doc = entry["doc"]
            extracted.append({"original_name": entry["name"], "role": doc["role"],
                              "fields": doc["fields"],
                              "error_reason": doc.get("error_reason", ""),
                              "source_sha256": sha256(content)})
            extracted_seen.add(entry["name"])

    for entry in spec["attachments"]:
        if "zip_entries" in entry:
            packed = []
            for child in entry["zip_entries"]:
                content = payload(child)
                packed.append((child["name"], content))
                save_doc(child, Path("attachments") / "zip" / child["name"],
                         content, entry["name"])
            zipped = zip_bytes(packed)
            zip_path = Path("attachments") / "direct" / entry["name"]
            (folder / zip_path).parent.mkdir(parents=True, exist_ok=True)
            (folder / zip_path).write_bytes(zipped)
            input_inventory.append({"path": zip_path.as_posix(), "name": entry["name"],
                                    "source": "MIME", "bytes": len(zipped),
                                    "sha256": sha256(zipped)})
            input_parts.append((entry["name"], zipped))
        else:
            content = payload(entry)
            save_doc(entry, Path("attachments") / "direct" / entry["name"],
                     content, "MIME")
            input_parts.append((entry["name"], content))

    if spec["subject"] is not None:
        raw = eml_bytes(spec["subject"], input_parts)
        (folder / "00_raw.eml").write_bytes(raw)
        input_inventory.insert(0, {"path": "00_raw.eml", "name": "原始邮件",
                                   "source": "RFC822/MIME", "bytes": len(raw),
                                   "sha256": sha256(raw)})
    write_json(folder / "input_inventory.json", input_inventory)
    write_json(folder / "extract.json", {
        "type": "受控逐文件提取结果；不是 AI 或真实 PDF 处理器的实测输出",
        "documents": extracted})
    write_json(folder / "expected.json", {
        "type": "人工标准答案草案，待逐条审核",
        "case_id": case["id"], "variant": spec["code"],
        "expected": spec["expected"], "reason": spec["note"]})
    return {"id": spec["code"], "path": f"variants/{spec['code']}",
            "subject": spec["subject"], "note": spec["note"],
            "expected": spec["expected"]}


def build_case(case: dict, pdf_cache: dict[str, bytes]) -> dict:
    folder = CASES / case["id"]
    folder.mkdir(parents=True, exist_ok=False)
    variants = [build_variant(case, spec, pdf_cache) for spec in case["variants"]]
    manifest = {key: case[key] for key in ("id", "title", "priority", "stages",
                                            "source_kind", "review_status", "review_focus")}
    manifest["variants"] = [{key: item[key] for key in ("id", "path", "subject", "note")}
                            for item in variants]
    write_json(folder / "case.json", manifest)
    lines = [f"# {case['id']} {case['title']}", "",
             "- 优先级：必选", "- 样本性质：合成；没有真实用户数据",
             "- 审核状态：待审核", f"- 对应任务：{'、'.join(case['stages'])}",
             f"- 本例审核重点：{case['review_focus']}", "",
             "| 变体 | 固定输入 | 受控提取结果 | 标准答案草案 |",
             "| --- | --- | --- | --- |"]
    for item in variants:
        path = item["path"]
        inp = "imap_state.json" if item["subject"] is None else "00_raw.eml"
        lines.append(f"| {item['id']} | [原始输入]({path}/{inp}) | "
                     f"[逐文件结果]({path}/extract.json) | "
                     f"[预期与理由]({path}/expected.json) |")
    lines += ["", "## 逐条审核", "",
              "1. 打开原始邮件或邮箱状态，核对主题、附件数量、MIME 与哈希；ZIP 同时核对内部文件。",
              "2. 查看合成 PDF 页 1 或损坏 PDF 字节，逐字段核对受控提取结果；它不证明真实识别能力。",
              "3. 对照 expected.json 检查每环节结果；运行值须另存 result，不得覆盖标准答案。",
              "4. 在下表填写审核意见。若标准答案需修改，记录依据及版本。", "",
              "| 审核人 | 日期 | 结论（通过/退回/暂缓） | 依据与修改意见 |",
              "| --- | --- | --- | --- |", "| 待填写 | 待填写 | 待审核 | 待填写 |", ""]
    (folder / "review.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


def main() -> None:
    if CASES.exists():
        raise SystemExit("cases/ already exists; refusing to overwrite review material")
    definitions = all_cases()
    wanted = [f"C{i:02d}" for i in range(1, 20) if i != 10]
    actual = [case["id"] for case in definitions]
    if actual != wanted:
        raise ValueError(f"frozen mandatory IDs mismatch: {actual}")
    pdf_cache: dict[str, bytes] = {}
    index = [build_case(case, pdf_cache) for case in definitions]
    write_json(ROOT / "index.json", {
        "scope": "2026-10-08 首批范围，18 条必选；C10/C20/C21 不在本库",
        "source_kind": "全部合成", "review_status": "全部待审核",
        "cases": [{"id": item["id"], "title": item["title"],
                   "path": f"cases/{item['id']}/review.md",
                   "variants": [variant["id"] for variant in item["variants"]]}
                  for item in index]})
    lines = ["# 固定用例库：18 条必选场景", "",
             "本库仅包含冻结范围的 C01–C09、C11–C19；C10、C20、C21 可选，暂不制作。",
             "所有输入均为**合成样本**；全部人工标准答案为**待审核草案**，没有真实邮箱凭据或用户邮件。",
             "原始输入、附件与 SHA-256、受控逐文件结果、标准答案、审核页分开保存；运行结果另存，不覆盖标准答案。",
             "", "| 用例 | 场景 | 变体 | 任务 | 审核 |", "| --- | --- | --- | --- | --- |"]
    for item in index:
        lines.append(f"| [{item['id']}](cases/{item['id']}/review.md) | {item['title']} | "
                     f"{', '.join(v['id'] for v in item['variants'])} | "
                     f"{'、'.join(item['stages'])} | 待审核 |")
    lines += ["", "## 复现边界", "",
              "- `00_raw.eml` 和 `imap_state.json` 是固定输入；`extract.json` 是受控 PDF 接口返回草案。",
              "- 主流程与 T2-3 可以在受控提取条件下验证；真实 PDF 分类与字段识别须单独运行并记录，不得由受控返回值冒充通过。",
              "- PDF 页上印有‘合成测试’，票号、时间、金额和单位均为虚构；原件不用于报销。",
              "- 审核时逐例检查输入、环节输出、异常、副作用和来源。审核通过后才计入覆盖率。",
              "- `build_synthetic_cases.py` 仅用于首次生成；它拒绝覆盖 `cases/`，防止破坏人工审核结果。", ""]
    (ROOT / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"cases={len(index)}, variants={sum(len(item['variants']) for item in index)}")


if __name__ == "__main__":
    main()
