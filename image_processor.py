"""T3-5 single-image document processor and checked PDF delivery copy."""
from __future__ import annotations

import io
import os
import re
import warnings
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from ai_client import ModelFileInput, extract_single_file
from attachment_processor import CollectedFile
from config import RunConfig
from data_model import NOT_FOUND
from processor_contracts import DocumentResult
from type_classifier import classify_type_from_attachment_name
from utils import clean_receipt_type

_MAX_IMAGE_BYTES = 15_000_000
_MAX_PIXELS = 35_000_000
_MAX_FRAMES = 8
_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF", "BMP", "GIF"}
_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp",
         "TIFF": "image/tiff", "BMP": "image/bmp", "GIF": "image/gif"}


def _load_frames(path: str) -> tuple[str, list[Image.Image], tuple[tuple[bytes, str], ...]]:
    if os.path.getsize(path) > _MAX_IMAGE_BYTES:
        raise ValueError("IMAGE_FILE_LIMIT_EXCEEDED")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        return _read_frames(path)


def _read_frames(path: str) -> tuple[str, list[Image.Image], tuple[tuple[bytes, str], ...]]:
    with Image.open(path) as source:
        actual = str(source.format or "").upper()
        if actual not in _ALLOWED_FORMATS:
            raise ValueError("IMAGE_FORMAT_UNSUPPORTED")
        if getattr(source, "n_frames", 1) > _MAX_FRAMES:
            raise ValueError("IMAGE_FRAME_LIMIT_EXCEEDED")
        frames = []
        requests = []
        for frame in ImageSequence.Iterator(source):
            if frame.width * frame.height > _MAX_PIXELS or min(frame.size) == 0:
                raise ValueError("IMAGE_PIXEL_LIMIT_EXCEEDED")
            frame.load()
            complete = ImageOps.exif_transpose(frame.copy())
            # PDF and the model see exactly the same upright full frame.
            if complete.mode in ("RGBA", "LA") or "transparency" in complete.info:
                rgba = complete.convert("RGBA")
                white = Image.new("RGB", rgba.size, "white")
                white.paste(rgba, mask=rgba.getchannel("A"))
                complete = white
            else:
                complete = complete.convert("RGB")
            frames.append(complete)
            buffer = io.BytesIO()
            complete.save(buffer, format="JPEG", quality=92)
            requests.append((buffer.getvalue(), "image/jpeg"))
        return actual, frames, tuple(requests)


def _checked_pdf_copy(file: CollectedFile, cfg: RunConfig,
                      frames: list[Image.Image]) -> str:
    target_dir = Path(cfg.extract_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(file.meta.internal_name).stem or f"{file.meta.file_seq}_image"
    target = target_dir / f"{stem}_image.pdf"
    if target.exists():
        raise ValueError("IMAGE_PDF_TARGET_COLLISION")
    staged = target.with_suffix(".pdf.tmp")
    try:
        # Pillow preserves the complete upright frame and its aspect ratio on
        # one PDF page per image frame. No crop box or OCR overlay is invented.
        frames[0].save(staged, format="PDF", save_all=True,
                       append_images=frames[1:], resolution=200.0)
        content = staged.read_bytes()
        if not content.startswith(b"%PDF-") or len(content) < 1000:
            raise ValueError("IMAGE_PDF_INVALID")
        import pdfplumber
        with pdfplumber.open(staged) as pdf:
            if len(pdf.pages) != len(frames):
                raise ValueError("IMAGE_PDF_PAGE_COUNT_MISMATCH")
            for page, frame in zip(pdf.pages, frames):
                if page.width <= 0 or page.height <= 0:
                    raise ValueError("IMAGE_PDF_PAGE_INVALID")
                if abs(page.width / page.height - frame.width / frame.height) > 0.02:
                    raise ValueError("IMAGE_PDF_ASPECT_MISMATCH")
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)
    return str(target.resolve())


def _invoice_rules(fields: dict[str, str]) -> list[str]:
    errors = []
    number = fields.get("invoice_number", NOT_FOUND)
    if number != NOT_FOUND and not re.fullmatch(r"[0-9]{20}", number):
        fields["invoice_number"] = NOT_FOUND
        errors.append("IMAGE_INVOICE_NUMBER_INVALID")
    date = fields.get("invoice_date", NOT_FOUND)
    if date != NOT_FOUND:
        try:
            if datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d") != date:
                raise ValueError
        except ValueError:
            fields["invoice_date"] = NOT_FOUND
            errors.append("IMAGE_INVOICE_DATE_INVALID")
    amount = fields.get("total_amount", NOT_FOUND)
    if amount != NOT_FOUND:
        try:
            value = Decimal(amount)
            if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -4:
                raise ValueError
        except (InvalidOperation, ValueError):
            fields["total_amount"] = NOT_FOUND
            errors.append("IMAGE_TOTAL_AMOUNT_INVALID")
    receipt = fields.get("receipt_type", NOT_FOUND)
    if receipt != NOT_FOUND:
        fields["receipt_type"] = clean_receipt_type(receipt)
        if not fields["receipt_type"]:
            fields["receipt_type"] = NOT_FOUND
            errors.append("IMAGE_RECEIPT_TYPE_INVALID")
    return errors


def _itinerary_rules(fields: dict[str, str]) -> list[str]:
    errors = []
    amount = fields.get("match_amount", NOT_FOUND)
    if amount != NOT_FOUND:
        try:
            value = Decimal(amount)
            if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -4:
                raise ValueError
        except (InvalidOperation, ValueError):
            fields["match_amount"] = NOT_FOUND
            errors.append("IMAGE_ITINERARY_AMOUNT_INVALID")
    travel_time = fields.get("travel_time", NOT_FOUND)
    if travel_time != NOT_FOUND:
        try:
            if datetime.strptime(travel_time, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M") != travel_time:
                raise ValueError
        except ValueError:
            fields["travel_time"] = NOT_FOUND
            errors.append("IMAGE_ITINERARY_TIME_INVALID")
    return errors


def process_image(file: CollectedFile, cfg: RunConfig, mail_subject: str,
                  mail_date: str, run_id: str = "") -> DocumentResult:
    """Handle exactly one registered image; model failures keep its file entry.

    ``extract_single_file`` is the only replaceable external-service boundary.
    The prewritten response tests patch that function, while actual image
    decoding, validation, conversion and file bookkeeping still execute.
    """
    seq = file.meta.file_seq
    try:
        actual, frames, image_bytes = _load_frames(file.path)
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        return DocumentResult(file_seq=seq, role="Unknown",
                              source_file_seq=[seq],
                              error_reason="IMAGE_READ_" + str(exc).splitlines()[0][:80])
    name_hint = classify_type_from_attachment_name(file.meta.original_name) or "Unknown"
    file.meta.pre_read_type = name_hint
    request = ModelFileInput(file_seq=seq, internal_name=file.meta.internal_name,
                             original_name=file.meta.original_name,
                             file_type=actual, pre_read_role=name_hint,
                             images=image_bytes,
                             mail_id=(re.match(r"Msg[0-9]+", file.meta.internal_name) or [""])[0])
    candidate = extract_single_file(request, cfg)
    role = candidate.role
    fields = dict(candidate.fields)
    errors = [candidate.error_reason] if candidate.error_reason else []
    if role == "Invoice":
        errors.extend(_invoice_rules(fields))
        file.meta.pre_read_invoice_no = fields.get("invoice_number", NOT_FOUND)
        file.meta.pre_read_amount = fields.get("total_amount", NOT_FOUND)
    elif role == "Itinerary":
        errors.extend(_itinerary_rules(fields))
        file.meta.pre_read_amount = fields.get("match_amount", NOT_FOUND)
    mismatches = []
    if name_hint in ("Invoice", "Itinerary") and role in ("Invoice", "Itinerary") and name_hint != role:
        mismatches.append({"file_seq": seq, "filename": file.meta.original_name,
                           "pre_read_type": name_hint, "ai_role": role})
        errors.append("IMAGE_ROLE_CONFLICT")
    pdf_path = ""
    if role in ("Invoice", "Itinerary"):
        try:
            pdf_path = _checked_pdf_copy(file, cfg, frames)
        except (OSError, ValueError, ImportError) as exc:
            errors.append("IMAGE_PDF_CONVERSION_" + type(exc).__name__)
    return DocumentResult(file_seq=seq, role=role, fields=fields,
                          source_file_seq=[seq], error_reason=";".join(filter(None, errors)),
                          field_evidence=candidate.field_evidence,
                          mismatches=mismatches, pdf_path=pdf_path)
