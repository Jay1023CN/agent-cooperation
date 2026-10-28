from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import time
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .github_web import GitHubWeb, GitHubWebError
from .store import ACTIVE, Conflict, Store, digest, encoded, uid

Short = Annotated[str, Field(min_length=1, max_length=200)]
Long = Annotated[str, Field(min_length=1, max_length=40_000)]
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
COOKIE = "pal_arena_session"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    token: Annotated[str, Field(min_length=20, max_length=200)]


class Invitation(Input):
    name: Short
    kind: Literal["member", "machine"]
    # Lets the invited partner sign in with this GitHub account instead of pasting a token.
    github_login: str = Field(default="", max_length=39, pattern=r"^[A-Za-z0-9-]*$")


class GitHubApp(Input):
    client_id: str = Field(min_length=4, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    client_secret: str = Field(min_length=10, max_length=200)


class Policy(Input):
    push_branches: bool = False
    auto_merge: bool = False
    auto_deploy: bool = False
    release_profile: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9._-]*$")
    routing: Literal["balanced", "quality", "economy"] = "balanced"
    preferred_providers: list[Short] = Field(default_factory=list, max_length=30)
    # Workspace member whose GitHub account authors and pushes task commits; empty keeps machine Git settings.
    commit_as: str = Field(default="", max_length=64, pattern=r"^[a-f0-9]*$")

    @model_validator(mode="after")
    def require_release_executor(self):
        if (self.auto_merge or self.auto_deploy) and not self.release_profile:
            raise ValueError("自动合并和部署必须指定执行电脑上已配置的 release_profile")
        if (self.auto_merge or self.auto_deploy) and not self.push_branches:
            raise ValueError("自动发布需要开启 push_branches，让发布电脑能够从 GitHub 获取已验收的提交")
        return self


class Project(Input):
    name: Short
    brief: str = Field(default="", max_length=40_000)
    repo: str = Field(default="", max_length=500)
    base_ref: str = Field(default="main", min_length=1, max_length=200)
    requirements: list[Short] = Field(default_factory=list, max_length=100)
    policy: Policy = Field(default_factory=Policy)

    @model_validator(mode="after")
    def release_repository(self):
        if (self.policy.auto_merge or self.policy.auto_deploy) and not self.repo:
            raise ValueError("自动发布必须关联 GitHub 仓库")
        return self

    @field_validator("repo")
    @classmethod
    def github_repo(cls, value):
        if not value:
            return value
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", value):
            raise ValueError("仓库必须是 HTTPS GitHub 仓库地址，不包含账号口令或查询参数")
        return value

    @field_validator("base_ref")
    @classmethod
    def git_ref(cls, value):
        if value.startswith(("-", "/")) or any(c in value for c in ("..", "@{", "\\", ":", "?", "*", "[", " ")):
            raise ValueError("分支名称无效")
        if any(ord(c) < 32 for c in value):
            raise ValueError("分支名称无效")
        return value


class ProjectUpdate(Input):
    expected_settings_version: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    name: Short | None = None
    brief: str | None = Field(default=None, max_length=40_000)
    repo: str | None = Field(default=None, max_length=500)
    base_ref: str | None = Field(default=None, min_length=1, max_length=200)
    requirements: list[Short] | None = Field(default=None, min_length=1, max_length=100)
    policy: dict | None = None
    expected_revision: int = Field(ge=1)


class Message(Input):
    content: Long
    channel: Literal["shared", "personal"] = "shared"
    client_request_id: str | None = Field(default=None, min_length=8, max_length=100)


class Task(Input):
    title: Short
    prompt: Long
    kind: Literal["leader", "worker", "reviewer"] = "worker"
    capability: Short = "code"
    acceptance: list[Short] = Field(default_factory=list, max_length=100)
    dependencies: list[Short] = Field(default_factory=list, max_length=40)
    parent_id: str | None = Field(default=None, max_length=100)
    source_id: str | None = Field(default=None, max_length=100)
    requested_agent: str | None = Field(default=None, max_length=100)
    client_request_id: str | None = Field(default=None, min_length=8, max_length=100)
    difficulty: Literal["routine", "standard", "deep"] = "standard"
    context_tokens: int = Field(default=0, ge=0, le=1_000_000)


class Requirements(Input):
    requirements: list[Short] = Field(min_length=1, max_length=100)
    expected_revision: int | None = Field(default=None, ge=1)


class Readiness(Input):
    provider: str = Field(default="", max_length=200)
    installed: bool = False
    transport_ready: bool | None = None
    account_readiness: Literal["unknown", "available", "unavailable"] = "unknown"
    supports_new: bool = False
    supports_existing: bool = False
    dispatchable: bool = False
    reason: str = Field(default="", max_length=2000)


class RoutingProfile(Input):
    quality: int = Field(default=3, ge=1, le=5)
    speed: int = Field(default=3, ge=1, le=5)
    economy: int = Field(default=3, ge=1, le=5)
    max_difficulty: Literal["routine", "standard", "deep"] = "deep"
    quota_state: Literal["unknown", "available", "limited", "exhausted"] = "unknown"
    paused_until: float = Field(default=0, ge=0)
    context_window: int | None = Field(default=None, ge=1, le=10_000_000)
    preferred_for: list[Short] = Field(default_factory=list, max_length=30)


class Agent(Input):
    key: Short
    name: Short
    provider: Short
    roles: list[Literal["leader", "worker", "reviewer", "publisher"]] = Field(min_length=1, max_length=4)
    capabilities: list[Short] = Field(default_factory=lambda: ["code"], max_length=30)
    model: str | None = Field(default=None, max_length=200)
    preference: int = Field(default=10, ge=0, le=1000)
    enabled: bool = True
    mode: Literal["new", "existing"] = "new"
    readiness: Readiness | None = None
    routing_profile: RoutingProfile = Field(default_factory=RoutingProfile)


class GitHubMember(Input):
    member_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-f0-9]+$")
    github_id: int = Field(ge=1)
    github_login: str = Field(min_length=1, max_length=39, pattern=r"^[A-Za-z0-9-]+$")


class Registration(Input):
    name: Short
    os: Short
    slots: int = Field(default=1, ge=1, le=16)
    agents: list[Agent] = Field(default_factory=list, max_length=32)
    storage_bytes: int = Field(default=0, ge=0)
    github_members: list[GitHubMember] = Field(default_factory=list, max_length=16)

    @field_validator("github_members")
    @classmethod
    def distinct_members(cls, value):
        if len({item.member_id for item in value}) != len(value):
            raise ValueError("每位成员在一台电脑上只能绑定一个 GitHub 账号")
        return value

    @field_validator("agents")
    @classmethod
    def distinct_agents(cls, value):
        if len({a.key for a in value}) != len(value) or len({a.name for a in value}) != len(value):
            raise ValueError("每个本机 agent 的 key 和 name 必须唯一")
        return value


class Heartbeat(Input):
    storage_bytes: int = Field(default=0, ge=0)


class WorkspaceAttempt(Input):
    id: Short
    attempt: int = Field(ge=1)


class WorkspaceStatus(Input):
    tasks: list[WorkspaceAttempt] = Field(default_factory=list, max_length=100)


class Log(Input):
    text: str = Field(max_length=20_000)


class Completion(Input):
    result: str = Field(default="", max_length=80_000)
    evidence: dict = Field(default_factory=dict)
    failed: bool = False


class Review(Input):
    decision: Literal["accept", "changes_requested"]
    summary: Long
    evidence: list[Long] = Field(min_length=1, max_length=100)


class Finish(Input):
    summary: Long


class Recovery(Input):
    action: Literal["retry", "cancel"]
    requested_agent: str | None = Field(default=None, max_length=100)


class Pin(Input):
    pinned: bool


class PermissionItem(Input):
    id: Short
    tool: dict = Field(default_factory=dict)
    options: list[dict] = Field(default_factory=list, max_length=30)


class PermissionBatch(Input):
    permissions: list[PermissionItem] = Field(default_factory=list, max_length=50)


class PermissionDecision(Input):
    option_id: str | None = Field(default=None, max_length=200)


class MemoryCreate(Input):
    scope: Literal["project", "member"] = "project"
    category: Short = "context"
    content: str = Field(min_length=1, max_length=8000)
    status: Literal["active", "proposed"] = "active"

    @field_validator("content")
    @classmethod
    def meaningful_content(cls, value):
        if not value.strip():
            raise ValueError("记忆内容不能为空")
        return value.strip()


class MemoryUpdate(Input):
    category: Short | None = None
    content: str | None = Field(default=None, min_length=1, max_length=8000)
    status: Literal["proposed", "active", "disabled"] | None = None
    expected_revision: int = Field(ge=1)

    @field_validator("content")
    @classmethod
    def meaningful_content(cls, value):
        if value is not None and not value.strip():
            raise ValueError("记忆内容不能为空")
        return value.strip() if value is not None else None


class MemoryProposal(Input):
    category: Short = "context"
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("content")
    @classmethod
    def meaningful_content(cls, value):
        if not value.strip():
            raise ValueError("记忆内容不能为空")
        return value.strip()


class Cleanup(Input):
    project_id: Short
    keep: int = Field(default=3, ge=0, le=100)
    channel: Literal["shared", "personal"] = "shared"


def create_app(root: str | Path, quota: int = 5_000_000_000) -> FastAPI:
    app = FastAPI(title="PAL Arena", docs_url=None, redoc_url=None, openapi_url=None)
    store = Store(root, quota=quota)
    app.state.store = store
    github_web = GitHubWeb(store.root)
    app.state.github_web = github_web
    store.github_lookup = github_web.public
    app.state.upload_lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        with store.lock:
            store.db.close()

    app.router.lifespan_context = lifespan
    with store.tx():
        store.db.execute(
            "CREATE TABLE IF NOT EXISTS permissions ("
            "task_id TEXT NOT NULL, attempt INTEGER NOT NULL, id TEXT NOT NULL, "
            "tool TEXT NOT NULL, options TEXT NOT NULL, decided INTEGER NOT NULL DEFAULT 0, "
            "option_id TEXT, PRIMARY KEY(task_id,attempt,id))"
        )

    with store.tx():
        store.db.execute(
            "CREATE TABLE IF NOT EXISTS request_receipts ("
            "actor TEXT NOT NULL, operation TEXT NOT NULL, request_id TEXT NOT NULL, "
            "fingerprint TEXT NOT NULL, response TEXT NOT NULL, created REAL NOT NULL, "
            "PRIMARY KEY(actor,operation,request_id))"
        )

    with store.tx():
        store.db.execute(
            "CREATE TABLE IF NOT EXISTS identity_revocations "
            "(identity_id TEXT PRIMARY KEY, revoked_at REAL NOT NULL)"
        )

    def receipt_key(request, body):
        key = request.headers.get("idempotency-key") or body.client_request_id
        if key is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{8,100}", key):
            raise HTTPException(422, "请求 ID 必须是 8–100 个字母、数字或 . _ : -")
        return key

    def receipt_read(actor, operation, key, body):
        if key is None:
            return None
        previous = store.one(
            "SELECT fingerprint,response FROM request_receipts " "WHERE actor=? AND operation=? AND request_id=?",
            (actor, operation, key),
        )
        if previous:
            if previous["fingerprint"] != digest(encoded(body)):
                raise Conflict("同一个请求 ID 不能用于不同内容")
            return json.loads(previous["response"])
        return None

    def receipt_write(actor, operation, key, body, response):
        if key is not None:
            store.db.execute(
                "INSERT INTO request_receipts VALUES(?,?,?,?,?,?)",
                (actor, operation, key, digest(encoded(body)), encoded(response), time.time()),
            )
        return response

    @app.exception_handler(Conflict)
    async def handle_conflict(_request: Request, exc: Conflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.middleware("http")
    async def headers_and_body_limit(request: Request, call_next):
        # JSON actions are intentionally bounded separately from streamed artifacts.
        size = request.headers.get("content-length")
        if size:
            try:
                limit = MAX_ARTIFACT_BYTES if request.url.path.endswith("/artifacts") else 1_000_000
                if int(size) < 0 or int(size) > limit:
                    return JSONResponse({"detail": "请求过大"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Content-Length 无效"}, status_code=400)
        result = await call_next(request)
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "same-origin"
        result.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            result.headers["Cache-Control"] = "no-store"
        return result

    def bearer(request):
        value = request.headers.get("authorization", "")
        if value.startswith("Bearer "):
            return value[7:]
        return None

    def check_origin(request):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if origin == "null" or parsed.netloc != request.url.netloc or parsed.scheme != request.url.scheme:
                raise HTTPException(403, "只能从当前工作台提交操作")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "不能跨站提交操作")

    def identity(request, kind="member"):
        token = bearer(request)
        with store.lock:
            if token:
                actor = store.authenticate(token)
            else:
                if request.method not in ("GET", "HEAD"):
                    check_origin(request)
                actor = store.authenticate(request.cookies.get(COOKIE), cookie=True)
            if (
                not actor
                or actor["kind"] != kind
                or store.one("SELECT identity_id FROM identity_revocations WHERE identity_id=?", (actor["id"],))
            ):
                raise HTTPException(401, "请使用有效的成员或电脑凭据")
            return actor

    def run(request, *, worker_only=False):
        token = bearer(request)
        if not token:
            raise HTTPException(401, "缺少运行凭据")
        try:
            return store.active_run(token, worker_only=worker_only), token
        except Conflict as exc:
            raise HTTPException(410, {"code": "run_inactive", "message": str(exc)}) from exc

    def project_view(project):
        result = dict(project)
        result["policy"] = {**Policy().model_dump(), **project["policy"]}
        settings = {
            key: result[key] for key in ("name", "brief", "repo", "base_ref", "requirements", "policy", "revision")
        }
        result["settings_version"] = digest(json.dumps(settings, ensure_ascii=False, sort_keys=True))
        return result

    def visible_task(actor, task_id):
        task = store.task(task_id)
        if task["channel"] not in ("shared", f'personal:{actor["id"]}'):
            raise HTTPException(404, "任务不存在")
        return task

    def artifact_actor(request, project_id, task_id=None, write=False):
        token = bearer(request)
        if token and not store.authenticate(token):
            task, _ = run(request)
            if task["project_id"] != project_id or (write and task_id and task["id"] != task_id):
                raise HTTPException(403, "产物必须属于当前运行")
            if task_id and store.task(task_id)["channel"] not in ("shared", task["channel"]):
                raise HTTPException(404, "产物不存在")
            return task["id"] if write else task_id
        actor = identity(request)
        if task_id:
            visible_task(actor, task_id)
        return task_id

    def validate_task(data: Task, project_id):
        if data.kind == "reviewer" or data.source_id:
            raise Conflict("验收任务由执行完成后的独立验收流程自动创建")
        if data.requested_agent and not store.one("SELECT id FROM agents WHERE id=?", (data.requested_agent,)):
            raise Conflict("指定的 agent 不存在")

    def revise_requirements(project_id, requirements, expected_revision=None, task_id=None, force=False):
        project = store.project(project_id)
        if requirements == project["requirements"] and not force:
            if task_id:
                store.db.execute(
                    "UPDATE tasks SET revision=?,acceptance=? WHERE id=?",
                    (project["revision"], encoded(project["requirements"]), task_id),
                )
            return project
        if expected_revision is not None and expected_revision != project["revision"]:
            raise Conflict("需求已被其他成员修改，请刷新后再保存")
        if store.one(
            'SELECT id FROM tasks WHERE project_id=? AND kind="publisher" AND status="running"', (project_id,)
        ):
            raise Conflict("发布任务正在执行；先停止发布并确认其实际状态，再修改需求")
        store.db.execute(
            'UPDATE projects SET requirements=?,revision=revision+1,status="active" WHERE id=?',
            (encoded(requirements), project_id),
        )
        updated = store.project(project_id)
        store.db.execute(
            'UPDATE tasks SET status="cancelled",lease_hash=NULL,agent_hash=NULL,lease_until=NULL,updated=? '
            'WHERE project_id=? AND kind="publisher" AND status="queued"',
            (time.time(), project_id),
        )
        stale_tasks = store.all(
            'SELECT id FROM tasks WHERE project_id=? AND channel="shared" AND kind="worker" '
            'AND status IN ("done","in_review","queued","coordinating")',
            (project_id,),
        )
        for stale in stale_tasks:
            store.db.execute(
                'UPDATE tasks SET status="changes_requested",review=NULL,updated=? WHERE id=?',
                (time.time(), stale["id"]),
            )
            store.db.execute(
                'UPDATE tasks SET status="cancelled",lease_hash=NULL,agent_hash=NULL,lease_until=NULL,updated=? '
                'WHERE source_id=? AND kind="reviewer" AND status="queued"',
                (time.time(), stale["id"]),
            )
            store.event(project_id, stale["id"], "changes_requested", "需求已变更，请按新验收清单复核并重试此任务")
        if task_id:
            store.db.execute(
                "UPDATE tasks SET revision=?,acceptance=? WHERE id=?",
                (updated["revision"], encoded(requirements), task_id),
            )
        store.event(project_id, task_id, "requirements", f'验收清单更新为 v{updated["revision"]}')
        return updated

    def set_requirements(project_id, data: Requirements, task_id=None):
        return revise_requirements(project_id, data.requirements, data.expected_revision, task_id)

    @app.get("/health")
    def health():
        return {"status": "ok", "name": "PAL Arena"}

    @app.post("/api/login")
    def login(data: Login, request: Request, response: Response):
        check_origin(request)
        token = secrets.token_urlsafe(32)
        with store.tx():
            actor = store.authenticate(data.token)
            if (
                not actor
                or actor["kind"] != "member"
                or store.one("SELECT identity_id FROM identity_revocations WHERE identity_id=?", (actor["id"],))
            ):
                raise HTTPException(401, "成员凭据无效")
            store.db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            store.db.execute(
                "INSERT INTO sessions VALUES(?,?,?)", (digest(token), actor["id"], time.time() + 7 * 86400)
            )
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            max_age=7 * 86400,
            path="/",
        )
        return actor

    @app.get("/api/me")
    def me(request: Request):
        return identity(request)

    STATE_COOKIE = "pal_arena_github_state"

    def callback_uri(request: Request) -> str:
        host = request.headers.get("host") or request.url.netloc
        # Tailscale Serve terminates HTTPS and forwards plain HTTP to this loopback port.
        scheme = "https" if host.split(":")[0].endswith(".ts.net") else request.url.scheme
        return f"{scheme}://{host}/api/github/callback"

    def github_redirect(url: str, request: Request, state: str | None = None) -> RedirectResponse:
        response = RedirectResponse(url, status_code=303)
        if state:
            # Lax: GitHub's redirect back is a top-level navigation from another site.
            response.set_cookie(
                STATE_COOKIE,
                state,
                httponly=True,
                secure=callback_uri(request).startswith("https://"),
                samesite="lax",
                max_age=600,
                path="/api/github",
            )
        return response

    def github_failure(message: str) -> RedirectResponse:
        response = RedirectResponse("/?" + urlencode({"github_error": message}), status_code=303)
        response.delete_cookie(STATE_COOKIE, path="/api/github")
        return response

    @app.get("/api/github/status")
    def github_status():
        return {"enabled": github_web.app() is not None}

    @app.get("/api/github/app")
    def github_app_get(request: Request):
        identity(request)
        settings = github_web.app()
        return {
            "configured": settings is not None,
            "client_id": settings["client_id"] if settings else "",
            "callback_url": callback_uri(request),
        }

    @app.put("/api/github/app")
    def github_app_set(data: GitHubApp, request: Request):
        identity(request)
        github_web.set_app(data.client_id, data.client_secret)
        return {"configured": True, "client_id": data.client_id, "callback_url": callback_uri(request)}

    @app.get("/api/github/login")
    def github_login(request: Request):
        try:
            state, url = github_web.start("login", callback_uri(request))
        except GitHubWebError as exc:
            return github_failure(str(exc))
        return github_redirect(url, request, state)

    @app.get("/api/github/connect")
    def github_connect(request: Request):
        actor = identity(request)
        try:
            state, url = github_web.start("connect", callback_uri(request), actor["id"])
        except GitHubWebError as exc:
            return github_failure(str(exc))
        return github_redirect(url, request, state)

    @app.get("/api/github/callback")
    def github_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if error or not code:
            return github_failure("GitHub 授权已取消")
        if not state or not secrets.compare_digest(state, request.cookies.get(STATE_COOKIE, "")):
            return github_failure("登录请求无效或已过期，请重新点一次「用 GitHub 登录」")
        try:
            result = github_web.finish(state, code)
            user, tokens = result["user"], result["tokens"]
            if result["purpose"] == "connect":
                identity_id = result["identity_id"]
            else:
                identity_id, _ = github_web.identity_for(user)
                if not identity_id:
                    raise GitHubWebError(
                        f"GitHub 账号 {user['login']} 还没有加入这个工作台。请成员在「伙伴与令牌」里邀请你时填写这个用户名。"
                    )
            with store.tx():
                member = store.one(
                    'SELECT id FROM identities i WHERE id=? AND kind="member" AND NOT EXISTS '
                    "(SELECT 1 FROM identity_revocations r WHERE r.identity_id=i.id)",
                    (identity_id,),
                )
                if not member:
                    raise GitHubWebError("这个成员的访问已被撤销")
                github_web.bind(identity_id, user, tokens)
                session_token = secrets.token_urlsafe(32)
                store.db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
                store.db.execute(
                    "INSERT INTO sessions VALUES(?,?,?)", (digest(session_token), identity_id, time.time() + 7 * 86400)
                )
        except GitHubWebError as exc:
            return github_failure(str(exc))
        response = RedirectResponse("/?github=connected", status_code=303)
        response.delete_cookie(STATE_COOKIE, path="/api/github")
        response.set_cookie(
            COOKIE,
            session_token,
            httponly=True,
            secure=callback_uri(request).startswith("https://"),
            samesite="strict",
            max_age=7 * 86400,
            path="/",
        )
        return response

    @app.delete("/api/github/account")
    def github_disconnect(request: Request):
        actor = identity(request)
        github_web.unbind(actor["id"])
        return {"ok": True}

    @app.get("/api/setup/address")
    def setup_address(request: Request):
        """The HTTPS address other computers should use, as last published by `share`."""
        identity(request)
        from .network import saved

        return saved(store.root) or {"state": "not_shared", "url": None, "message": "", "action_url": None}

    @app.post("/api/setup/share")
    def setup_share(request: Request):
        """Publish this workspace with Tailscale Serve; only from the center computer itself."""
        identity(request)
        client = request.client.host if request.client else ""
        if client not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(403, "只能在中心那台电脑上开启共享")
        from .network import share

        return share(request.url.port or 8765, store.root)

    @app.get("/api/setup/center")
    def setup_center(request: Request):
        identity(request)
        from agent_link.device_setup import inspect_device

        result = inspect_device()
        result["scope"] = "workbench_host"
        return result

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        identity(request)
        token = request.cookies.get(COOKIE)
        if token:
            with store.tx():
                store.db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/invites", status_code=201)
    def invite(data: Invitation, request: Request):
        with store.tx():
            identity(request)
            created = store.new_identity(data.name, data.kind)
        if data.kind == "member" and data.github_login:
            github_web.expect(created["id"], data.github_login)
        return created

    def invalidate_machine(machine_id):
        running = store.all(
            "SELECT t.id,t.project_id,t.kind,t.channel FROM tasks t JOIN agents a ON a.id=t.agent_id "
            'WHERE a.machine_id=? AND t.status="running"',
            (machine_id,),
        )
        for task in running:
            store.db.execute(
                'UPDATE tasks SET status="interrupted",lease_hash=NULL,agent_hash=NULL,lease_until=NULL,updated=? WHERE id=?',
                (time.time(), task["id"]),
            )
            if task["kind"] == "publisher":
                store.db.execute('UPDATE projects SET status="blocked" WHERE id=?', (task["project_id"],))
            store.event(task["project_id"], task["id"], "interrupted", "电脑凭据已撤销或轮换，旧执行端已失去回写权限")
        store.db.execute("UPDATE agents SET enabled=0 WHERE machine_id=?", (machine_id,))
        store.db.execute("UPDATE machines SET seen=0 WHERE id=?", (machine_id,))
        for project_id, channel in {(task["project_id"], task["channel"]) for task in running}:
            store.reconcile(project_id, channel)

    @app.get("/api/identities")
    def identities_list(request: Request):
        actor = identity(request)
        with store.lock:
            identities = store.all(
                "SELECT i.id,i.name,i.kind,i.created,r.revoked_at FROM identities i "
                "LEFT JOIN identity_revocations r ON r.identity_id=i.id ORDER BY i.created"
            )
            github = github_web.logins()
            for item in identities:
                item["is_self"] = item["id"] == actor["id"]
                item["revoked"] = item["revoked_at"] is not None
                item["github"] = github.get(item["id"])
            return {"identities": identities}

    @app.post("/api/identities/{identity_id}/rotate")
    def identity_rotate(identity_id: str, request: Request, response: Response):
        with store.tx():
            actor = identity(request)
            target = store.one("SELECT * FROM identities WHERE id=?", (identity_id,))
            if not target:
                raise HTTPException(404, "成员或电脑不存在")
            if target["kind"] == "member" and target["id"] != actor["id"]:
                raise HTTPException(403, "成员只能轮换自己的登录凭据，不能获取 partner 的个人会话权限")
            token = secrets.token_urlsafe(32)
            store.db.execute("UPDATE identities SET token_hash=? WHERE id=?", (digest(token), identity_id))
            store.db.execute("DELETE FROM sessions WHERE identity_id=?", (identity_id,))
            store.db.execute("DELETE FROM identity_revocations WHERE identity_id=?", (identity_id,))
            if target["kind"] == "machine":
                invalidate_machine(identity_id)
            else:
                session_token = secrets.token_urlsafe(32)
                store.db.execute(
                    "INSERT INTO sessions VALUES(?,?,?)", (digest(session_token), identity_id, time.time() + 7 * 86400)
                )
                response.set_cookie(
                    COOKIE,
                    session_token,
                    httponly=True,
                    secure=request.url.scheme == "https",
                    samesite="strict",
                    max_age=7 * 86400,
                    path="/",
                )
                bootstrap = store.root / "owner-token.txt"
                if (
                    bootstrap.is_file()
                    and digest(bootstrap.read_text(encoding="utf-8").strip()) == target["token_hash"]
                ):
                    replacement = bootstrap.with_suffix(".tmp")
                    replacement.write_text(token, encoding="utf-8")
                    replacement.chmod(0o600)
                    replacement.replace(bootstrap)
            return {
                "id": target["id"],
                "name": target["name"],
                "kind": target["kind"],
                "token": token,
                "revoked": False,
            }

    @app.delete("/api/identities/{identity_id}")
    def identity_revoke(identity_id: str, request: Request, response: Response):
        with store.tx():
            actor = identity(request)
            target = store.one("SELECT id,name,kind FROM identities WHERE id=?", (identity_id,))
            if not target:
                raise HTTPException(404, "成员或电脑不存在")
            if store.one("SELECT identity_id FROM identity_revocations WHERE identity_id=?", (identity_id,)):
                return {"ok": True, "revoked": True}
            if target["kind"] == "member":
                remaining = store.one(
                    'SELECT count(*) AS n FROM identities i WHERE i.kind="member" AND NOT EXISTS '
                    "(SELECT 1 FROM identity_revocations r WHERE r.identity_id=i.id)"
                )["n"]
                if remaining <= 1:
                    raise Conflict("至少保留一位有效成员，不能撤销最后一个登录凭据")
            store.db.execute(
                "UPDATE identities SET token_hash=? WHERE id=?", (digest(secrets.token_urlsafe(32)), identity_id)
            )
            store.db.execute("INSERT INTO identity_revocations VALUES(?,?)", (identity_id, time.time()))
            store.db.execute("DELETE FROM sessions WHERE identity_id=?", (identity_id,))
            if target["kind"] == "machine":
                invalidate_machine(identity_id)
            else:
                personal_tasks = store.all(
                    "SELECT id,project_id FROM tasks WHERE channel=? "
                    'AND status IN ("queued","running","in_review","coordinating")',
                    ("personal:" + identity_id,),
                )
                for task in personal_tasks:
                    store.db.execute(
                        'UPDATE tasks SET status="cancelled",lease_hash=NULL,agent_hash=NULL,lease_until=NULL,updated=? WHERE id=?',
                        (time.time(), task["id"]),
                    )
                    store.event(task["project_id"], task["id"], "cancelled", "个人会话所属成员的访问权限已撤销")
            if identity_id == actor["id"]:
                response.delete_cookie(COOKIE, path="/")
        if target["kind"] == "member":
            github_web.unbind(identity_id)
        return {"ok": True, "revoked": True}

    @app.get("/api/state")
    def state(request: Request, project_id: str | None = None, channel: Literal["shared", "personal"] = "shared"):
        with store.tx():
            actor = identity(request)
            store.expire_leases()
        snapshot = store.snapshot(project_id, channel, actor["id"])
        snapshot["projects"] = [project_view(project) for project in snapshot["projects"]]
        return snapshot

    def check_commit_identity(policy: dict):
        member = policy.get("commit_as")
        if member and not store.one(
            'SELECT i.id FROM identities i WHERE i.id=? AND i.kind="member" AND NOT EXISTS '
            "(SELECT 1 FROM identity_revocations r WHERE r.identity_id=i.id)",
            (member,),
        ):
            raise HTTPException(422, "提交身份必须是工作台中仍有效的成员")

    @app.post("/api/projects", status_code=201)
    def project_create(data: Project, request: Request):
        project_id = uid()
        with store.tx():
            identity(request)
            check_commit_identity(data.policy.model_dump())
            store.db.execute(
                "INSERT INTO projects(id,name,brief,repo,base_ref,requirements,policy,status,created) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    data.name,
                    data.brief,
                    data.repo,
                    data.base_ref,
                    encoded(data.requirements),
                    encoded(data.policy.model_dump()),
                    "active",
                    time.time(),
                ),
            )
            store.event(project_id, None, "project", f"创建项目：{data.name}")
            return project_view(store.project(project_id))

    @app.patch("/api/projects/{project_id}")
    def project_update(project_id: str, data: ProjectUpdate, request: Request):
        with store.tx():
            identity(request)
            previous = project_view(store.project(project_id))
            updates = data.model_dump(exclude={"expected_revision", "expected_settings_version"}, exclude_none=True)
            if not updates:
                raise HTTPException(422, "至少更新一个项目字段")
            candidate = {key: previous[key] for key in ("name", "brief", "repo", "base_ref", "requirements", "policy")}
            candidate.update(updates)
            if "policy" in updates:
                candidate["policy"] = {**previous["policy"], **updates["policy"]}
            try:
                checked = Project.model_validate(candidate)
            except ValidationError as exc:
                raise HTTPException(422, exc.errors(include_context=False)) from exc
            final = checked.model_dump()
            check_commit_identity(final["policy"])
            if all(previous[key] == value for key, value in final.items()):
                return previous
            if (
                data.expected_revision != previous["revision"]
                or data.expected_settings_version != previous["settings_version"]
            ):
                raise Conflict("项目已被其他成员更新，请刷新后再保存")
            execution_changed = any(final[key] != previous[key] for key in ("repo", "base_ref", "policy"))
            if execution_changed and store.one(
                'SELECT id FROM tasks WHERE project_id=? AND channel="shared" '
                'AND status IN ("queued","running","in_review","coordinating")',
                (project_id,),
            ):
                raise Conflict("项目还有活动任务；结束或取消后再修改仓库、分支或调度发布设置")
            scope_changed = any(final[key] != previous[key] for key in ("brief", "repo", "base_ref", "requirements"))
            if scope_changed:
                revise_requirements(project_id, final["requirements"], data.expected_revision, force=True)
            elif execution_changed:
                # A changed release profile requires a fresh plan even when source requirements are unchanged.
                store.db.execute(
                    'UPDATE tasks SET status="changes_requested",updated=? '
                    'WHERE project_id=? AND kind="publisher" AND attempt=0 AND status!="done"',
                    (time.time(), project_id),
                )
                store.db.execute('UPDATE projects SET status="active" WHERE id=?', (project_id,))
            store.db.execute(
                "UPDATE projects SET name=?,brief=?,repo=?,base_ref=?,policy=? WHERE id=?",
                (final["name"], final["brief"], final["repo"], final["base_ref"], encoded(final["policy"]), project_id),
            )
            store.event(project_id, None, "project_updated", "项目设置已更新")
            return project_view(store.project(project_id))

    @app.post("/api/projects/{project_id}/messages", status_code=201)
    def message_create(project_id: str, data: Message, request: Request):
        request_id = receipt_key(request, data)
        body = data.model_dump(exclude={"client_request_id"})
        operation = f"message:{project_id}"
        with store.tx():
            actor = identity(request)
            store.project(project_id)
            previous = receipt_read(actor["id"], operation, request_id, body)
            if previous is not None:
                return previous
            message_id = uid()
            channel = f'personal:{actor["id"]}' if data.channel == "personal" else "shared"
            store.db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?)",
                (message_id, project_id, actor["name"], data.content, channel, time.time()),
            )
            task = store.create_task(
                project_id,
                "回应并协调 · " + data.content[:60],
                data.content,
                kind="leader",
                capability="planning",
                channel=channel,
            )
            return receipt_write(
                actor["id"],
                operation,
                request_id,
                body,
                {"id": message_id, "task": task, "dispatched": True, "note": ""},
            )

    @app.post("/api/projects/{project_id}/requirements")
    def requirements_update(project_id: str, data: Requirements, request: Request):
        with store.tx():
            identity(request)
            if data.expected_revision is None and data.requirements != store.project(project_id)["requirements"]:
                raise HTTPException(422, "修改验收清单必须提供 expected_revision；请先刷新项目再保存")
            return project_view(set_requirements(project_id, data))

    @app.post("/api/projects/{project_id}/tasks", status_code=201)
    def task_create(project_id: str, data: Task, request: Request):
        request_id = receipt_key(request, data)
        body = data.model_dump(exclude={"client_request_id"})
        operation = f"task:{project_id}"
        with store.tx():
            actor = identity(request)
            previous = receipt_read(actor["id"], operation, request_id, body)
            if previous is not None:
                return previous
            validate_task(data, project_id)
            for ref in [*data.dependencies, *([data.parent_id] if data.parent_id else [])]:
                if visible_task(actor, ref)["channel"] != "shared":
                    raise Conflict("共享任务不能引用个人会话任务")
            response = store.create_task(project_id, **body)
            return receipt_write(actor["id"], operation, request_id, body, response)

    def memory_for_member(actor, memory_id):
        memory = store.one("SELECT * FROM memories WHERE id=?", (memory_id,))
        if not memory or (memory["scope"] == "member" and memory["scope_id"] != actor["id"]):
            raise HTTPException(404, "记忆不存在")
        return memory

    def insert_memory(scope, scope_id, category, content, status, author_id, source_task=None):
        previous = store.one(
            "SELECT * FROM memories WHERE scope=? AND scope_id=? AND category=? "
            "AND content=? AND status=? ORDER BY updated DESC LIMIT 1",
            (scope, scope_id, category, content, status),
        )
        if previous:
            return previous
        memory_id = uid()
        now = time.time()
        store.db.execute(
            "INSERT INTO memories(id,scope,scope_id,category,content,status,revision,"
            "source_task,author_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (memory_id, scope, scope_id, category, content, status, 1, source_task, author_id, now, now),
        )
        return store.one("SELECT * FROM memories WHERE id=?", (memory_id,))

    @app.get("/api/projects/{project_id}/memories")
    def memories_list(project_id: str, request: Request, scope: Literal["project", "member"] = "project"):
        actor = identity(request)
        with store.lock:
            store.project(project_id)
            scope_id = project_id if scope == "project" else actor["id"]
            return {
                "memories": store.all(
                    "SELECT * FROM memories WHERE scope=? AND scope_id=? " "ORDER BY updated DESC LIMIT 200",
                    (scope, scope_id),
                )
            }

    @app.post("/api/projects/{project_id}/memories", status_code=201)
    def memory_create(project_id: str, data: MemoryCreate, request: Request):
        with store.tx():
            actor = identity(request)
            store.project(project_id)
            scope_id = project_id if data.scope == "project" else actor["id"]
            memory = insert_memory(data.scope, scope_id, data.category, data.content, data.status, actor["id"])
            if data.scope == "project":
                store.event(project_id, None, "memory", f"保存项目记忆：{data.category}（{data.status}）")
            return memory

    @app.put("/api/memories/{memory_id}")
    def memory_update(memory_id: str, data: MemoryUpdate, request: Request):
        with store.tx():
            actor = identity(request)
            memory = memory_for_member(actor, memory_id)
            fields = data.model_dump(exclude={"expected_revision"}, exclude_none=True)
            if not fields:
                raise HTTPException(422, "至少更新一个记忆字段")
            updated = {**memory, **fields}
            if all(memory[key] == value for key, value in fields.items()):
                return memory
            if data.expected_revision != memory["revision"]:
                raise Conflict("记忆已被其他成员更新，请刷新后再保存")
            store.db.execute(
                "UPDATE memories SET category=?,content=?,status=?,revision=revision+1,updated=? WHERE id=?",
                (updated["category"], updated["content"], updated["status"], time.time(), memory_id),
            )
            if memory["scope"] == "project":
                store.event(
                    memory["scope_id"],
                    memory["source_task"],
                    "memory",
                    f'更新项目记忆：{updated["category"]}（{updated["status"]}）',
                )
            return store.one("SELECT * FROM memories WHERE id=?", (memory_id,))

    @app.get("/api/tasks/{task_id}")
    def task_read(task_id: str, request: Request):
        actor = identity(request)
        with store.lock:
            task = store.public_task(visible_task(actor, task_id))
            task["permissions"] = store.all(
                "SELECT id,tool,options,decided,option_id FROM permissions WHERE task_id=? AND attempt=?",
                (task_id, task["attempt"]),
            )
            for permission in task["permissions"]:
                permission["tool"] = json.loads(permission["tool"])
                permission["options"] = json.loads(permission["options"])
            return task

    @app.get("/api/tasks/{task_id}/timeline")
    def task_timeline(
        task_id: str,
        request: Request,
        before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 40,
    ):
        actor = identity(request)
        with store.lock:
            task = visible_task(actor, task_id)
            # Include independent reviews, but never pull in another channel's history.
            events = store.all(
                "SELECT e.id,e.task_id,e.kind,e.text,e.created,t.title AS task_title,t.kind AS task_kind "
                "FROM events e JOIN tasks t ON t.id=e.task_id "
                "WHERE t.project_id=? AND t.channel=? AND (t.id=? OR (t.source_id=? AND t.kind='reviewer')) "
                "AND (? IS NULL OR e.id<?) ORDER BY e.id DESC LIMIT ?",
                (task["project_id"], task["channel"], task_id, task_id, before, before, limit + 1),
            )
            more = len(events) > limit
            events = events[:limit]
            return {
                "task_id": task_id,
                "events": list(reversed(events)),
                "next_before": events[-1]["id"] if more else None,
            }

    def retry_task(task, reason):
        task_id = task["id"]
        if (
            task["channel"] == "shared"
            and task["kind"] != "publisher"
            and store.one(
                'SELECT id FROM tasks WHERE project_id=? AND kind="publisher" AND status="running"',
                (task["project_id"],),
            )
        ):
            raise Conflict("发布执行期间不能重新启动共享任务，请先停止发布并确认实际状态")
        project = store.project(task["project_id"])
        stale_done = task["status"] == "done" and task["revision"] != project["revision"]
        if (
            task["status"] not in ("failed", "blocked", "interrupted", "changes_requested", "cancelled")
            and not stale_done
        ):
            raise Conflict("只有失败、中断、阻塞、取消、待修改或旧需求版本的任务可以重试")
        if task["kind"] == "worker" and store.one(
            "SELECT id FROM tasks WHERE source_id=? " 'AND status IN ("queued","running")', (task_id,)
        ):
            raise Conflict("独立验收仍在进行，请先结束验收")
        if task["kind"] == "reviewer":
            source = store.task(task["source_id"])
            if source["status"] != "in_review" or source["revision"] != project["revision"]:
                raise Conflict("源任务已不再等待当前版本验收，请重新执行源任务后安排验收")
        project = store.project(task["project_id"])
        if task["kind"] == "publisher":
            if task["revision"] != project["revision"]:
                raise Conflict("旧需求版本的发布任务不能重试，请先完成当前版本验收并重新确认交付")
            workers = [
                store.task(row["id"])
                for row in store.all(
                    'SELECT id FROM tasks WHERE project_id=? AND channel="shared" AND kind="worker" AND status!="cancelled"',
                    (task["project_id"],),
                )
            ]
            if not workers or any(
                worker["status"] != "done"
                or worker["revision"] != project["revision"]
                or not worker["review"]
                or worker["review"]["decision"] != "accept"
                for worker in workers
            ):
                raise Conflict("当前项目还有未验收的工作，不能重试发布")
            if store.one(
                'SELECT id FROM tasks WHERE project_id=? AND id!=? AND channel="shared" '
                'AND status IN ("queued","running","in_review","coordinating")',
                (task["project_id"], task_id),
            ):
                raise Conflict("当前项目还有活动任务，不能重试发布")
        store.db.execute(
            'UPDATE tasks SET status="queued",lease_hash=NULL,agent_hash=NULL,lease_until=NULL,review=NULL,'
            "revision=?,acceptance=?,updated=? WHERE id=?",
            (
                project["revision"],
                encoded(task["acceptance"] if task["revision"] == project["revision"] else project["requirements"]),
                time.time(),
                task_id,
            ),
        )
        if task["channel"] == "shared":
            store.db.execute(
                "UPDATE projects SET status=? WHERE id=?",
                ("publishing" if task["kind"] == "publisher" else "active", task["project_id"]),
            )
        store.event(task["project_id"], task_id, "queued", reason)
        return store.public_task(store.task(task_id))

    def cancellation_scope(task):
        pending = [task]
        affected = {}
        while pending:
            current = pending.pop()
            if current["id"] in affected:
                continue
            affected[current["id"]] = current
            children = store.all(
                "SELECT id FROM tasks WHERE project_id=? AND channel=? AND (parent_id=? OR source_id=?)",
                (task["project_id"], task["channel"], current["id"], current["id"]),
            )
            pending.extend(store.task(child["id"]) for child in children)
        return affected

    def cancel_task(task, reason):
        if task["status"] in ("done", "cancelled"):
            raise Conflict("任务已经结束")
        for current in cancellation_scope(task).values():
            store.db.execute(
                'UPDATE tasks SET status="cancelled",lease_hash=NULL,agent_hash=NULL,lease_until=NULL,updated=? WHERE id=?',
                (time.time(), current["id"]),
            )
            if current["kind"] == "publisher":
                project_state = "blocked" if current["attempt"] else "active"
                store.db.execute(
                    'UPDATE projects SET status=? WHERE id=? AND status="publishing"',
                    (project_state, current["project_id"]),
                )
            store.event(current["project_id"], current["id"], "cancelled", reason)
        store.reconcile(task["project_id"], task["channel"])
        return store.public_task(store.task(task["id"]))

    @app.post("/api/tasks/{task_id}/retry")
    def task_retry(task_id: str, request: Request):
        with store.tx():
            actor = identity(request)
            return retry_task(visible_task(actor, task_id), "成员已明确要求重试")

    @app.post("/api/tasks/{task_id}/cancel")
    def task_cancel(task_id: str, request: Request):
        with store.tx():
            actor = identity(request)
            return cancel_task(
                visible_task(actor, task_id),
                "任务及其子任务已取消并撤销运行权限；已发生的文件修改或发布需检查实际状态。",
            )

    def worker_protocol(request):
        if request.headers.get("x-arena-worker-protocol") != "2":
            raise HTTPException(
                409,
                {
                    "code": "worker_upgrade_required",
                    "message": "请更新并重启执行端；当前版本需要分别保护工作机回写凭据和模型操作凭据",
                },
            )

    @app.post("/api/machines/register")
    def machine_register(data: Registration, request: Request):
        with store.tx():
            actor = identity(request, "machine")
            worker_protocol(request)
            incoming_desktop = any(agent.provider == "desktop_mcp" for agent in data.agents)
            existing = store.all("SELECT provider FROM agents WHERE machine_id=?", (actor["id"],))
            existing_desktop = any(agent["provider"] == "desktop_mcp" for agent in existing)
            if incoming_desktop and (
                len(data.agents) != 1
                or data.slots != 1
                or data.agents[0].roles != ["worker"]
                or data.agents[0].mode != "new"
            ):
                raise Conflict("桌面 MCP 执行端必须使用单独的单槽共享任务身份")
            if existing and incoming_desktop != existing_desktop:
                raise Conflict("桌面 MCP 执行端必须使用单独的电脑令牌")
            agents = store.register(
                actor["id"],
                data.name,
                data.os,
                data.slots,
                [a.model_dump(exclude_none=True) for a in data.agents],
                data.storage_bytes,
                [item.model_dump() for item in data.github_members],
            )
        return {"machine_id": actor["id"], "agents": agents}

    @app.post("/api/machines/heartbeat")
    def machine_heartbeat(data: Heartbeat, request: Request):
        with store.tx():
            actor = identity(request, "machine")
            worker_protocol(request)
            if not store.one("SELECT id FROM machines WHERE id=?", (actor["id"],)):
                raise Conflict("先注册执行电脑")
            store.db.execute(
                "UPDATE machines SET seen=?,storage_bytes=? WHERE id=?", (time.time(), data.storage_bytes, actor["id"])
            )
        return {"status": "online"}

    @app.post("/api/machines/claim")
    def machine_claim(request: Request):
        with store.tx():
            actor = identity(request, "machine")
            worker_protocol(request)
            job = store.claim(actor["id"])
        member = (job or {}).get("commit_identity")
        project = (job or {}).get("project") or {}
        if (
            member
            and member.get("source") == "center"
            and project.get("repo")
            and project["policy"].get("push_branches")
        ):
            # Refreshing may call GitHub, so it happens after the claim transaction has committed.
            try:
                member.update(github_web.push_credential(member["member_id"]))
            except GitHubWebError as exc:
                member["credential_error"] = str(exc)
        return job

    @app.post("/api/machines/workspaces/status")
    def machine_workspaces_status(data: WorkspaceStatus, request: Request):
        actor = identity(request, "machine")
        records = []
        with store.lock:
            for task_id, attempt in dict.fromkeys((item.id, item.attempt) for item in data.tasks):
                row = store.one(
                    "SELECT t.id FROM tasks t JOIN agents a ON a.id=t.agent_id "
                    "WHERE t.id=? AND t.attempt=? AND a.machine_id=?",
                    (task_id, attempt, actor["id"]),
                )
                if row:
                    task = store.task(row["id"])
                    records.append(
                        {
                            key: task[key]
                            for key in ("id", "attempt", "project_id", "status", "kind", "revision", "evidence")
                        }
                    )
        return {"tasks": records}

    @app.post("/api/runs/heartbeat")
    def run_heartbeat(request: Request):
        with store.tx():
            task, _ = run(request, worker_only=True)
            store.db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (time.time() + 90, task["id"]))
            return {"status": "running"}

    @app.post("/api/runs/log")
    def run_log(data: Log, request: Request):
        with store.tx():
            task, _ = run(request)
            store.event(task["project_id"], task["id"], "log", data.text)
        return {"ok": True}

    @app.post("/api/runs/permissions")
    def permissions_publish(data: PermissionBatch, request: Request):
        with store.tx():
            task, _ = run(request)
            for item in data.permissions:
                if len(encoded(item.tool)) > 20_000 or len(encoded(item.options)) > 20_000:
                    raise HTTPException(413, "权限请求过大")
                options = item.options
                option_ids = [option.get("optionId", option.get("id")) for option in options]
                if any(not isinstance(option, str) or not option for option in option_ids):
                    raise Conflict("权限选项缺少有效 ID")
                existing = store.one(
                    "SELECT * FROM permissions WHERE task_id=? AND attempt=? AND id=?",
                    (task["id"], task["attempt"], item.id),
                )
                if existing:
                    if existing["tool"] != encoded(item.tool) or existing["options"] != encoded(options):
                        raise Conflict("同一权限请求的内容不能在展示后改变")
                    continue
                store.db.execute(
                    "INSERT INTO permissions(task_id,attempt,id,tool,options) VALUES(?,?,?,?,?)",
                    (task["id"], task["attempt"], item.id, encoded(item.tool), encoded(options)),
                )
                store.event(task["project_id"], task["id"], "permission", "执行代理正在等待权限确认")
        return {"ok": True}

    @app.get("/api/runs/permissions")
    def permissions_read(request: Request):
        with store.lock:
            task, _ = run(request)
            decisions = store.all(
                "SELECT id,option_id FROM permissions WHERE task_id=? AND attempt=? AND decided=1",
                (task["id"], task["attempt"]),
            )
        return {"decisions": decisions}

    @app.post("/api/tasks/{task_id}/permissions/{permission_id}")
    def permissions_decide(task_id: str, permission_id: str, data: PermissionDecision, request: Request):
        with store.tx():
            actor = identity(request)
            task = visible_task(actor, task_id)
            if task["status"] != "running" or task["lease_until"] < time.time():
                raise Conflict("运行已结束或失联，不能回复旧的权限请求")
            permission = store.one(
                "SELECT * FROM permissions WHERE task_id=? AND attempt=? AND id=?",
                (task_id, task["attempt"], permission_id),
            )
            if not permission:
                raise HTTPException(404, "权限请求不存在")
            options = json.loads(permission["options"])
            ids = {option.get("optionId", option.get("id")) for option in options}
            if data.option_id is not None and data.option_id not in ids:
                raise Conflict("只能选择当前权限请求提供的选项")
            if permission["decided"] and permission["option_id"] != data.option_id:
                raise Conflict("此权限请求已经回复")
            store.db.execute(
                "UPDATE permissions SET decided=1,option_id=? WHERE task_id=? AND attempt=? AND id=?",
                (data.option_id, task_id, task["attempt"], permission_id),
            )
            store.event(task["project_id"], task_id, "permission_reply", f'{actor["name"]} 已回复权限请求')
        return {"ok": True}

    @app.post("/api/runs/complete")
    def run_complete(data: Completion, request: Request):
        if len(encoded(data.evidence)) > 100_000:
            raise HTTPException(413, "执行证据过大，请上传产物并引用")
        with store.tx():
            _, token = run(request, worker_only=True)
            return store.complete(token, data.result, data.evidence, data.failed)

    @app.get("/api/runs/context")
    def run_context(request: Request):
        with store.lock:
            task, _ = run(request)
            project_id = task["project_id"]
            private_owner = task["channel"].partition(":")[2] if task["channel"] != "shared" else None
            snapshot = store.snapshot(project_id, "personal" if private_owner else "shared", private_owner)
            return {
                "task": store.public_task(task),
                "project": store.project(project_id),
                "tasks": snapshot["tasks"],
                "agents": snapshot["agents"],
                "machines": snapshot["machines"],
                "messages": snapshot["messages"],
                "artifacts": snapshot["artifacts"],
                "source": store.public_task(store.task(task["source_id"])) if task["source_id"] else None,
                "dependencies": [store.public_task(store.task(d)) for d in task["dependencies"]],
                "memories": task.get("memory_snapshot", []),
            }

    @app.get("/api/runs/memories")
    def run_memories(request: Request, refresh: bool = False):
        with store.tx():
            task, _ = run(request)
            if refresh:
                owner = task["channel"].partition(":")[2] if task["channel"] != "shared" else None
                memories = store.active_memories(task["project_id"], owner)
                store.db.execute("UPDATE tasks SET memory_snapshot=? WHERE id=?", (encoded(memories), task["id"]))
                store.event(task["project_id"], task["id"], "memory_refresh", "当前运行已显式更新记忆快照")
            else:
                memories = task.get("memory_snapshot", [])
            return {"memories": memories}

    @app.post("/api/runs/memories", status_code=201)
    def run_memory_propose(data: MemoryProposal, request: Request):
        with store.tx():
            task, _ = run(request)
            if task["channel"] == "shared":
                scope, scope_id = "project", task["project_id"]
            else:
                scope, scope_id = "member", task["channel"].partition(":")[2]
            memory = insert_memory(
                scope, scope_id, data.category, data.content, "proposed", task["agent_id"], task["id"]
            )
            store.event(task["project_id"], task["id"], "memory_proposed", "代理提出记忆建议，等待成员确认")
            return memory

    @app.post("/api/runs/tasks", status_code=201)
    def run_task(data: Task, request: Request):
        request_id = receipt_key(request, data)
        body = data.model_dump(exclude={"client_request_id"})
        with store.tx():
            task, _ = run(request)
            receipt_actor = f'run:{task["id"]}:{task["attempt"]}'
            previous = receipt_read(receipt_actor, "task", request_id, body)
            if previous is not None:
                return previous
            if task["kind"] == "reviewer":
                raise Conflict("验收成员请提交验收结论，由负责人安排后续工作")
            if data.kind != "worker" and task["kind"] != "leader":
                raise Conflict("只有负责人可以创建协调或验收任务")
            if data.parent_id not in (None, task["id"]):
                raise Conflict("子任务必须关联当前运行")
            validate_task(data, task["project_id"])
            fields = dict(body)
            fields["parent_id"] = task["id"]
            fields["channel"] = task["channel"]
            response = store.create_task(task["project_id"], **fields)
            return receipt_write(receipt_actor, "task", request_id, body, response)

    @app.post("/api/runs/requirements")
    def run_requirements(data: Requirements, request: Request):
        with store.tx():
            task, _ = run(request)
            if task["kind"] != "leader" or task["channel"] != "shared":
                raise Conflict("只有共享对话的负责人可以修改项目验收清单；个人会话请先发布到共享对话")
            if data.expected_revision is None:
                data = data.model_copy(update={"expected_revision": task["revision"]})
            return set_requirements(task["project_id"], data, task["id"])

    def uncertain_outcome(task):
        evidence = task["evidence"] or {}
        return (
            task["status"] == "interrupted"
            or bool(evidence.get("execution_uncertain"))
            or bool(evidence.get("outcome_unknown"))
            or evidence.get("push_status") == "unconfirmed"
            or evidence.get("completion_status") in ("unconfirmed", "outcome_unknown")
        )

    @app.post("/api/runs/tasks/{task_id}/recover")
    def run_task_recover(task_id: str, data: Recovery, request: Request):
        with store.tx():
            leader, _ = run(request)
            if leader["kind"] != "leader":
                raise Conflict("只有负责人可以恢复其他任务")
            target = store.task(task_id)
            if target["project_id"] != leader["project_id"] or target["channel"] != leader["channel"]:
                raise HTTPException(404, "任务不存在")
            if target["id"] == leader["id"] or target["kind"] == "publisher":
                raise Conflict("负责人不能恢复自身或发布任务；发布结果需要成员确认")
            if uncertain_outcome(target):
                raise Conflict("任务结果尚不确定，请说明需要成员核对的事实，不能自动重试或替代")
            if data.action == "cancel":
                if "requested_agent" in data.model_fields_set:
                    raise Conflict("取消任务时不能重新指定执行成员")
                if target["status"] not in ("failed", "blocked", "changes_requested", "queued"):
                    raise Conflict("负责人只能取消已知失败或尚未开始的任务范围")
                scope = cancellation_scope(target)
                if any(
                    current["id"] == leader["id"]
                    or current["kind"] == "publisher"
                    or uncertain_outcome(current)
                    or current["status"] in ("running", "coordinating", "in_review")
                    or (current["status"] == "queued" and current["attempt"] > 0)
                    for current in scope.values()
                ):
                    raise Conflict("此范围仍有执行、验收或结果不确定的任务，需要成员检查后取消")
                return cancel_task(target, f'负责人「{leader["title"]}」取消了已知失败或未开始的工作范围')
            if target["status"] not in ("failed", "blocked", "changes_requested"):
                raise Conflict("负责人只能重试已知失败、阻塞或待修改的任务")
            if target["attempt"] >= 3:
                raise Conflict("已达到三次执行上限，请向成员汇报失败原因和现有成果")
            if "requested_agent" in data.model_fields_set:
                if data.requested_agent:
                    agent = store.one("SELECT * FROM agents WHERE id=? AND enabled=1", (data.requested_agent,))
                    if (
                        not agent
                        or target["kind"] not in json.loads(agent["roles"])
                        or (
                            target["kind"] != "leader" and target["capability"] not in json.loads(agent["capabilities"])
                        )
                        or (
                            target["kind"] == "reviewer"
                            and store.task(target["source_id"])["agent_id"] == data.requested_agent
                        )
                    ):
                        raise Conflict("指定成员必须已启用并具备当前角色与能力，且不能验收自己的成果")
                store.db.execute("UPDATE tasks SET requested_agent=? WHERE id=?", (data.requested_agent, target["id"]))
            return retry_task(target, f'负责人「{leader["title"]}」安排已知失败的任务重试（最多三次执行）')

    @app.post("/api/runs/review")
    def run_review(data: Review, request: Request):
        with store.tx():
            task, _ = run(request)
            if task["kind"] != "reviewer" or not task["source_id"]:
                raise Conflict("此运行不是独立验收任务")
            source = store.task(task["source_id"])
            project = store.project(task["project_id"])
            if (
                source["status"] != "in_review"
                or task["revision"] != project["revision"]
                or source["revision"] != project["revision"]
            ):
                raise Conflict("源任务或需求版本已变化，需要重新安排验收")
            if task["agent_id"] == source["agent_id"]:
                raise Conflict("执行者不能验收自己的任务")
            store.db.execute("UPDATE tasks SET review=? WHERE id=?", (encoded(data.model_dump()), task["id"]))
            store.event(task["project_id"], task["id"], "review", data.summary)
        return {"ok": True}

    @app.post("/api/runs/finish")
    def run_finish(data: Finish, request: Request):
        with store.tx():
            task, _ = run(request)
            project = store.project(task["project_id"])
            if task["kind"] != "leader" or task["channel"] != "shared":
                raise Conflict("只有共享对话的负责人可以确认整体交付")
            if task["revision"] != project["revision"]:
                raise Conflict("负责人运行的需求版本已过期，请读取当前需求并重新核对")
            if not project["requirements"]:
                raise Conflict("先明确项目验收清单")
            workers = [
                store.task(t["id"])
                for t in store.all(
                    'SELECT id FROM tasks WHERE project_id=? AND kind="worker" AND channel="shared" AND status!="cancelled"',
                    (project["id"],),
                )
            ]
            if not workers or any(
                t["status"] != "done"
                or t["revision"] != project["revision"]
                or not t["review"]
                or t["review"]["decision"] != "accept"
                for t in workers
            ):
                raise Conflict("执行任务尚未全部通过当前需求版本的独立验收")
            pending = store.one(
                'SELECT id FROM tasks WHERE project_id=? AND id!=? AND channel="shared" AND kind!="publisher" '
                'AND status IN ("queued","running","in_review","coordinating")',
                (project["id"], task["id"]),
            )
            if pending:
                raise Conflict("还有其他活动任务，需要完成后再交付")
            policy = project["policy"]
            if policy.get("auto_merge") or policy.get("auto_deploy"):
                if not policy.get("release_profile") or not project["repo"]:
                    raise Conflict("请先配置 GitHub 仓库及执行电脑上的发布 profile")
                if not policy.get("push_branches"):
                    raise Conflict("自动发布需要开启 push_branches 并将已验收的提交同步到 GitHub")
                commits = []
                for worker in workers:
                    evidence = worker["evidence"] or {}
                    commit = evidence.get("commit")
                    if not commit and worker["capability"] in ("research", "docs") and evidence.get("artifacts"):
                        continue
                    if (
                        evidence.get("delivery") != "github"
                        or evidence.get("push_status") == "unconfirmed"
                        or not isinstance(commit, str)
                        or not re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", commit)
                    ):
                        raise Conflict(
                            f'任务「{worker["title"]}」缺少已确认同步到 GitHub 的验收提交；'
                            "先在执行电脑确认提交和推送状态，再重新核对交付"
                        )
                    commits.append(commit)
                if not commits:
                    raise Conflict("自动发布至少需要一个已验收并同步到 GitHub 的代码提交")
                existing = store.one(
                    'SELECT id,status,attempt FROM tasks WHERE project_id=? AND kind="publisher" AND revision=? ORDER BY created DESC LIMIT 1',
                    (project["id"], project["revision"]),
                )
                if existing:
                    if existing["status"] == "done":
                        return {"status": "delivered", "task_id": existing["id"]}
                    if existing["status"] == "changes_requested" and existing["attempt"] == 0:
                        store.db.execute(
                            'UPDATE tasks SET status="cancelled",updated=? WHERE id=?', (time.time(), existing["id"])
                        )
                        store.event(
                            project["id"],
                            existing["id"],
                            "cancelled",
                            "工作范围已变化，尚未执行的发布将由新发布任务替代",
                        )
                    else:
                        if existing["status"] not in ("queued", "running"):
                            raise Conflict("本版本已有未成功的发布任务；请检查实际发布状态后手动重试该任务")
                        return {"status": "publishing", "task_id": existing["id"]}
                publishing = store.create_task(
                    project["id"],
                    "发布 · " + project["name"],
                    data.summary,
                    kind="publisher",
                    capability="release:" + policy["release_profile"],
                    acceptance=project["requirements"],
                    dependencies=[worker["id"] for worker in workers],
                    parent_id=task["id"],
                )
                store.db.execute('UPDATE projects SET status="publishing" WHERE id=?', (project["id"],))
                store.event(project["id"], publishing["id"], "publishing", "已排队交给本机发布配置执行")
                return {"status": "publishing", "task_id": publishing["id"]}
            store.db.execute('UPDATE projects SET status="ready" WHERE id=?', (project["id"],))
            store.event(project["id"], task["id"], "ready", data.summary)
        return {"status": "ready"}

    def storage_used(exclude=None):
        total = 0
        for path in store.root.rglob("*"):
            if path != exclude and path.is_file() and not path.is_symlink():
                with contextlib.suppress(FileNotFoundError):
                    total += path.stat().st_size
        return total

    @app.post("/api/projects/{project_id}/artifacts", status_code=201)
    async def artifact_upload(
        project_id: str, request: Request, name: str = Query(min_length=1, max_length=240), task_id: str | None = None
    ):
        with store.lock:
            task_id = artifact_actor(request, project_id, task_id, write=True)
            store.project(project_id)
            if task_id and store.task(task_id)["project_id"] != project_id:
                raise Conflict("产物关联的任务不属于当前项目")
        clean_name = name.replace("\\", "/").split("/")[-1]
        clean_name = "".join(c for c in clean_name if ord(c) >= 32 and ord(c) != 127).strip()
        if clean_name in ("", ".", ".."):
            raise HTTPException(422, "文件名无效")
        artifact_id = uid()
        path = store.root / "artifacts" / artifact_id
        temporary = path.with_suffix(".part")
        async with app.state.upload_lock:
            used = storage_used()
            received = 0
            try:
                with temporary.open("xb") as output:
                    temporary.chmod(0o600)
                    async for chunk in request.stream():
                        received += len(chunk)
                        if received > MAX_ARTIFACT_BYTES:
                            raise HTTPException(413, "单个产物最多 128 MiB")
                        if used + received > store.quota:
                            raise HTTPException(413, "暂存空间已满，请清理已交付且未固定的旧产物")
                        output.write(chunk)
                if storage_used(exclude=temporary) + received > store.quota:
                    raise HTTPException(413, "暂存空间已满")
                with store.tx():
                    # Revalidate a run after a potentially long streaming upload.
                    artifact_actor(request, project_id, task_id, write=True)
                    temporary.replace(path)
                    store.db.execute(
                        "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)",
                        (artifact_id, project_id, task_id, clean_name, received, 1, time.time()),
                    )
                    store.event(project_id, task_id, "artifact", f"上传产物：{clean_name}")
                    return store.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
            except BaseException:
                temporary.unlink(missing_ok=True)
                path.unlink(missing_ok=True)
                raise

    @app.get("/api/artifacts/{artifact_id}/download")
    def artifact_download(artifact_id: str, request: Request):
        with store.lock:
            artifact = store.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
            if not artifact:
                raise HTTPException(404, "产物不存在")
            artifact_actor(request, artifact["project_id"], artifact["task_id"])
            path = store.root / "artifacts" / artifact["id"]
            if not path.is_file() or path.is_symlink():
                raise HTTPException(404, "产物文件不存在")
        return FileResponse(
            path,
            filename=artifact["name"],
            media_type="application/octet-stream",
            headers={"Content-Security-Policy": "sandbox"},
        )

    @app.post("/api/artifacts/{artifact_id}/pin")
    def artifact_pin(artifact_id: str, data: Pin, request: Request):
        with store.tx():
            identity(request)
            artifact = store.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
            if not artifact:
                raise HTTPException(404, "产物不存在")
            artifact_actor(request, artifact["project_id"], artifact["task_id"])
            store.db.execute("UPDATE artifacts SET pinned=? WHERE id=?", (int(data.pinned), artifact_id))
            return store.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))

    def remove_artifact(artifact):
        if artifact["pinned"]:
            raise Conflict("请先取消固定，再删除产物")
        if artifact["task_id"] and store.task(artifact["task_id"])["status"] in ACTIVE:
            raise Conflict("活动任务的产物不能删除")
        path = store.root / "artifacts" / artifact["id"]
        path.unlink(missing_ok=True)
        store.db.execute("DELETE FROM artifacts WHERE id=?", (artifact["id"],))
        store.event(artifact["project_id"], artifact["task_id"], "cleanup", f'清理产物：{artifact["name"]}')

    @app.delete("/api/artifacts/{artifact_id}")
    def artifact_delete(artifact_id: str, request: Request):
        with store.tx():
            identity(request)
            artifact = store.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
            if not artifact:
                raise HTTPException(404, "产物不存在")
            artifact_actor(request, artifact["project_id"], artifact["task_id"])
            remove_artifact(artifact)
        return {"ok": True}

    @app.post("/api/artifacts/cleanup")
    def artifact_cleanup(data: Cleanup, request: Request):
        deleted = []
        with store.tx():
            actor = identity(request)
            store.project(data.project_id)
            channel = "shared" if data.channel == "shared" else f'personal:{actor["id"]}'
            artifacts = store.all(
                "SELECT a.* FROM artifacts a LEFT JOIN tasks t ON t.id=a.task_id "
                "WHERE a.project_id=? AND COALESCE(t.channel,'shared')=? ORDER BY a.created DESC",
                (data.project_id, channel),
            )
            for artifact in artifacts[data.keep :]:
                if artifact["pinned"]:
                    continue
                if artifact["task_id"] and store.task(artifact["task_id"])["status"] in ACTIVE:
                    continue
                remove_artifact(artifact)
                deleted.append(artifact["id"])
        return {"deleted": deleted}

    static = Path(__file__).with_name("static")
    static.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        path = static / "index.html"
        if not path.is_file():
            return JSONResponse({"name": "PAL Arena", "status": "UI preparing"})
        return FileResponse(path)

    return app
