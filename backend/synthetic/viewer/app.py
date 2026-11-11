# AI contribution: 50% or more AI-generated
"""Local web app: one page plus the GET endpoints it reads."""

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from synthetic.viewer.data import Dataset, View

PAGE = Path(__file__).with_name("index.html")


def create_app(data: Dataset) -> FastAPI:
    view = View(data)
    app = FastAPI(title="Synthetic data viewer", docs_url=None, redoc_url=None)
    known_sectors = {row["code"] for row in view.sectors()}

    def checked(sector: str | None) -> str | None:
        if sector is not None and sector not in known_sectors:
            raise HTTPException(404, f"no sector {sector!r} in this data")
        return sector

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/summary")
    def summary() -> dict[str, Any]:
        return view.summary()

    @app.get("/api/sectors")
    def sector_shares() -> list[dict[str, Any]]:
        return view.sectors()

    @app.get("/api/organizations")
    def organizations(sector: str | None = None) -> list[dict[str, Any]]:
        return view.organizations(checked(sector))

    @app.get("/api/people")
    def people(sector: str | None = None, employer: str | None = None) -> list[dict[str, Any]]:
        return view.people(checked(sector), employer)

    @app.get("/api/duplicates")
    def duplicates() -> list[dict[str, Any]]:
        return view.duplicates()

    @app.get("/api/check")
    def check() -> dict[str, Any]:
        return view.check()

    return app
