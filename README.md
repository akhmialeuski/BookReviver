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
