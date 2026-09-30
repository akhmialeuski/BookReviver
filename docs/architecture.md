# BookReviver architecture

BookReviver turns scans of old printed books into corrected, re-typeset editions. One project is one book, owned by
an account, and it is kept apart from the files it was assembled from. A book moves through stages (import, page
split, page order, geometry, cleanup, layout, background, recognition, proofreading, typesetting), and every stage
stays viewable and re-runnable at any time. Processing steps are plugins, OCR engines and language models are
interchangeable, and heavy work runs on workers that can live on other machines.

This document is the source of truth for structure and contracts. `CLAUDE.md` holds the working rules derived from
it. A change that contradicts this document updates the document in the same pull request.

## Principles

1. **Reuse before writing.** Every concern starts from the library, framework feature or community pattern that
   already solves it, found by reading its documentation and its bundled guides (FastAPI ships one in
   `fastapi/.agents/skills/fastapi/`). Code of our own covers only what is specific to BookReviver, and a pull request
   that writes something a dependency already offers says why.
2. **Ports and adapters.** The core (domain and services) defines abstract ports for everything outside it:
   persistence, file storage, mail, image processing, AI engines, jobs, events, time. Adapters implement the ports.
   The core imports no framework, no driver and no library that touches I/O.
3. **Every adapter is replaceable.** Swapping the database, the file store, the job broker, an OCR engine or an LLM
   provider means another adapter of the same port selected in the composition root. No service, route or service
   test changes.
4. **Substitutability is proven.** Each port has a contract test suite that runs against every one of its adapters,
   including the in-memory adapter the service tests use.
5. **Asynchronous end to end.** Every port method is `async`. The API, the database (SQLAlchemy 2.0 `AsyncSession`),
   file access and outbound HTTP (`httpx.AsyncClient`) never block the event loop. Blocking library calls go through
   Asyncer (`asyncify`) inside adapters, and heavy work runs only in background jobs.
6. **Extension without rewriting.** Processing steps, OCR engines, layout analysers and language models are plugins
   discovered through Python entry points. Adding one is a new package or module, not a change to the application.
7. **Dependencies point inwards only.** `import-linter` enforces the layer order and forbids infrastructure libraries
   in the core, as part of the pre-commit gate.

## Libraries

What each concern reuses, and therefore what we do not write ourselves.

| Concern                    | Library or feature                                              | Not written by us                                |
| -------------------------- | --------------------------------------------------------------- | ------------------------------------------------ |
| HTTP API                   | FastAPI: `Annotated` dependencies, router-level prefix and tags | Routing, validation, OpenAPI                     |
| Server-sent events         | FastAPI `EventSourceResponse`, `ServerSentEvent`                | Event stream formatting                          |
| Serving the frontend       | FastAPI `app.frontend()`                                        | Static files and client-side routing fallback    |
| Running                    | `fastapi dev`, `fastapi run`, `[tool.fastapi]` entrypoint       | Server bootstrap                                 |
| Dependency injection       | dishka, with its FastAPI and Taskiq integrations                | Container, scopes, wiring of workers             |
| Accounts                   | fastapi-users: register, verify, reset, cookie sessions, OAuth  | Auth flows, password hashing (pwdlib argon2)     |
| Social sign-in             | httpx-oauth: ready-made Google and Facebook clients             | OAuth 2.0 protocol, state                        |
| Rate limiting              | slowapi                                                         | Throttling of sign-in and registration           |
| CSRF                       | starlette-csrf                                                  | Double-submit cookie check                       |
| Persistence                | SQLAlchemy 2.0 async with advanced-alchemy repositories         | Generic CRUD, pagination, filters, column types  |
| Request validation         | Pydantic 2 models and constrained types, email-validator        | Parsing and checking input                       |
| Collection responses       | fastapi-pagination `Page[T]` and its `Params`                   | Paging schema and query parameters               |
| Error responses            | fastapi-problem (RFC 9457 problem details)                      | Error format, error schemas in OpenAPI           |
| Migrations                 | Alembic through advanced-alchemy's integration                  | Schema versioning                                |
| Secrets at rest            | advanced-alchemy `EncryptedString` with the Fernet backend      | Encryption of provider keys                      |
| Settings                   | pydantic-settings                                               | Environment and `.env` parsing                   |
| Background jobs            | Taskiq, in-process broker locally, Redis on a server            | Queueing, retries, worker processes              |
| Mail                       | aiosmtplib                                                      | SMTP                                             |
| PDF and images             | PyMuPDF, Pillow                                                 | Parsing and rasterising                          |
| DjVu                       | DjVuLibre command-line tools: `djvused`, `ddjvu`, `djvutxt`     | DjVu parsing, rendering and text extraction      |
| XMP metadata               | defusedxml                                                      | XML parsing safe against hostile documents       |
| Page order                 | fractional-indexing                                             | Order keys that sort between two neighbours      |
| Tiles                      | pyvips `dzsave` with the IIIF 3 layout                          | Tile pyramids and `info.json`                    |
| Image processing plugins   | OpenCV, scikit-image                                            | Geometry, filtering, morphology                  |
| Language and vision models | pydantic-ai                                                     | Provider clients, structured output validation   |
| Plugin discovery           | `importlib.metadata.entry_points`                               | Plugin loading                                   |

SQLAlchemy 2.0 is kept even though FastAPI's guide suggests SQLModel, because the persistence adapter is the only
place that sees the ORM and SQLAlchemy is the explicit project choice.

## Layers

```mermaid
flowchart TD
    app["app: composition root, settings, dishka container, workers"] --> api
    app --> adapters
    app --> plugins
    api["api: FastAPI routers, schemas, fastapi-users routers, SSE"] --> services
    adapters["adapters: persistence, storage, imaging, AI, jobs, mail"] --> ports
    plugins["plugins: processing steps"] --> ports
    services["services: use cases"] --> ports
    ports["ports: abstract interfaces and the plugin contract"] --> domain
    domain["domain: entities, value objects, enums, errors"]
```

| Package    | Owns                                                                   | May import                     |
| ---------- | ---------------------------------------------------------------------- | ------------------------------ |
| `app`      | Settings, adapter selection, plugin discovery, container, worker entry | every package below            |
| `api`      | HTTP routes, schemas, auth routers, SSE, error mapping                 | `services`, `domain`           |
| `adapters` | Implementations of ports over concrete technologies                    | `ports`, `domain`              |
| `plugins`  | Built-in processing plugins, written exactly like external ones        | `ports`, `domain`              |
| `services` | Use cases, business rules, authorisation                               | `ports`, `domain`              |
| `ports`    | Abstract base classes, the plugin contract                             | `domain`                       |
| `domain`   | Entities, value objects, enums with labels, the error hierarchy        | the standard library and attrs |

- `api`, `adapters` and `plugins` never import each other. Adapter families never import each other.
- `services` and `domain` may not import SQLAlchemy, advanced-alchemy, FastAPI, fastapi-users, Pydantic, Taskiq,
  pydantic-ai, PyMuPDF, Pillow, pyvips or OpenCV. The import-linter contract lists these packages explicitly.
- Accounts are the one place where a framework owns a table: fastapi-users keeps its user, OAuth account and access
  token tables inside the persistence adapter, and the core sees only an `AccountId` and the acting `Actor`.
- Background job entry points live in `app`: dishka resolves a service for the job, which calls one method.

## Domain

Pure Python with `attrs`. Entities are frozen and changed through `attrs.evolve`. Identifiers are `NewType`s, and
every closed set of values is a `StrEnum` carrying its own label.

| Area         | Types                                                                                         |
| ------------ | --------------------------------------------------------------------------------------------- |
| Identifiers  | `AccountId`, `ProjectId`, `SourceId`, `ScanId`, `PageId`, `PageVersionId`, `JobId`            |
| Accounts     | `Actor`, `AccountSettings` (default engine and model per `AiTask`)                            |
| Credentials  | `ProviderCredential` with a masked secret, never printed or logged                            |
| Books        | `Project`, `ProjectOverview`, `BookDetails`, `ImagePolicy`                                    |
| Changes      | `BookDetailsChanges`, the description fields a change replaces, None keeping a field          |
| Sources      | `Source`, `SourceFile`, `SourceKind`, `FileType`, `MetadataSuggestion`                        |
| Scans        | `Scan`, `ScanFacts`, `Renditions`                                                             |
| Pages        | `Page`, `PageKind`, `PageOrigin`, `PageVersion`, `VersionState`, `Transform`, `PageStage`     |
| Storage keys | `StorageKey`, and `ProjectKeys` in `domain/keys.py`, the one builder of every key             |
| Processing   | `Stage`, `ProcessorRef`, `Recipe`, `Step`, `Variant`, `ArtifactKind`, `Artifact`              |
| Edits        | `PageEdit` with geometry (`Rect`, `Quad`, `Mesh`, `Region` with `RegionKind`) or a mask       |
| Events       | `JobChanged`, `SourceImported`, `ScanReady`, `PagesChanged`, `PageVersionReady`, and others   |
| Jobs         | `Job`, `JobKind`, `JobState`, `Progress`, `WorkerPool` (cpu, gpu, llm)                        |
| Queries      | `Slice[T]` (items and total), `SliceRequest` (offset and limit)                               |
| Errors       | `DomainError`, `NotFoundError`, `PermissionDeniedError`, `UploadRejectedError`, and others    |

### The book and its sources

A project describes one book, and the book does not depend on which files it was assembled from. A source can lack
some pages or the cover, the cover and missing pages can come from another copy, and one scan of a spread becomes two
pages of the book. The model therefore has five objects, each with its own identity and metadata:

- A **project** (`Project`) is the book: its bibliographic description (`BookDetails`) and the fields of the work on
  it.
- A **source** (`Source`) is one uploaded file, never changed after import, with its technical metadata.
- A **scan** (`Scan`) is one image inside a source: a page of a PDF or DjVu document, a frame of a multi-page TIFF, or
  the single image of a JPEG file.
- A **page** (`Page`) is a page of the book in book order, with its own copy of the image, its printed number and its
  kind. It does not depend on its source, and its link to a scan only records where it came from.
- A **page version** (`PageVersion`) is the result of one processing step on one page: the image in several sizes,
  the data of the step and the transform of coordinates from its input.

In the diagram a green outline marks objects that never change after they are written, and a purple outline marks
objects the user edits.

```mermaid
flowchart TD
    P["Project<br/>book description"] -- "1..n" --> S["Source<br/>one uploaded file"]
    S -- "1..n" --> C["Scan<br/>one image of the source"]
    P -- "ordered 1..n" --> G["Page<br/>page of the book"]
    C -. "0..n slots" .-> G
    G -- "per stage and step" --> V["PageVersion<br/>result of one step"]
    style P stroke:#5b3fd1,stroke-width:2px
    style G stroke:#5b3fd1,stroke-width:2px
    style S stroke:#1e7a4d,stroke-width:2px
    style C stroke:#1e7a4d,stroke-width:2px
    style V stroke:#1e7a4d,stroke-width:2px
```

Scans and pages are separate objects because their numbers differ. A scan of a spread gives two pages, a cover from
another copy gives a page with no scan in the main source, and a page missed during scanning stays in the book as a
placeholder without a scan. Versions belong to the page and not to the scan, because the printed number, the order,
manual edits and recognised text have to survive a re-run of any stage.

The boundary between the sources and the book is the creation of pages. Before it everything depends on the kind of
source: PDF, DjVu and image files are read by different formats and give scans with different metadata. After it a
page stands on its own, with its own copy of the image and its own versions, and a PDF, a DjVu and a TIFF source look
the same to it. The stages run in this order, where the green ones depend on the kind of source and the purple ones
work only with pages of the book:

```mermaid
flowchart TD
    I["import<br/>sources and scans"] --> S["page-split, can be skipped<br/>pages from scans"]
    S --> O["page-order<br/>order, labels, placeholders"]
    O --> G["geometry, cleanup,<br/>layout, background"]
    G --> R["recognition, proofreading,<br/>typesetting"]
    style I stroke:#1e7a4d,stroke-width:2px
    style S stroke:#1e7a4d,stroke-width:2px
    style O stroke:#5b3fd1,stroke-width:2px
    style G stroke:#5b3fd1,stroke-width:2px
    style R stroke:#5b3fd1,stroke-width:2px
```

The page split creates pages from scans, so it still looks at the scan, and when it is skipped every scan becomes one
page. In the page order stage, `Stage.PAGE_ORDER` (`'page-order', 'Page order'`) right after `Stage.PAGE_SPLIT`, the
user arranges the pages and adds what the scans lack: missing pages, covers, title pages, blank leaves after the
cover and endpapers. Every later stage works only with pages of the book.

### Project and book description

A project holds two kinds of data. The book description belongs to the printed edition alone and is filled in by
hand or from the metadata of the sources, and the project fields belong to the work on the project. `BookDetails` has
eleven string fields in the code today. The extended description below, with contributors by role, identifiers and
the physical description, is part of the model and arrives with its own task, so the table marks what exists now.

| Group                | Fields                                                                                   | In the code now                  |
| -------------------- | ---------------------------------------------------------------------------------------- | -------------------------------- |
| Title                | `title`, `subtitle`, `parallel_titles`, `original_title`                                 | only `title`                     |
| Contributors         | `contributors`: pairs of a name and a role                                               | one `authors` string             |
| Imprint              | `publisher`, `printer`, `publication_place`, `publication_year`, `edition`, `censorship` | without `printer`, `censorship`  |
| Series and volume    | `series`, `series_number`, `volume`                                                      | without `series_number`          |
| Language and script  | `languages`, `orthography`, `script` (Cyrillic, Latin)                                   | one `language`, `orthography`    |
| Physical description | `printed_pagination`, `height_cm`, `illustrations`, `binding`                            | none                             |
| Identifiers          | `identifiers`: pairs of a scheme and a value (ISBN, OCLC, shelfmark, URL of a copy)      | none                             |
| Subject and rights   | `subjects`, `rights` (public domain or not)                                              | none                             |
| Copy                 | `copy_holder` (whose copy was scanned), `copy_notes` (bookplates, marks)                 | none                             |
| Notes                | `notes`                                                                                  | present                          |

Contributor roles are author, editor, compiler, translator, illustrator, engraver, author of the preface and
commentator, taken from the [MARC Relator](https://www.loc.gov/marc/relators/relaterm.html) codes. Field names follow
the [DCMI Metadata Terms](https://www.dublincore.org/specifications/dublin-core/dcmi-terms/), so an export to library
formats later needs no translation of concepts. `printer` is the printing house, `censorship` the censor's permit
that pre-reform Russian books print, and `printed_pagination` the pagination as a catalogue states it, such as
"XII, 340 p., 8 l. of plates". Languages are ISO 639-3 codes.

The project fields are these:

- `id`, `owner_id` (the owning account), `created_at` and `updated_at`.
- `cover_page_id`, the page whose thumbnail the project list shows, or empty for the first page.
- `image_policy`, how the images of page versions are stored, `compact` or `lossless`, described with page versions
  below.
- `page_count` (pages of the book without the excluded ones), `source_count` and `scan_count`, computed when read
  and returned in `ProjectOverview`.

### Source

One file is one source. The model of a source is the same for every kind: a kind, its files, technical metadata and
an ordered list of scans. The one exception is an indirect DjVu document, whose index file and page files are not
documents on their own, so the whole set is stored as one source with several files. The metadata of a file stays in
its source and is never copied into the project, and the description values a source suggests fill only empty fields
of `BookDetails`.

| Upload                                                  | Sources             | Scans in a source          |
| ------------------------------------------------------- | ------------------- | -------------------------- |
| One PDF holding the whole book                          | 1                   | one per PDF page           |
| A book in three PDF parts                               | 3                   | one per page of each part  |
| One bundled DjVu document                               | 1                   | one per page               |
| An indirect DjVu document: an index and 300 page files  | 1, with 301 files   | 300                        |
| 300 single-page DjVu files                              | 300                 | 1                          |
| A directory of 400 TIFF, JPEG or PNG scans              | 400                 | 1                          |
| A multi-page TIFF                                       | 1                   | one per frame              |
| A separate `cover.jpg` from another copy                | 1                   | 1                          |

| Field           | Type                   | Meaning                                                                            |
| --------------- | ---------------------- | ---------------------------------------------------------------------------------- |
| `id`            | `SourceId`             | Identifier of the source, also the name of its storage directory                   |
| `project_id`    | `ProjectId`            | Project owning the source                                                          |
| `kind`          | `SourceKind`           | `pdf`, `djvu` or `image`                                                           |
| `file_type`     | `FileType`             | Exact format: `pdf`, `djvu`, `tiff`, `jpeg`, `jpeg-2000`, `png`                    |
| `file_name`     | `str`                  | Name the browser sent, with the relative path of a directory upload sanitised      |
| `files`         | `Sequence[SourceFile]` | Stored files with name, size and SHA-256; more than one only for indirect DjVu     |
| `size_bytes`    | `int`                  | Total size of the files                                                            |
| `sha256`        | `str`                  | Hash of the main file, which refuses a second upload of the same file              |
| `scan_count`    | `int`                  | Number of scans                                                                    |
| `metadata`      | `MetadataMap`          | Technical metadata of the format                                                   |
| `suggestion`    | `MetadataSuggestion`   | Values of the book description found in the file                                   |
| `import_job_id` | `JobId \| None`        | Import job that created the source                                                 |
| `imported_at`   | `datetime`             | When the source became part of the project                                         |

Each format reads its technical metadata with its own library:

| Format                     | Library                                    |
| -------------------------- | ------------------------------------------ |
| PDF                        | PyMuPDF (`pymupdf.Document`)               |
| DjVu                       | DjVuLibre (`djvused`, `ddjvu`, `djvutxt`)  |
| TIFF, JPEG, JPEG 2000, PNG | Pillow (`PIL.Image`)                       |

A PDF source records the PDF version, the Info dictionary (Title, Author, Subject, Keywords, Creator, Producer and
the dates), whether it has XMP metadata, the page count, the page labels, the outline, whether the file had to be
repaired and whether it is encrypted. XMP is parsed with defusedxml. A DjVu source records whether the document is
bundled or indirect, the page count, the `print-meta` metadata, the `print-outline` outline and whether it has a text
layer, and it suggests a publication year only from `print-meta`. An image source records the format, the Pillow
mode, the frame count, the compression, the ICC profile, the EXIF data with `Orientation`, and the DPI.

### Scan

A scan is one image of a source as the file holds it. It is created by the import, never changes and holds the facts
of the image in `ScanFacts`. The source file of a scan is known from its source, so `ScanFacts` has no field naming
it.

| Field          | Type         | Meaning                                                                                  |
| -------------- | ------------ | ---------------------------------------------------------------------------------------- |
| `id`           | `ScanId`     | Identifier of the scan                                                                   |
| `project_id`   | `ProjectId`  | Project owning the source, so the scans of a book are listed without its sources         |
| `source_id`    | `SourceId`   | Source holding the scan                                                                  |
| `number`       | `int`        | Number of the scan in its source from zero: the PDF or DjVu page, the TIFF frame         |
| `source_label` | `str`        | Page label the file itself gives, such as the PDF page label `xii`, or empty             |
| `facts`        | `ScanFacts`  | Size in pixels, DPI, physical size, colour mode, bit depth, text layer, `extra`          |
| `renditions`   | `Renditions` | Readiness and version of the derived files                                               |

A scan has four derived files, and a page version has the same four:

- `full` at native resolution, in the format the project's `image_policy` selects. A bilevel image is always a 1-bit
  PNG, and a gray or colour image is a JPEG under `compact` and a PNG under `lossless`. A PDF page or an image file
  that already is a fitting JPEG is copied byte for byte.
- `preview`, 2048 px on the longer side, for interactive previews of steps within the budget of under 1 s on the
  visible page.
- `thumb`, `imaging.thumbnail_long_side_px` on the longer side, 320 px by default.
- `iiif`, an IIIF Image API 3 level 0 tile pyramid cut by pyvips `dzsave`.

`preview` and `thumb` are always JPEG.

### Page

A page of the book is created from a scan by the page split, or added by the user in the page order stage, and from
then on it stands on its own. It has its own copy of the image, its own versions per step and its own metadata, and
nothing about it depends on the kind of source. Its link to a scan records where it came from and is needed only to
split the scan again. The identity of a page is stable: reordering, splitting again and re-running stages keep its
`PageId`, so its storage keys, manual edits and recognised text stay attached to it.

| Field                      | Type             | Meaning                                                                       |
| -------------------------- | ---------------- | ----------------------------------------------------------------------------- |
| `id`                       | `PageId`         | Identifier of the page                                                        |
| `project_id`               | `ProjectId`      | Project owning the page                                                       |
| `order_key`                | `str`            | Position in the book as a fractional index string                             |
| `label`                    | `str`            | Printed number, such as `xii`, `12` or `[4]`, or empty for an unnumbered page |
| `kind`                     | `PageKind`       | Role of the page in the book                                                  |
| `origin`                   | `PageOrigin`     | Where the image comes from: `scan`, `blank` or `placeholder`                  |
| `scan_id`                  | `ScanId \| None` | Scan of origin of a `scan` page, or empty                                     |
| `slot`                     | `int`            | Part of the scan the page shows                                               |
| `included`                 | `bool`           | Whether the page is part of the book                                          |
| `notes`                    | `str`            | Notes of the user                                                             |
| `created_at`, `updated_at` | `datetime`       | When the page was created and last changed                                    |

`PageKind` is `cover`, `back-cover`, `endpaper`, `frontispiece`, `title`, `text`, `plate`, `blank` or `other`. A
`scan` page holds a copy of a part of a scan, a `blank` page holds a generated blank leaf, and a `placeholder` holds
no image and waits for a scan. `scan_id` is empty for the last two, and for a `scan` page whose source was deleted.
`slot` is `0` for the whole scan, `1` and `2` for the left and right halves of a spread, and higher for fold-outs.
`included` is off for a page kept out of the book, such as a colour chart or a duplicate. The pair
`(scan_id, slot)` is unique, so one part of a scan never becomes two pages. The life cycle of a page follows these
rules:

- After an import the page split creates pages from the new scans. When the split is skipped, every scan gives one
  page with `slot = 0`, whose base version is a copy of the scan's `full` image. Pages of new scans are appended to
  the end of the book in upload order, and their printed number is the scan's `source_label`.
- Splitting a spread keeps the existing page as the left half (`slot = 1`) and inserts the right half (`slot = 2`)
  right after it. Each half gets its own copy of its part of the scan. Moving the split line recreates the base
  versions of both pages and marks their later stages stale. Undoing the split deletes the right page with its
  versions and edits, so the interface asks for confirmation.
- In the page order stage a blank leaf (`origin = blank`), such as the back of the cover, an endpaper or the empty
  leaf after the title, gets a generated white image of the median page size of the book and then passes the stages
  like any page. A missing page, cover or title page without a scan (`origin = placeholder`) stays without an image
  until a scan from a new source is bound to it, and then it becomes a `scan` page whose base version is a copy of
  that scan.
- Deleting a source leaves the pages of the book alone, because they hold their own copies of the images. The pages
  of that source lose their `scan_id`, and splitting them again is no longer possible.

The split line of a spread is stored as the `transform` of the base version, so moving it recreates only the base
versions of the two pages and marks their later stages stale. From then on the two pages live independently of each
other and of the scan.

### Page versions

Stages are made of steps, and a step is one processor of the stage's recipe. A page version is recorded per step and
not per stage, because binarisation and despeckling are separate results, as the processing plugins below require.
The stages follow ScanTailor's order: split, deskew, content selection, cleanup, output.

A version is not a new page but a record of a page, a stage, a step and its parameters. A page object per stage would
have to carry the number, the order and the edits across every re-run, and the question "what is page 12 after
deskewing" would become a search along a chain. Versions never change: a re-run with other parameters creates a new
version, and the old one stays cached and returns at once when the user restores the old parameters.

| Field        | Type                    | Meaning                                                                          |
| ------------ | ----------------------- | -------------------------------------------------------------------------------- |
| `id`         | `PageVersionId`         | Hash of what produced the version, which is also the cache key of its result     |
| `page_id`    | `PageId`                | Page of the book                                                                 |
| `stage`      | `Stage`                 | Stage of the step                                                                |
| `processor`  | `ProcessorRef`          | Key and version of the processor, such as `geometry.deskew` 1.2                  |
| `input_id`   | `PageVersionId \| None` | Input version; empty for the base version, whose input is a scan or nothing      |
| `params`     | `MetadataMap`           | Parameters of the step, following the processor's JSON Schema                    |
| `transform`  | `Transform`             | Transform of coordinates from the input to the output                            |
| `data`       | `MetadataMap`           | Data of the step: angle, frames, removed areas, thresholds, confidence           |
| `renditions` | `Renditions \| None`    | The image in the same four sizes as a scan, or empty for a step without an image |
| `state`      | `VersionState`          | `pending`, `running`, `ready` or `failed`                                        |
| `created_at` | `datetime`              | When the version was created                                                     |

The identifier hashes the page, the processor key and version, the parameters, the input version and the manual
edit, cut to 16 hexadecimal digits. A `Transform` is `identity`, `crop(quad)`, `rotate(angle)`, `perspective(quad)`
or `mesh(key)`.

The first version of every page is its base version. For a page cut from a scan the `page-split` stage creates it,
with the step `split.spread` for a half of a spread or `split.none` when the split is skipped and the page is the
whole scan. For a blank leaf the `page-order` stage creates it with the step `pages.blank`. The base version holds its
own copy of the image, so the page does not depend on the files of the scan and survives the deletion of its source.

Metadata of the whole page lives in `Page`, and data of one step lives in `PageVersion.data` and
`PageVersion.transform`. The chain of transforms from the scan to any version maps coordinates back to the scan,
which is how recognised words are highlighted on the original and illustrations are cut for typesetting from the
colour scan instead of the binarised page.

| Stage         | Step                   | `transform`                | `data`                                                      |
| ------------- | ---------------------- | -------------------------- | ----------------------------------------------------------- |
| `page-split`  | `split.none`           | `identity`                 | copy of the whole scan, split skipped                       |
| `page-split`  | `split.spread`         | `crop(quad)` of a half     | position of the spine, confidence                           |
| `page-order`  | `pages.blank`          | `identity`                 | size of the generated blank leaf                            |
| `geometry`    | `geometry.deskew`      | `rotate(angle)`            | angle in degrees, method, confidence                        |
| `geometry`    | `geometry.perspective` | `perspective(quad)`        | four corners of the page                                    |
| `geometry`    | `geometry.dewarp`      | `mesh(key)`                | key of the mesh, root mean square error                     |
| `geometry`    | `geometry.crop`        | `crop(quad)`               | content frame, margins                                      |
| `cleanup`     | `cleanup.despeckle`    | `identity`                 | number of removed specks, key of the mask `mask.png`        |
| `cleanup`     | `cleanup.binarize`     | `identity`                 | method (Otsu, Sauvola), threshold or window                 |
| `cleanup`     | `cleanup.eraser`       | `identity`                 | key of the mask from the manual edit                        |
| `layout`      | `layout.regions`       | `identity`, no image       | text and illustration regions as polygons                   |
| `recognition` | `recognition.ocr`      | `identity`, no image       | engine, model, confidence, key of the hOCR or ALTO text     |

The chain of versions of one page looks like this. The left half of a spread passes the split, deskewing, cropping
and cleanup, and each step refers to the one before. Recognition continues the same chain with a version without an
image.

```mermaid
flowchart TD
    C["Scan 12 of kniga-ch1.pdf<br/>full, preview, thumb, iiif"] --> A["page-split: split.spread<br/>slot 1, quad of the left half"]
    A --> B["geometry: geometry.deskew<br/>rotate 0.8°"]
    B --> D["geometry: geometry.crop<br/>content frame"]
    D --> E["cleanup: cleanup.despeckle<br/>mask.png of removed specks"]
    E --> F["cleanup: cleanup.binarize<br/>Sauvola, window 31"]
    style C stroke:#1e7a4d,stroke-width:2px
```

The current version of a stage is kept in a separate `PageStage` record per page and stage: a reference to the
latest version of the stage's active recipe and a state, `fresh`, `stale` or `failed`. A change of an earlier version
marks the later stages of that page stale without deleting them, so the interface can show the old result until it
is recomputed. Recipes, variants and manual edits (`PageEdit`) are described under the processing plugins.

The format of `full` is set by the project setting `image_policy`, chosen when the project is created or in the stage
that creates pages:

- `compact`, the default: a bilevel page is a lossless PNG, and a gray or colour page a JPEG with the quality from the
  settings. A PNG of a colour scan at 600 DPI takes hundreds of megabytes per page, which is why this is the default.
- `lossless`: every version is a lossless PNG, so re-encoding between steps loses nothing. A colour book with five
  steps takes tens of gigabytes.

Changing the setting after pages exist applies only to new versions, and old ones are not re-encoded.

## Ports

Ports are abstract base classes, so every adapter names its parent explicitly and the type checkers verify it.

| Group       | Ports                                                                                            |
| ----------- | ------------------------------------------------------------------------------------------------ |
| Persistence | `Repository[EntityT, IdT]` and one child per aggregate, `UnitOfWork`                             |
| Ordering    | `OrderKeys`: the key after the last page and the key between two neighbours                      |
| Storage     | `SourceStore` (uploads and the files of each source), `AssetStore` (derived files by key)        |
| Mail        | `Mailer`                                                                                         |
| Imaging     | `SourceInspector` (group an upload into sources, inspect one), `PageRasterizer`, `Tiler`         |
| AI engines  | `TextRecognizer`, `LayoutAnalyzer`, `LanguageModel`, each with an engine catalogue               |
| Processing  | `Processor` (the plugin contract), `ProcessorCatalog`                                            |
| Runtime     | `JobQueue`, `EventPublisher`, `EventStream`, `Clock`                                             |

Every repository method takes the acting account, so a query can never cross account boundaries.

The persistence ports address data through the source, the scan and the page of the book. `ProjectRepository`,
`SourceRepository`, `ScanRepository`, `PageRepository`, `PageVersionRepository` and `JobRepository` share the
`UnitOfWork`, so one use case changes all of them in one transaction. `PageRepository` addresses a page by its
`PageId`, lists the pages of a project in `order_key` order and gives the last key of a book, and the position of a
page in the book is computed when it is read, never stored. `ProjectRepository.overview` counts the book of one
project, and the project listing counts every project of a window in the same query.
`OrderKeys` is a port with an adapter on fractional-indexing, because the domain imports only the standard library
and attrs. Like `Clock.now`, its methods are synchronous, because they compute a string and wait on nothing.

The storage ports divide the files of a project by prefix. `SourceStore` owns `incoming/` and `sources/`, and
`AssetStore` owns `assets/`, so each port can delete everything of a project it holds:

| Port and method                                 | Target                                                                        |
| ----------------------------------------------- | ----------------------------------------------------------------------------- |
| `SourceStore.stage`                             | `(project_id, job_id, files, max_bytes) -> Sequence[SourceFile]`              |
| `SourceStore.promote`                           | `(project_id, job_id, source_id, names)`                                      |
| `SourceStore.discard`                           | `(project_id, job_id)`                                                        |
| `SourceStore.staged_files`, `source_files`      | Local paths of the files of one job, or of one source                         |
| `SourceStore.delete_source`                     | `(project_id, source_id)`                                                     |
| `SourceStore.delete_project`                    | Removes `sources/` and `incoming/` of a project                               |
| `AssetStore.writable`, `readable`               | Only keys under `projects/<id>/assets/`                                       |
| `AssetStore.delete_prefix`                      | Only prefixes under `projects/<id>/assets/`                                   |
| `AssetStore.delete_project`                     | Removes `assets/` of the project                                              |

`stage` reports the name, the size and the SHA-256 digest of every staged file as a `SourceFile`, computed while the
upload streams in, so no service reads files itself to learn their size or to refuse a duplicate. It no longer refuses
an upload to a project that has sources. `promote` moves the files of one source from the job's directory to the
source's own directory in one rename, and a refused promotion leaves the files staged.

The imaging ports work on one source at a time. `SourceInspector.group(files)` splits the files of an upload into
sources and assembles an indirect DjVu document from its index file and page files, because which files make one
source is a property of the format. `SourceInspector.inspect` describes one source and its scans, and
`PageRasterizer.extract` writes one scan of one source.

## Adapters

| Port family | Adapter now                                                     | Test adapter   | Later                  |
| ----------- | --------------------------------------------------------------- | -------------- | ---------------------- |
| Persistence | advanced-alchemy repositories on SQLAlchemy 2.0, aiosqlite      | in-memory      | PostgreSQL by URL only |
| Ordering    | fractional-indexing                                             | the same       |                        |
| Storage     | Local directory tree under `data/`                              | local, tmp dir | S3-compatible storage  |
| Mail        | Log mailer, aiosmtplib over SMTP                                | recording fake |                        |
| Imaging     | Source reader with PDF, image, DjVu formats, pyvips tiler       | fake images    | remote workers         |
| AI engines  | pydantic-ai for cloud and Ollama models, local Surya, Tesseract | scripted fakes | more providers         |
| Jobs        | Taskiq with the in-process broker                               | inline runner  | Taskiq with Redis      |
| Events      | In-process broadcast                                            | in-memory      | Redis pub/sub          |

The storage ports hand out local paths for the imaging libraries to read and write, so the storage test adapter is
the local one over a temporary directory rather than an in-memory store.

One `SourceReader` implements both `SourceInspector` and `PageRasterizer`, and hands each call to the `SourceFormat`
registered for the `SourceKind` of the source: `PdfFormat` (PyMuPDF), `ImageFormat` (Pillow) and `DjvuFormat`. Each
format holds the grouping, the inspection and the scan extraction of its kind, in a module of its own under
`adapters/imaging/`. `group` finds the kind of every file from its `FileType` and lets the format of that kind decide
which of its files make one source; by default every file is a source of its own. A PDF source is one file whose
pages are its scans, and an image source is one file whose TIFF frames are its scans, a JPEG, JPEG 2000 or PNG file
holding one. The imaging provider registers the formats, and the reader refuses to start unless every kind has
exactly one. Supporting another kind of source is a new `SourceKind` member, a new format and one entry in the
provider. `DjvuFormat` refuses every source for now, so a DjVu upload is accepted and fails its import with a clear
message until reading DjVu pages is written. Until then it also keeps the default grouping, because only the index
file of an indirect document names its page files, in a directory DjVuLibre decodes, so assembling an indirect
document arrives with reading DjVu.

The persistence adapter keeps its table classes private and maps rows to domain entities in one mapper per entity.
Each port repository wraps an advanced-alchemy `SQLAlchemyAsyncRepository`, so generic queries come from the library
and each repository adds only its own. The database's checks surface as domain errors naming the keys involved: a
missing row or a missing parent row as `NotFoundError`, and a key already stored as `ConflictError`. The port states
both, so the in-memory adapter raises the same errors.
The library's audit columns are not used, because the domain sets `updated_at` through its `Clock`.
Tables are SQLAlchemy 2.0 declarative classes on advanced-alchemy's `DefaultBase`, which brings the shared metadata
and the portable `GUID`, `DateTimeUTC` and `JsonB` column types. Keys are declared on each table, and a project's
sources, pages and jobs are relationships with `lazy="raise"`, so an `AsyncSession` never loads them implicitly, and
with `passive_deletes=True`, so their deletion is left to the `ON DELETE CASCADE` foreign keys.
Adapters are selected by settings read once in `app` (`BOOKREVIVER_DATABASE_URL`, `BOOKREVIVER_STORAGE`,
`BOOKREVIVER_JOB_BROKER` and so on).

### Database tables

The first diagram shows the book and its sources and leaves out the link from `projects` to `jobs`, and the second
shows the processing tables of page versions. The `recipes` table belongs to the project and is left out of the
second diagram. The diagrams show only keys and links, and the list below them gives the constraints and columns.

```mermaid
erDiagram
    projects }o--|| user : "owned by"
    projects ||--o{ sources : has
    projects ||--o{ pages : orders
    sources ||--o{ scans : holds
    pages }o--o| scans : shows
    sources }o--o| jobs : "imported by"
```

```mermaid
erDiagram
    pages ||--o{ page_versions : has
    pages ||--o{ page_stages : tracks
    page_stages |o--o| page_versions : head
    page_versions |o--o{ page_versions : feeds
    pages ||--o{ page_edits : has
```

- `projects` has the primary key `id`, and `owner_id` references `user.id` with `ON DELETE RESTRICT`. Its columns are
  the `BookDetails` fields, `cover_page_id`, `image_policy`, `created_at` and `updated_at`. `cover_page_id`
  references `pages.id` with `ON DELETE SET NULL`, so a deleted cover falls back to the first page, and the
  repository refuses a cover that is not a page of the project, which a key over both columns could not empty alone.
- `sources` has the primary key `id`, `project_id` with `ON DELETE CASCADE`, `import_job_id` with
  `ON DELETE SET NULL`, and a unique `(project_id, sha256)`. Its columns are `kind`, `file_type`, `file_name`, `files`
  as JSON, `size_bytes`, `sha256`, `scan_count`, `metadata` and `suggestion` as JSON, and `imported_at`.
- `scans` has the primary key `id`, `source_id` and `project_id` with `ON DELETE CASCADE`, and a unique
  `(source_id, number)`. Its columns are `number`, `source_label`, one column per field of `ScanFacts`,
  `renditions_ready` and `renditions_version`.
- `pages` has the primary key `id`, `project_id` with `ON DELETE CASCADE`, `scan_id` with `ON DELETE SET NULL`, and
  the unique pairs `(project_id, order_key)` and `(scan_id, slot)`. Its columns are `order_key`, `label`, `kind`,
  `origin`, `slot`, `included`, `notes`, `created_at` and `updated_at`.
- `jobs` keeps its columns and gains a partial unique index on `project_id`
  `WHERE kind = 'import-source' AND state IN ('queued', 'running')`, so a project runs one import at a time.
- `page_versions` has the primary key `id`, `page_id` with `ON DELETE CASCADE`, `input_id` with `ON DELETE SET NULL`,
  and an index on `(page_id, stage)`. Its columns are `stage`, `processor_key`, `processor_version`, `params`,
  `transform` and `data` as JSON, `renditions_ready`, `state` and `created_at`.
- `page_stages` has the primary key `(page_id, stage)` and `head_version_id` with `ON DELETE SET NULL`. Its columns
  are `recipe_id`, `state` and `updated_at`.
- `page_edits` has the primary key `(page_id, stage, processor_key)`. Its columns are `kind`, `geometry` as JSON,
  `mask_key` and `updated_at`.
- `recipes` has the primary key `id` and `project_id` with `ON DELETE CASCADE`. Its columns are `stage`, `name`,
  `steps` as JSON and `active`.

`order_key` holds a fractional index string from the fractional-indexing package, such as `a0`, `a0V` or `a1`, so
inserting a page between two neighbours writes one row instead of renumbering the book. Keys compare byte by byte,
which is SQLite's `BINARY` collation and needs `COLLATE "C"` on the column in PostgreSQL. The API returns the
computed position of a page and never the key.

The owner's foreign key uses `RESTRICT` because a cascade would delete the rows of the projects but not their files,
so an account is deleted only after `ProjectService` has deleted its projects with their files. Only the SQLAlchemy
adapter checks this key: its contract fixtures create `user` rows, and `test_tables.py` pins the `RESTRICT`, while
the in-memory adapter has no accounts and there is no accounts port.

The tables `sources`, `scans`, `pages` and `page_versions` come with the book model, because a page gets its base
version with its own copy of the image when it is created. `page_stages`, `page_edits` and `recipes` come with the
processing framework, through an Alembic migration. Until the first migration exists the schema changes in place,
and the local database in `./data` is created again. Migrations are applied only by hand with advanced-alchemy's
`alchemy upgrade head` command, and the application only compares the database revision with its own at start-up
and refuses to start when they differ.

## Services

| Service             | Use cases                                                                |
| ------------------- | ------------------------------------------------------------------------ |
| `AccountService`    | Account settings, provider credentials, default engines and models       |
| `ProjectService`    | List, create, read, edit the description, delete one or all with files   |
| `ImportService`     | Accept an upload and enqueue the import, run the import job step by step |
| `PageService`       | Page manifest, facts of one page, asset locations for the viewer         |
| `ProcessingService` | Recipes, previews, runs, variants, invalidation of later stages          |
| `EditService`       | Save and load manual page edits (frames, meshes, masks, regions)         |
| `JobService`        | Job state, cancellation, the event stream of a project                   |

Each service is a class constructed with the ports it needs and nothing else, and each checks that the acting
account may touch what it asks for. A use case that needs two services is composed by the caller. Registration,
sign-in, verification and password reset are fastapi-users routers, whose user manager hooks send mail through the
`Mailer` port.

## Accounts and security

Accounts use FastAPI Users instead of hand-written sign-in code, because registration, verification, password reset,
hashing, sessions and the OAuth flow are what it already maintains. The rules that are ours sit in the user manager
(`app/providers/accounts.py`) and in `app/security.py`.

- **Sign-in.** Email and password with a verified address, or Google and Facebook. A provider is enabled by
  configuring its client identifier and secret. A provider that returns no email address gets the error
  `400 OAUTH_NOT_AVAILABLE_EMAIL`.
- **Sign-in with X, future work.** X needs a fresh PKCE verifier on every authorization request, which the shared
  OAuth router cannot supply, so it returns as a router of its own. The scenario in which a user adds and confirms an
  address after a provider returned none comes back together with X.
- **Session.** An opaque token in the `bookreviver_session` cookie, which is `HttpOnly` and `SameSite=Lax`, and
  `Secure` unless `cookie_secure` is off for local development over HTTP. The database strategy stores the token in
  the `accesstoken` table, so deleting the row revokes the session at once, which a signed token cannot do before it
  expires. No tokens in browser storage.
- **CSRF.** starlette-csrf with the Double Submit Cookie pattern. The check applies to every mutating request that
  carries the `bookreviver_session` cookie, because only such a request can act as a user, and always to the six
  sign-in paths (login, register, request verification, verify, forgot password, reset password), so a forged form
  cannot sign a visitor into an account of the attacker. A mutating request without a session is not checked. OAuth
  callbacks are exempt because a provider redirects to them with a GET, and FastAPI Users compares their state with
  a cookie of its own. The frontend reads the `csrftoken` cookie and sends its value in the `x-csrftoken` header.
  A failed check is answered with a 403 RFC 9457 problem. `ProblemCSRFMiddleware` subclasses the starlette-csrf
  middleware and overrides its private `_get_error_response`, so its signature has to be checked whenever
  starlette-csrf is upgraded.
- **Rate limit.** The sign-in routes accept `10/minute;100/hour` per client address and path, and the counters are
  kept in process memory. A server behind a reverse proxy, or one that runs several processes, needs a Redis store
  and the real client address, otherwise every client shares the proxy's address and each process counts alone. The
  limit is a router dependency, `include_router(..., dependencies=[throttle])`, and not a `@limiter.limit`
  decorator, because FastAPI Users builds the routes. The empty function `count_sign_in_attempt(request)` carries
  the decorator, since SlowApi finds the request by the parameter named `request`.
- **Registered addresses stay private.** Registering an address that already has an account returns a response
  indistinguishable from a new registration and mails the owner instead. `PATCH /users/me` refuses an email change
  with 422, so an "address taken" answer cannot reveal which addresses are registered.
- **Linking a provider to an account.** A provider sign-in whose address matches an unconfirmed account joins that
  account. The address becomes confirmed and the password is replaced by an unknown one, so whoever registered the
  address before anyone proved owning it can no longer sign in with that password.
- **Password rules.** At least 12 characters, and the password must not contain the email address.
- **Deleting an account.** The user manager's `on_before_delete` hook first deletes every project of the user with
  its files through `ProjectService`, and only then is the user deleted, because the `RESTRICT` key of
  `projects.owner_id` refuses to leave projects without an owner.
- **Mail links.** Account messages link to `/verify-email`, `/reset-password` and `/sign-in` under
  `settings.public_url`. Stage 2 of the delivery plan builds these pages.
- Provider API keys are stored with `EncryptedString`, returned only masked, and decrypted only by the adapter that
  calls the provider.

## Processing plugins

A processing step is a plugin implementing the `Processor` contract. Built-in plugins live in `plugins/` and are
registered through the entry point group `bookreviver.processors` that an external package also uses, so the two are
indistinguishable to the application.

| Part of a processor | Meaning                                                                           |
| ------------------- | --------------------------------------------------------------------------------- |
| `spec`              | Key (`cleanup.despeckle`), version, title, `Stage`, scope (page or whole book)    |
| inputs and outputs  | `ArtifactKind`s it reads and writes: page image, mask, regions, text              |
| parameters          | A JSON Schema, from which the interface builds the settings form                  |
| editor              | Its `EditorKind`: none, line, rect, quad, rotation, mesh, brush mask, regions     |
| worker pool         | cpu, gpu or llm, which routes its jobs to the right workers                       |
| `run`, `preview`    | Full run, and a fast run on a downscaled page for interactive tuning              |

- A **recipe** is the ordered list of steps with parameters for one stage, saved per project, with presets per
  account. A **variant** is an alternative recipe for the same stage, and one variant per stage feeds the next.
- Every result of a page step is a **page version** with its provenance: processor key and version, parameters,
  input version and manual edit. Their hash is the version's identifier and the cache key, so re-running a recipe
  recomputes only changed steps, and changing a stage marks the later stages of the page stale. A result of a
  whole-book step, such as typesetting, is an **artifact** with the same provenance.
- Manual edits are inputs: a frame or mesh drawn by the user becomes the geometry a crop or dewarp processor reads,
  an eraser stroke becomes a mask, and the split line of a spread, drawn with the `line` editor, becomes the geometry
  of `split.spread`.
- A preview of a step runs as a background job, like every heavy computation, and its result reaches the browser
  over SSE.
- The base steps `split.none` and `pages.blank` are ordinary processors. Until the plugin framework exists, the
  import and the page order stage produce the same base versions with interim code that the processors replace.
- First plugins, in delivery order: page split, deskew, perspective crop by quad, dewarp by mesh, despeckle,
  binarisation (a cleanup step of its own, so the despeckled and the binarised page are separate artifacts), eraser
  mask, layout regions (text versus illustration), background separation, background unification (white, aged paper
  texture, custom colour, consistent across the book), recognition, proofreading.
- Heavy plugins declare optional dependency groups (`bookreviver[cv]`, `[gpu]`, `[llm]`) installed only on the
  workers of their pool, and a worker loads only the plugins of its pool.

## AI engines and models

- `TextRecognizer`, `LayoutAnalyzer` and `LanguageModel` are ports. Each engine adapter describes itself in a
  catalogue: name, languages and scripts, models, whether it needs a key or a GPU.
- Cloud language and vision models go through pydantic-ai (OpenAI, Anthropic, Google, Mistral, Ollama), with typed,
  validated structured output for proofreading and layout answers.
- Local engines (Surya, Tesseract, olmOCR served by vLLM) run on gpu or cpu workers behind the same ports.
- The account settings page sets provider keys and the default engine and model per task (recognition, layout,
  proofreading). Every run can override the engine, the model and the prompt preset from the processing panel.

## Files and assets

Storage keys, not paths, cross the ports. The local adapter maps them to `data/`, and an S3 adapter maps them to a
bucket when workers run on other machines.

Every file of a project lives under `projects/<project_id>/`, divided between the two storage ports. `SourceStore`
owns `incoming/` and `sources/`, and `AssetStore` owns `assets/`. An asset key always starts with
`projects/<id>/assets/`, so one prefix check keeps asset keys away from the files of the sources, and one
`delete_prefix` removes every derived file of a project. One class, `ProjectKeys` in `domain/keys.py`, builds every
key and refuses a `..` segment, so the layout is written down in one place and keys are stable when pages move.

- `incoming/<job_id>/` holds the upload of one import job while it is being received and checked.
- `sources/<source_id>/` holds the files of one source exactly as uploaded, written once.
- `assets/scans/<source_id>/<number>/v<n>/` holds the four renditions of one scan in version `n`.
- `assets/pages/<page_id>/<stage>/<processor>/<version_id>/` holds one page version: its renditions, a mask or
  a text such as `text.hocr`, and nothing for a step without output files.
- `assets/pages/<page_id>/edits/<processor>/` holds the manual edits of a page that steps read as inputs.
- `assets/book/` holds the results of whole-book steps, such as typesetting and export.

The layout of a book made of two PDF parts, a cover from another copy and an indirect DjVu document that supplies
missing pages looks like this:

```text
data/storage/
└── projects/
    └── 3f2a…c9/                               ProjectId
        ├── incoming/                          SourceStore: uploads still being received
        │   └── 7b1e…04/                       JobId of the import job owning the upload
        │       └── cover.jpg
        ├── sources/                           SourceStore: files as uploaded, written once
        │   ├── 0c55…a1/                       SourceId: PDF, part 1
        │   │   └── kniga-ch1.pdf
        │   ├── 9d02…17/                       SourceId: PDF, part 2
        │   │   └── kniga-ch2.pdf
        │   ├── 51aa…e3/                       SourceId: cover from another copy
        │   │   └── cover.jpg
        │   └── e4b7…60/                       SourceId: indirect DjVu, index and pages
        │       ├── index.djvu
        │       ├── p0045.djvu
        │       └── p0046.djvu
        └── assets/                            AssetStore: derived files, written once
            ├── scans/                         import stage: images of the scans
            │   └── 0c55…a1/                   SourceId
            │       ├── 0/                     number of the scan in its source
            │       │   └── v1/                version of the scan's renditions
            │       │       ├── full.jpg       native resolution
            │       │       ├── preview.jpg    2048 px on the longer side
            │       │       ├── thumb.jpg      320 px on the longer side
            │       │       └── iiif/          IIIF Image API 3 level 0 pyramid
            │       │           ├── info.json
            │       │           └── 0,0,512,512/…
            │       └── 12/
            │           └── v1/…
            ├── pages/                         pages of the book, from the split on
            │   ├── 1d4b…90/                   PageId: whole scan 11, split skipped
            │   │   └── page-split/
            │   │       └── split.none/
            │   │           └── 8c21f0d7e4a6b913/  base version: own copy of the scan
            │   │               ├── full.jpg
            │   │               ├── preview.jpg
            │   │               ├── thumb.jpg
            │   │               └── iiif/…
            │   ├── 77e0…2b/                   PageId: blank leaf after the cover
            │   │   └── page-order/
            │   │       └── pages.blank/
            │   │           └── 3a9c55e1b0f24d68/…  generated white leaf
            │   └── a81c…5d/                   PageId: left half of scan 12
            │       ├── page-split/
            │       │   └── split.spread/
            │       │       └── 4e9f0c2ab1d3e570/  base version: copy of the left half
            │       │           ├── full.jpg
            │       │           ├── preview.jpg
            │       │           ├── thumb.jpg
            │       │           └── iiif/…
            │       ├── geometry/
            │       │   ├── geometry.deskew/
            │       │   │   ├── 71c2d09e5b44a0f3/  angle 0.8°
            │       │   │   └── 0b93a1f7c2e85d19/  angle 1.1° after a manual edit
            │       │   └── geometry.crop/
            │       │       └── c3d1e8a04f77b2c6/…
            │       ├── cleanup/
            │       │   ├── cleanup.despeckle/
            │       │   │   └── 9a4e61b2d0c3f845/
            │       │   │       ├── full.jpg
            │       │   │       ├── mask.png   removed specks
            │       │   │       └── …
            │       │   └── cleanup.binarize/
            │       │       └── 2f60b7e9a15d8c07/
            │       │           ├── full.png   bilevel, PNG under any image_policy
            │       │           └── …
            │       ├── recognition/
            │       │   └── recognition.ocr/
            │       │       └── 5d17c4a9e002f6b1/
            │       │           └── text.hocr  no image
            │       └── edits/                 manual edits as inputs of steps
            │           └── cleanup.eraser/
            │               └── e1f0…/mask.png
            └── book/                          results of the whole book: typesetting, export
                └── typesetting/…
```

Deleting a source removes its files, its scans and the renditions of its scans, and leaves the pages of the book
with their copies of the images, their versions, labels and order. Deleting a project calls `delete_project` on both
storage ports and then removes its rows. Both methods treat a project without files as deleted, so a deletion that
fails part-way keeps the project, and repeating it finishes the job. Removing the rows first would leave the files of
a failed deletion where no request can reach them, because every request finds a project by its row.

Nothing stored is ever replaced: a new version gets a new directory. Derived assets are regenerable under a new key,
the next version or content hash, which their URLs carry, so browsers cache them forever. Each is written under a
hidden sibling name and moved onto its key once complete, in a step the file system refuses when the key is taken,
so a second writer gets a conflict instead of replacing the first.

A tile pyramid's `info.json` carries as `id` the path of the IIIF route that serves it, passed to the `Tiler` port,
because the viewer builds tile URLs from it, and the root of that path comes from `app`, where the routes are
mounted. The path has no scheme or host, so a change of domain, port or the address a device uses leaves the
cut pyramids valid. IIIF formally asks for an absolute URI there, but OpenSeadragon resolves a path, and BookReviver
serves its own viewer, so the path is the deliberate choice.

## Import pipeline

An import handles every file of an upload on its own, so one broken file does not cancel the others. One import
runs this way, where the purple steps are done by the storage ports and the green one by the imaging ports:

```mermaid
flowchart TD
    U(["POST /projects/id/sources<br/>N files"]) --> St["SourceStore.stage<br/>→ incoming/job/"]
    St --> Ch{{"for each file:<br/>new SHA-256 and readable?"}}
    Ch -- no --> R["rejected file,<br/>reason kept in the job"]
    Ch -- yes --> S["Source row,<br/>promote → sources/id/"]
    S --> C["Scan rows, full,<br/>iiif, preview, thumb"]
    C --> P["page-split skipped:<br/>one page per new scan"]
    style St stroke:#5b3fd1,stroke-width:2px
    style S stroke:#5b3fd1,stroke-width:2px
    style C stroke:#1e7a4d,stroke-width:2px
```

1. `POST /api/v1/projects/{id}/sources` streams the upload into `incoming/<job_id>/`, records an import job and
   enqueues it, and the response returns the job at once. The frontend offers two ways to choose files, a whole
   directory or individual files, and both reach the API as the same list of files, so the backend has one upload
   path for both. An upload holds at most `max_upload_files` files, 10000 by default, and `max_upload_bytes`, 4 GiB
   by default, and a larger one is refused with an RFC 9457 problem.
2. `SourceInspector.group` splits the upload into sources. `FileType.from_name` decides the type of every file, and
   any mix of types is accepted, because each file is a source of its own. An indirect DjVu document is assembled
   from its index file, and one that lacks some of its page files is rejected as a whole, with the list of missing
   files, while the other files of the upload are still imported. The files are taken in the natural order of their
   names, so `part2.pdf` precedes `part10.pdf`, and this order becomes the order of their pages in the book.
3. A file whose SHA-256 is already stored in the project is rejected with the name of the existing source.
4. Every source is inspected, promoted to `sources/<source_id>/` and committed with its scans in a transaction of its
   own. A job retried after a crash skips the sources already committed, and deletes only the version directories of
   scans that are not marked ready.
5. Every scan gets `full`: a scanned PDF page whose content is one fitting JPEG image, or an image file that is one,
   is copied byte for byte, and anything else is rasterised once at its native resolution. pyvips cuts `full` into
   the IIIF pyramid, the preview and the thumbnail, in a bounded pool. Each scan is marked ready as soon as it is done,
   so the viewer shows the first pages while the rest are being cut. An import job that was cancelled leaves scans
   without renditions, and the next import job of the project cuts them first.
6. The job then runs the page split skipped: every new scan becomes a page at the end of the book, with a base
   version `split.none` that holds a copy of the scan's image and the scan's `source_label` as its printed number.
   The user splits spreads and arranges the pages later, in the `page-split` and `page-order` stages.
7. Empty fields of `BookDetails` are filled from the `suggestion` of the first source that has a value for them. The
   title is never overwritten.
8. The job succeeds when at least one file was imported. Its result lists the files it skipped as `skipped`, each
   with the reason, and every step publishes progress events, which reach the browser over SSE.

A project runs one import at a time: a second upload while one is queued or running gets a 409 problem, and so does
deleting a source during an import. When the DjVuLibre tools are not installed the application starts with a
warning in its log, and every DjVu source is rejected with a message asking to install `djvulibre`.

## HTTP API

All endpoints live under `/api/v1`. The OpenAPI schema is generated from the routers and committed as
`docs/openapi.json`, a test checks that it equals `app.openapi()`, and the frontend client is generated from it.

| Area       | Endpoints                                                                                                 |
| ---------- | --------------------------------------------------------------------------------------------------------- |
| Auth       | fastapi-users routers under `/auth`: cookie login and logout, register, verify, reset, OAuth per provider |
| Account    | `GET /users/me`, `GET, PATCH /me/settings`, `GET, PUT, DELETE /me/credentials/{provider}`                 |
| Catalogue  | `GET /engines`, `GET /processors`                                                                         |
| Projects   | `GET, POST /projects`, `GET, PATCH, DELETE /projects/{id}`                                                |
| Sources    | `GET, POST /projects/{id}/sources`, `GET, DELETE /projects/{id}/sources/{source_id}`                      |
| Scans      | `GET /projects/{id}/scans`                                                                                |
| Pages      | `GET, POST /projects/{id}/pages`, `GET, PATCH, DELETE /projects/{id}/pages/{page_id}`                     |
| Page order | `POST /projects/{id}/pages/{page_id}/move`, `PUT /projects/{id}/pages/{page_id}/scan`                     |
| Edits      | `GET, PUT /projects/{id}/pages/{page_id}/edits/{stage}`                                                   |
| Processing | `GET, PUT /projects/{id}/stages/{stage}/recipe`, `POST .../preview`, `POST .../run`, `GET .../variants`   |
| Jobs       | `GET /jobs/{id}`, `DELETE /jobs/{id}`, `GET /projects/{id}/events` as SSE                                 |
| Images     | `GET /iiif/{asset}/...` as immutable static files                                                         |

On a server, a reverse proxy serves `/iiif` straight from disk or object storage, after an access check by the API.

Pages are addressed by their `PageId`, never by their position. `POST /projects/{id}/pages` adds a placeholder or a
blank leaf, `POST .../move` moves a page to another position, and `PUT .../scan` binds a scan to a placeholder. A
scan that the import already made into a page of its own is bound with the flag `take_over`, which moves the scan
from that page to the placeholder. `POST /projects/{id}/source`, a `source` field of the project schema and page
addresses by index are never published, because the frontend client is generated from the OpenAPI schema and would
carry them.

The page manifest, `GET /projects/{id}/pages`, returns up to 1000 pages per request through its own `Params`, with
the computed position of every page and never its order key. Excluded pages are returned with `included` false, and
the viewer asks for `?included=true`.

The events of a project reach the browser over `GET /projects/{id}/events`:

- `JobChanged` when a job changes state or progress.
- `SourceImported` when a source and its scans are committed.
- `ScanReady` when the renditions of a scan can be shown.
- `PagesChanged` when pages are added, removed or moved, or their labels or kinds change.
- `PageVersionReady` when a page version is ready.
- `ProjectChanged` when the book description changes.

The project list counts in `page_count` the included pages of the book, and shows the number of scans separately.

### Request and response conventions

- Every input is a Pydantic model or a constrained `Annotated` type, validated by FastAPI before the route runs:
  bodies as models, query parameters as a model bound with `Annotated[Model, Query()]`, forms with `Form()`, headers
  with `Header()`, uploads as `UploadFile` with the file set checked by a model of their names and sizes. Handlers
  never parse or check raw input by hand.
- Constraints are declared, not coded: `Field` limits, `StringConstraints`, enums, `EmailStr`, `AnyHttpUrl`, and
  `field_validator` or `model_validator` only for a rule no declaration expresses. Reused constrained types (a title,
  a year, a page number) are defined once in `api/schemas/types.py`.
- Two base classes carry the shared configuration, so no schema repeats it. `RequestModel` forbids unknown fields and
  strips whitespace. `ResponseModel` reads attributes, so a schema is built straight from a domain object with
  `model_validate`.
- Following FastAPI's guide: no `...` as a default, no `RootModel` (an `Annotated` list with `Field` instead), and
  `Annotated` for every parameter and dependency.
- `PATCH` follows JSON Merge Patch, RFC 7396: a field left out stays as it is, and `null` clears it to the value a
  new resource has for it. The title of a book cannot be cleared. The body is sent as `application/merge-patch+json`
  or `application/json`. The service receives a domain change such as `BookDetailsChanges`, in which None keeps a
  field, so clearing needs no third state in the domain.
- Pydantic stays at the edges: request and response schemas in `api`, settings in `app`. Domain invariants are
  `attrs` validators, so the core does not depend on Pydantic.
- Every route declares a typed Pydantic response. The shapes are the same everywhere: a single resource is its
  schema, a collection is fastapi-pagination's `Page[T]` (`items`, `total`, `page`, `size`, `pages`), and every error,
  including validation errors, is an RFC 9457 problem (`application/problem+json` with `type`, `title`, `status`,
  `detail`, `instance`) produced by fastapi-problem.
- Domain errors map to problems in one place, the exception handler registered in `app`. Routes never build error
  responses themselves.
- Status codes come from `fastapi.status` or `http.HTTPStatus`, never as number literals. A pre-commit `pygrep` hook
  rejects a literal status code in Python code.
- Services return domain objects and `Slice` results (items and total), and the API maps them to schemas and pages, so
  paging never reaches into a database query from a route.

## Frontend

- React 19, TypeScript, Vite, TanStack Router (file-based routes) and TanStack Query, shadcn/ui on Radix with
  Tailwind CSS 4, lucide icons. Biome lints and formats, `tsc --noEmit` checks types, Vitest runs unit tests,
  Playwright runs end-to-end tests. FastAPI serves the built application through `app.frontend()`.
- `src/api/` is generated by `@hey-api/openapi-ts`, including TanStack Query hooks, and is never edited by hand.
  `src/features/<area>/` holds one area each (auth, projects, settings, viewer, stages), and `src/shared/` holds UI
  components and hooks.
- Viewer state (page, spread, variant) lives in search params, so every view can be linked and reloaded. Server
  state lives in TanStack Query, and SSE events patch or invalidate the affected queries.
- The viewer is OpenSeadragon over IIIF tiles: one page or a two-page spread, page turns by buttons, keys, slider and
  go-to field, zoom by wheel, pinch, fit-to-width and fit-to-page, preloading of neighbouring pages, and a
  collapsible page panel.
- Editors are a react-konva layer kept in step with the OpenSeadragon viewport. An editor registry maps each
  `EditorKind` to a component: draggable frame, quad with corner handles, rotation handle, dewarp mesh, brush and
  eraser, region polygons labelled text or illustration.
- Processor parameter forms are rendered from their JSON Schema by react-jsonschema-form with its shadcn theme.
  Previews run on the visible page, and variants compare side by side or with a swipe.

## Testing

| Level          | What it proves                                       | Runs against                        |
| -------------- | ---------------------------------------------------- | ----------------------------------- |
| Domain         | Value rules and invariants                           | plain objects                       |
| Services       | Use cases, business rules, authorisation             | in-memory adapters of every port    |
| Port contracts | Every adapter behaves as its port promises           | each adapter, one shared test suite |
| Plugins        | A processor's output on reference pages              | the plugin with sample images       |
| API            | Routing, validation, auth, status codes, schemas     | the app with in-memory adapters     |
| End to end     | Sign in, upload a real PDF, tiles appear, pages turn | the full stack, Playwright          |

## Performance budgets

| Interaction                               | Budget on a laptop            |
| ----------------------------------------- | ----------------------------- |
| Turn to an already tiled page             | Under 150 ms to a sharp image |
| First page visible after an import starts | Under 3 s for a scanned PDF   |
| Interactive preview of a processing step  | Under 1 s on the visible page |
| Project list and page manifest requests   | Under 50 ms                   |
| Request handlers                          | Never block on CPU-bound work |

## Delivery plan

1. **Core, accounts, import.** Domain, ports, services, persistence and storage adapters with contract tests,
   accounts with email, Google and Facebook (ready-made httpx-oauth clients, while X is deferred until it has its own
   PKCE router), the book model with sources, scans, pages and their base versions, the JSON API for projects,
   sources and pages, the import job with tiling, SSE, import-linter. The Jinja interface is removed.
2. **Frontend shell.** Sign-in and registration, settings, project list and book details, upload with live progress.
3. **Viewer.** OpenSeadragon book viewer with spreads, navigation, zoom, preloading and the page panel.
4. **Processing framework.** Plugin contract and catalogue, recipes, page versions per step with their stage state,
   previews, variants, the editor layer, the page split and page order stages, and the first geometry and cleanup
   plugins.
5. **Layout and background.** Region detection, background separation and unification.
6. **Recognition and models.** Engine catalogue, account model settings, OCR engines, proofreading with LLMs.
7. **Typesetting and export.** Later.

Steps 2 and 3 start in parallel once the OpenAPI schema of step 1 is committed, and develop against it.

## Decisions

The book model rests on these decisions, each with its reason.

1. **One page structure per project.** Stages and recipe variants do not create pages of their own, they create
   versions of the same page, because the printed number, the order and the edits must not move with every run.
2. **A page stands on its own.** A page keeps its own copy of its base image in its own directory, so deleting a
   source does not break it. The price is about twice the space of the scans.
3. **The page split creates pages.** The split is a step that creates pages from scans, and it can be skipped. The
   split area lives in the `transform` of the base version of `split.spread`, and the `scan_id` and `slot` of a page
   only record its origin, so moving the line changes versions and never the identity of a page.
4. **Order by fractional index.** `order_key` is a fractional index string from the `fractional-indexing` package,
   so moving or inserting a page writes one row and nothing is renumbered.
5. **How pages appear.** An import with the split skipped appends one page per scan to the end of the book in upload
   order, which is predictable, and the user arranges pages in the `page-order` stage.
6. **Blank leaves and placeholders.** A blank leaf gets a generated white image of the median page size and passes
   the stages like any page, because it is a real leaf of the printed book. A missing page, cover or title page
   without a scan stays without an image until a scan is bound to it, since it has nothing to process.
7. **Printed number.** Every page has its label as a string, and no numbering ranges are stored, because printed
   numbering has exceptions that ranges would need rules for. An operation such as "number from this page in Roman
   numerals from i" writes the labels, and the import takes PDF page labels through PyMuPDF's `Page.get_label`.
8. **Storage layout.** Sources live in `sources/<source_id>/`, scans in `assets/scans/<source_id>/<number>/v<n>/`,
   and page versions in `assets/pages/<page_id>/<stage>/<processor>/<version>/`, and both storage ports have
   `delete_project`. Keys by identifier stay valid when pages move, and each port deletes a project with one prefix.
9. **Events and page count.** The events are those of the HTTP API section. `page_count` counts the included pages,
   because that is the size of the book, and the number of scans is shown separately.
10. **Owner key.** `projects.owner_id` references `user.id` with `ON DELETE RESTRICT`. A cascade would delete the
    rows of the projects but not their files, so an account is deleted only after its projects are deleted through
    `ProjectService`.
11. **Source boundary.** One file is one source, except an indirect DjVu document, because a file is what is
    uploaded, recognised as a duplicate and deleted, while the files of an indirect DjVu are no documents alone.
12. **Version granularity.** A version is recorded per step and the current version of a stage is kept in
    `PageStage`, because steps such as despeckling and binarisation are separate results the user compares.
13. **A page order stage.** `Stage` gains `PAGE_ORDER = 'page-order', 'Page order'` after `PAGE_SPLIT`, because
    ordering pages, numbering them and adding placeholders is a step of its own between the split and the geometry.
14. **Image format.** The project setting `image_policy` is `compact` by default or `lossless`, chosen when the
    project is created or in the stage that creates pages, because lossless colour pages take hundreds of megabytes
    each and not every book needs them.
15. **Deleting an account.** The `UserManager.on_before_delete` hook first deletes every project of the user with
    its files through `ProjectService`, and then the user is deleted, which the `RESTRICT` key requires.
16. **Owner key in the in-memory adapter.** Only SQLAlchemy checks the owner key: its contract fixtures create `user`
    rows and `test_tables.py` pins the `RESTRICT`. There is no accounts port, since fastapi-users owns the accounts.
17. **Version identifier.** A hash of the `page_id`, the processor key and version, the parameters, the input version
    and the manual edit, cut to 16 hexadecimal digits, so equal work gets the same identifier and hits the cache.
18. **The `OrderKeys` port.** It comes with the domain and persistence part of the book model, with an adapter on
    `fractional-indexing`, because the domain may not import a third-party library.
19. **One import at a time.** A partial unique index on `jobs (project_id)` for queued and running imports gives a
    second upload a 409, because two concurrent imports would race for the same order keys and duplicate checks.
20. **Incomplete indirect DjVu.** Such a source is rejected as a whole with the list of missing files, because it is
    not a readable document, and the other files of the upload are still imported, because they are independent.
21. **Cancelled import.** The next import job of the project first cuts the renditions of scans that have none, so
    no scan stays unviewable.
22. **Format of a scan's `full`.** As for versions: a bilevel scan is always a 1-bit PNG, and a gray or colour scan
    follows `image_policy`, so scans and pages share one rule.
23. **Upload size.** The setting `max_upload_files = 10000` sits next to `max_upload_bytes = 4 GiB`, and exceeding
    either gives an RFC 9457 problem, which bounds what one request can make the server hold.
24. **DjVuLibre not installed.** The application starts with a warning in its log, and every DjVu source is rejected
    with a message asking to install `djvulibre`, because the other kinds of source do not need it.
25. **Grouping an upload into sources.** A new port method, `SourceInspector.group(files)`, splits an upload into
    sources and assembles an indirect DjVu, because which files make a source depends on the format, which only the
    imaging adapter knows.
26. **Base steps.** `split.none` and `pages.blank` are ordinary processors, so base versions follow the same rules as
    every other version. Until the plugin framework exists, the import and the page order stage do the same with
    interim code that the processors replace.
27. **PATCH.** JSON Merge Patch by RFC 7396: a field left out does not change, `null` clears it, and the title cannot
    be cleared, because a standard format needs no schema of our own for partial updates.
28. **Page manifest.** Up to 1000 pages per request through its own `Params`, excluded pages returned with their
    `included` flag, and the filter `?included=true` for the viewer, which needs the whole book in few requests.
29. **OpenAPI schema.** It is the file `docs/openapi.json`, and a test checks that it equals `app.openapi()`, because
    the frontend client is generated from it and must not drift from the routes.
30. **Processing.** A step preview runs as a background job with its result over SSE, because heavy work runs only
    in jobs. OpenCV is the optional dependency group `bookreviver[cv]`, installed only where plugins need it. The
    split line is edited with `EditorKind.line`. Binding a scan to a placeholder takes the scan away from the page
    the import made of it with the flag `take_over`, since a pair of scan and slot belongs to one page.
31. **Sign-in with X.** It is left out, and Google and Facebook remain. X returns in a task of its own with a fresh
    PKCE verifier on every request, which the shared OAuth router cannot supply.
32. **Migrations.** They are applied only by hand with advanced-alchemy's `alchemy upgrade head` command, and the
    application at start-up only compares the revision and refuses to start on a mismatch, because a schema change
    is a deliberate step and running against the wrong schema corrupts data.

Smaller technical choices follow the same model. Languages are ISO 639-3 codes, XMP is parsed with defusedxml, the
parameters of processing jobs are kept in `Job.params`, a DjVu source suggests a publication year only from its
metadata, the result of an import lists its `skipped` files, the root of the IIIF paths comes from `app`, and deleting
a source during an import gets a 409.
