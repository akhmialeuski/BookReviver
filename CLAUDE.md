# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

BookReviver digitises old printed books, mainly pre-reform Russian and Belarusian. Each book moves
through stages that stay viewable and re-runnable at any time. The rules below and the import-linter contracts in the
gate describe the structure the code keeps, and the OpenAPI schema in `docs/openapi.json` describes the API.

## Commands

```bash
uv sync                                  # backend environment
uv sync --extra cv                       # with OpenCV, for the plugins split.spread and geometry.deskew
                                         # the uv-sync hook syncs every extra after a checkout, merge or rebase
uv run bookreviver-migrate upgrade head  # apply the migrations of the catalogue of the settings, by hand only
uv run fastapi dev                       # API on http://127.0.0.1:8000, data in ./data, needs .env and migrations
uv run bookreviver-migrate make-migrations --autogenerate -m "Add the recipes table."  # catalogue revision
uv run bookreviver-migrate make-migrations --bind-key book --autogenerate -m "Add a column."  # book revision
uv run bookreviver-migrate split-shared-database  # once: the shared bookreviver.db into the catalogue and the books
uv run bookreviver-migrate move-book-directories  # once: data/storage/projects/ to data/storage/books/
uv run pytest -m unit                    # the unit tests alone, which run in memory
uv run pytest tests/contract             # port contracts, run against every adapter of the port
uv run lint-imports                      # the layer contracts on their own
uv run bookreviver-openapi               # write docs/openapi.json again after a route or a schema changes
uv run bookreviver-trace-report spans.jsonl --check  # summarise a file of spans and check the time budgets
uv run pre-commit run --all-files        # the gate: ruff, ty, mypy, pyrefly, import-linter, code-line limits, fixers
bash tools/crap-gate.sh                  # pytest with coverage, CRAP per function, changed-line coverage, before a PR
uv run --no-project tools/crap_gate.py size $(git ls-files 'src/*.py') --baseline size-baseline.json --update-baseline
                                         # record a function or file that shrank, then commit size-baseline.json
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
- A route annotates its parameters in place (`Annotated[BookId, Path()]`, `FromDishka[BooksService]`), with no
  module-level alias made only to keep an import at runtime. `ruff.toml` lists the `fastapi.APIRouter` methods and
  `dishka.integrations.fastapi.inject` under `runtime-evaluated-decorators`, and ruff resolves `@router.get` to
  them, so it keeps the imports those annotations need. A dependency shared by many routers, such as `AccessDep`,
  stays an alias in its own module. A path parameter, such as the `book_id` of a book, is written in place on the
  route (or in a dataclass the route takes with `Depends()`), since ruff's `FAST003` does not follow an alias to the
  path parameter it reads. A route docstring puts a form feed
  (`\N{FORM FEED}`) before its reST fields, because FastAPI ends the OpenAPI description there.

## Architecture rules

- Ports and adapters. `domain`, `core` and `services` depend only on `ports`, which are abstract base classes. Every
  database, file, imaging, job and event technology lives in an adapter under `adapters/`, selected in `app`.
- `core` is the book as an object, below `services` and above `ports`. `Book` is one open book. Inside the work of
  `Book.read` or `Book.change`, `BookContent(repos)` is the book of that transaction: `content.pages` are its pages, a
  `BookPage` holds its `versions` and the record and the current version of each of its stages, and what a stage reads
  on a page is asked of `content.pages.inputs`. A use case or a plugin that walks pages and versions asks these objects
  and joins no repositories itself, and a rule of the book that several use cases follow is a method there.
- Swapping a technology is a new adapter of an existing port plus configuration in `app`. If a service, a route or a
  service test has to change for it, the port is wrong: fix the port first.
- A plugin is one step of a recipe, and its contract is `core/plugins.py`. `Processor` is the half that computes: it
  is synchronous, reads the paths of its `StepInput` and runs in a worker. `BookStep` is the half that reads the book:
  it runs in the process of the application inside a transaction and is given the `BookContent`. A plugin whose result
  on one page depends on the other pages sets `book_step` on its class, and the engine asks it what the book gives
  (`leaves_to_book`, `from_book`, `measured`, `page_size`). `services` never names a plugin to do this: it asks
  `BookSteps` which steps of a recipe have that half.
- The built-in plugins lie in `plugins/` by group, which is the stage their steps belong to: `split`, `pages`,
  `geometry`, `cleanup`. A group imports `plugins/kit`, which holds what plugins share, and never another group. Where
  the application needs a plugin for work of its own, such as the image of a page no stage has processed yet, the
  plugin declares a `ProcessorRole` in its spec and the catalogue finds it with `of_role`, so `services` holds no key
  and no version of a plugin. The roles of `ProcessorRole.base()` belong to the base plugins, which are always
  installed: the application refuses to start without a plugin for each. A step that runs on new pages without the
  user asking says `unattended` in its spec.
- The interface knows a plugin by its spec alone and names none. The settings of a step are a form drawn from the JSON
  Schema of `ProcessorSpec.parameters`, its editor is the one registered for `ProcessorSpec.editor`, and the measure of
  the book stands beside the settings of a step whose spec has `book_fields`. The history, the panels, the compare
  and the run controls are the same for every step. A new plugin that needs something else of the interface adds a
  field to the spec, and `tests/unit/plugins/test_plugin_contract.py` holds every installed plugin to the contract.
- `services`, `core` and `domain` never import SQLAlchemy, advanced-alchemy, FastAPI, fastapi-users, Pydantic,
  pydantic-ai, PyMuPDF, Pillow, pyvips or OpenCV. `api`, `adapters` and `plugins` never import each other. Adapter families never import each other. `import-linter` in the gate enforces
  all of this, and a contract failure is fixed by moving code, never by editing the contract.
- A route validates input, calls one method of a service or of the `Book` it was given, and returns a typed response
  built from what it got back, reading the overview and the stages of a `Book` from `StageSummaries`, which it gets
  with `FromDishka[StageSummaries]` and calls with the `Book`: the resource schema, or
  fastapi-pagination's `Page[T]` for a collection. Every error is an RFC 9457 problem from fastapi-problem, mapped from
  domain errors in the one handler registered in `app`.
- Who may work on what is decided only by `Access` in `api/access.py`. Every route of the API is mounted under one
  router whose dependency is `access`, so a request without a session is refused before any route runs; only sign-in,
  the routes of the signed-in account and `/iiif` are mounted outside it. A route that needs the account, a book or the
  list of books declares `access: AccessDep`. `Access.book` opens a book of the signed-in account and answers one of
  another account like a missing one, `Access.books` reads the account's list, and `Access.actor` is the owner of a
  record the route creates. Services, the domain and the jobs check no owner and choose no account's records: a use
  case works on the `Book` it is given, and takes an account only as the owner of a record it creates. A recipe profile
  lives in its book and is reached through the routes of the book. A route of a book declares
  `book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)]` in place (or takes it from a path dataclass) and
  calls `await access.book(book_id)`. A new router is added to `GUARDED_ROUTERS` in `api/routing.py`.
- All request input is validated by Pydantic before the route runs: bodies, query models (`Annotated[Model, Query()]`),
  forms and headers, built on the shared `RequestModel` base and the constrained types in `api/schemas/types.py`.
  Handlers never check raw input by hand.
- HTTP status codes come from `fastapi.status` or `http.HTTPStatus`, never as number literals, in code and in tests.
- Everything on the event loop is async: the API and the ports of files, jobs, events and HTTP. The persistence
  repositories are synchronous and run only inside the work a service or an access dependency hands to
  `CatalogueDatabase.read` or `change` of the catalogue, or `Book.read` or `change` of one book, which the adapter runs
  in a worker thread as one transaction of that file. A change of the catalogue waits for its writer for the short
  limit of a request or the long one of a job (`change_wait`), while a change of a book always waits for the long one,
  because a book opens its database through the registration `app` makes once, which knows no scope of a request.
  Other blocking library calls go through
  `anyio.to_thread.run_sync` inside adapters, keyword arguments through `functools.partial`. Heavy work (parsing,
  rasterising, tiling, OCR, LLM calls) runs only in background jobs, whose entry points live in `app` and call one
  service method.
- A background job is what was asked for: a step or a stage, the pages it goes over and its parameters, as the value
  of its kind in `Job.work` (`StageRun`, `StepPreview`, `TileCut`, …), which a worker reads with `job.asked(...)`.
  Jobs live in the memory of the process and are never stored, so a restart begins with an empty queue. The queue of a
  book runs one job at a time in the order they were put, first in, first out, and `JobStarter` refuses nothing
  because the book is busy: a second run waits behind the first and reads what it made. A job that follows from the
  running one, such as the split of the pages an import made or the detection of their content, is put `next_up`,
  ahead of the queued jobs. A preview is the one job that is dropped for another: a run, a measure of the book and a
  new preview cancel it. `app/jobs.py` has one runner for every job and `job_workers`, which names the method of a
  service that does each `JobKind`, each taking the book, the job and its control.
- A job that does the same work for many pages or scans hands it to `WorkerGroup` of `services/worker_group.py`, which
  does a few at a time. A run of a stage processes as many pages together as the step executor has processes for full
  runs (`processing.step_processes` less `processing.preview_step_processes`), and the page split one at a time.
- Every new port gets a fake in `tests/harness/fakes` and a contract test suite that runs against the fake and every adapter.
  `src` holds no code that only tests select. The persistence port has no fake: its tests run on the SQLAlchemy adapter
  over SQLite files in the test's directory, the catalogue and every book copied from a template of its schema built
  once per test process.

## Types and classes

- The work of a use case is a chain of classes, each owning the next and holding what its methods share, so a value
  is given once to the class it belongs to and never handed down from call to call. The run of a stage is the model:
  `StageWork` is one job on one book, `PageWork` one stage on one page of it, `StepWork` one step on that page, and
  each reaches the book and the ports through the one it belongs to. The other use cases follow it: `PagesWork` is the
  pages of one transaction with their `PageOverviews`, `PagePlaces` and `Leaves`, `PageImages` the prepare-pages job
  with a `BaseVersionWork` for each version, `StepOnPage` a step on a page with its `StepUndo` and `StepClear`,
  `DetectionWork` a detect-content job with a `PageDetection` for a page, `StageRunJob` a run-stage job with the
  `PagesRun` of its pages, and `PageSplit` the split of a scan with the `MadeSplit` of its result. A group of methods
  that pass the same three values on is a class that is missing. A class of a chain lives in a module that imports the
  module of its owner and never the reverse, which the `acyclic_siblings` contract on `services` enforces.

- Closed value sets are `StrEnum` or `IntEnum` in `domain`, each carrying its own label.
- Data travelling together is a frozen `attrs` class in `domain`, changed with `attrs.evolve`. Identifiers are
  `NewType`s. API schemas are separate Pydantic models in `api`, mapped from domain objects.
- Shared behaviour goes into a generic base class (`Repository[EntityT, IdT]`, `SqlAlchemyRepository`) instead of
  being repeated per entity. A class holds the state its methods share, so no function passes the same values around.
- No loose string or number constants: names are enums, tunables belong to the settings or class that owns them,
  URLs come from route names.

## Conventions

- Python 3.14 with deferred annotations. SQLAlchemy 2.0 synchronous engines over SQLite in the persistence adapter, its
  tables private to it. SQLite is the only database: the catalogue `data/catalogue.db` holds the accounts and the
  library that says which account owns which book, and every book is `book.db` in its own directory
  `data/storage/books/<id>/`, beside its files, with its recipe profiles and no account in it. Nothing refers across
  two files by a key.
- A change of a table ships with an Alembic revision in `adapters/persistence/sqlalchemy/migrations/catalogue/versions/`
  or `.../book/versions/`, generated with `make-migrations --autogenerate` (`--bind-key book` for a book), read by eye
  for every key and index, and formatted by the gate.
  `tests/integration/adapters/persistence/sqlalchemy/test_migrations.py` fails until the revisions match the tables.
  The application never migrates the catalogue itself: it refuses to start until
  `bookreviver-migrate upgrade head` has run. A book is brought to the head revision of its schema when it is opened, and
  a book of a newer release is refused with `BookTooNewError`. ty's `unsound-assignment` is disabled only for the table
  modules, because `Mapped[...] = mapped_column()` trips it.
- Async tests use the anyio plugin (`@pytest.mark.anyio`).
- A test sits under the directory of its level: `tests/unit` runs in memory, `tests/integration` uses one real
  technology in the process, `tests/contract` runs the suite of a port on its fake and every adapter, and
  `tests/component` runs the whole backend through httpx. `unit` and `integration` mirror `src/bookreviver`, and
  `component` is grouped by feature of the API. `tests/harness/plugin.py` marks every test with its level and fails
  one whose fixtures its level refuses, such as a unit test taking `tmp_path`. Code the tests share lives in
  `tests/harness`, and a fixture lives in the `conftest.py` of the lowest directory covering its users, or in
  `tests/harness/fixtures` when tests of several levels take it.
- The features of the frontend in `frontend/src/features/` form layers, lowest first: `auth`, `stages`, `books`,
  `pages`, `place`, `viewer`, `processing`, then `editors` and `profiles` side by side, `workspace`, `about`, and
  `order` and `import` side by side. A feature imports only the features below it, and `frontend/src/shared/` imports
  no feature but for the types `messages.ts` is keyed by. Biome's `noRestrictedImports` in `frontend/biome.jsonc`
  enforces it, and a failure is fixed by moving what is shared down, never by editing the rule. A test may put
  features of any layer together.
- The frontend client in `frontend/src/api/` is generated from the committed OpenAPI schema and never edited by hand.
  Every text the interface shows is in `frontend/src/shared/messages.ts`, and TypeScript runs without `any` or
  suppressions.
