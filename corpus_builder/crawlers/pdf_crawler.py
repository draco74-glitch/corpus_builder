"""PDF-краулер: PyMuPDF + опциональный OCR (tesseract) + двухколоночная вёрстка +
таблицы через pdfplumber + фильтр схем через OCR-ключевые слова."""
from __future__ import annotations

import io
import os
import hashlib
from pathlib import Path

import fitz  # PyMuPDF

from ..http import download_file
from ..logging_setup import get_logger
from ..models import AppConfig, CorpusRecord, DownloadedFile
from .base import BaseCrawler

log = get_logger(__name__)


class PdfCrawler(BaseCrawler):
    source_type = "pdf"

    def _crawl(self, url: str) -> CorpusRecord | None:
        cfg = self.config.crawlers.pdf
        result = download_file(
            url,
            self.config.output.download_dir,
            self.config.output.max_file_size_mb,
            self.config.output.request_timeout,
            session=self.session,
        )
        if not result:
            return None
        pdf_path, pdf_sha, pdf_size = result

        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            log.warning(f"Failed to open PDF {url}: {e}")
            return None

        full_text: list[str] = []
        schematics: list[DownloadedFile] = []
        all_tables: list[dict] = []
        ocr_applied = False
        page_count = len(doc)
        has_images = False
        has_tables = False

        try:
            toc = doc.get_toc() or []
        except Exception:
            toc = []

        # Определяем, является ли PDF двухколоночным (на первой странице с текстом)
        is_two_column = False
        if cfg.two_column_detection:
            is_two_column = self._detect_two_column(doc, cfg.two_column_x_threshold)
            if is_two_column:
                log.info(f"PDF {url}: detected two-column layout")

        for page_num in range(page_count):
            page = doc[page_num]

            # Извлечение текста: разный подход для двухколоночных PDF
            if is_two_column:
                page_text = self._extract_two_column_text(page)
            else:
                page_text = page.get_text() or ""

            full_text.append(page_text)

            # Если текста мало — пробуем OCR
            if cfg.ocr_enabled and len(page_text.strip()) < cfg.ocr_min_chars_per_page:
                try:
                    ocr_text = self._ocr_page(page, cfg.ocr_lang)
                    if ocr_text and len(ocr_text) > len(page_text):
                        full_text[-1] = ocr_text
                        ocr_applied = True
                except Exception as e:
                    log.debug(f"OCR failed on page {page_num} of {url}: {e}")

            # Извлечение таблиц через pdfplumber (опционально)
            if cfg.extract_tables:
                try:
                    tables = self._extract_tables_pdfplumber(pdf_path, page_num)
                    if tables:
                        has_tables = True
                        for i, table in enumerate(tables):
                            all_tables.append({
                                "page": page_num,
                                "table_index": i,
                                "rows": table,
                                "n_rows": len(table),
                                "n_cols": len(table[0]) if table else 0,
                            })
                except Exception as e:
                    log.debug(f"Table extraction failed on page {page_num}: {e}")

            # Извлечение изображений
            try:
                images = page.get_images(full=True)
            except Exception:
                images = []
            if images:
                has_images = True
            for img_index, img in enumerate(images):
                xref = img[0]
                try:
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    ext = base_image.get("ext", "png")
                    # Фильтр по размеру
                    from PIL import Image
                    im = Image.open(io.BytesIO(image_bytes))
                    width, height = im.size
                    if width < cfg.image_min_width or height < cfg.image_min_height:
                        continue

                    # Фильтр схем: проверяем OCR на ключевые слова
                    is_schematic = True
                    if cfg.filter_schematic_images:
                        is_schematic = self._is_image_schematic(im, cfg.schematic_keywords)
                    if not is_schematic:
                        continue  # пропускаем логотипы и декорации

                    # Уникальное имя: pdf_<sha1prefix>_p<page>_i<idx>.<ext>
                    prefix = hashlib.sha1(image_bytes).hexdigest()[:12]
                    img_filename = f"pdf_{prefix}_p{page_num}_i{img_index}.{ext}"
                    img_path = os.path.join(self.config.output.download_dir, img_filename)
                    if not os.path.exists(img_path):
                        with open(img_path, "wb") as f:
                            f.write(image_bytes)
                    size = os.path.getsize(img_path)
                    schematics.append(DownloadedFile(
                        type="image",
                        original_url=None,
                        local_path=img_path,
                        sha1=prefix,
                        size_bytes=size,
                    ))
                except Exception as e:
                    log.debug(f"Image extract failed on {url} page {page_num} img {img_index}: {e}")

        doc.close()

        # Структурируем контент по TOC, если есть
        content = "\n".join(full_text).strip()
        if cfg.use_toc_as_structure and toc:
            content = self._structure_by_toc(content, toc)

        # Если есть таблицы — добавляем их в конец контента как структурированный блок
        if all_tables:
            tables_block = self._format_tables_block(all_tables)
            content = content + "\n\n" + tables_block

        return CorpusRecord(
            source_url=url,
            source_type=self.source_type,
            content=content,
            downloaded_files=schematics,
            metadata={
                "pdf_path": pdf_path,
                "pdf_sha1": pdf_sha,
                "page_count": page_count,
                "has_images": has_images,
                "has_tables": has_tables,
                "tables_count": len(all_tables),
                "ocr_applied": ocr_applied,
                "is_two_column": is_two_column,
                "toc": toc if toc else None,
                "title": (toc[0][1] if toc else Path(pdf_path).stem),
            },
            license=None,
        )

    # ============================================================
    # Расширенные методы для PDF (Этап 3)
    # ============================================================

    def _detect_two_column(self, doc, x_threshold: float = 0.35) -> bool:
        """Определить, является ли PDF двухколоночным.

        Алгоритм: на первых 5 страницах с текстом собираем координаты x0 всех
        текстовых блоков. Если > 30% блоков имеют x0 < page_width * 0.35 —
        это двухколоночная вёрстка (по эмпирическим тестам).
        """
        checked_pages = 0
        left_blocks = 0
        total_blocks = 0

        for page_num in range(min(len(doc), 10)):
            page = doc[page_num]
            try:
                blocks = page.get_text("blocks") or []
            except Exception:
                continue
            if not blocks:
                continue
            page_width = page.rect.width
            if page_width == 0:
                continue
            threshold_x = page_width * x_threshold
            for b in blocks:
                if len(b) < 5:
                    continue
                x0 = b[0]
                if x0 < threshold_x:
                    left_blocks += 1
                total_blocks += 1
            checked_pages += 1
            if checked_pages >= 5:
                break

        if total_blocks < 10:
            return False
        return (left_blocks / total_blocks) > 0.30

    def _extract_two_column_text(self, page) -> str:
        """Извлечь текст из двухколоночной страницы, не перемешивая колонки.

        Алгоритм:
          1. Получаем блоки через page.get_text("blocks")
          2. Разделяем по x0 < page_width / 2 → left/right
          3. Сортируем каждый по (y0, x0)
          4. Конкатенируем сначала все левые, потом все правые
        """
        try:
            blocks = page.get_text("blocks") or []
        except Exception:
            return page.get_text() or ""

        page_width = page.rect.width
        if page_width == 0:
            return page.get_text() or ""

        mid_x = page_width / 2
        left_blocks = []
        right_blocks = []
        for b in blocks:
            if len(b) < 5:
                continue
            x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            if not text or not text.strip():
                continue
            # Если блок начинается в левой половине
            if x0 < mid_x:
                left_blocks.append((y0, x0, text))
            else:
                right_blocks.append((y0, x0, text))

        # Сортируем каждый по y0 (сверху вниз), потом по x0 (слева направо)
        left_blocks.sort(key=lambda b: (b[0], b[1]))
        right_blocks.sort(key=lambda b: (b[0], b[1]))

        parts = [b[2] for b in left_blocks]
        parts.extend(b[2] for b in right_blocks)
        return "\n".join(parts)

    def _extract_tables_pdfplumber(self, pdf_path: str, page_num: int) -> list[list[list[str]]]:
        """Извлечь таблицы со страницы через pdfplumber.

        Возвращает список таблиц, каждая таблица — список строк, каждая строка — список ячеек.
        """
        try:
            import pdfplumber
        except ImportError:
            log.debug("pdfplumber not installed, skipping table extraction")
            return []
        tables_data = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if page_num < len(pdf.pages):
                    page = pdf.pages[page_num]
                    tables = page.extract_tables() or []
                    for table in tables:
                        if table:
                            tables_data.append(table)
        except Exception as e:
            log.debug(f"pdfplumber error on page {page_num}: {e}")
        return tables_data

    def _is_image_schematic(self, image, keywords: list[str]) -> bool:
        """Проверить, является ли изображение схемой/диаграммой через OCR.

        Если OCR находит хотя бы одно ключевое слово (figure, circuit, diagram и т.д.)
        или не находит текста вообще (тогда это, скорее всего, схема, а не логотип
        с названием бренда) — считаем схемой.

        Реализация упрощённая: используем tesseract для распознавания, затем
        ищем ключевые слова. Если текста нет — пропускаем как возможный логотип.
        """
        try:
            import pytesseract
            text = pytesseract.image_to_string(image, lang="eng").lower()
            # Если OCR ничего не дал — это, скорее всего, чистая схема (без подписей)
            # Сохраняем как схему
            if not text.strip():
                return True
            for kw in keywords:
                if kw.lower() in text:
                    return True
            # Если есть текст, но без ключевых слов — возможно, это логотип/баннер
            # Пропускаем, если текст короткий (логотипы обычно содержат < 30 символов)
            if len(text.strip()) < 30:
                return False
            # Длинный текст без ключевых слов — это, скорее всего, таблица или график
            # Сохраняем на всякий случай
            return True
        except Exception:
            # OCR не сработал — сохраняем как схему (страховка)
            return True

    def _structure_by_toc(self, content: str, toc: list) -> str:
        """Использовать TOC для разметки разделов.

        Если в content встречаются заголовки из TOC, вставляем перед ними маркер
        '## LEVEL TITLE' для последующего удобного парсинга.
        """
        if not toc:
            return content
        result = content
        for entry in toc:
            # entry = [level, title, page_num]
            if len(entry) < 2:
                continue
            level, title = entry[0], entry[1]
            # Ищем заголовок в контенте (точное совпадение)
            if title and title in result:
                marker = f"\n\n{'#' * min(level + 1, 6)} {title}\n"
                result = result.replace(title, marker, 1)
        return result

    def _format_tables_block(self, tables: list[dict]) -> str:
        """Форматировать таблицы как Markdown для контента."""
        parts = ["=== EXTRACTED TABLES ==="]
        for t in tables:
            parts.append(f"\n--- Table on page {t['page']}, index {t['table_index']} "
                         f"({t['n_rows']} rows × {t['n_cols']} cols) ---")
            for row in t.get("rows") or []:
                # Каждую ячейку оборачиваем в |, пустые — как пустые
                cells = [(c or "").strip().replace("\n", " ") for c in row]
                parts.append("| " + " | ".join(cells) + " |")
        return "\n".join(parts)

    @staticmethod
    def _ocr_page(page, lang: str) -> str:
        """Прогнать страницу через tesseract."""
        import pytesseract
        # Рендерим страницу в изображение с разумным DPI
        pix = page.get_pixmap(dpi=200)
        from PIL import Image
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(img, lang=lang)
