# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

BookReviver digitises old printed books, mainly pre-reform Russian and Belarusian. One project is one book, and a
project moves through the stages declared in `src/bookreviver/stages.py`. Every stage must stay viewable at any time,
even after later stages ran, because an earlier stage can be corrected and the rest rerun.

## Commands

```bash
uv sync                                  # install the locked environment
uv run bookreviver                       # serve on http://127.0.0.1:8000, data in ./data
uv run pytest tests/test_app.py::TestCreateApp::test_startup_migrates_schema
uv run pre-commit run --all-files        # the gate: ruff, ty, mypy, pyrefly and file fixers
uv run alembic revision --autogenerate -m "Describe the change"   # after editing models.py
```

## Architecture

A single async FastAPI application renders HTML with Jinja2, without a separate frontend build.
`app.create_app()` wires everything, and its lifespan migrates the schema with Alembic, then puts the settings,
the `ProjectStorage` and the session factory on `app.state`. Handlers reach them only through the typed dependencies
in `web/dependencies.py` (`SessionDep`, `StorageDep`, `SettingsDep`).

- `models.py` holds the schema: `Project` carries the bibliographic description and the imported source, `Page`
  carries the technical facts of one page. Migrations live in `migrations/versions/` and ship inside the package.
- `storage.py` owns the file layout `data/projects/<id>/source` (the upload as received) and `.../cache`
  (regenerable renders).
- `analysis.py` reads a PDF or an image set into `SourceAnalysis`, and `rendering.py` renders cached WebP page
  images. Both are synchronous and CPU bound, so handlers call them through `anyio.to_thread.run_sync`.
- `web/pages.py` serves the Import stage, the upload and the page viewer. `web/projects.py` serves project
  management and the other stage tabs. `app.py` includes `pages.router` first, because the generic stage route in
  `projects.router` would otherwise capture the Import stage.
- Templates extend `base.html`, and every project page includes `_project_tabs.html` for the stage tabs.

## Conventions

- Python 3.14 with deferred annotations. SQLAlchemy and FastAPI evaluate annotations at runtime, which is why
  `ruff.toml` exempts `fastapi` and the declarative `Base` from the type-checking-import rules.
- `pyproject.toml` disables ty's `unsound-assignment` for `models.py` only, because the SQLAlchemy
  `Mapped[...] = mapped_column()` idiom trips it. Do not widen that override.
- Async tests use the anyio plugin (`@pytest.mark.anyio`) with the `fx_app` and `fx_client` fixtures from
  `tests/conftest.py`, which give every test its own data directory and database.
- The repository runs from a Windows drive under WSL, which is why `[tool.uv] link-mode = "copy"` is set.
