"""T3-4: bounded OFD extraction and a checked PDF delivery copy.

Unsupported drawing features or missing resources leave fields intact but do
not yield a delivery PDF. This renderer does not verify a signature.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
import zipfile

from attachment_processor import CollectedFile
from config import RunConfig
from data_model import NOT_FOUND
from processor_contracts import DocumentResult

MM = 72 / 25.4
DATE = re.compile(r"(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?")
NUMBER = re.compile(r"\d{20}")
AMOUNT = re.compile(r"[¥￥]?\s*(\d+(?:\.\d{1,4})?)")


def tag(node):
    return node.tag.rsplit("}", 1)[-1]


def one(root, name):
    return next((node for node in root.iter() if tag(node) == name), None)


def many(root, name):
    return [node for node in root.iter() if tag(node) == name]


def path_name(name):
    if not name or name.startswith(("/", "\\")):
        raise ValueError("OFD_UNSAFE_PATH")
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if any(part in (".", "..") for part in parts):
        raise ValueError("OFD_UNSAFE_PATH")
    return "/".join(parts)


def relative(base, name):
    if name.startswith("/"):
        return path_name(name[1:])
    return path_name(str(PurePosixPath(base).parent / name))


class Archive:
    def __init__(self, path):
        if Path(path).stat().st_size > 15 * 1024 * 1024:
            raise ValueError("OFD_TOO_LARGE")
        self.zf = zipfile.ZipFile(path)
        entries = [entry for entry in self.zf.infolist() if not entry.is_dir()]
        if len(entries) > 200 or sum(e.file_size for e in entries) > 40 * 1024 * 1024:
            raise ValueError("OFD_RESOURCE_LIMIT")
        self.names = {}
        for entry in entries:
            name = path_name(entry.filename)
            if entry.flag_bits & 1:
                raise ValueError("OFD_ENCRYPTED")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("OFD_SYMLINK")
            if entry.compress_size and entry.file_size / entry.compress_size > 1000:
                raise ValueError("OFD_COMPRESSION_RATIO")
            if name.casefold() in self.names:
                raise ValueError("OFD_DUPLICATE_PATH")
            self.names[name.casefold()] = name

    def read(self, name):
        real = self.names.get(path_name(name).casefold())
        if real is None:
            raise ValueError(f"OFD_MISSING_RESOURCE:{name}")
        return self.zf.read(real)

    def xml(self, name):
        raw = self.read(name)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("OFD_XML_TOO_LARGE")
        if re.search(br"<!\s*(DOCTYPE|ENTITY)", raw, re.I):
            raise ValueError("OFD_FORBIDDEN_XML_DECLARATION")
        root = ET.fromstring(raw)
        if sum(1 for _ in root.iter()) > 30000:
            raise ValueError("OFD_XML_NODE_LIMIT")
        return root


def text(node):
    return "".join(child.text or "" for child in node.iter() if tag(child) == "TextCode")


def norm_date(raw):
    found = DATE.search(raw.strip())
    if not found:
        return None
    try:
        return date(*(int(value) for value in found.groups())).isoformat()
    except ValueError:
        return None


def norm_amount(raw):
    found = AMOUNT.fullmatch(raw.strip())
    if not found:
        return None
    try:
        value = Decimal(found.group(1))
        return format(value, "f") if value.is_finite() and value > 0 else None
    except InvalidOperation:
        return None


def load_pages(archive):
    top = archive.xml("OFD.xml")
    if tag(top) != "OFD" or top.attrib.get("Version") not in ("1.0", "1.1", "1.2"):
        raise ValueError("OFD_UNSUPPORTED_VERSION")
    root_ref = one(top, "DocRoot")
    if root_ref is None or not (root_ref.text or "").strip():
        raise ValueError("OFD_MISSING_DOCUMENT")
    doc_name = path_name((root_ref.text or "").strip().lstrip("/"))
    document = archive.xml(doc_name)
    pages_node = one(document, "Pages")
    refs = [node for node in pages_node if tag(node) == "Page"] if pages_node is not None else []
    if not refs or len(refs) > 30:
        raise ValueError("OFD_PAGE_LIMIT_OR_MISSING")
    templates = {node.attrib["ID"]: relative(doc_name, node.attrib["BaseLoc"])
                 for node in many(document, "TemplatePage") if "BaseLoc" in node.attrib}
    pages = []
    for ref in refs:
        name = relative(doc_name, ref.attrib.get("BaseLoc", ""))
        root = archive.xml(name)
        physical = one(root, "PhysicalBox")
        dims = [float(value) for value in (physical.text or "").split()] if physical is not None else []
        if len(dims) != 4 or dims[2] <= 0 or dims[3] <= 0:
            raise ValueError("OFD_INVALID_PAGE_BOX")
        template_ref = one(root, "Template")
        template_name = templates.get(template_ref.attrib.get("TemplateID")) if template_ref is not None else None
        pages.append((name, root, archive.xml(template_name) if template_name else None,
                      dims[2], dims[3]))
    return top, document, doc_name, pages


def extract(archive, top, document, doc_name, pages, seq):
    objects = {node.attrib["ID"]: (index, text(node))
               for index, (_, root, _, _, _) in enumerate(pages, 1)
               for node in root.iter() if tag(node) == "TextObject" and node.attrib.get("ID")}
    fields, evidence, conflicts = {}, {}, []
    def add(field, raw, value, location):
        if value is None:
            return
        evidence.setdefault(field, []).append({"file_seq": seq, "raw": raw,
            "normalized": value, "location": location})
        if field in fields and fields[field] != value:
            conflicts.append({"field": field, "reason": "ofd_internal_conflict",
                              "candidates": [fields[field], value], "location": location})
        else:
            fields[field] = value
    mapped = {"InvoiceNo": "invoice_number", "IssueDate": "invoice_date",
              "SellerName": "seller", "TaxInclusiveTotalAmount": "total_amount",
              "Item": "receipt_type", "InvoiceType": "invoice_type"}
    cleaners = {"invoice_number": lambda value: value.strip() if NUMBER.fullmatch(value.strip()) else None,
                "invoice_date": norm_date, "seller": lambda value: value.strip() or None,
                "total_amount": norm_amount,
                "receipt_type": lambda value: value.rsplit("*", 1)[-1].strip() or None,
                "invoice_type": lambda value: value.strip() or None}
    for custom in many(document, "CustomTags"):
        if not (custom.text or "").strip():
            continue
        index_name = relative(doc_name, custom.text.strip())
        for location in many(archive.xml(index_name), "FileLoc"):
            tag_name = relative(index_name, (location.text or "").strip())
            for node in archive.xml(tag_name).iter():
                field = mapped.get(tag(node))
                if field:
                    for ref in many(node, "ObjectRef"):
                        ref_id = (ref.text or "").strip()
                        if ref_id in objects:
                            page, raw = objects[ref_id]
                            add(field, raw, cleaners[field](raw),
                                f"{tag_name}#ObjectRef={ref_id};page={page}")
    page_values = [(index, text(node)) for index, (_, root, _, _, _) in enumerate(pages, 1)
                   for node in root.iter() if tag(node) == "TextObject"]
    if "invoice_number" not in fields:
        found = [(i, raw) for i, raw in page_values if NUMBER.fullmatch(raw.strip())]
        if len(found) == 1:
            add("invoice_number", found[0][1], found[0][1].strip(), f"page={found[0][0]};TextObject")
    if "invoice_date" not in fields:
        found = [(i, raw, norm_date(raw)) for i, raw in page_values if norm_date(raw)]
        if found and len({value for _, _, value in found}) == 1:
            add("invoice_date", found[0][1], found[0][2], f"page={found[0][0]};TextObject")
    for node in many(top, "CustomData"):
        name, raw = node.attrib.get("Name", ""), (node.text or "").strip()
        field = "invoice_number" if "发票号码" in name else "invoice_date" if "开票日期" in name else None
        if field:
            add(field, raw, cleaners[field](raw), f"OFD.xml#CustomData[{name}]")
    if "invoice_type" not in fields:
        titles = [(i, raw) for i, raw in page_values if "电子" in raw and "发票" in raw]
        if len(titles) == 1:
            add("invoice_type", titles[0][1], titles[0][1].strip(),
                f"page={titles[0][0]};TextObject")
    if "receipt_type" not in fields:
        items = [(i, raw) for i, raw in page_values
                 if raw.startswith("*") and raw.count("*") >= 2]
        if len(items) == 1:
            add("receipt_type", items[0][1], cleaners["receipt_type"](items[0][1]),
                f"page={items[0][0]};TextObject")
    if "total_amount" not in fields:
        # The Chinese lower-case grand-total label and amount must share a row.
        # A bare amount elsewhere may be untaxed total, tax or unit price.
        labelled = []
        for page_index, (_, root, _, _, _) in enumerate(pages, 1):
            objects = [node for node in root.iter() if tag(node) == "TextObject"]
            labels = [node for node in objects if "小写" in text(node)]
            for label in labels:
                try:
                    lx, ly, _, _ = [float(v) for v in label.attrib["Boundary"].split()]
                except (KeyError, ValueError):
                    continue
                for candidate in objects:
                    raw = text(candidate)
                    normalized = norm_amount(raw)
                    if normalized is None:
                        continue
                    try:
                        cx, cy, _, _ = [float(v) for v in candidate.attrib["Boundary"].split()]
                    except (KeyError, ValueError):
                        continue
                    if cx > lx and abs(cy - ly) <= 3:
                        labelled.append((page_index, candidate, raw, normalized))
        if len(labelled) == 1:
            page_index, node, raw, normalized = labelled[0]
            add("total_amount", raw, normalized,
                f"page={page_index};TextObject={node.attrib.get('ID')};label=小写")
    fields.setdefault("total_amount", NOT_FOUND)
    combined = "\n".join(value for _, value in page_values)
    role = "Invoice" if fields.get("invoice_number") or "发票" in combined else (
        "Itinerary" if "行程单" in combined else "Unknown")
    if role == "Itinerary":
        fields = {"match_amount": fields.get("total_amount", NOT_FOUND)}
    return DocumentResult(file_seq=seq, role=role, fields=fields,
                          source_file_seq=[seq], field_evidence=evidence,
                          conflicts=conflicts,
                          error_reason="OFD_INTERNAL_CONFLICT" if conflicts else "")


def resources(archive, document, doc_name):
    output = {}
    for node in document.iter():
        if tag(node) not in ("DocumentRes", "PublicRes") or not (node.text or "").strip():
            continue
        path = relative(doc_name, node.text.strip())
        root = archive.xml(path)
        for medium in many(root, "MultiMedia"):
            file_node = one(medium, "MediaFile")
            if file_node is not None and (file_node.text or "").strip():
                output[medium.attrib.get("ID")] = relative(path, root.attrib.get("BaseLoc", "Res") + "/" + file_node.text.strip())
    return output


def pdf_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path in (r"C:\Windows\Fonts\simfang.ttf", r"C:\Windows\Fonts\simhei.ttf",
                 r"C:\Windows\Fonts\simkai.ttf",
                 "/usr/share/fonts/truetype/arphic/ukai.ttc"):
        if Path(path).is_file():
            try:
                pdfmetrics.registerFont(TTFont("OFD-CJK", path))
                return "OFD-CJK"
            except Exception:
                pass
    raise ValueError("OFD_CONVERSION_FONT_UNAVAILABLE")


def draw(canvas, root, width, height, archive, image_map, font, warnings):
    from reportlab.lib.utils import ImageReader
    for node in root.iter():
        kind = tag(node)
        if kind not in ("TextObject", "ImageObject", "PathObject"):
            continue
        box = [float(value) for value in node.attrib.get("Boundary", "").split()]
        if len(box) != 4:
            raise ValueError("OFD_CONVERSION_INVALID_BOUNDARY")
        x, y, w, h = box
        # Some issuers use a loose object boundary wider than its glyphs.
        # Permit a small boundary overhang; the extracted PDF text is checked.
        if min(box) < 0 or x > width or y > height or (
                kind == "ImageObject" and (x + w > width + 1 or y + h > height + 1)):
            raise ValueError(f"OFD_CONVERSION_OUT_OF_PAGE:{kind}:{node.attrib.get('ID')}:{box}")
        if kind == "TextObject":
            value = text(node)
            if not value:
                continue
            code = one(node, "TextCode")
            dx = float(code.attrib.get("X", 0)) if code is not None else 0
            dy = float(code.attrib.get("Y", h * .7)) if code is not None else h * .7
            font_size = min(float(node.attrib.get("Size", 3)) * MM, h * MM)
            canvas.setFont(font, font_size)
            fill = one(node, "FillColor")
            rgb = (fill.attrib.get("Value", "").split() if fill is not None else [])
            if len(rgb) >= 3:
                canvas.setFillColorRGB(*(float(value) / 255 for value in rgb[:3]))
            else:
                canvas.setFillColorRGB(0, 0, 0)
            from reportlab.pdfbase import pdfmetrics
            deltas = []
            delta_tokens = (code.attrib.get("DeltaX", "").split() if code is not None else [])
            delta_index = 0
            while delta_index < len(delta_tokens):
                try:
                    token = delta_tokens[delta_index]
                    if token == "g":
                        count = int(delta_tokens[delta_index + 1])
                        repeated_delta = float(delta_tokens[delta_index + 2])
                        if count < 1 or count > 1000:
                            raise ValueError("invalid repeat")
                        deltas.extend([repeated_delta] * count)
                        delta_index += 3
                    else:
                        deltas.append(float(token))
                        delta_index += 1
                except ValueError:
                    raise ValueError("OFD_CONVERSION_UNSUPPORTED_TEXT_DELTA")
                except IndexError:
                    raise ValueError("OFD_CONVERSION_UNSUPPORTED_TEXT_DELTA")
            ctm_raw = node.attrib.get("CTM", "1 0 0 1 0 0").split()
            if len(ctm_raw) != 6:
                raise ValueError("OFD_CONVERSION_INVALID_CTM")
            a, b, c, d, e, f = (float(part) for part in ctm_raw)
            offset = dx
            for index, character in enumerate(value):
                displayed = "￥" if character == "¥" else character
                if ord(displayed) not in pdfmetrics.getFont(font).face.charToGlyph:
                    raise ValueError(f"OFD_CONVERSION_GLYPH_MISSING:U+{ord(character):04X}")
                glyph_width = pdfmetrics.stringWidth(displayed, font, font_size) / MM
                gx = x + a * offset + c * dy + e
                gy = y + b * offset + d * dy + f
                end_x = gx + a * glyph_width
                end_y = gy + b * glyph_width
                if min(gx, end_x) < -.5 or max(gx, end_x) > width + .5 or min(gy, end_y) < -.5 or max(gy, end_y) > height + .5:
                    raise ValueError(f"OFD_CONVERSION_TEXT_CLIPPED:{node.attrib.get('ID')}")
                canvas.saveState()
                canvas.translate(gx * MM, (height - gy) * MM)
                canvas.transform(a, -b, -c, d, 0, 0)
                advance = deltas[index] if index < len(deltas) else glyph_width
                canvas.scale(min(1.0, advance / glyph_width * .95), 1)
                canvas.drawString(0, 0, displayed)
                canvas.restoreState()
                offset += advance
        elif kind == "ImageObject":
            name = image_map.get(node.attrib.get("ResourceID"))
            if not name:
                raise ValueError("OFD_CONVERSION_UNKNOWN_IMAGE")
            image_bytes = archive.read(name)
            canvas.drawImage(ImageReader(BytesIO(image_bytes)), x * MM,
                             (height - y - h) * MM, w * MM, h * MM, mask="auto")
        else:
            abbreviated = one(node, "AbbreviatedData")
            tokens = (abbreviated.text or "").split() if abbreviated is not None else []
            path = canvas.beginPath()
            i = 0
            commands = []
            values = []
            while i < len(tokens):
                command = tokens[i]
                count = 6 if command == "B" else 2 if command in ("M", "L") else 0
                if not count or i + count >= len(tokens):
                    raise ValueError("OFD_CONVERSION_UNSUPPORTED_PATH")
                coordinates = [float(value) for value in tokens[i + 1:i + count + 1]]
                commands.append((command, coordinates))
                values.extend(coordinates)
                i += count + 1
            sx = min(1.0, w / max(values[::2])) if values and max(values[::2]) > 0 else 1.0
            sy = min(1.0, h / max(values[1::2])) if values and max(values[1::2]) > 0 else 1.0
            def point(dx, dy):
                return (x + dx * sx) * MM, (height - y - dy * sy) * MM
            for command, coordinates in commands:
                if command == "M":
                    path.moveTo(*point(*coordinates))
                elif command == "L":
                    path.lineTo(*point(*coordinates))
                else:
                    path.curveTo(*(coordinate for index in (0, 2, 4)
                                   for coordinate in point(coordinates[index], coordinates[index + 1])))
            canvas.setStrokeColorRGB(0, 0, 0)
            canvas.setLineWidth(max(.2, float(node.attrib.get("LineWidth", .2)) * MM))
            canvas.drawPath(path, stroke=1, fill=0)


def convert(archive, document, doc_name, pages, result, destination):
    import pdfplumber
    from reportlab.pdfgen import canvas
    font = pdf_font()
    image_map = resources(archive, document, doc_name)
    warnings = []
    output = BytesIO()
    pdf = canvas.Canvas(output, pageCompression=1)
    for _, root, template, width, height in pages:
        pdf.setPageSize((width * MM, height * MM))
        if template is not None:
            draw(pdf, template, width, height, archive, image_map, font, warnings)
        draw(pdf, root, width, height, archive, image_map, font, warnings)
        pdf.showPage()
    pdf.save()
    content = output.getvalue()
    if not content.startswith(b"%PDF-"):
        raise ValueError("OFD_CONVERSION_NOT_PDF")
    with pdfplumber.open(BytesIO(content)) as checked:
        if len(checked.pages) != len(pages):
            raise ValueError("OFD_CONVERSION_PAGE_COUNT")
        extracted = "\n".join(page.extract_text() or "" for page in checked.pages)
        compact = re.sub(r"\s+", "", extracted)
        for field in ("invoice_number", "total_amount", "seller", "receipt_type"):
            value = result.fields.get(field)
            if value and value != NOT_FOUND and re.sub(r"\s+", "", str(value)) not in compact:
                raise ValueError(f"OFD_CONVERSION_TEXT_MISSING:{field}")
        if result.role == "Invoice" and "发票" not in compact:
            raise ValueError("OFD_CONVERSION_TITLE_MISSING")
        date_value = result.fields.get("invoice_date")
        if date_value and date_value != NOT_FOUND:
            date_digits = re.sub(r"\D", "", str(date_value))
            if date_digits not in re.sub(r"\D", "", extracted):
                raise ValueError("OFD_CONVERSION_DATE_MISSING")
        for page, (_, root, _, _, _) in zip(checked.pages, pages):
            if len(page.images) < len(many(root, "ImageObject")):
                raise ValueError("OFD_CONVERSION_IMAGE_MISSING")
        if any(not page.chars and not page.images for page in checked.pages):
            raise ValueError("OFD_CONVERSION_BLANK_PAGE")
    destination.write_bytes(content)
    return sorted(set(warnings))


def process_ofd(item: CollectedFile, cfg: RunConfig, subject: str,
                mail_date: str, run_id: str) -> DocumentResult:
    """Return a single OFD candidate and a checked PDF copy when possible."""
    archive = None
    try:
        archive = Archive(item.path)
        top, document, doc_name, pages = load_pages(archive)
        result = extract(archive, top, document, doc_name, pages, item.meta.file_seq)
        if result.role == "Unknown":
            result.error_reason = ";".join(filter(None, (result.error_reason, "OFD_ROLE_UNKNOWN")))
            return result
        destination = Path(cfg.extract_dir) / f"{Path(item.meta.internal_name).stem}_converted.pdf"
        try:
            warnings = convert(archive, document, doc_name, pages, result, destination)
            result.pdf_path = str(destination)
            if warnings:
                result.error_reason = ";".join(filter(None, (result.error_reason, *warnings)))
        except Exception as exc:
            destination.unlink(missing_ok=True)
            result.error_reason = ";".join(filter(None, (result.error_reason,
                                                       f"OFD_CONVERSION_FAILED:{exc}")))
        return result
    except Exception as exc:
        return DocumentResult(file_seq=item.meta.file_seq, role="Unknown",
                              source_file_seq=[item.meta.file_seq],
                              error_reason=f"OFD_EXTRACTION_FAILED:{exc}")
    finally:
        if archive is not None:
            archive.zf.close()
