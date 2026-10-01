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

The interface is a React application in `frontend/`, built with [Node](https://nodejs.org/) 22.18 or newer, in an even-numbered release line (22, 24, 26 and so on), which is what its tools support.

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
