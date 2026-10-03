# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

BookReviver digitises old printed books, mainly pre-reform Russian and Belarusian. One project is one book, moving
through stages that stay viewable and re-runnable at any time. The rules below and the import-linter contracts in the
gate describe the structure the code keeps, and the OpenAPI schema in `docs/openapi.json` describes the API.

## Commands

```bash
uv sync                                  # backend environment
uv sync --extra cv                       # with OpenCV, for the plugins split.spread and geometry.deskew
                                         # the uv-sync hook syncs every extra after a checkout, merge or rebase
uv run bookreviver-migrate upgrade head  # apply the schema migrations to the database of the settings, by hand only
uv run fastapi dev                       # API on http://127.0.0.1:8000, data in ./data, needs .env and migrations
uv run bookreviver-migrate make-migrations --autogenerate -m "Add the recipes table."  # revision from the tables
uv run pytest tests/contracts            # port contracts, run against every adapter of the port
uv run lint-imports                      # the layer contracts on their own
uv run bookreviver-openapi               # write docs/openapi.json again after a route or a schema changes
uv run pre-commit run --all-files        # the gate: ruff, ty, mypy, pyrefly, import-linter, file fixers
npm --prefix frontend ci                 # frontend dependencies, Node 22.18 or newer (even-numbered releases only)
npm --prefix frontend run dev            # frontend on http://127.0.0.1:5173, proxies /api to the backend
npm --prefix frontend run build          # frontend/dist, which the backend serves at / when the directory exists
npm --prefix frontend run generate       # frontend/src/api again, after docs/openapi.json changes
npm --prefix frontend run check          # Biome, tsc and Vitest
npm --prefix frontend run e2e            # builds, then Playwright against a real backend with an empty database```

## Reuse before writing

- Before any task, find how the library in use already does it and how the community does it: its documentation,
  its bundled agent guides (FastAPI ships `fastapi/.agents/skills/fastapi/SKILL.md` in the virtual environment),
  then established packages. `pyproject.toml` and `frontend/package.json` list what the project already depends on.
- Write only what is specific to BookReviver. A pull request that re-implements something a dependency offers
  states why the dependency does not fit.
- FastAPI specifics: `Annotated` dependency aliases, prefix and tags on the `APIRouter`, return types instead of
  `response_model` where they match, `EventSourceResponse` for SSE, `app.frontend()` for the built frontend,
  `fastapi dev` and `fastapi run` with the `[tool.fastapi]` entrypoint.
- A route annotates its parameters in place (`Annotated[ProjectId, Path()]`, `FromDishka[ProjectService]`), with no
  module-level alias made only to keep an import at runtime. `ruff.toml` lists the `fastapi.APIRouter` methods and
  `dishka.integrations.fastapi.inject` under `runtime-evaluated-decorators`, and ruff resolves `@router.get` to
  them, so it keeps the imports those annotations need. A dependency shared by many routers, such as `ActorDep`,
  stays an alias in its own module. A route docstring puts a form feed (`\N{FORM FEED}`) before its reST fields,
  because FastAPI ends the OpenAPI description there.

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
- A change of a table ships with an Alembic revision in `adapters/persistence/sqlalchemy/migrations/versions/`,
  generated with `make-migrations --autogenerate`, read by eye for every key and index, and formatted by the gate.
  `tests/adapters/persistence/sqlalchemy/test_migrations.py` fails until the revision matches the tables. The
  application never migrates itself: it refuses to start until `bookreviver-migrate upgrade head` has run.
  ty's `unsound-assignment` is disabled only for the table module, because `Mapped[...] = mapped_column()` trips it.
- Async tests use the anyio plugin (`@pytest.mark.anyio`).
- The frontend client in `frontend/src/api/` is generated from the committed OpenAPI schema and never edited by hand.
  Every text the interface shows is in `frontend/src/shared/messages.ts`, and TypeScript runs without `any` or
  suppressions.
