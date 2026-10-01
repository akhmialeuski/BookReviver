# BookReviver

A workbench for digitising old printed books, with a focus on pre-reform Russian and Belarusian orthography. Each
book is a project that moves through stages: import, page split, cleanup, alignment, recognition and layout of a new
printed edition. Every stage stays viewable at any time, so an earlier stage can be corrected and the later ones rerun.

The application is being rebuilt as a JSON API with a React frontend. `docs/architecture.md` describes the design and
the delivery plan.

## Running

Requires [uv](https://docs.astral.sh/uv/), which installs the pinned Python version by itself.

```bash
uv sync
cp .env.example .env          # then set BOOKREVIVER_AUTH__SECRET
uv run bookreviver-migrate upgrade head
uv run fastapi dev
```

The server listens on http://127.0.0.1:8000. The database, uploaded books and the render cache live in `data/`, and
settings are read from `BOOKREVIVER_*` environment variables or a `.env` file, as listed in `.env.example`.

## Frontend

The interface is a React application in `frontend/`, built with [Node](https://nodejs.org/) 22 or newer.

```bash
npm --prefix frontend ci
npm --prefix frontend run build   # writes frontend/dist, which the server then serves at http://127.0.0.1:8000/
npm --prefix frontend run dev     # or: the Vite server on http://127.0.0.1:5173, proxying /api to port 8000
```

The server serves the build when the directory in `BOOKREVIVER_FRONTEND_DIR` (default `frontend/dist`, relative to
where the server starts) exists, and the API alone otherwise. A confirmation mail that has no SMTP server to go
through is written to the server log, and its link opens `/verify-email` of the application.

`npm --prefix frontend run generate` rebuilds `frontend/src/api` from `docs/openapi.json` after a route or a schema
changes. `npm --prefix frontend run check` runs Biome, the type check and the unit tests.

`npm --prefix frontend run e2e` builds the frontend and runs the Playwright scenarios against a real server, started
on port 8765 with an empty database in `frontend/.e2e-data`, which is also where the confirmation mail is read from.
It needs Playwright's Chromium (`npx --prefix frontend playwright install --with-deps chromium`), or another one named
in `BOOKREVIVER_E2E_CHROMIUM`.

The application never changes the database schema itself. After pulling code that adds a migration, copy `data/` if
it holds anything worth keeping and run `uv run bookreviver-migrate upgrade head` again; until then the server
refuses to start and names that command.

## System dependencies

Reading DjVu books needs the DjVuLibre command-line tools (`djvused`, `djvudump` and `ddjvu`) on the machine that
runs the server and its workers. Without them the application still starts, logs a warning, and refuses every DjVu
file with a message naming the package. The other kinds of source do not need them.

```bash
sudo zypper install djvulibre                                   # openSUSE
sudo apt-get install --no-install-recommends djvulibre-bin      # Debian and Ubuntu, in a Docker image too
```

The DjVu tests build their samples with the same package (`c44`, `cjb2`, `djvm`, `djvmcvt`) and are skipped, with
the reason "DjVuLibre is not installed", when it is missing.

## Development

```bash
uv run pytest
uv run pre-commit install --install-hooks
uv run pre-commit run --all-files
```

## License

AGPL-3.0, see [LICENSE](LICENSE).
