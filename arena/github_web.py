"""Sign in with GitHub and short-lived push credentials issued by the center.

Uses a GitHub App's user authorization: access tokens expire after eight hours and are refreshed
here with the member's refresh token. Tokens are kept in `github.sqlite` beside `arena.db`; the
backup tool only copies `arena.db`, so restoring a backup asks members to sign in again rather
than reviving old credentials.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
STATE_TTL = 600
REFRESH_MARGIN = 30 * 60


class GitHubWebError(ValueError):
    """A sign-in or credential problem the member can act on; the message is shown to them."""


def noreply_email(user: dict) -> str:
    return f'{user["id"]}+{user["login"]}@users.noreply.github.com'


class GitHubWeb:
    def __init__(self, root: Path, http: httpx.Client | None = None):
        self.path = Path(root) / "github.sqlite"
        self.http = http or httpx.Client(timeout=15, trust_env=True)
        self.lock = threading.Lock()
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS app (id INTEGER PRIMARY KEY CHECK (id = 1), client_id TEXT NOT NULL,
              client_secret TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS accounts (identity_id TEXT PRIMARY KEY, github_id INTEGER UNIQUE NOT NULL,
              github_login TEXT NOT NULL, git_name TEXT NOT NULL, git_email TEXT NOT NULL,
              access_token TEXT NOT NULL, access_expires REAL NOT NULL,
              refresh_token TEXT NOT NULL, refresh_expires REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS expected (identity_id TEXT PRIMARY KEY, github_login TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS states (state TEXT PRIMARY KEY, purpose TEXT NOT NULL,
              identity_id TEXT, redirect_uri TEXT NOT NULL, created REAL NOT NULL);
            """)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        return closing_commit(db)

    # ----- GitHub App settings -----
    def app(self) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT client_id, client_secret FROM app WHERE id=1").fetchone()
        return dict(row) if row else None

    def set_app(self, client_id: str, client_secret: str):
        with self.connect() as db:
            db.execute(
                "INSERT INTO app VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET client_id=excluded.client_id,"
                "client_secret=excluded.client_secret,updated=excluded.updated",
                (client_id, client_secret, time.time()),
            )

    # ----- Sign-in flow -----
    def start(self, purpose: str, redirect_uri: str, identity_id: str | None = None) -> tuple[str, str]:
        app = self.app()
        if not app:
            raise GitHubWebError("GitHub 登录还没有配置")
        state = secrets.token_urlsafe(24)
        with self.connect() as db:
            db.execute("DELETE FROM states WHERE created<?", (time.time() - STATE_TTL,))
            db.execute("INSERT INTO states VALUES(?,?,?,?,?)", (state, purpose, identity_id, redirect_uri, time.time()))
        query = urlencode({"client_id": app["client_id"], "redirect_uri": redirect_uri, "state": state})
        return state, f"{AUTHORIZE_URL}?{query}"

    def finish(self, state: str, code: str) -> dict:
        """Exchange the code; returns purpose, identity_id (for connect), GitHub user and tokens."""
        with self.connect() as db:
            row = db.execute(
                "SELECT purpose, identity_id, redirect_uri, created FROM states WHERE state=?", (state,)
            ).fetchone()
            db.execute("DELETE FROM states WHERE state=?", (state,))
        if not row or row["created"] < time.time() - STATE_TTL:
            raise GitHubWebError("登录请求已过期，请重新点一次「用 GitHub 登录」")
        app = self.app()
        if not app:
            raise GitHubWebError("GitHub 登录还没有配置")
        tokens = self._token(
            {
                "client_id": app["client_id"],
                "client_secret": app["client_secret"],
                "code": code,
                "redirect_uri": row["redirect_uri"],
            }
        )
        try:
            response = self.http.get(
                USER_URL,
                headers={"Authorization": f'Bearer {tokens["access_token"]}', "Accept": "application/vnd.github+json"},
            )
            user = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubWebError("连不上 GitHub，请稍后重试") from exc
        if response.status_code != 200 or not isinstance(user.get("id"), int) or not user.get("login"):
            raise GitHubWebError("GitHub 没有返回账号信息")
        return {"purpose": row["purpose"], "identity_id": row["identity_id"], "user": user, "tokens": tokens}

    def _token(self, form: dict) -> dict:
        try:
            response = self.http.post(TOKEN_URL, data=form, headers={"Accept": "application/json"})
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubWebError("连不上 GitHub，请稍后重试") from exc
        if not body.get("access_token"):
            raise GitHubWebError(
                "GitHub 拒绝了授权：" + str(body.get("error_description") or body.get("error") or "未知原因")
            )
        if not body.get("refresh_token"):
            # Without expiring tokens the center would hold a credential that never expires.
            raise GitHubWebError("请在 GitHub App 设置里开启「Expire user authorization tokens」后重试")
        now = time.time()
        return {
            "access_token": body["access_token"],
            "access_expires": now + int(body.get("expires_in") or 28800),
            "refresh_token": body["refresh_token"],
            "refresh_expires": now + int(body.get("refresh_token_expires_in") or 15897600),
        }

    # ----- Accounts -----
    def bind(self, identity_id: str, user: dict, tokens: dict):
        with self.connect() as db:
            other = db.execute(
                "SELECT identity_id FROM accounts WHERE github_id=? AND identity_id!=?", (user["id"], identity_id)
            ).fetchone()
            if other:
                raise GitHubWebError("这个 GitHub 账号已经连接到另一位成员")
            db.execute(
                "INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(identity_id) DO UPDATE SET "
                "github_id=excluded.github_id,github_login=excluded.github_login,git_name=excluded.git_name,"
                "git_email=excluded.git_email,access_token=excluded.access_token,"
                "access_expires=excluded.access_expires,refresh_token=excluded.refresh_token,"
                "refresh_expires=excluded.refresh_expires,updated=excluded.updated",
                (
                    identity_id,
                    user["id"],
                    user["login"],
                    user.get("name") or user["login"],
                    noreply_email(user),
                    tokens["access_token"],
                    tokens["access_expires"],
                    tokens["refresh_token"],
                    tokens["refresh_expires"],
                    time.time(),
                ),
            )
            db.execute("DELETE FROM expected WHERE identity_id=?", (identity_id,))

    def unbind(self, identity_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM accounts WHERE identity_id=?", (identity_id,))
            db.execute("DELETE FROM expected WHERE identity_id=?", (identity_id,))

    def expect(self, identity_id: str, github_login: str):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO expected VALUES(?,?)", (identity_id, github_login))

    def identity_for(self, user: dict) -> tuple[str | None, bool]:
        """(identity_id, invited): an existing binding, or an invitation naming this GitHub login."""
        with self.connect() as db:
            row = db.execute("SELECT identity_id FROM accounts WHERE github_id=?", (user["id"],)).fetchone()
            if row:
                return row["identity_id"], False
            row = db.execute(
                "SELECT identity_id FROM expected WHERE lower(github_login)=lower(?)", (user["login"],)
            ).fetchone()
        return (row["identity_id"], True) if row else (None, False)

    def public(self, identity_id: str) -> dict | None:
        """Who this member is on GitHub, without tokens; None when not connected or expired."""
        with self.connect() as db:
            row = db.execute(
                "SELECT github_id, github_login, git_name, git_email, refresh_expires FROM accounts "
                "WHERE identity_id=?",
                (identity_id,),
            ).fetchone()
        if not row or row["refresh_expires"] < time.time():
            return None
        return {key: row[key] for key in ("github_id", "github_login", "git_name", "git_email")}

    def logins(self) -> dict[str, str]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT identity_id, github_login FROM accounts WHERE refresh_expires>?", (time.time(),)
            ).fetchall()
            expected = db.execute("SELECT identity_id, github_login FROM expected").fetchall()
        return {
            **{row["identity_id"]: {"github_login": row["github_login"], "connected": False} for row in expected},
            **{row["identity_id"]: {"github_login": row["github_login"], "connected": True} for row in rows},
        }

    def push_credential(self, identity_id: str) -> dict:
        """A token valid for at least the next 30 minutes, refreshing it with GitHub when needed."""
        with self.lock:
            with self.connect() as db:
                row = db.execute("SELECT * FROM accounts WHERE identity_id=?", (identity_id,)).fetchone()
            if not row:
                raise GitHubWebError("这位成员还没有连接 GitHub")
            if row["access_expires"] - time.time() > REFRESH_MARGIN:
                return {"token": row["access_token"], "expires": row["access_expires"]}
            if row["refresh_expires"] < time.time():
                raise GitHubWebError("GitHub 授权已过期，请这位成员重新用 GitHub 登录一次")
            app = self.app()
            if not app:
                raise GitHubWebError("GitHub 登录还没有配置")
            tokens = self._token(
                {
                    "client_id": app["client_id"],
                    "client_secret": app["client_secret"],
                    "grant_type": "refresh_token",
                    "refresh_token": row["refresh_token"],
                }
            )
            with self.connect() as db:
                db.execute(
                    "UPDATE accounts SET access_token=?,access_expires=?,refresh_token=?,refresh_expires=?,updated=? "
                    "WHERE identity_id=?",
                    (
                        tokens["access_token"],
                        tokens["access_expires"],
                        tokens["refresh_token"],
                        tokens["refresh_expires"],
                        time.time(),
                        identity_id,
                    ),
                )
            return {"token": tokens["access_token"], "expires": tokens["access_expires"]}


class closing_commit:
    """`with` block that commits on success and always closes the connection."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, kind, value, traceback):
        with closing(self.db):
            if kind is None:
                self.db.commit()
