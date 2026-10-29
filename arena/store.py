from __future__ import annotations

import contextlib
import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
import zlib
from pathlib import Path

from .routing import evaluate

ACTIVE = ("queued", "running", "in_review", "coordinating")
ROLES = ("leader", "worker", "reviewer", "publisher")
MIN_FREE_DISK_BYTES = 1_073_741_824


class Conflict(ValueError):
    pass


class StorageFull(ValueError):
    pass


def uid():
    return uuid.uuid4().hex


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, root, quota=5_000_000_000):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        (self.root / "artifacts").mkdir(exist_ok=True)
        self.quota = quota
        # Set by the API: identity id -> public GitHub identity held by the center, or None.
        self.github_lookup = lambda identity_id: None
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "arena.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.create_function(
            "arena_message_text", 1, lambda payload: zlib.decompress(payload).decode("utf-8") if payload else None
        )
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA wal_autocheckpoint=128;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS identities (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
          token_hash TEXT UNIQUE NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, identity_id TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, brief TEXT NOT NULL, repo TEXT NOT NULL,
          base_ref TEXT NOT NULL, requirements TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 1,
          policy TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS machines (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, os TEXT NOT NULL, seen REAL NOT NULL,
          slots INTEGER NOT NULL, storage_bytes INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS agents (
          id TEXT PRIMARY KEY, machine_id TEXT NOT NULL, name TEXT NOT NULL, provider TEXT NOT NULL,
          roles TEXT NOT NULL, capabilities TEXT NOT NULL, model TEXT, preference INTEGER NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1, mode TEXT NOT NULL DEFAULT 'new');
        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, author TEXT NOT NULL, content TEXT NOT NULL,
          channel TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS message_payloads (
          message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE, payload BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, parent_id TEXT, source_id TEXT,
          title TEXT NOT NULL, prompt TEXT NOT NULL, kind TEXT NOT NULL, capability TEXT NOT NULL,
          acceptance TEXT NOT NULL, dependencies TEXT NOT NULL, status TEXT NOT NULL,
          agent_id TEXT, requested_agent TEXT, revision INTEGER NOT NULL,
          attempt INTEGER NOT NULL DEFAULT 0, lease_hash TEXT, lease_until REAL,
          result TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '{}',
          review TEXT, channel TEXT NOT NULL DEFAULT 'shared', created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, task_id TEXT,
          kind TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS artifacts (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, name TEXT NOT NULL,
          size INTEGER NOT NULL, pinned INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS permissions (
          task_id TEXT NOT NULL, attempt INTEGER NOT NULL, id TEXT NOT NULL,
          tool TEXT NOT NULL, options TEXT NOT NULL, decided INTEGER NOT NULL DEFAULT 0,
          option_id TEXT, PRIMARY KEY(task_id,attempt,id));
        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_id TEXT NOT NULL, category TEXT NOT NULL,
          content TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
          source_task TEXT, author_id TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope,scope_id,status);
        CREATE TABLE IF NOT EXISTS conversation_origins (
          task_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, member_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversation_summaries (
          project_id TEXT NOT NULL, channel TEXT NOT NULL, summary TEXT NOT NULL,
          through_created REAL NOT NULL, through_id TEXT NOT NULL, revision INTEGER NOT NULL, updated REAL NOT NULL,
          PRIMARY KEY(project_id,channel));
        CREATE INDEX IF NOT EXISTS tasks_project ON tasks(project_id, created);
        CREATE INDEX IF NOT EXISTS messages_history ON messages(project_id, channel, created, id);
        CREATE INDEX IF NOT EXISTS events_project ON events(project_id, id);
        """)
        if "channel" not in {row[1] for row in self.db.execute("PRAGMA table_info(tasks)")}:
            self.db.execute("ALTER TABLE tasks ADD COLUMN channel TEXT NOT NULL DEFAULT 'shared'")
        for table, additions in {
            "agents": {"readiness": "TEXT NOT NULL DEFAULT '{}'", "routing_profile": "TEXT NOT NULL DEFAULT '{}'"},
            # Members whose GitHub login this machine verified at registration (no credentials).
            "machines": {"github_members": "TEXT NOT NULL DEFAULT '[]'"},
            "tasks": {
                "difficulty": "TEXT NOT NULL DEFAULT 'standard'",
                "context_tokens": "INTEGER NOT NULL DEFAULT 0",
                "routing_reason": "TEXT NOT NULL DEFAULT ''",
                "memory_snapshot": "TEXT NOT NULL DEFAULT '[]'",
                "agent_hash": "TEXT",
                "recovery_notified": "REAL NOT NULL DEFAULT 0",
            },
        }.items():
            present = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
            for column, declaration in additions.items():
                if column not in present:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
                    if table == "tasks" and column == "agent_hash":
                        # Older workers shared their completion credential with the model.
                        # Pause those runs instead of carrying the old authority across an upgrade.
                        for legacy in self.all("SELECT id,project_id FROM tasks WHERE status='running'"):
                            self.event(
                                legacy["project_id"],
                                legacy["id"],
                                "interrupted",
                                "平台已升级运行凭据隔离；旧执行结果需检查后再用新版本执行端重试。",
                            )
                        self.db.execute(
                            "UPDATE tasks SET status='interrupted',lease_hash=NULL,lease_until=NULL,updated=? "
                            "WHERE status='running'",
                            (time.time(),),
                        )
                        self.db.execute("UPDATE machines SET seen=0")
        self.db.commit()
        with self.tx():
            if not self.one('SELECT id FROM identities WHERE kind="member"'):
                token = self.new_identity("Owner", "member")["token"]
                path = self.root / "owner-token.txt"
                path.write_text(token, encoding="utf-8")
                path.chmod(0o600)

    @contextlib.contextmanager
    def tx(self):
        with self.lock:
            try:
                self.limit_database()
                self.db.execute("BEGIN IMMEDIATE")
                yield
                self.db.commit()
            except sqlite3.OperationalError as exc:
                self.db.rollback()
                if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                    raise StorageFull("中心存储已达到上限，请清理未保留的成果或提高存储额度") from exc
                raise
            except BaseException:
                self.db.rollback()
                raise

    def storage_used(self):
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                with contextlib.suppress(FileNotFoundError):
                    total += path.stat().st_size
        return total

    def limit_database(self):
        """Keep SQLite growth within the configured center budget, with WAL headroom."""
        database_files = {"arena.db", "arena.db-wal", "arena.db-shm"}
        other = 0
        for path in self.root.rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and not (path.parent == self.root and path.name in database_files)
            ):
                with contextlib.suppress(FileNotFoundError):
                    other += path.stat().st_size
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        headroom = self.storage_headroom()
        limit = max(pages, max(0, self.quota - other - headroom) // page_size)
        self.db.execute(f"PRAGMA max_page_count={limit}")

    def ensure_capacity(self, additional=0):
        reserve = self.storage_headroom()
        if self.storage_used() + additional + reserve > self.quota:
            raise StorageFull("中心存储已达到上限，请清理未保留的成果或提高存储额度")
        if shutil.disk_usage(self.root).free < additional + MIN_FREE_DISK_BYTES:
            raise StorageFull("电脑剩余空间不足，已暂停新增数据，请先释放磁盘空间")

    def storage_headroom(self):
        return min(max(self.quota // 100, 1_048_576), 32 * 1_048_576)

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def event(self, project, task, kind, text):
        self.db.execute(
            "INSERT INTO events(project_id,task_id,kind,text,created) VALUES(?,?,?,?,?)",
            (project, task, kind, str(text)[-20_000:], time.time()),
        )
        # Keep bounded logs; durable task results and review evidence are separate.
        self.db.execute("DELETE FROM events WHERE id <= (SELECT MAX(id)-10000 FROM events)")

    def save_message(self, message_id, project_id, author, content, channel, created):
        raw = content.encode("utf-8")
        compressed = zlib.compress(raw) if len(raw) >= 4096 else b""
        preview = content[:512]
        use_compression = bool(compressed) and len(compressed) + len(preview.encode("utf-8")) + 64 < len(raw)
        preview = preview if use_compression else content
        self.db.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?)", (message_id, project_id, author, preview, channel, created)
        )
        if use_compression:
            self.db.execute("INSERT INTO message_payloads VALUES(?,?)", (message_id, compressed))

    def conversation_compaction(self, project_id, channel):
        """Prepare a bounded, ordered older slice, leaving recent messages intact."""
        checkpoint = self.one(
            "SELECT * FROM conversation_summaries WHERE project_id=? AND channel=?", (project_id, channel)
        )
        created = checkpoint["through_created"] if checkpoint else -1
        last_id = checkpoint["through_id"] if checkpoint else ""
        rows = self.all(
            "SELECT m.id,m.author,COALESCE(arena_message_text(p.payload),m.content) AS content,m.created "
            "FROM messages m LEFT JOIN message_payloads p ON p.message_id=m.id "
            "WHERE m.project_id=? AND m.channel=? AND (m.created>? OR (m.created=? AND m.id>?)) "
            "ORDER BY m.created,m.id LIMIT 151",
            (project_id, channel, created, created, last_id),
        )
        older = rows[:-12]
        if not older or (len(rows) < 40 and sum(len(row["content"]) for row in rows) < 12000):
            return None
        selected, size = [], 0
        for row in older:
            if selected and size + len(row["content"]) > 24000:
                break
            selected.append(row)
            size += len(row["content"])
        return {
            "summary": checkpoint["summary"] if checkpoint else "",
            "revision": checkpoint["revision"] if checkpoint else 0,
            "messages": selected,
            "through_id": selected[-1]["id"],
        }

    def new_identity(self, name, kind):
        token = secrets.token_urlsafe(32)
        identity = {"id": uid(), "name": name, "kind": kind, "token": token}
        self.db.execute(
            "INSERT INTO identities VALUES(?,?,?,?,?)", (identity["id"], name, kind, digest(token), time.time())
        )
        return identity

    def authenticate(self, token, cookie=False):
        if not token:
            return None
        with self.lock:
            if cookie:
                return self.one(
                    "SELECT i.id,i.name,i.kind FROM sessions s JOIN identities i ON i.id=s.identity_id "
                    "WHERE s.token_hash=? AND s.expires>?",
                    (digest(token), time.time()),
                )
            return self.one("SELECT id,name,kind FROM identities WHERE token_hash=?", (digest(token),))

    def project(self, project_id):
        p = self.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not p:
            raise Conflict("项目不存在")
        for key in ("requirements", "policy"):
            p[key] = json.loads(p[key])
        return p

    def task(self, task_id):
        t = self.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not t:
            raise Conflict("任务不存在")
        for key in ("acceptance", "dependencies", "evidence", "review", "memory_snapshot"):
            t[key] = json.loads(t[key]) if t[key] else None
        return t

    @staticmethod
    def public_task(t):
        return {k: v for k, v in t.items() if k not in ("lease_hash", "agent_hash", "lease_until")}

    def active_memories(self, project_id, member_id=None):
        """Select bounded, versioned context; private preferences never enter shared runs."""
        rows = self.all(
            "SELECT id,scope,category,content,revision,source_task,updated FROM memories "
            "WHERE status='active' AND ((scope='project' AND scope_id=?) "
            "OR (scope='member' AND scope_id=?)) "
            "ORDER BY CASE scope WHEN 'project' THEN 0 ELSE 1 END, "
            "CASE category WHEN 'constraint' THEN 0 WHEN 'preference' THEN 1 ELSE 2 END, updated DESC,id",
            (project_id, member_id),
        )
        selected, remaining = [], 24_000
        for row in rows:
            if len(selected) >= 24:
                break
            if len(row["content"]) > remaining:
                continue
            remaining -= len(row["content"])
            selected.append(row)
        return selected

    def search_memories(self, project_id, member_id, query):
        """Find relevant active memories beyond the bounded startup snapshot."""
        terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", query.lower())
        keys = set()
        for term in terms:
            if all("\u4e00" <= char <= "\u9fff" for char in term) and len(term) > 2:
                keys.update(term[index : index + 2] for index in range(len(term) - 1))
            else:
                keys.add(term)
        if not keys:
            return []
        rows = self.all(
            "SELECT id,scope,category,content,revision,source_task,updated FROM memories "
            "WHERE status='active' AND ((scope='project' AND scope_id=?) OR (scope='member' AND scope_id=?))",
            (project_id, member_id),
        )
        scored = [(sum(key in row["content"].lower() for key in keys), row) for row in rows]
        scored.sort(key=lambda item: (item[0], item[1]["updated"]), reverse=True)
        return [row for score, row in scored if score][:20]

    def reconcile(self, project_id, channel="shared"):
        """Resume coordination after completion or cancellation, in the caller's transaction."""
        now = time.time()
        for raw in self.all(
            "SELECT id FROM tasks WHERE project_id=? AND channel=? AND kind='worker' AND status='coordinating'",
            (project_id, channel),
        ):
            parent = self.task(raw["id"])
            children = self.all("SELECT id,status FROM tasks WHERE parent_id=? AND kind='worker'", (parent["id"],))
            if any(c["status"] not in ("done", "cancelled") for c in children):
                continue
            cancelled = {c["id"] for c in children if c["status"] == "cancelled"}
            dependencies = list(
                dict.fromkeys(
                    [d for d in parent["dependencies"] if d not in cancelled]
                    + [c["id"] for c in children if c["status"] == "done"]
                )
            )
            self.db.execute(
                "UPDATE tasks SET status='queued',dependencies=?,updated=? WHERE id=?",
                (encoded(dependencies), now, parent["id"]),
            )
            self.event(project_id, parent["id"], "queued", "子任务已验收或取消，继续整合保留的成果")
        affected_channels = set()
        for raw in self.all("SELECT id FROM tasks WHERE project_id=? AND status='queued'", (project_id,)):
            task = self.task(raw["id"])
            cancelled = [d for d in task["dependencies"] if self.task(d)["status"] == "cancelled"]
            if cancelled:
                reason = "依赖任务已取消，请调整任务依赖后重试：" + ", ".join(cancelled)
                self.db.execute(
                    "UPDATE tasks SET status='blocked',result=?,updated=? WHERE id=?", (reason, now, task["id"])
                )
                self.event(project_id, task["id"], "blocked", reason)
                affected_channels.add(task["channel"])
        for affected in affected_channels - {channel}:
            self.reconcile(project_id, affected)
        project = self.project(project_id)
        if channel == "shared" and project["status"] != "active":
            return
        if channel == "shared" and self.one(
            "SELECT id FROM tasks WHERE project_id=? AND kind='publisher' AND status IN ('queued','running') "
            "AND revision=?",
            (project_id, project["revision"]),
        ):
            return
        leader_active = self.one(
            "SELECT id FROM tasks WHERE project_id=? AND channel=? AND kind='leader' "
            "AND status IN ('queued','running')",
            (project_id, channel),
        )
        if (
            leader_active
            or self.one(
                "SELECT count(*) AS n FROM tasks WHERE project_id=? AND status IN ('queued','running')",
                (project_id,),
            )["n"]
            >= 40
        ):
            return
        needs_help = self.all(
            "SELECT id,title,status,attempt,result,updated FROM tasks WHERE project_id=? AND channel=? "
            "AND kind IN ('worker','reviewer') AND status IN ('failed','blocked','interrupted','changes_requested') "
            "AND updated>recovery_notified ORDER BY updated LIMIT 12",
            (project_id, channel),
        )
        if needs_help:
            details = "\n".join(
                f"{w['id']} · {w['title']} · {w['status']} · 第 {w['attempt']} 次：{w['result'][-2000:]}"
                for w in needs_help
            )
            self.create_task(
                project_id,
                "处理执行阻塞",
                "以下任务需要处理。先查看上下文与证据，已知失败可使用受限恢复工具重试或取消。"
                "结果未知、断线、提交或发布未确认时，只向成员说明需检查的具体事实，不要创建替代任务重复写入。"
                "自动恢复最多三次执行，超过后交给成员决定。\n" + details,
                kind="leader",
                capability="planning",
                channel=channel,
            )
            for work in needs_help:
                self.db.execute("UPDATE tasks SET recovery_notified=? WHERE id=?", (work["updated"], work["id"]))
            return
        workers = [
            self.task(w["id"])
            for w in self.all(
                "SELECT id FROM tasks WHERE project_id=? AND channel=? AND kind='worker' AND status!='cancelled'",
                (project_id, channel),
            )
        ]
        if not workers or any(
            w["status"] != "done"
            or w["revision"] != project["revision"]
            or not w["review"]
            or w["review"].get("decision") != "accept"
            for w in workers
        ):
            return
        # A completed/failed summary remains visible; do not loop or silently retry it.
        if self.one(
            "SELECT id FROM tasks WHERE project_id=? AND channel=? AND kind='leader' "
            "AND title=? AND revision=? AND created>=?",
            (project_id, channel, "汇总交付并核对需求", project["revision"], max(w["updated"] for w in workers)),
        ):
            return
        prompt = (
            "所有执行任务已独立验收。请复核需求和成果，调用 finish_project 或安排缺失工作。"
            if channel == "shared"
            else "个人会话的执行任务已验收。请向当前成员总结成果；项目共享交付状态由共享负责人管理。"
        )
        self.create_task(
            project_id, "汇总交付并核对需求", prompt, kind="leader", capability="planning", channel=channel
        )

    def create_task(
        self,
        project_id,
        title,
        prompt,
        kind="worker",
        capability="code",
        acceptance=None,
        dependencies=None,
        parent_id=None,
        source_id=None,
        requested_agent=None,
        channel="shared",
        difficulty="standard",
        context_tokens=0,
    ):
        project = self.project(project_id)
        if channel == "shared" and self.one(
            'SELECT id FROM tasks WHERE project_id=? AND kind="publisher" ' 'AND status="running"', (project_id,)
        ):
            raise Conflict("发布正在执行，请等待发布结束或先取消发布，再修改项目工作")
        if difficulty not in ("routine", "standard", "deep"):
            raise Conflict("未知任务难度")
        if kind not in ROLES:
            raise Conflict("未知任务角色")
        if kind == "publisher" and channel != "shared":
            raise Conflict("发布任务只能属于共享项目")
        if kind == "reviewer" and not source_id:
            raise Conflict("验收任务必须绑定实际执行任务")
        if source_id and kind != "reviewer":
            raise Conflict("只有验收任务可以绑定待验收成果")
        dependencies = list(dict.fromkeys(dependencies or []))
        for dep in [*dependencies, *([parent_id] if parent_id else []), *([source_id] if source_id else [])]:
            related = self.task(dep)
            if related["project_id"] != project_id:
                raise Conflict("依赖和父任务必须属于同一项目")
            if related["channel"] not in ("shared", channel):
                raise Conflict("不能读取另一个个人会话的任务")
        if parent_id:
            depth, current = 0, parent_id
            while current:
                depth += 1
                if current in dependencies:
                    raise Conflict("子任务不能等待仍负责协调它的祖先任务")
                current = self.task(current)["parent_id"]
            if depth > 3:
                raise Conflict("任务最多分三层，进一步工作请在现有任务中完成")
        count = self.one(
            'SELECT count(*) AS n FROM tasks WHERE project_id=? AND status IN ("queued","running")', (project_id,)
        )["n"]
        if count >= 40:
            raise Conflict("已有 40 个待处理任务，先完成或取消再继续分派")
        task_id = uid()
        now = time.time()
        self.db.execute(
            """INSERT INTO tasks(id,project_id,parent_id,source_id,title,prompt,kind,capability,
          acceptance,dependencies,status,requested_agent,revision,channel,created,updated,difficulty,context_tokens)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                project_id,
                parent_id,
                source_id,
                title,
                prompt,
                kind,
                capability,
                encoded(acceptance or project["requirements"]),
                encoded(dependencies),
                "queued",
                requested_agent,
                project["revision"],
                channel,
                now,
                now,
                difficulty,
                context_tokens,
            ),
        )
        if channel == "shared":
            self.db.execute('UPDATE projects SET status="active" WHERE id=?', (project_id,))
        self.event(project_id, task_id, "queued", f"创建任务：{title}")
        return self.public_task(self.task(task_id))

    def snapshot(self, project_id=None, channel="shared", author=None):
        with self.lock:
            projects = [self.project(p["id"]) for p in self.all("SELECT id FROM projects ORDER BY created DESC")]
            machines = self.all("SELECT * FROM machines ORDER BY name")
            for m in machines:
                m["online"] = m["seen"] > time.time() - 45
                m["github_members"] = [
                    {"member_id": item["member_id"], "github_login": item["github_login"]}
                    for item in json.loads(m["github_members"])
                ]
            agents = self.all("SELECT * FROM agents ORDER BY preference,name")
            for a in agents:
                a["roles"] = json.loads(a["roles"])
                a["capabilities"] = json.loads(a["capabilities"])
                a["readiness"] = json.loads(a["readiness"])
                a["routing_profile"] = json.loads(a["routing_profile"])
                a["busy"] = self.one(
                    'SELECT count(*) AS n FROM tasks WHERE agent_id=? AND status="running"', (a["id"],)
                )["n"]
            tasks, messages, events, artifacts = [], [], [], []
            if project_id:
                self.project(project_id)
                visible_channel = f"personal:{author}" if channel == "personal" else "shared"
                tasks = [
                    self.public_task(self.task(t["id"]))
                    for t in self.all(
                        "SELECT id FROM tasks WHERE project_id=? AND channel IN (?,?) ORDER BY created DESC LIMIT 500",
                        (project_id, "shared", visible_channel),
                    )
                ]
                for task in tasks:
                    task["permissions"] = self.all(
                        "SELECT id,tool,options,decided,option_id FROM permissions WHERE task_id=? AND attempt=?",
                        (task["id"], task["attempt"]),
                    )
                    for permission in task["permissions"]:
                        permission["tool"] = json.loads(permission["tool"])
                        permission["options"] = json.loads(permission["options"])
                # Personal messages and execution results remain in their owner's channel.
                personal = f"personal:{author}"
                messages = self.all(
                    "SELECT m.id,m.project_id,m.author,COALESCE(arena_message_text(p.payload),m.content) AS content,"
                    "m.channel,m.created FROM (SELECT * FROM messages WHERE project_id=? AND channel IN (?,?) "
                    "ORDER BY created DESC LIMIT 150) m LEFT JOIN message_payloads p ON p.message_id=m.id ORDER BY m.created",
                    (project_id, "shared", personal if channel == "personal" else "shared"),
                )
                events = self.all(
                    "SELECT e.* FROM events e LEFT JOIN tasks t ON t.id=e.task_id WHERE e.project_id=? "
                    "AND (e.task_id IS NULL OR t.channel IN (?,?)) ORDER BY e.id DESC LIMIT 120",
                    (project_id, "shared", visible_channel),
                )
                artifacts = self.all(
                    "SELECT a.* FROM artifacts a LEFT JOIN tasks t ON t.id=a.task_id WHERE a.project_id=? "
                    "AND (a.task_id IS NULL OR t.channel IN (?,?)) ORDER BY a.created DESC",
                    (project_id, "shared", visible_channel),
                )
            used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file() and not p.is_symlink())
            return {
                "projects": projects,
                "machines": machines,
                "agents": agents,
                "tasks": tasks,
                "messages": messages,
                "events": events,
                "artifacts": artifacts,
                "storage": {"used": used, "quota": self.quota},
                "server_time": time.time(),
            }

    def expire_leases(self):
        affected = set()
        for t in self.all('SELECT * FROM tasks WHERE status="running" AND lease_until<?', (time.time(),)):
            self.db.execute(
                'UPDATE tasks SET status="interrupted",lease_hash=NULL,agent_hash=NULL,updated=? WHERE id=?',
                (time.time(), t["id"]),
            )
            self.event(
                t["project_id"], t["id"], "interrupted", "机器失联，执行结果未知；检查后再重试，系统不会重复提交"
            )
            affected.add((t["project_id"], t["channel"]))
        for project_id, channel in affected:
            self.reconcile(project_id, channel)

    def register(self, machine_id, name, os_name, slots, agents, storage_bytes=0, github_members=()):
        self.db.execute(
            "INSERT INTO machines(id,name,os,seen,slots,storage_bytes,github_members) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,os=excluded.os,seen=excluded.seen,"
            "slots=excluded.slots,storage_bytes=excluded.storage_bytes,github_members=excluded.github_members",
            (machine_id, name, os_name, time.time(), slots, storage_bytes, encoded(list(github_members))),
        )
        self.db.execute("UPDATE agents SET enabled=0 WHERE machine_id=?", (machine_id,))
        for a in agents:
            identity = uuid.uuid5(uuid.NAMESPACE_URL, machine_id + ":" + a["key"]).hex
            self.db.execute(
                """INSERT INTO agents(id,machine_id,name,provider,roles,capabilities,model,preference,enabled,mode,readiness,routing_profile)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,provider=excluded.provider,roles=excluded.roles,capabilities=excluded.capabilities,
              model=excluded.model,preference=excluded.preference,enabled=excluded.enabled,mode=excluded.mode,
              readiness=excluded.readiness,routing_profile=excluded.routing_profile""",
                (
                    identity,
                    machine_id,
                    a["name"],
                    a["provider"],
                    encoded(a["roles"]),
                    encoded(a["capabilities"]),
                    a.get("model"),
                    a.get("preference", 10),
                    int(a.get("enabled", True)),
                    a.get("mode", "new"),
                    encoded(a.get("readiness", {})),
                    encoded(a.get("routing_profile", {})),
                ),
            )
        return {a["name"]: a["id"] for a in self.all("SELECT id,name FROM agents WHERE machine_id=?", (machine_id,))}

    def claim(self, machine_id):
        self.expire_leases()
        machine = self.one("SELECT * FROM machines WHERE id=?", (machine_id,))
        if not machine or machine["seen"] < time.time() - 45:
            raise Conflict("先注册执行电脑")
        busy = self.one(
            "SELECT count(*) AS n FROM tasks t JOIN agents a ON a.id=t.agent_id "
            'WHERE a.machine_id=? AND t.status="running"',
            (machine_id,),
        )["n"]
        if busy >= machine["slots"]:
            return None
        for raw in self.all('SELECT id FROM tasks WHERE status="queued" ORDER BY created'):
            t = self.task(raw["id"])
            if t["kind"] == "publisher" and t["revision"] != self.project(t["project_id"])["revision"]:
                self.db.execute('UPDATE tasks SET status="changes_requested" WHERE id=?', (t["id"],))
                self.event(t["project_id"], t["id"], "changes_requested", "发布计划的需求版本已失效")
                continue
            if t["kind"] == "publisher":
                if self.one(
                    'SELECT id FROM tasks WHERE project_id=? AND kind="publisher" AND status="running"',
                    (t["project_id"],),
                ):
                    continue
                workers = [
                    self.task(w["id"])
                    for w in self.all(
                        'SELECT id FROM tasks WHERE project_id=? AND kind="worker" AND channel="shared" '
                        'AND status!="cancelled"',
                        (t["project_id"],),
                    )
                ]
                if {w["id"] for w in workers} != set(t["dependencies"]):
                    self.db.execute('UPDATE tasks SET status="changes_requested" WHERE id=?', (t["id"],))
                    self.db.execute('UPDATE projects SET status="active" WHERE id=?', (t["project_id"],))
                    self.event(
                        t["project_id"], t["id"], "changes_requested", "执行任务范围已变化，需要重新生成发布计划"
                    )
                    continue
                if not workers or any(
                    w["status"] != "done"
                    or w["revision"] != t["revision"]
                    or not w["review"]
                    or w["review"].get("decision") != "accept"
                    for w in workers
                ):
                    continue
                if self.one(
                    'SELECT id FROM tasks WHERE project_id=? AND channel="shared" AND kind!="publisher" '
                    'AND status IN ("queued","running","in_review","coordinating")',
                    (t["project_id"],),
                ):
                    continue
            if any(self.task(d)["status"] != "done" for d in t["dependencies"]):
                continue
            if t["kind"] == "leader" and self.one(
                'SELECT id FROM tasks WHERE project_id=? AND kind="leader" ' 'AND status="running" AND channel=?',
                (t["project_id"], t["channel"]),
            ):
                continue
            # Pick globally so cost preference is independent of polling order.
            candidates = self.all(
                "SELECT a.*,m.github_members AS machine_github_members FROM agents a JOIN machines m "
                "ON m.id=a.machine_id WHERE a.enabled=1 AND m.seen>? ORDER BY a.preference,a.id",
                (time.time() - 45,),
            )
            chosen, chosen_score, reason = None, float("-inf"), ""
            project = self.project(t["project_id"])
            # Commits and pushes under a member's GitHub account can only happen where that member logged in.
            commit_as = project["policy"].get("commit_as") if t["kind"] in ("worker", "leader") else ""
            # A member signed in with GitHub on the web gets credentials from the center on any computer.
            center_identity = self.github_lookup(commit_as) if commit_as else None
            # Commit availability is a hard constraint, independent of model quality.
            if t["source_id"]:
                inputs = [self.task(t["source_id"])["evidence"]]
            else:
                inputs = [self.task(d)["evidence"] for d in t["dependencies"]]
                if t["attempt"] and t["evidence"].get("commit"):
                    inputs.append(t["evidence"])
            local_inputs = [e for e in inputs if e.get("commit") and e.get("delivery") != "github"]
            if not project.get("repo") and t["source_id"]:
                local_inputs = inputs
            elif not project.get("repo") and t["attempt"] and t["evidence"].get("commit"):
                local_inputs.append(t["evidence"])
            for a in candidates:
                # A long-lived GUI conversation is a shared execution identity:
                # it must never receive private context or act as an independent reviewer.
                if a["provider"] == "desktop_mcp" and (t["channel"] != "shared" or t["kind"] != "worker"):
                    continue
                if t["kind"] not in json.loads(a["roles"]):
                    continue
                if t["requested_agent"] and a["id"] != t["requested_agent"]:
                    continue
                if any(e.get("machine_id") != a["machine_id"] for e in local_inputs):
                    continue
                if (
                    commit_as
                    and not center_identity
                    and not any(item["member_id"] == commit_as for item in json.loads(a["machine_github_members"]))
                ):
                    continue
                if t["capability"] not in json.loads(a["capabilities"]) and t["kind"] in (
                    "worker",
                    "reviewer",
                    "publisher",
                ):
                    continue
                if t["kind"] == "reviewer" and t["source_id"] and a["id"] == self.task(t["source_id"])["agent_id"]:
                    continue
                if self.one('SELECT id FROM tasks WHERE agent_id=? AND status="running"', (a["id"],)):
                    continue
                m = self.one("SELECT slots FROM machines WHERE id=?", (a["machine_id"],))
                active = self.one(
                    "SELECT count(*) AS n FROM tasks t JOIN agents a ON a.id=t.agent_id "
                    'WHERE a.machine_id=? AND t.status="running"',
                    (a["machine_id"],),
                )["n"]
                if active >= m["slots"]:
                    continue
                history = self.one(
                    "SELECT sum(status='done') AS accepted,sum(status='failed') AS failed FROM "
                    "(SELECT status FROM tasks WHERE agent_id=? AND kind='worker' AND status IN ('done','failed') "
                    "ORDER BY updated DESC LIMIT 12)",
                    (a["id"],),
                )
                history = {k: v or 0 for k, v in history.items()}
                score, explanation = evaluate(t, project, a, history, time.time())
                if score is not None and score > chosen_score:
                    chosen, chosen_score, reason = a, score, explanation
            if not chosen or chosen["machine_id"] != machine_id:
                if not chosen:
                    waiting = (
                        "等待可读取本地提交的同机成员；跨电脑协作需先同步工作分支到 GitHub。"
                        if local_inputs
                        else (
                            "项目要求以成员的 GitHub 身份提交；请这位成员在网页上用 GitHub 登录一次，"
                            "或在执行电脑上运行 pal-arena github login。"
                            if commit_as
                            and not center_identity
                            and not any(
                                item["member_id"] == commit_as
                                for a in candidates
                                for item in json.loads(a["machine_github_members"])
                            )
                            else "暂无同时满足角色、能力、接入状态、并发和模型偏好的可用成员。"
                        )
                    )
                    self.db.execute("UPDATE tasks SET routing_reason=? WHERE id=?", (waiting, t["id"]))
                continue
            token = secrets.token_urlsafe(32)
            agent_token = secrets.token_urlsafe(32)
            member_id = t["channel"].partition(":")[2] if t["channel"] != "shared" else None
            memories = self.active_memories(t["project_id"], member_id)
            if t["kind"] == "leader":
                # A queued conversation starts with the latest agreed project context.
                # Once leased, its revision stays fixed for compare-and-swap updates.
                self.db.execute(
                    "UPDATE tasks SET revision=?,acceptance=? WHERE id=?",
                    (project["revision"], encoded(project["requirements"]), t["id"]),
                )
            self.db.execute(
                "UPDATE tasks SET routing_reason=?,memory_snapshot=? WHERE id=?", (reason, encoded(memories), t["id"])
            )
            self.db.execute(
                'UPDATE tasks SET status="running",agent_id=?,attempt=attempt+1,lease_hash=?,agent_hash=?,lease_until=?, '
                "updated=? WHERE id=?",
                (chosen["id"], digest(token), digest(agent_token), time.time() + 90, time.time(), t["id"]),
            )
            self.event(t["project_id"], t["id"], "running", f'{chosen["name"]} 已领取任务')
            bound = {item["member_id"]: item for item in json.loads(chosen.pop("machine_github_members"))}
            return {
                "task": self.public_task(self.task(t["id"])),
                "project": self.project(t["project_id"]),
                "agent": chosen,
                "commit_identity": (
                    {
                        **{key: bound[commit_as][key] for key in ("member_id", "github_id", "github_login")},
                        "source": "local",
                    }
                    if commit_as and commit_as in bound
                    else {"member_id": commit_as, **center_identity, "source": "center"} if commit_as else None
                ),
                "run_token": token,
                "agent_token": agent_token,
                "source": self.public_task(self.task(t["source_id"])) if t["source_id"] else None,
                "dependencies": [self.public_task(self.task(d)) for d in t["dependencies"]],
            }
        return None

    def active_run(self, token, *, worker_only=False):
        predicate = "lease_hash=?" if worker_only else "(lease_hash=? OR agent_hash=?)"
        params = (digest(token),) if worker_only else (digest(token), digest(token))
        t = self.one(
            f'SELECT id FROM tasks WHERE {predicate} AND status="running" AND lease_until>?',
            (*params, time.time()),
        )
        if not t:
            raise Conflict("运行已失效或取消，不能继续回写")
        return self.task(t["id"])

    def complete(self, token, result, evidence, failed=False):
        t = self.active_run(token, worker_only=True)
        p = self.project(t["project_id"])
        commit_as = p["policy"].get("commit_as")
        if not failed and commit_as and t["kind"] in ("worker", "leader") and evidence.get("branch"):
            if (evidence.get("commit_identity") or {}).get("member_id") != commit_as:
                failed = True
                result += "\n提交未使用项目指定成员的 GitHub 身份，成果未被接受。"
        state = "failed" if failed else "done"
        if not failed and t["revision"] != p["revision"]:
            state = "changes_requested"
            result += "\n需求版本已变化，需要按当前清单重新执行和验收。"
        if t["kind"] == "publisher":
            project_state = "delivered" if state == "done" else "release_failed"
            self.db.execute("UPDATE projects SET status=? WHERE id=?", (project_state, t["project_id"]))
        if not failed and state == "done" and t["kind"] == "worker":
            unfinished = self.one(
                'SELECT id FROM tasks WHERE parent_id=? AND kind="worker" AND status NOT IN ("done","cancelled")',
                (t["id"],),
            )
            state = "coordinating" if unfinished else "in_review"
            self.db.execute("UPDATE tasks SET status=? WHERE id=?", (state, t["id"]))
            if not unfinished:
                self.create_task(
                    t["project_id"],
                    "验收 · " + t["title"],
                    "独立验证原任务、当前需求及实际产物。通过 report_review 提交结构化结论和证据。",
                    kind="reviewer",
                    capability=t["capability"],
                    acceptance=t["acceptance"],
                    source_id=t["id"],
                    channel=t["channel"],
                    difficulty=t["difficulty"],
                    context_tokens=t["context_tokens"],
                )
        if not failed and t["kind"] == "reviewer":
            review = t["review"]
            source = self.task(t["source_id"])
            expected_commit = source["evidence"].get("commit")
            if expected_commit and evidence.get("commit") != expected_commit:
                review = None
                result += "\n验收执行端未确认与原成果相同的提交，不能通过验收。"
            if source["status"] != "in_review":
                self.db.execute(
                    'UPDATE tasks SET status="cancelled",lease_hash=NULL,agent_hash=NULL,updated=? WHERE id=?',
                    (time.time(), t["id"]),
                )
                self.event(t["project_id"], t["id"], "cancelled", "原任务已取消或重新执行，本次验收不再生效")
                return {"status": "cancelled"}
            if source["revision"] != p["revision"] or t["revision"] != p["revision"]:
                review = {
                    "decision": "changes_requested",
                    "summary": "需求已经变更，请按照当前验收清单修订。",
                    "evidence": [f'待验收版本 {source["revision"]}，当前需求版本 {p["revision"]}'],
                }
            if (
                not review
                or review.get("decision") not in ("accept", "changes_requested")
                or not review.get("summary")
                or not review.get("evidence")
            ):
                state = "blocked"
                result += "\n缺少结构化验收结论；不能把普通回复视为验收通过。"
            else:
                source_state = "done" if review["decision"] == "accept" else "changes_requested"
                if source["revision"] != p["revision"] or t["revision"] != p["revision"]:
                    source_state = "changes_requested"
                self.db.execute(
                    "UPDATE tasks SET status=?,review=?,updated=? WHERE id=?",
                    (source_state, encoded(review), time.time(), source["id"]),
                )
                self.event(t["project_id"], source["id"], source_state, review["summary"])
                if source_state == "changes_requested" and source["attempt"] < 3:
                    self.db.execute(
                        'UPDATE tasks SET status="queued",result=?,revision=?,lease_hash=NULL,agent_hash=NULL,review=NULL '
                        "WHERE id=?",
                        (review["summary"], p["revision"], source["id"]),
                    )
                    self.event(t["project_id"], source["id"], "queued", "已按验收意见安排修订")
        self.db.execute(
            "UPDATE tasks SET status=?,result=?,evidence=?,lease_hash=NULL,agent_hash=NULL,lease_until=NULL,updated=? WHERE id=?",
            (state, result[-80_000:], encoded(evidence), time.time(), t["id"]),
        )
        self.event(t["project_id"], t["id"], state, result[-8000:] or state)
        self.save_message(uid(), t["project_id"], t["title"], result[-20_000:], t["channel"], time.time())
        self.reconcile(t["project_id"], t["channel"])
        return {"status": state}
