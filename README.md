# BookReviver

A workbench for digitising old printed books, with a focus on pre-reform Russian and Belarusian orthography. Each
book is a project that moves through stages: import, page split, cleanup, alignment, recognition and layout of a new
printed edition. Every stage stays viewable at any time, so an earlier stage can be corrected and the later ones rerun.

The current version covers project management and the Import stage: upload a PDF or a set of page images, read the
technical facts of every page, describe the book, and leaf through its pages.

## Running

Requires [uv](https://docs.astral.sh/uv/), which installs the pinned Python version by itself.

```bash
uv sync
uv run bookreviver
```

The server listens on http://127.0.0.1:8000. The database, uploaded books and the render cache live in `data/`, and
the schema is migrated on startup. Settings are read from `BOOKREVIVER_*` environment variables or a `.env` file, for
example `BOOKREVIVER_DATA_DIR` and `BOOKREVIVER_PORT`.

## Development

```bash
uv run pytest
uv run pre-commit install --install-hooks
uv run pre-commit run --all-files
```

After changing `src/bookreviver/models.py`, add a migration with
`uv run alembic revision --autogenerate -m "Describe the change"` and review the generated file.

## License

AGPL-3.0, see [LICENSE](LICENSE).
