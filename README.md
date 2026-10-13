# EDNP Clergy Records System

A web portal for the Episcopal Diocese of Northern Philippines that keeps clergy records, their required documents, and reports. It has two sides:

- **Registrar portal**: clergy records, document checklists, account creation (one by one or from an Excel file), reports, and backups.
- **Clergy portal**: each clergy member's own profile, documents and notifications.

It is a Django 4.2 application backed by MySQL or MariaDB. This guide covers installing it on a computer for development or a demo, and deploying it to a server.

---

## Contents

1. [What you need](#1-what-you-need)
2. [Install on your computer](#2-install-on-your-computer)
3. [Create the first registrar account](#3-create-the-first-registrar-account)
4. [Run the tests](#4-run-the-tests)
5. [Deploy to a server (Docker / Railway)](#5-deploy-to-a-server-docker--railway)
6. [Settings reference (`.env`)](#6-settings-reference-env)
7. [Management commands](#7-management-commands)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. What you need

| Software | Version | Notes |
| --- | --- | --- |
| **Python** | 3.12 or 3.13, 64-bit | The server uses 3.12. On Windows, tick "Add python.exe to PATH" in the installer. |
| **MySQL or MariaDB** | MariaDB 10.4+ or MySQL 8 | [XAMPP](https://www.apachefriends.org/) is the easiest way to get one on Windows. |
| **Git** | any | To download the code. |
| **GTK / Pango libraries** | any recent | Needed by WeasyPrint, which makes the PDFs (reports, credential slips, clergy profiles). See [step 2.4](#24-install-the-pdf-libraries-weasyprint). |
| **A Semaphore account** | – | Only for a real server. Semaphore sends the SMS sign-in codes. On your own computer the codes are shown on screen instead. |

Mobile numbers must be Philippine mobile numbers (for example `0917 123 4567`), because the system texts its sign-in codes to them.

---

## 2. Install on your computer

The commands below are for **Windows PowerShell**. macOS and Linux commands are listed where they differ.

### 2.1 Get the code

```powershell
git clone https://github.com/YzonMF/EDNP.git
cd EDNP
```

### 2.2 Create a virtual environment

A virtual environment keeps this project's Python packages separate from everything else on the computer.

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

- **Command Prompt (cmd):** `venv\Scripts\activate.bat`
- **macOS / Linux:** `python3 -m venv venv` then `source venv/bin/activate`

When it is active, your prompt starts with `(venv)`. Activate it again every time you open a new terminal for this project.

> If PowerShell says running scripts is disabled, run this once and try again:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 2.3 Install the Python packages

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2.4 Install the PDF libraries (WeasyPrint)

`pip` installs WeasyPrint itself, but WeasyPrint also needs the GTK/Pango libraries, which `pip` cannot install. Without them the site still runs, but every PDF download fails.

- **Windows:** install the **GTK3 Runtime for Windows** (64-bit) from <https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases>. Leave "Set up PATH environment variable" ticked. Then **close and reopen** your terminal and activate the virtual environment again.
  - Alternative from the WeasyPrint documentation: install [MSYS2](https://www.msys2.org/), run `pacman -S mingw-w64-x86_64-pango` in the MSYS2 shell, then set the environment variable `WEASYPRINT_DLL_DIRECTORIES=C:\msys64\mingw64\bin`.
- **macOS:** `brew install pango`
- **Debian / Ubuntu:** `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0`

Check that it works:

```powershell
python -m weasyprint --info
```

If this prints version information, you are done. If it shows `cannot load library`, see [Troubleshooting](#8-troubleshooting).

More detail: <https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation>

### 2.5 Start the database server

With XAMPP, open the **XAMPP Control Panel** and click **Start** next to **MySQL**. You do not need Apache.

### 2.6 Create the `.env` settings file

The system reads its settings and secrets from a file named `.env` in the project folder. That file is never committed to git. Start from the template:

```powershell
copy .env.example .env
```

(macOS / Linux: `cp .env.example .env`)

Make a secret key:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Open `.env` in a text editor and paste the key after `SECRET_KEY=`. For a fresh XAMPP install, the other values in the template already work:

```ini
SECRET_KEY=<the key you just made>
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

DB_NAME=ednp_clergy_records
DB_USER=root
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=3306
```

If your MySQL user has a password, or the server is on another port, change the `DB_` lines to match. Every setting is listed in [section 6](#6-settings-reference-env).

With `DEBUG=True`, no texts are sent: SMS codes are shown on screen. You do not need a Semaphore key on your own computer.

### 2.7 Create the database and its tables

```powershell
python ensure_db.py
python manage.py migrate
```

- `ensure_db.py` creates the database named in `DB_NAME` if it does not exist yet. You can create it in phpMyAdmin instead, with the `utf8mb4` character set.
- `migrate` creates all of the tables.

> **Do not import `database/ednp_schema.sql`.** It is the schema from the early prototype, kept for reference only. It creates tables with the same names, so `migrate` would then fail. Django's migrations build the real schema.

### 2.8 Load the starting data

```powershell
python manage.py seed_deaneries
python manage.py seed_document_types
```

- `seed_deaneries` adds the diocese's deaneries and parishes.
- `seed_document_types` adds the checklist of documents each clergy rank must submit.

Both are safe to run again; they do not create duplicates.

### 2.9 Start the site

```powershell
python manage.py runserver
```

Open <http://127.0.0.1:8000/> in your browser. Press `Ctrl+C` in the terminal to stop the server.

---

## 3. Create the first registrar account

Registrar accounts are made from the command line. Clergy accounts are then made by a registrar inside the portal.

```powershell
python manage.py createsuperuser
```

Enter a username, an email address (optional) and a password. Then sign in at <http://127.0.0.1:8000/>. The first sign-in walks you through a short setup:

1. Confirm your full name. Reports print it as "Generated by …".
2. Register a Philippine mobile number and enter the code sent to it. On your own computer the code is shown on screen.
3. Choose a new password.

After that, signing in from a new device or browser asks for a text code.

To add more registrars, run `createsuperuser` again for each one. Once there are two or more, they can reset each other's accounts from **Account Security** in the portal.

---

## 4. Run the tests

```powershell
python manage.py test
```

- The MySQL user in `.env` must be allowed to create databases. The tests use a temporary `test_ednp_clergy_records` database, so your own data is never touched. XAMPP's `root` user is allowed by default.
- The full suite takes a while, roughly 20 minutes, mostly from hashing test passwords securely.
- A few tests are skipped unless optional packages are installed. The storage bucket tests need `pip install "moto[s3]"`.

---

## 5. Deploy to a server (Docker / Railway)

The included `Dockerfile` builds the production image. When the container starts, it:

1. creates the database if it is missing (`ensure_db.py`),
2. applies any new migrations (`manage.py migrate`),
3. serves the site with gunicorn on `$PORT` (8000 if unset).

Static files (CSS, JavaScript, images) are collected while the image is built and served by WhiteNoise, so no separate web server is needed.

### 5.1 Railway

1. Create a project with a **MySQL** service and a service for this repository. Railway builds it from the `Dockerfile` automatically.
2. Set these variables on the web service:

   | Variable | Value |
   |---|---|
   | `SECRET_KEY` | A new random key, different from your local one. |
   | `DEBUG` | `False` |
   | `ALLOWED_HOSTS` | Your domain, e.g. `your-app.up.railway.app` |
   | `CSRF_TRUSTED_ORIGINS` | The same with `https://`, e.g. `https://your-app.up.railway.app` |
   | `BEHIND_PROXY` | `True` (Railway handles HTTPS in front of the app) |
   | `SMS_BACKEND` | `semaphore` |
   | `SEMAPHORE_API_KEY` | Your Semaphore API key |
   | Database | References to the MySQL service, e.g. `MYSQLHOST=${{MySQL.MYSQLHOST}}`, and likewise `MYSQLPORT`, `MYSQLUSER`, `MYSQLPASSWORD`, `MYSQLDATABASE`. The `DB_` names work too. |

3. **Decide where uploaded files will live.** Without further setup, documents and reports are saved on the container's own disk, and Railway replaces that disk at every redeploy, deleting them. Use one of:
   - a **Railway Storage Bucket**, by setting the `BUCKET_` variables (see `resources/storage-bucket.md`), or
   - a **Railway volume** mounted at `/data/media`.
4. Deploy. Then open a shell on the web service (with the Railway CLI: `railway ssh`), load the starting data and create the first registrar:

   ```sh
   python manage.py seed_deaneries
   python manage.py seed_document_types
   python manage.py createsuperuser
   ```

After you add or change variables on Railway, use the staged-changes **Deploy** button. A plain redeploy does not pick up the new values.

### 5.2 Any other Docker host

```sh
docker build -t ednp .
docker run -p 8000:8000 --env-file .env.production -v ednp-media:/data/media ednp
```

`.env.production` holds the same variables as the Railway table, with `DB_HOST` and the other `DB_` values pointing at your MySQL server. The volume keeps uploaded files between container restarts.

### 5.3 Production checklist

- `DEBUG=False`, and the site is served over **HTTPS**. With `DEBUG=False`, sign-in cookies only travel over HTTPS, so on plain HTTP nobody can sign in.
- A real `SEMAPHORE_API_KEY`. The site refuses to start with real SMS and no key.
- Uploaded files are kept somewhere that survives a redeploy (bucket or volume).
- Optional, recommended: encryption at rest for documents and government ID numbers. Run `python manage.py generate_data_key`, keep a copy of the key **somewhere other than the server**, and set it as `DATA_ENCRYPTION_KEY`. Without that key, encrypted data and its backups cannot be recovered. Then run `python manage.py encrypt_existing_data --dry-run`, and the same without `--dry-run`.
- Optional: a daily scheduled job running `python manage.py purge_archived_reports`. The Generate Report page also does this whenever it opens.
- `python manage.py check --deploy` prints the project's own warnings: `ednp.W001` (documents and ID numbers not encrypted) and `ednp.W002` (files on the server's own disk). Every install also shows `models.W042`, `security.W004` and `security.W008`. Those three are expected with the current settings.

---

## 6. Settings reference (`.env`)

Locally these go in `.env`; on a server they are environment variables. Only `SECRET_KEY` is always required.

### Core

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | none (required) | Django's secret key. Keep it secret. Use a different one per server. |
| `DEBUG` | `False` | `True` only on your own computer. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names the site answers to. |
| `CSRF_TRUSTED_ORIGINS` | empty | Comma-separated `https://` origins. Needed behind a proxy such as Railway. |
| `BEHIND_PROXY` | `False` | `True` when a proxy in front of the app handles HTTPS. |

### Database

| Variable | Default | Railway name also accepted |
| --- | --- | --- |
| `DB_NAME` | `ednp_clergy_records` | `MYSQLDATABASE` / `MYSQL_DATABASE` |
| `DB_USER` | `root` | `MYSQLUSER` |
| `DB_PASSWORD` | empty | `MYSQLPASSWORD` |
| `DB_HOST` | `localhost` | `MYSQLHOST` |
| `DB_PORT` | `3306` | `MYSQLPORT` |

### SMS codes (Semaphore)

| Variable | Default | Purpose |
| --- | --- | --- |
| `SMS_BACKEND` | `test` when `DEBUG=True`, otherwise `semaphore` | `test` shows codes on screen and sends nothing. `semaphore` sends real texts, which cost credits. |
| `SEMAPHORE_API_KEY` | empty | Required when `SMS_BACKEND=semaphore`. (`API_Key` is also read.) |
| `SEMAPHORE_SENDER_NAME` | account default | Sender name shown on the text. |
| `SEMAPHORE_ROUTE` | `standard` | `standard` (1 credit per text) or `otp` (2 credits, Semaphore's priority route). |

### Stored files

| Variable | Default | Purpose |
| --- | --- | --- |
| `MEDIA_ROOT` | `media/` in the project (`/data/media` in Docker) | Where files are kept when no bucket is set. |
| `BUCKET_NAME`, `BUCKET_ENDPOINT`, `BUCKET_REGION`, `BUCKET_ACCESS_KEY_ID`, `BUCKET_SECRET_ACCESS_KEY` | empty | Set all five to keep files in an S3-compatible bucket instead. See `resources/storage-bucket.md`. |
| `BUCKET_URL_STYLE` | `virtual` | `path`, only if the bucket requires path-style URLs. |

### Encryption at rest

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATA_ENCRYPTION_KEY` | empty (off) | Encrypts uploaded documents and government ID numbers. Make one with `python manage.py generate_data_key`. Several keys may be listed, comma-separated: the first encrypts, all of them decrypt. |
| `DATA_ENCRYPTION_REQUIRED` | `False` | Set to `True` only after `encrypt_existing_data` has run. From then on, unencrypted data is refused. |

---

## 7. Management commands

Run each with `python manage.py <command>`, with the virtual environment active.

| Command | What it does |
| --- | --- |
| `migrate` | Creates or updates the database tables. Run it after every update to the code. |
| `createsuperuser` | Creates a registrar account. |
| `seed_deaneries` | Adds the deaneries and parishes. Safe to re-run. |
| `seed_document_types` | Adds the required-documents checklist. Safe to re-run. |
| `reset_account <username>` | Recovers an account whose owner lost their phone or password, after confirming who they are in person. It forgets their number, signs out all of their devices, and prints a temporary password. This is how the only registrar gets back in. |
| `generate_data_key` | Prints a new `DATA_ENCRYPTION_KEY`. |
| `encrypt_existing_data [--dry-run] [--rotate]` | Encrypts files and ID numbers that are still unencrypted. `--rotate` re-encrypts under the newest key so an old key can be removed. |
| `copy_files_to_bucket [--dry-run]` | Copies files already on disk into the bucket when moving to one. |
| `purge_archived_reports` | Deletes reports that have been archived for longer than the retention period. |
| `check --deploy` | Prints security and configuration warnings for a production setup (see the [checklist](#53-production-checklist)). |

---

## 8. Troubleshooting

| Problem | Fix |
| --- | --- |
| `SECRET_KEY is not set` | There is no `.env` file, or it has no `SECRET_KEY` line. See [step 2.6](#26-create-the-env-settings-file). |
| `SMS_BACKEND is "semaphore" but no API key was found` | Locally, set `DEBUG=True` (or `SMS_BACKEND=test`) in `.env`. On a server, set `SEMAPHORE_API_KEY`. |
| `ensure_db: waiting for MySQL …` / `Can't connect to MySQL server` | MySQL is not running (start it in XAMPP), or the `DB_HOST`, `DB_PORT`, `DB_USER` or `DB_PASSWORD` values are wrong. |
| `Access denied for user 'root'@'localhost'` | Your MySQL `root` user has a password. Put it in `DB_PASSWORD`. |
| `pip install` fails on **mysqlclient** | Windows: use 64-bit Python 3.12 or 3.13, which have ready-made packages. Linux: install `build-essential pkg-config default-libmysqlclient-dev` first. macOS: `brew install mysql-client pkg-config`. |
| `cannot load library 'gobject-2.0-0'` or `'pango-1.0-0'` when downloading a PDF | The GTK/Pango libraries are missing or not on `PATH`. Redo [step 2.4](#24-install-the-pdf-libraries-weasyprint), then open a **new** terminal. |
| `Table 'deaneries' already exists` during `migrate` | `database/ednp_schema.sql` was imported. Drop the database, then run `ensure_db.py` and `migrate` again. |
| `Bad Request (400)` | The address you are using is missing from `ALLOWED_HOSTS`. |
| `CSRF verification failed`, or you get sent back to the sign-in page after signing in (on a server) | The site must be on HTTPS when `DEBUG=False`. Behind Railway or another proxy, set `BEHIND_PROXY=True` and `CSRF_TRUSTED_ORIGINS=https://<your domain>`. |
| Pages have no styling with `DEBUG=False` on your computer | Run `python manage.py collectstatic`. With `DEBUG=True`, static files are served for you. |
| The site will not start, with error `ednp.E001` or `ednp.E002` | `DATA_ENCRYPTION_KEY` is invalid, or missing while `DATA_ENCRYPTION_REQUIRED=True`. |
| The site will not start, with error `ednp.E003`, `E004` or `E005` | `BUCKET_NAME` is set, but the other `BUCKET_` variables are missing or invalid. |
| Uploaded documents went missing after a redeploy | The files were on the container's disk. Set up a bucket or a volume ([section 5.1](#51-railway), step 3). Files already lost cannot be recovered. |
| Signed out with a message that you signed in somewhere else | Each account can have only one active session. Signing in on another browser or device ends the earlier one. |
| The only registrar is locked out | On the server, run `python manage.py reset_account <username>` and hand over the temporary password in person. |
