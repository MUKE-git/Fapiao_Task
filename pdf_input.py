"""Shared bounded PDF input preparation for extraction and arbitration."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class PdfInput:
    text: str
    images: tuple
    image_pages: tuple
    page_count: int


def prepare_pdf(path: str) -> PdfInput:
    if Path(path).stat().st_size > 30_000_000:
        raise ValueError('PDF_FILE_LIMIT_EXCEEDED')
    texts, raw_texts, images, pages = [], [], [], []
    with pdfplumber.open(path) as pdf:
        if not 1 <= len(pdf.pages) <= 8:
            raise ValueError('PDF_PAGE_LIMIT_EXCEEDED')
        for number, page in enumerate(pdf.pages, 1):
            raw = page.extract_text() or ''
            raw_texts.append(raw)
            # A heuristic, not a completeness guarantee. Raster content is
            # always shown even if the page also carries a text layer.
            raster = getattr(page, 'images', [])
            area = max(getattr(page, 'width', 1) * getattr(page, 'height', 1), 1)
            raster_area = sum(abs((im.get('x1', 0) - im.get('x0', 0)) *
                                  (im.get('bottom', 0) - im.get('top', 0))) for im in raster)
            suspicious = (not raw.strip() or '\ufffd' in raw or
                          (bool(raster) and (len(''.join(raw.split())) < 40 or raster_area / area >= .20)))
            texts.append(f'[PDF page {number}]\n{raw}')
            if suspicious:
                if page.width * page.height * (150 / 72) ** 2 > 12_000_000:
                    raise ValueError('PDF_PAGE_PIXEL_LIMIT_EXCEEDED')
                image = page.to_image(resolution=150).original
                stream = BytesIO()
                image.convert('RGB').save(stream, format='PNG')
                images.append((stream.getvalue(), 'image/png'))
                pages.append(number)
                texts.append(f'[image {len(images)} = PDF page {number}]')
        text = '\n'.join(texts if images else raw_texts)
        if len(text) > 12000 or sum(len(data) for data, _ in images) > 15_000_000:
            raise ValueError('PDF_INPUT_LIMIT_EXCEEDED')
        return PdfInput(text, tuple(images), tuple(pages), len(pdf.pages))
