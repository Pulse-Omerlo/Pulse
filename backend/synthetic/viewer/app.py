# AI contribution: 50% or more AI-generated
"""Local web app: one page, the GET endpoints it reads and a sync to read the data again."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from synthetic.viewer.data import Dataset, View

logger = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("index.html")
# A cross-site form cannot send this header, so another page cannot start a sync.
SYNC_HEADER = "x-viewer-sync"


def in_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


class Source:
    """The data the API answers from, read again in the background when asked."""

    def __init__(
        self,
        load: Callable[[], Dataset],
        data: Dataset | None = None,
        start: Callable[[Callable[[], None]], None] = in_thread,
    ):
        self.load = load
        self.start = start
        self.view = View(data) if data else None
        self.syncing = False
        self.error: str | None = None
        self.lock = threading.Lock()

    def sync(self) -> bool:
        """Start reading the data again; False when a read is already running."""
        with self.lock:
            if self.syncing:
                return False
            self.syncing = True
        self.start(self.read)
        return True

    def read(self) -> None:
        try:
            view = View(self.load())
        except Exception as error:
            logger.exception("Could not read the data")
            self.error = str(error) or type(error).__name__
        else:
            self.view, self.error = view, None
        finally:
            self.syncing = False

    def status(self) -> dict[str, Any]:
        return {
            "syncing": self.syncing,
            "error": self.error,
            "read_at": self.view.data.read_at if self.view else None,
        }

    def current(self) -> View:
        if self.view is None:
            raise HTTPException(503, "the data is still being read")
        return self.view


def create_app(source: Source) -> FastAPI:
    app = FastAPI(title="Synthetic data viewer", docs_url=None, redoc_url=None)

    def checked(view: View, sector: str | None) -> str | None:
        if sector is not None and sector not in view.sector_codes:
            raise HTTPException(404, f"no sector {sector!r} in this data")
        return sector

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/sync")
    def sync_status() -> dict[str, Any]:
        return source.status()

    @app.post("/api/sync", status_code=202)
    def sync(x_viewer_sync: str | None = Header(None)) -> dict[str, Any]:
        if x_viewer_sync is None:
            raise HTTPException(403, f"a sync needs the {SYNC_HEADER} header")
        source.sync()
        return source.status()

    @app.get("/api/summary")
    def summary() -> dict[str, Any]:
        return source.current().summary()

    @app.get("/api/sectors")
    def sector_shares() -> list[dict[str, Any]]:
        return source.current().sectors()

    @app.get("/api/organizations")
    def organizations(sector: str | None = None) -> list[dict[str, Any]]:
        view = source.current()
        return view.organizations(checked(view, sector))

    @app.get("/api/people")
    def people(sector: str | None = None, employer: str | None = None) -> list[dict[str, Any]]:
        view = source.current()
        return view.people(checked(view, sector), employer)

    @app.get("/api/duplicates")
    def duplicates() -> list[dict[str, Any]]:
        return source.current().duplicates()

    @app.get("/api/check")
    def check() -> dict[str, Any]:
        return source.current().check()

    return app
