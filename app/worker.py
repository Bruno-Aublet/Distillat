"""Thread de traitement (extraction EPUB/PDF + appel Gemini) pour ne pas bloquer l'UI."""
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from app import epub_parser, gemini_client, pdf_parser
from app.book_report import BookReport
from app.epub_parser import BookContent
from app.quota_tracker import QuotaSnapshot, QuotaTracker


def parse_book(file_path: str) -> BookContent:
    suffix = Path(file_path).suffix.lower()
    if suffix == ".pdf":
        return pdf_parser.parse_pdf(file_path)
    return epub_parser.parse_epub(file_path)


class SummarizeWorker(QThread):
    progress = pyqtSignal(int, int, str)
    quota_updated = pyqtSignal(object)  # QuotaSnapshot
    retry_wait = pyqtSignal(float, str)  # secondes d'attente, nom du quota concerné (ou "")
    finished_ok = pyqtSignal(object)  # BookReport
    failed = pyqtSignal(str)

    def __init__(self, book_path: str, api_key: str, quota_tracker: QuotaTracker, parent=None):
        super().__init__(parent)
        self.book_path = book_path
        self.api_key = api_key
        self.quota_tracker = quota_tracker

    def run(self) -> None:
        try:
            self.progress.emit(0, 1, "Lecture du fichier…")
            content = parse_book(self.book_path)

            gemini_client.configure(self.api_key)

            def on_progress(done: int, total: int, message: str) -> None:
                self.progress.emit(done, total, message)

            def on_quota_update(snapshot: QuotaSnapshot) -> None:
                self.quota_updated.emit(snapshot)

            def on_retry_wait(wait_seconds: float, quota_id: str) -> None:
                self.retry_wait.emit(wait_seconds, quota_id)

            result: BookReport = gemini_client.generate_book_report(
                content,
                quota_tracker=self.quota_tracker,
                on_progress=on_progress,
                on_quota_update=on_quota_update,
                on_retry_wait=on_retry_wait,
            )
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001 - on veut afficher toute erreur à l'utilisateur
            self.failed.emit(str(exc))
