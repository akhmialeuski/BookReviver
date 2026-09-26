# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

BookReviver digitises old printed books, mainly pre-reform Russian and Belarusian. One project is one book, moving
through stages that stay viewable and re-runnable at any time. `docs/architecture.md` is the source of truth for
structure, ports, adapters, API and delivery plan. Read it before any change, and update it in the same pull request
when a change alters what it describes.

## Commands

```bash
uv sync                                  # backend environment
uv run bookreviver                       # API on http://127.0.0.1:8000, data in ./data
uv run pytest tests/services             # one test package, e.g. services against in-memory adapters
uv run pre-commit run --all-files        # the gate: ruff, ty, mypy, pyrefly, import-linter, file fixers
npm --prefix frontend run dev            # frontend on http://127.0.0.1:5173, proxies /api to the backend
npm --prefix frontend run check          # Biome, tsc and Vitest
```

## Reuse before writing

- Before any task, find how the library in use already does it and how the community does it: its documentation,
  its bundled agent guides (FastAPI ships `fastapi/.agents/skills/fastapi/SKILL.md` in the virtual environment),
  then established packages. The "Libraries" table in `docs/architecture.md` lists what each concern reuses.
- Write only what is specific to BookReviver. A pull request that re-implements something a dependency offers
  states why the dependency does not fit.
- FastAPI specifics: `Annotated` dependency aliases, prefix and tags on the `APIRouter`, return types instead of
  `response_model` where they match, `EventSourceResponse` for SSE, `app.frontend()` for the built frontend,
  `fastapi dev` and `fastapi run` with the `[tool.fastapi]` entrypoint.

## Architecture rules

- Ports and adapters. `domain` and `services` depend only on `ports`, which are abstract base classes. Every
  database, file, imaging, job and event technology lives in an adapter under `adapters/`, selected in `app`.
- Swapping a technology is a new adapter of an existing port plus configuration in `app`. If a service, a route or a
  service test has to change for it, the port is wrong: fix the port first.
- `services` and `domain` never import SQLAlchemy, advanced-alchemy, FastAPI, fastapi-users, Pydantic, Taskiq,
  pydantic-ai, PyMuPDF, Pillow, pyvips or OpenCV. `api`, `adapters` and `plugins` never import each other. Adapter families never import each other. `import-linter` in the gate enforces
  all of this, and a contract failure is fixed by moving code, never by editing the contract.
- A route validates input, calls one service method and returns a typed response: the resource schema, or
  fastapi-pagination's `Page[T]` for a collection. Every error is an RFC 9457 problem from fastapi-problem, mapped from
  domain errors in the one handler registered in `app`.
- All request input is validated by Pydantic before the route runs: bodies, query models (`Annotated[Model, Query()]`),
  forms and headers, built on the shared `RequestModel` base and the constrained types in `api/schemas/types.py`.
  Handlers never check raw input by hand.
- HTTP status codes come from `fastapi.status` or `http.HTTPStatus`, never as number literals, in code and in tests.
- Everything is async: ports, API, SQLAlchemy `AsyncSession`, files, HTTP. Blocking library calls go through
  `asyncer.asyncify` inside adapters. Heavy work (parsing, rasterising, tiling, OCR, LLM calls) runs only in
  background jobs, whose entry points live in `app` and call one service method.
- Every new port gets an in-memory adapter and a contract test suite that runs against all its adapters.

## Types and classes

- Closed value sets are `StrEnum` or `IntEnum` in `domain`, each carrying its own label.
- Data travelling together is a frozen `attrs` class in `domain`, changed with `attrs.evolve`. Identifiers are
  `NewType`s. API schemas are separate Pydantic models in `api`, mapped from domain objects.
- Shared behaviour goes into a generic base class (`Repository[EntityT, IdT]`, `SqlAlchemyRepository`) instead of
  being repeated per entity. A class holds the state its methods share, so no function passes the same values around.
- No loose string or number constants: names are enums, tunables belong to the settings or class that owns them,
  URLs come from route names.

## Conventions

- Python 3.14 with deferred annotations. SQLAlchemy 2.0 async in the persistence adapter, its tables private to it.
  ty's `unsound-assignment` is disabled only for the table module, because `Mapped[...] = mapped_column()` trips it.
- Async tests use the anyio plugin (`@pytest.mark.anyio`).
- The frontend client in `frontend/src/api/` is generated from the committed OpenAPI schema and never edited by hand.
- The repository runs from a Windows drive under WSL, which is why `[tool.uv] link-mode = "copy"` is set.
