# Bash Script Web Chat App

This app runs an interactive Bash script in the background and lets the user answer script questions from a web page in chat mode.

## Run

```bash
pip install -r requirements.txt
chmod +x scripts/server_check.sh
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Microsoft Auth

Create an app registration in Microsoft Entra ID and add this redirect URI:

```text
http://127.0.0.1:5000/auth/callback
```

For another host or port, use that app URL with `/auth/callback`.

Required environment variables:

```text
FLASK_SECRET_KEY=change-this-long-random-value
MS_CLIENT_ID=your-application-client-id
MS_CLIENT_SECRET=your-client-secret
MS_TENANT_ID=your-tenant-id
```

Optional:

```text
AUTH_ENABLED=true
MS_SCOPES=User.Read
MS_REDIRECT_PATH=/auth/callback
APP_HOST=127.0.0.1
APP_PORT=5000
APP_SCHEME=http
```

Put these values in `.env` for local development. The app loads `.env` automatically.

Accepted output files are saved with the signed-in Microsoft username.

To disable Microsoft Auth for local development:

```text
AUTH_ENABLED=false
```

When auth is disabled, the app is open to anyone who can reach it and uses proxy/header client username detection if available.

If you see a Flask log like `Bad request version` with unreadable characters, the browser is using HTTPS against an HTTP server. Use `http://127.0.0.1:5000` and register this redirect URI:

```text
http://127.0.0.1:5000/auth/callback
```

Or enable local HTTPS:

```text
APP_SCHEME=https
```

Then register and open:

```text
https://127.0.0.1:5000/auth/callback
```

## Replace the sample script

Edit:

```text
scripts/server_check.sh
```

Keep using `echo` for questions/output and `read` for inputs.

## Important

Do not expose this directly to public internet without authentication and input validation.
Running shell scripts from a web app can be dangerous if users can control commands or file paths.
