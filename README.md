# BookReviver

A workbench for digitising old printed books, with a focus on pre-reform Russian and Belarusian orthography. Each
book is a project that moves through stages: import, page split, cleanup, alignment, recognition and layout of a new
printed edition. Every stage stays viewable at any time, so an earlier stage can be corrected and the later ones rerun.

The application is a JSON API with a React frontend. `docs/architecture.md` describes the design and the delivery plan.

## What to install

| What | Needed for | When to install | How |
| --- | --- | --- | --- |
| [uv](https://docs.astral.sh/uv/) | The backend and its Python, which uv installs by itself | Before anything else | See the uv documentation |
| [Node](https://nodejs.org/) 22.18 or newer, in an even-numbered release line (22, 24, 26 and so on) | Building the interface | Before the first start, and again after the frontend changes | See the Node documentation |
| DjVuLibre | Reading DjVu books | Before the first start, or at the latest before the first DjVu upload | `sudo scripts/install-system-deps.sh` |
| OpenCV, the optional group `cv` | The processing steps that split a spread and straighten a page | Only when you use those steps | `uv run --extra cv ...` |
| An SMTP server | Real confirmation and password reset mail | Optional, without it the mail is written to the server log | `BOOKREVIVER_MAIL__*` in `.env`, see [Mail](#mail) |
| Google and Facebook keys | The sign-in buttons of the two providers | Optional, before other people use the server | `BOOKREVIVER_AUTH__GOOGLE__*` and `BOOKREVIVER_AUTH__FACEBOOK__*` in `.env`, see [Sign-in with Google and Facebook](#sign-in-with-google-and-facebook) |

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

4. **Database.** The application never changes the schema itself, so create it by hand.

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

| What changed | What to run |
| --- | --- |
| A new file in `src/bookreviver/adapters/persistence/sqlalchemy/migrations/versions/` | `uv run bookreviver-migrate upgrade head`, after copying `data/` if it holds anything worth keeping. Until then the server refuses to start and names this command. |
| `pyproject.toml` or `uv.lock` | `uv sync` |
| Anything under `frontend/` | `npm --prefix frontend ci` and `npm --prefix frontend run build` |
| A route or a schema of the API | `uv run bookreviver-openapi`, then `npm --prefix frontend run generate`, and commit both results |
| The system packages, see the list above | `sudo scripts/install-system-deps.sh`, then start the server again |

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

```bash
npm --prefix frontend run dev     # the Vite server on http://127.0.0.1:5173, proxying /api to port 8000
npm --prefix frontend run check   # Biome, the type check and the unit tests
npm --prefix frontend run generate  # rebuilds frontend/src/api from docs/openapi.json
```

`npm --prefix frontend run e2e` builds the frontend and runs the Playwright scenarios against a real server, started
on port 8765 with an empty database in `frontend/.e2e-data`, which is also where the confirmation mail is read from.
It needs Playwright's Chromium (`npx --prefix frontend playwright install --with-deps chromium`), or another one named
in `BOOKREVIVER_E2E_CHROMIUM`.

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

## Development

```bash
uv run pytest
uv run pre-commit install --install-hooks
uv run pre-commit run --all-files
```

The `ty` hook needs OpenCV to resolve the processing plugins. Run the checks in an environment made with
`uv sync --extra cv`, and tell `uv run` not to change it with `UV_NO_SYNC=1`:

```bash
uv sync --extra cv
UV_NO_SYNC=1 uv run pre-commit run --all-files
```

## License

AGPL-3.0, see [LICENSE](LICENSE).
