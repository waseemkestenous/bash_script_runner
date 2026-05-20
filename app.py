from flask import Flask, render_template, request, jsonify, session as flask_session, redirect, url_for
import subprocess
import threading
import queue
import uuid
import os
import signal
import json
import pty
import termios
import time
import shlex
import re
from datetime import datetime
from functools import wraps
from dotenv import load_dotenv
import msal

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

# Store running script sessions in memory
sessions = {}

BASE_DIR = os.path.dirname(__file__)
SCRIPTS_JSON_PATH = os.path.join(BASE_DIR, "scripts.json")
SAVED_OUTPUTS_DIR = os.path.join(BASE_DIR, "saved_outputs")
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "true").lower() not in ("0", "false", "no", "off")
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID")
MS_CLIENT_SECRET = os.environ.get("MS_CLIENT_SECRET")
MS_TENANT_ID = os.environ.get("MS_TENANT_ID", "common")
MS_AUTHORITY = f"https://login.microsoftonline.com/{MS_TENANT_ID}"
MS_REDIRECT_PATH = os.environ.get("MS_REDIRECT_PATH", "/auth/callback")
MS_SCOPES = os.environ.get("MS_SCOPES", "User.Read").split()
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "5000"))
APP_SCHEME = os.environ.get("APP_SCHEME", "http").lower()


def clean_username(value):
    if not value:
        return None

    username = value.strip()
    if not username:
        return None

    if "\\" in username:
        username = username.rsplit("\\", 1)[-1]
    if "@" in username:
        username = username.split("@", 1)[0]

    return username or None


def get_client_username():
    signed_in_user = flask_session.get("user") or {}
    username = clean_username(
        signed_in_user.get("preferred_username")
        or signed_in_user.get("email")
        or signed_in_user.get("name")
    )
    if username:
        return username

    candidates = [
        request.environ.get("REMOTE_USER"),
        request.environ.get("LOGON_USER"),
        request.environ.get("AUTH_USER"),
        request.headers.get("X-Remote-User"),
        request.headers.get("X-Forwarded-User"),
        request.headers.get("X-Authenticated-User"),
        request.headers.get("X-Auth-Request-User"),
    ]

    for candidate in candidates:
        username = clean_username(candidate)
        if username:
            return username

    return "PT"


def microsoft_auth_configured():
    return bool(AUTH_ENABLED and MS_CLIENT_ID and MS_CLIENT_SECRET)


def build_msal_app(cache=None):
    return msal.ConfidentialClientApplication(
        MS_CLIENT_ID,
        authority=MS_AUTHORITY,
        client_credential=MS_CLIENT_SECRET,
        token_cache=cache
    )


def build_auth_flow():
    return build_msal_app().initiate_auth_code_flow(
        scopes=MS_SCOPES,
        redirect_uri=url_for("auth_callback", _external=True, _scheme=APP_SCHEME)
    )


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not AUTH_ENABLED:
            return view(*args, **kwargs)

        if flask_session.get("user"):
            return view(*args, **kwargs)

        if request.path != "/" or request.accept_mimetypes.best == "application/json":
            return jsonify({"error": "Authentication required"}), 401

        return redirect(url_for("login"))

    return wrapped_view


def safe_filename_part(value):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "unknown"


def load_scripts():
    with open(SCRIPTS_JSON_PATH, "r", encoding="utf-8") as scripts_file:
        scripts = json.load(scripts_file)

    normalized_scripts = []
    for index, script in enumerate(scripts):
        name = script.get("name")
        location = script.get("location")
        if not name or not location:
            continue

        script_path = location
        if not os.path.isabs(script_path):
            script_path = os.path.join(BASE_DIR, script_path)

        script_path = os.path.abspath(script_path)
        normalized_scripts.append({
            "id": str(index),
            "name": name,
            "location": location,
            "path": script_path
        })

    return normalized_scripts


def public_script(script):
    return {
        "id": script["id"],
        "name": script["name"]
    }


def find_script(script_id):
    for script in load_scripts():
        if script["id"] == script_id:
            return script
    return None


class ScriptSession:
    def __init__(self, script, parameters=None):
        self.id = str(uuid.uuid4())
        self.script = public_script(script)
        self.output_queue = queue.Queue()
        self.finished = False
        self.exit_code = None
        self.final_output = ""
        self.master_fd = None

        command = [script["path"]]
        if script["path"].endswith(".sh"):
            command = ["bash", script["path"]]
        command.extend(parameters or [])

        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd

        attrs = termios.tcgetattr(slave_fd)
        attrs[3] = attrs[3] & ~termios.ECHO
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)

        self.process = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True
        )
        os.close(slave_fd)

        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

    def _read_output(self):
        try:
            while True:
                try:
                    data = os.read(self.master_fd, 4096)
                except OSError:
                    break

                if not data:
                    break

                text = data.decode("utf-8", errors="replace")
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                self.final_output += text
                self.output_queue.put(text)

            self.process.wait()
            self.exit_code = self.process.returncode
            self.finished = True
            self.output_queue.put("__SCRIPT_FINISHED__")
        except Exception as e:
            self.finished = True
            self.output_queue.put(f"ERROR: {e}")
            self.output_queue.put("__SCRIPT_FINISHED__")

    def send_input(self, value):
        if self.finished or self.process.poll() is not None:
            return False

        try:
            os.write(self.master_fd, (value + "\n").encode("utf-8"))
            return True
        except OSError:
            self.finished = True
            return False

    def get_output(self):
        messages = []
        while not self.output_queue.empty():
            messages.append(self.output_queue.get())
        return messages

    def wait_for_output(self, timeout=0.25):
        deadline = time.time() + timeout
        while self.output_queue.empty() and not self.finished and time.time() < deadline:
            time.sleep(0.01)

    def stop(self):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
            except ProcessLookupError:
                pass

        self.exit_code = self.process.returncode
        self.finished = True

        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None


@app.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        scripts=[public_script(script) for script in load_scripts()],
        client_username=get_client_username(),
        user=flask_session.get("user") or {},
        auth_enabled=AUTH_ENABLED
    )


@app.route("/user")
@login_required
def current_user():
    return jsonify({
        "username": get_client_username(),
        "user": flask_session.get("user"),
        "auth_enabled": AUTH_ENABLED
    })


@app.route("/login")
def login():
    if not AUTH_ENABLED:
        return redirect(url_for("index"))

    if flask_session.get("user"):
        return redirect(url_for("index"))

    if not microsoft_auth_configured():
        return (
            "Microsoft authentication is not configured. "
            "Set MS_CLIENT_ID, MS_CLIENT_SECRET, FLASK_SECRET_KEY, and optionally MS_TENANT_ID.",
            500
        )

    flask_session["flow"] = build_auth_flow()
    return redirect(flask_session["flow"]["auth_uri"])


@app.route(MS_REDIRECT_PATH)
def auth_callback():
    if not microsoft_auth_configured():
        return redirect(url_for("login"))

    try:
        result = build_msal_app().acquire_token_by_auth_code_flow(
            flask_session.get("flow", {}),
            request.args
        )
    except ValueError:
        return redirect(url_for("login"))

    if "error" in result:
        return (
            f"Microsoft authentication failed: {result.get('error_description') or result.get('error')}",
            401
        )

    claims = result.get("id_token_claims") or {}
    flask_session["user"] = {
        "name": claims.get("name"),
        "preferred_username": claims.get("preferred_username"),
        "email": claims.get("email") or claims.get("preferred_username"),
        "oid": claims.get("oid")
    }
    flask_session.pop("flow", None)

    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    flask_session.clear()
    if not AUTH_ENABLED:
        return redirect(url_for("index"))

    post_logout_redirect = url_for("login", _external=True)
    return redirect(
        f"{MS_AUTHORITY}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={post_logout_redirect}"
    )


@app.route("/scripts")
@login_required
def scripts_list():
    return jsonify({"scripts": [public_script(script) for script in load_scripts()]})


@app.route("/start", methods=["POST"])
@login_required
def start_script():
    data = request.json or {}
    script_id = data.get("script_id")
    parameters_text = data.get("parameters", "")
    script = find_script(script_id)
    if not script:
        return jsonify({"error": "Script not found"}), 404

    if not os.path.isfile(script["path"]):
        return jsonify({"error": "Script file not found"}), 404

    if not os.access(script["path"], os.X_OK) and not script["path"].endswith(".sh"):
        return jsonify({"error": "Script is not executable"}), 400

    try:
        parameters = shlex.split(parameters_text)
    except ValueError as e:
        return jsonify({"error": f"Invalid parameters: {e}"}), 400

    try:
        session = ScriptSession(script, parameters)
    except OSError as e:
        return jsonify({"error": f"Could not start script: {e}"}), 500

    sessions[session.id] = session
    session.wait_for_output()
    return jsonify({
        "session_id": session.id,
        "script": session.script,
        "messages": session.get_output(),
        "finished": session.finished
    })


@app.route("/send", methods=["POST"])
@login_required
def send_answer():
    data = request.json
    session_id = data.get("session_id")
    answer = data.get("answer", "")

    session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    ok = session.send_input(answer)
    if ok:
        session.wait_for_output()

    return jsonify({
        "ok": ok,
        "messages": session.get_output(),
        "finished": session.finished,
        "exit_code": session.exit_code
    })


@app.route("/poll/<session_id>")
@login_required
def poll(session_id):
    session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    return jsonify({
        "messages": session.get_output(),
        "finished": session.finished,
        "exit_code": session.exit_code
    })


@app.route("/stop", methods=["POST"])
@login_required
def stop_script():
    data = request.json or {}
    session_id = data.get("session_id")

    session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    session.stop()

    return jsonify({
        "stopped": True,
        "messages": session.get_output(),
        "finished": session.finished,
        "exit_code": session.exit_code
    })


@app.route("/accept", methods=["POST"])
@login_required
def accept_final_output():
    data = request.json or {}
    session_id = data.get("session_id")

    session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    username = get_client_username()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_filename_part(username)}_{timestamp}.txt"
    file_path = os.path.join(SAVED_OUTPUTS_DIR, filename)

    os.makedirs(SAVED_OUTPUTS_DIR, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as output_file:
        output_file.write(f"User: {username}\n")
        output_file.write(f"Script: {session.script['name']}\n")
        output_file.write(f"Saved At: {datetime.now().isoformat(timespec='seconds')}\n")
        output_file.write("\n")
        output_file.write(session.final_output)

    return jsonify({
        "accepted": True,
        "final_output": session.final_output,
        "saved_file": file_path
    })


if __name__ == "__main__":
    ssl_context = "adhoc" if APP_SCHEME == "https" else None
    app.run(host=APP_HOST, port=APP_PORT, debug=True, ssl_context=ssl_context)
