# BookReviver

A workbench for digitising old printed books, with a focus on pre-reform Russian and Belarusian orthography. Each
book moves through stages: import, page split, cleanup, alignment, recognition and layout of a new
printed edition. Every stage stays viewable at any time, so an earlier stage can be corrected and the later ones rerun.

The application is a JSON API with a React frontend. `docs/openapi.json` describes the API.

## What to install

- [uv](https://docs.astral.sh/uv/) runs the backend and installs its Python. Install it before anything else, following
  the uv documentation.
- [Node](https://nodejs.org/) 22.18 or newer, in an even-numbered release line (22, 24, 26 and so on), builds the
  interface. Install it before the first start, and again after the frontend changes.
- DjVuLibre reads DjVu books. Install it before the first start, or at the latest before the first DjVu upload, with
  `sudo scripts/install-system-deps.sh`.
- OpenCV, the optional group `cv`, runs the processing steps that split a spread and straighten a page. Install it only
  when you use those steps, with `uv sync --extra cv`.
- An SMTP server sends real confirmation and password reset mail. It is optional, and without it the mail is written to
  the server log. Set `BOOKREVIVER_MAIL__*` in `.env`, see [Mail](#mail).
- Google and Facebook keys switch on the sign-in buttons of the two providers. They are optional, until other people
  use the server. Set `BOOKREVIVER_AUTH__GOOGLE__*` and `BOOKREVIVER_AUTH__FACEBOOK__*` in `.env`, see
  [Sign-in with Google and Facebook](#sign-in-with-google-and-facebook).

Everything except DjVuLibre comes from the package managers of the project. DjVuLibre is a system package, so no Python
or Node command can bring it.

## First start, step by step

Run these once on a new machine, in this order, from the root of the repository.

1. **System packages.** Install DjVuLibre before you start the server, because the server looks for it once and keeps that answer until it restarts.

   ```bash
   sudo scripts/install-system-deps.sh
   ```

   The script picks `zypper`, `apt-get` or `dnf`, installs the package and checks that `djvused`, `djvudump` and `ddjvu`
   are on the `PATH`. Skip this step when you never upload DjVu books. The other kinds of source do not need it.

2. **Backend packages.**

   ```bash
   uv sync
   ```

3. **Settings.** The secret signs the confirmation and reset tokens and the CSRF cookie, so it has to be set.

   ```bash
   cp .env.example .env
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Paste the printed text as the value of `BOOKREVIVER_AUTH__SECRET` in `.env`. Every other setting has a default, and
   the file lists them all.

4. **Database.** The application never changes the schema of its catalogue itself, so create it by hand. Every book
   gets a database file of its own in its directory, which the application makes and migrates itself.

   ```bash
   uv run bookreviver-migrate upgrade head
   ```

   The command asks for a confirmation, and `--no-prompt` skips the question.

5. **Interface.**

   ```bash
   npm --prefix frontend ci
   npm --prefix frontend run build
   ```

6. **Start.**

   ```bash
   uv run fastapi dev
   ```

   Open http://127.0.0.1:8000. The first account is made on the registration screen, and the confirmation link goes to
   your mailbox when SMTP is set and to the server log otherwise. The database, the uploaded books and the render
   cache live in `data/`.

## After you pull new code

- A new file in `src/bookreviver/adapters/persistence/sqlalchemy/migrations/catalogue/versions/` needs
  `uv run bookreviver-migrate upgrade head`, after copying `data/` if it holds anything worth keeping. Until then the
  server refuses to start and names this command. A new revision of a book needs nothing: each book is migrated when it
  is opened.
- A `data/` of the releases that kept everything in one `data/bookreviver.db` needs
  `uv run bookreviver-migrate split-shared-database` once, after `upgrade head` with the last of those releases. The
  command fills the catalogue and gives every book its own database, and leaves `bookreviver.db` as it was.
- A `data/storage/projects/` directory, where the earlier releases kept the books, needs
  `uv run bookreviver-migrate move-book-directories` once, with the server stopped, together with `upgrade head` for the
  catalogue. The command renames every book directory into `data/storage/books/`, refuses to overwrite one that is
  there, and does nothing when `projects/` is gone, so running it again is harmless.
- A change of `pyproject.toml` or `uv.lock` needs `uv sync --extra cv`. Plain `uv sync` removes OpenCV, and the server
  then starts without the steps that split a spread and straighten a page. With the pre-commit hooks installed, the
  `uv-sync` hook does this by itself after every checkout, merge and rebase, with every optional group.
- A change under `frontend/` needs `npm --prefix frontend ci` and `npm --prefix frontend run build`.
- A new route or schema of the API needs `uv run bookreviver-openapi`, then `npm --prefix frontend run generate`, and
  both results are committed.
- A change of the system packages, see the list above, needs `sudo scripts/install-system-deps.sh`, and then the server
  is started again.

## Running

`uv run fastapi dev` restarts the server when the code changes and is meant for development. `uv run fastapi run` is the
same server without the reload. Settings are read from `BOOKREVIVER_*` environment variables or a `.env` file.

The processing steps `split.spread` and `geometry.deskew` need OpenCV, the optional group `cv`. Start the server with
the group, so that `uv` keeps it installed:

```bash
uv run --extra cv fastapi dev
```

A plain `uv run` or `uv sync` installs the environment without the group and removes OpenCV again, so do not mix the
two forms in one checkout. The rest of the application works without it.

## Frontend

The interface is a React application in `frontend/`. The server serves the build when the directory in
`BOOKREVIVER_FRONTEND_DIR` (default `frontend/dist`, relative to where the server starts) exists, and the API alone
otherwise. A confirmation mail that has no SMTP server to go through is written to the server log, and its link opens
`/verify-email` of the application.

The dependencies are installed with `npm --prefix frontend ci`.

```bash
npm --prefix frontend run dev     # the Vite server on http://127.0.0.1:5173, proxying /api to port 8000
npm --prefix frontend run check   # Biome, the type check and the unit tests
npm --prefix frontend run generate  # rebuilds frontend/src/api from docs/openapi.json
```

`npm --prefix frontend run e2e` builds the frontend and runs the Playwright scenarios against a real server, started
on port 8765 with an empty database in `frontend/.e2e-data`, which is also where the confirmation mail is read from.
It needs Playwright's Chromium (`npx --prefix frontend playwright install --with-deps chromium`), or another one named
in `BOOKREVIVER_E2E_CHROMIUM`.

The scenario files run side by side on the one server and its database, three at a time. `BOOKREVIVER_E2E_WORKERS`
sets how many, and `1` runs them one after the other. Every scenario registers an account of its own and finds its
confirmation link in the log by the address it registered, so the scenarios do not depend on each other. The server of
the end-to-end run lifts the limit on sign-in attempts, which production keeps at ten a minute for each address.

A scenario that fails keeps a screenshot and a trace, and a scenario can capture a key state with `snap` from
`frontend/e2e/support/account.ts`. All of them land in the folder of the scenario under `frontend/test-results/`.
A trace opens with `npx --prefix frontend playwright show-trace frontend/test-results/<scenario>/trace.zip`.

## Mail

The application mails a link to confirm an address, to reset a password, and a notice to an address that someone
tried to register twice. The links open `/verify-email`, `/reset-password` and `/sign-in` under
`BOOKREVIVER_PUBLIC_URL`, the address the application is reached at from outside (default `http://127.0.0.1:8000`).
Mail is sent through SMTP when `BOOKREVIVER_MAIL__SMTP_HOST` is set:

| Variable                         | Default                                | Meaning                                                    |
| -------------------------------- | -------------------------------------- | ---------------------------------------------------------- |
| `BOOKREVIVER_MAIL__SMTP_HOST`    | empty                                  | SMTP server; empty writes the messages to the server log   |
| `BOOKREVIVER_MAIL__SMTP_PORT`    | `587`                                  | Port of the server, 587 for submission with STARTTLS       |
| `BOOKREVIVER_MAIL__SMTP_USERNAME`| empty                                  | User name, or empty for a server that needs none           |
| `BOOKREVIVER_MAIL__SMTP_PASSWORD`| empty                                  | Password, never printed or logged                          |
| `BOOKREVIVER_MAIL__SENDER`       | `BookReviver <no-reply@localhost>`     | From address of every message                              |

While the host is empty, every message, its link included, is written to the server log at info level, so a link can
be copied from the terminal. With a host, STARTTLS is used whenever the server offers it, and the server is
logged in to when a user name and a password are given.

**Gmail.** Sign-in to SMTP with the account password does not work for Gmail. Turn on 2-step verification for the
Google account, create an app password for it on the "App passwords" page of the account settings, and use it as the
password. Set the host to `smtp.gmail.com`, the port to `587`, the user name to the full Gmail address and the sender
to the same address:

```bash
BOOKREVIVER_MAIL__SMTP_HOST=smtp.gmail.com
BOOKREVIVER_MAIL__SMTP_PORT=587
BOOKREVIVER_MAIL__SMTP_USERNAME=you@gmail.com
BOOKREVIVER_MAIL__SMTP_PASSWORD=<the 16-character app password>
BOOKREVIVER_MAIL__SENDER=you@gmail.com
```

To switch real mail on for a local run:

1. Put the five variables in the `.env` file in the root of the repository, which git ignores, and never in a file
   that is committed. `.env.example` lists them, with the sender commented out because an empty sender would replace
   the default one.
2. Leave `BOOKREVIVER_MAIL__SMTP_HOST` out, or commented with a `#`, until the password is in place. A host without
   a password makes Gmail refuse the message, and the request that sent it fails. Without a host the messages go to
   the server log as before.
3. Restart the backend. It reads `.env` only when it starts, so the running server keeps the mail setting it began with.
4. Register an account. The message arrives at the address of the account, and the server log shows the error of the
   SMTP server if it does not.

A link in a message is valid for one hour, and a new one is requested with "Send a new confirmation link" on the
sign-in screen or with "Forgot password?". Copy a link whole: it is a long line, and a cut one is answered as not valid
or expired.

The Gmail values come from Google's documentation as remembered and could not be checked from the environment this
was written in, which cannot reach Google's pages. Confirm them against the current
[app password help](https://support.google.com/accounts/answer/185833).

**Trying mail locally without an external service.** Either leave the host empty and read the log, or start a debugging
SMTP server that prints what it receives, in a second terminal, and point the application at it. This was run against
the mailer of the application and printed the message:

```bash
uv run --no-project --with aiosmtpd python -m aiosmtpd -n -l 127.0.0.1:1025
```

```bash
BOOKREVIVER_MAIL__SMTP_HOST=127.0.0.1
BOOKREVIVER_MAIL__SMTP_PORT=1025
```

## Sign-in with Google and Facebook

A provider is offered when both of its variables are set, and the sign-in and registration screens then show a
"Continue with" button for it. With none set, there are no buttons, and `GET /api/v1/auth/providers`, which the
screens ask, returns an empty list. The list holds the names and labels only, never a key.

| Variable                                  | Meaning                                       |
| ----------------------------------------- | --------------------------------------------- |
| `BOOKREVIVER_AUTH__GOOGLE__CLIENT_ID`     | Client ID of the Google OAuth client         |
| `BOOKREVIVER_AUTH__GOOGLE__CLIENT_SECRET` | Client secret of the Google OAuth client     |
| `BOOKREVIVER_AUTH__FACEBOOK__CLIENT_ID`   | App ID of the Meta app                        |
| `BOOKREVIVER_AUTH__FACEBOOK__CLIENT_SECRET` | App secret of the Meta app                  |

The provider returns the browser to a page of the web interface, not to the API: the redirect URI is
`{BOOKREVIVER_PUBLIC_URL}/auth/{provider}/callback`, and the provider compares it to the registered one character by
character, so what is entered in its console must equal it, scheme, host, port and no trailing slash:

| Provider | Redirect URI to register with the default public URL | Behind a Vite dev server                    |
| -------- | ----------------------------------------------------- | ------------------------------------------- |
| Google   | `http://127.0.0.1:8000/auth/google/callback`          | set `BOOKREVIVER_PUBLIC_URL=http://127.0.0.1:5173` first, then `http://127.0.0.1:5173/auth/google/callback` |
| Facebook | `http://127.0.0.1:8000/auth/facebook/callback`        | the same with `facebook`                    |

On a server, use its public address, for example `https://books.example.org/auth/google/callback`, and leave
`BOOKREVIVER_AUTH__COOKIE_SECURE` at its default so the session cookie requires HTTPS. The page forwards the code to
the API from the same browser, so the sign-in must start and end on the same address: start it from the public URL.

The steps below follow the consoles as they were known when this was written. They could not be checked from the
environment this was written in, and the consoles change their layout, so follow the current provider documentation
where a name differs.

**Google.** In the [Google Cloud Console](https://console.cloud.google.com/), create or choose a project. Configure the
OAuth consent screen (Google Auth Platform), and while the app is in testing add your address as a test user. Under
Clients (APIs & Services, Credentials) create an OAuth client of the type "Web application", add the redirect URI to its
"Authorized redirect URIs", and copy the client ID and the client secret into the two variables. The application asks
for the email and the profile of the account; the address is read from Google's People API, which must be enabled for
the project.

**Facebook.** In [Meta for Developers](https://developers.facebook.com/), create an app and add the "Facebook Login"
product. In its settings, add the redirect URI to "Valid OAuth Redirect URIs". Copy the app ID and the app secret
from the app's basic settings into the two variables. The application asks for the `email` and `public_profile`
permissions. While the app is in development mode only its administrators, developers and testers can sign in. A
Facebook account without a confirmed email address returns none, and the sign-in then fails with a message that says
so.

A sign-in whose address belongs to an account registered with a password, and not yet confirmed, joins that account,
confirms it and replaces the password, so only the provider signs in to it from then on.

## System dependencies

Reading DjVu books needs the DjVuLibre command-line tools (`djvused`, `djvudump` and `ddjvu`) on the machine that
runs the server and its workers. Without them the application still starts, logs a warning, and refuses every DjVu
file with a message naming the package. The other kinds of source do not need them.

The script of the first start runs the single package command of the distribution, which can also be typed by hand:

```bash
sudo zypper install djvulibre                                   # openSUSE
sudo apt-get install --no-install-recommends djvulibre-bin      # Debian and Ubuntu, in a Docker image too
```

When a DjVu upload fails with "Reading DjVu files is not set up on this server", the packages are missing on the
machine that runs the server. Install them and start the server again. Installing them while the server runs is not
enough, because the server looks for the tools once and keeps that answer until it restarts.

The DjVu tests build their samples with the same package (`c44`, `cjb2`, `djvm`, `djvmcvt`) and are skipped, with
the reason "DjVuLibre is not installed", when it is missing.

## Measurements

The backend can record how long its work takes as OpenTelemetry spans, one JSON object per line in a file. Nothing is
recorded by default. Two variables switch it on, and the path is required with the switch and refused without it:

| Variable                           | Meaning                                                              |
| ---------------------------------- | -------------------------------------------------------------------- |
| `BOOKREVIVER_TRACING__ENABLED`     | `true` records spans                                                 |
| `BOOKREVIVER_TRACING__EXPORT_PATH` | The file spans are appended to, a new file for each run              |

The spans are `http.request` (route template, method and status), `job` (kind and job), `run.page` (page, stage and
outcome), `step` (processor, scale and outcome: cached, ran or failed), `write.block`, which times a block that holds
SQLite's write lock: the wait for the lock, how long it was held and the commit, with the book, and `loop.lag`, one tick
of a probe that records how much longer than its interval a sleep of the event loop took (0.1 s, set by
`BOOKREVIVER_TRACING__LOOP_LAG_INTERVAL_SECONDS`). The durations of a write block are measured on the event loop and
include its delay, which the `Event-loop lag, ms` line of the report shows. The file is complete once the server has
stopped. `bookreviver-trace-report` summarises one run for a note:

```bash
uv run bookreviver-trace-report path/to/spans.jsonl
```

To measure a full end-to-end run, export the two variables with a path outside `frontend/.e2e-data`, which the server
command deletes at its start, run the scenarios and read the report. Playwright stops the server with `SIGTERM`, so the
last spans are written before the file is read:

```bash
export BOOKREVIVER_TRACING__ENABLED=true BOOKREVIVER_TRACING__EXPORT_PATH=/path/to/e2e-spans.jsonl
npm --prefix frontend run e2e
uv run bookreviver-trace-report /path/to/e2e-spans.jsonl
```

The option `--check` compares the run with two budgets, a preview job on the visible page under 1 s and the 99th
percentile of the wait for the write lock under 100 ms. The preview is measured as the whole `job` span of the kind
`preview-step`, with every step of it, and not the time the job waited in the queue before it started. The option prints
a line for each budget and exits with 1 when a budget is exceeded:

```bash
uv run bookreviver-trace-report --check path/to/spans.jsonl
```

## Development

```bash
uv run pytest
uv run pre-commit install --install-hooks
uv run pre-commit run --all-files
```

The `ty` hook needs OpenCV to resolve the processing plugins. Run the checks in an environment made with
`uv sync --extra cv`, and tell `uv run` not to change it with `UV_NO_SYNC=1`. The `uv-sync` hook keeps every optional
group installed after a checkout, a merge or a rebase, so a change of branch does not take OpenCV away:

```bash
uv sync --extra cv
UV_NO_SYNC=1 uv run pre-commit run --all-files
```

## License

AGPL-3.0, see [LICENSE](LICENSE).
