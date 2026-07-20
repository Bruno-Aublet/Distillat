"""Thread de traitement (extraction EPUB/PDF + appel Gemini) pour ne pas bloquer l'UI."""
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from app import config, epub_parser, gemini_client, generation_resume, pdf_parser
from app.book_report import BookReport
from app.epub_parser import BookContent
from app.gemini_client import GeminiError, PartialGenerationError
from app.generation_resume import ResumeState
from app.i18n import tr
from app.quota_tracker import QuotaSnapshot, QuotaTracker


def parse_book(file_path: str) -> BookContent:
    suffix = Path(file_path).suffix.lower()
    if suffix == ".pdf":
        return pdf_parser.parse_pdf(file_path)
    return epub_parser.parse_epub(file_path)


class SummarizeWorker(QThread):
    progress = pyqtSignal(int, int, str)
    quota_updated = pyqtSignal(object)  # QuotaSnapshot
    finished_ok = pyqtSignal(object)  # BookReport
    # error_kind ("daily_quota"/"rate_quota"/None) : indépendant de la langue
    # du message, pour que main_window puisse adapter son comportement (ex :
    # proposer une reprise) sans jamais chercher un mot-clé dans le message
    # traduit, ce qui casserait selon la langue de l'UI.
    failed = pyqtSignal(str, object)

    def __init__(
        self,
        book_path: str,
        api_key: str,
        quota_tracker: QuotaTracker,
        resume_state: ResumeState | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.book_path = book_path
        self.api_key = api_key
        self.quota_tracker = quota_tracker
        self.resume_state = resume_state

    def run(self) -> None:
        settings_dir = config.get_settings_dir()
        try:
            self.progress.emit(0, 1, tr("worker_progress.reading_file"))
            content = parse_book(self.book_path)

            gemini_client.configure(self.api_key)

            def on_progress(done: int, total: int, message: str) -> None:
                self.progress.emit(done, total, message)

            def on_quota_update(snapshot: QuotaSnapshot) -> None:
                self.quota_updated.emit(snapshot)

            resume_summaries = None
            resume_batches_done = 0
            if (
                self.resume_state is not None
                and self.resume_state.book_hash == generation_resume.compute_book_hash(content.full_text)
            ):
                resume_summaries = self.resume_state.chapter_summaries
                resume_batches_done = self.resume_state.batches_done

            result: BookReport = gemini_client.generate_book_report(
                content,
                quota_tracker=self.quota_tracker,
                on_progress=on_progress,
                on_quota_update=on_quota_update,
                resume_chapter_summaries=resume_summaries,
                resume_batches_done=resume_batches_done,
            )
            generation_resume.clear_resume_state(settings_dir)
            self.finished_ok.emit(result)
        except PartialGenerationError as exc:
            generation_resume.save_resume_state(
                settings_dir,
                ResumeState(
                    book_path=self.book_path,
                    book_hash=generation_resume.compute_book_hash(content.full_text),
                    chapter_summaries=exc.chapter_summaries,
                    batches_done=exc.batches_done,
                    batches_total=exc.batches_total,
                ),
            )
            self.failed.emit(str(exc), exc.error_kind)
        except GeminiError as exc:
            self.failed.emit(str(exc), exc.error_kind)
        except Exception as exc:  # noqa: BLE001 - on veut afficher toute erreur à l'utilisateur
            self.failed.emit(str(exc), None)
