"""PDF-краулер: PyMuPDF + опциональный OCR (tesseract) + двухколоночная вёрстка +
таблицы через pdfplumber + фильтр схем через OCR-ключевые слова."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

try:                                  # PyMuPDF >= 1.24: новое имя пакета
    import pymupdf as fitz
except ImportError:                 # pragma: no cover — старые версии
    import fitz

from ..http import download_file
from ..logging_setup import get_logger
from ..models import CorpusRecord, DownloadedFile
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
            max_download_time=300,  # 5 минут максимум на PDF
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

        # Двухколоночность определяем ПО СТРАНИЦАМ (I10): документ может
        # смешивать одноколоночные титулы/приложения с двухколоночным телом, и
        # «среднее по документу» решение переставляло блоки там, где это
        # не нужно.
        two_col_pages: list[bool] = [False] * page_count
        if cfg.two_column_detection:
            two_col_pages = self._two_column_pages(doc, cfg.two_column_x_threshold)
        pages_two_column = sum(two_col_pages)
        if pages_two_column:
            log.info(f"PDF {url}: two-column layout on {pages_two_column}/{page_count} pages")

        def _page_text(page_num: int) -> str:
            page = doc[page_num]
            if two_col_pages[page_num]:
                return self._extract_two_column_text(page)
            return page.get_text() or ""

        try:
            # 1. базовый текст всех страниц
            page_texts = [_page_text(n) for n in range(page_count)]

            # 2. OCR страниц с «мало текста» — параллельно (tesseract — внешний
            #    процесс, GIL не мешает); раньше страницы шли строго последовательно,
            #    а настройка ocr_parallel_workers никуда не передавалась (I4).
            ocr_pages = [n for n in range(page_count)
                         if cfg.ocr_enabled
                         and len(page_texts[n].strip()) < cfg.ocr_min_chars_per_page]
            ocr_results: dict[int, str] = {}
            if ocr_pages:
                # PyMuPDF Document НЕ тредобезопасен: рендер страниц делаем
                # последовательно в основном потоке, параллелим только вызов
                # tesseract (внешний процесс, GIL не мешает).
                rendered = {n: self._render_page_png(doc, n) for n in ocr_pages}
                jobs = [(n, png) for n, png in rendered.items() if png]
                workers = max(1, int(cfg.ocr_parallel_workers or 1))
                if workers > 1 and len(jobs) > 1:
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as ex:
                        futs = {ex.submit(self._ocr_png_safe, png, cfg.ocr_lang): n
                                for n, png in jobs}
                        for fut, n in futs.items():
                            text = fut.result()
                            if text:
                                ocr_results[n] = text
                else:
                    for n, png in jobs:
                        text = self._ocr_png_safe(png, cfg.ocr_lang)
                        if text:
                            ocr_results[n] = text

            full_text: list[str] = []
            ocr_applied = False
            for n in range(page_count):
                text = page_texts[n]
                ocr_text = ocr_results.get(n)
                if ocr_text and len(ocr_text) > len(text):
                    text = ocr_text
                    ocr_applied = True
                full_text.append(text)

            # 3. таблицы — ОДИН проход pdfplumber по всему документу
            #    (I11: раньше pdfplumber.open() вызывался на каждой странице,
            #    т.е. файл парсился page_count раз → O(pages²)).
            if cfg.extract_tables:
                for page_num, tables in enumerate(self._extract_tables_all(pdf_path)):
                    if not tables:
                        continue
                    has_tables = True
                    for i, table in enumerate(tables):
                        all_tables.append({
                            "page": page_num,
                            "table_index": i,
                            "rows": table,
                            "n_rows": len(table),
                            "n_cols": len(table[0]) if table else 0,
                        })

            # 4. изображения страниц
            for page_num in range(page_count):
                page = doc[page_num]
                try:
                    images = page.get_images(full=True)
                except Exception:
                    images = []
                if images:
                    has_images = True
                for img_index, img in enumerate(images):
                    self._extract_page_image(doc, img, page_num, img_index, cfg, schematics)
        finally:
            # close() обязан быть в finally: раньше он стоял после цикла и при
            # исключении файл оставался открытым (I11).
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
                "is_two_column": bool(pages_two_column),
                "two_column_pages": pages_two_column,
                "toc": toc if toc else None,
                "title": (toc[0][1] if toc else Path(pdf_path).stem),
            },
            license=None,
        )

    # ============================================================
    # Расширенные методы для PDF (Этап 3)
    # ============================================================

    @classmethod
    def _detect_two_column(cls, doc, x_threshold: float = 0.35) -> bool:
        """Сводный признак «в документе есть двухколоночные страницы»."""
        return any(cls._two_column_pages(doc, x_threshold))

    @classmethod
    def _two_column_pages(cls, doc, x_threshold: float = 0.35,
                          sample_pages: int = 10) -> list[bool]:
        """Признак двухколоночной вёрстки для КАЖДОЙ страницы.

        ПРЕЖНИЙ алгоритм объявлял двухколоночным ЛЮБОЙ pdf, где ≥30% блоков
        начинаются левее 35% ширины страницы; у обычной одноколоночной вёрстки
        так начинаются ~100% блоков, поэтому детектор срабатывал почти всегда,
        а `_extract_two_column_text` затем переставлял текст (колонтитул
        уезжал в конец страницы) — I10.

        Новый критерий (по каждой странице отдельно) — два признака сразу:
          1) блоки начинаются и у левого поля, и примерно от середины
             страницы (кластер x0 в окне x_threshold+0.05 … +0.35);
          2) блоки узкие: медианная ширина < 55% ширины страницы, т.е. текст
             не растянут на всю ширину.
        Страницы вне `sample_pages` наследуют классификацию последней
        проверенной — типовой datasheet однороден, а читать блоки всех
        страниц документа слишком дорого.
        """
        flags: list[bool] = []
        last = False
        for page_num in range(len(doc)):
            if page_num < sample_pages:
                last = cls._page_is_two_column(doc[page_num], x_threshold)
            flags.append(last)
        return flags

    @staticmethod
    def _page_is_two_column(page, x_threshold: float = 0.35) -> bool:
        """Двухколоночная ли КОНКРЕТНАЯ страница (см. `_two_column_pages`)."""
        try:
            blocks = page.get_text("blocks") or []
        except Exception:
            return False
        page_width = page.rect.width
        if not blocks or not page_width:
            return False

        starts: list[float] = []
        widths: list[float] = []
        for b in blocks:
            if len(b) < 5 or not (b[4] or "").strip():
                continue
            starts.append(b[0] / page_width)
            widths.append((b[2] - b[0]) / page_width)
        if len(starts) < 5:
            return False

        right_lo, right_hi = x_threshold + 0.05, x_threshold + 0.35
        rightish = sum(1 for x in starts if right_lo <= x <= right_hi) / len(starts)
        leftish = sum(1 for x in starts if x < 0.30) / len(starts)
        median_width = sorted(widths)[len(widths) // 2]
        return rightish >= 0.20 and leftish >= 0.30 and median_width < 0.55

    # ============================================================
    # Вспомогательные стадии разбора PDF
    # ============================================================

    def _extract_page_image(self, doc, img: tuple, page_num: int, img_index: int,
                            cfg, schematics: list[DownloadedFile]) -> None:
        """Одна картинка со страницы: фильтр по размеру/схеме и сохранение."""
        xref = img[0]
        try:
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            ext = base_image.get("ext", "png")

            from PIL import Image
            im = Image.open(io.BytesIO(image_bytes))
            width, height = im.size
            if width < cfg.image_min_width or height < cfg.image_min_height:
                return

            # Фильтр схем: OCR на наличие ключевых слов
            if cfg.filter_schematic_images and not self._is_image_schematic(
                    im, cfg.schematic_keywords):
                return            # логотип/декорация

            prefix = hashlib.sha1(image_bytes).hexdigest()[:12]
            img_filename = f"pdf_{prefix}_p{page_num}_i{img_index}.{ext}"
            img_path = os.path.join(self.config.output.download_dir, img_filename)
            if not os.path.exists(img_path):
                with open(img_path, "wb") as f:
                    f.write(image_bytes)
            schematics.append(DownloadedFile(
                type="image",
                original_url=None,
                local_path=img_path,
                sha1=prefix,
                size_bytes=os.path.getsize(img_path),
            ))
        except Exception as e:
            log.debug(f"Image extract failed on page {page_num} img {img_index}: {e}")

    @staticmethod
    def _render_page_png(doc, page_num: int, dpi: int = 200) -> bytes | None:
        """Рендер страницы в PNG (только в основном потоке)."""
        try:
            return doc[page_num].get_pixmap(dpi=dpi).tobytes("png")
        except Exception as e:                     # noqa: BLE001
            log.debug(f"page render failed on {page_num}: {e}")
            return None

    @staticmethod
    def _ocr_png_safe(png: bytes, lang: str) -> str | None:
        """OCR по уже отрендеренной странице; вместо исключения — None (для pool'а)."""
        try:
            import io
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(io.BytesIO(png)), lang=lang)
        except Exception as e:                     # noqa: BLE001
            log.debug(f"OCR failed: {e}")
            return None

    @staticmethod
    def _extract_tables_all(pdf_path: str) -> list[list[list[list[str]]]]:
        """Таблицы всех страниц за ОДИН проход pdfplumber (I11).

        Возвращает список по числу страниц; элемент — список таблиц страницы.
        """
        try:
            import pdfplumber
        except ImportError:
            log.debug("pdfplumber not installed, skipping table extraction")
            return []
        per_page: list[list[list[list[str]]]] = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    try:
                        per_page.append([t for t in (page.extract_tables() or []) if t])
                    except Exception as e:
                        log.debug(f"pdfplumber page error: {e}")
                        per_page.append([])
        except Exception as e:
            log.debug(f"pdfplumber error on {pdf_path}: {e}")
        return per_page

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
            x0, y0, text = b[0], b[1], b[4]
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

    _tesseract_checked = False
    _tesseract_available = False

    @classmethod
    def _tesseract_ok(cls) -> bool:
        """Есть ли бинарь tesseract (кэшируем проверку — она дорогая)."""
        if not cls._tesseract_checked:
            cls._tesseract_checked = True
            try:
                import pytesseract
                pytesseract.get_tesseract_version()
                cls._tesseract_available = True
            except Exception as e:                # noqa: BLE001
                log.info(f"tesseract недоступен ({type(e).__name__}); "
                         f"OCR-фильтры изображений будут пропущены")
                cls._tesseract_available = False
        return cls._tesseract_available

    @classmethod
    def _is_image_schematic(cls, image, keywords: list[str]) -> bool:
        """Сохранять ли изображение как «схему» (OCR по ключевым словам).

        Логика (I12): раньше при ANY сбое OCR возвращалось True — т.е. без
        установленного tesseract каждая картинка ≥ image_min_* попадала в
        downloaded_files, и «фильтр схем» превращался в «сохранить всё».
        Теперь: если OCR недоступен, фильтр НЕ применять (не сохранять ничего
        по умолчанию), а не сохранять всё.
        """
        if not cls._tesseract_ok():
            log.debug("schematic filter skipped: tesseract unavailable")
            return False
        try:
            import pytesseract
            text = pytesseract.image_to_string(image, lang="eng").lower()
        except Exception as e:                     # noqa: BLE001
            log.debug(f"OCR on image failed, image skipped: {e}")
            return False

        # Документированное поведение: пустой OCR = вероятнее всего логотип или
        # чистая картинка без подписей → НЕ сохраняем.
        if not text.strip():
            return False

        if any(kw.lower() in text for kw in keywords):
            return True

        # Длинный текст без ключевых слов — таблица/график с подписями.
        # Сохраняем только если это похоже на техническую подпись, иначе —
        # декорация/баннер с кучей текста.
        return len(text.strip()) >= 30 and any(
            w in text for w in ("fig", "table", "pin", "voltage", "current",
                                "circuit", "supply", "output", "input", "max",
                                "рис", "табл", "вывод", "напряжени", "ток"))

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

    @classmethod
    def _ocr_page(cls, page, lang: str) -> str:
        """OCR страницы (синхронный convenience-вариант для одного вызова)."""
        png = cls._render_page_png(page.parent, page.number)
        if png is None:
            return ""
        text = cls._ocr_png_safe(png, lang)
        if text is None:
            raise RuntimeError("tesseract OCR failed")
        return text
