#!/usr/bin/env python3
"""Validate and store agent worklogs on a local POSIX filesystem."""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
import errno
import fcntl
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid


IDENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
FILENAME = re.compile(rf"({DATE})_[a-z0-9][a-z0-9-]{{0,63}}(?:_(?:[2-9]|[1-9][0-9]+))?\.md")
SHA = re.compile(r"[0-9a-f]{64}")
FIELDS = (
    "schema_version", "date", "session_id", "session_key", "worktree_id",
    "workspace", "project_id", "project", "topic", "status", "created_at",
    "updated_at", "start_anchor", "last_sync_anchor", "excluded_sessions",
)
COLUMNS = r"((?:[^\\|]|\\[\\|])+)"
SECTIONS = {
    "WORK": ("T", r"\[(TODO|DOING|DONE|BLOCKED|SUPERSEDED)\] "),
    "METRICS": ("M", ""),
    "PITFALLS": ("P", r"\[(OPEN|RESOLVED|SUPERSEDED)\] "),
    "DECISIONS": ("D", ""),
}
ROWS = {
    section: re.compile(rf"{prefix}([1-9][0-9]*) {status}" + r" \| ".join([COLUMNS] * 4))
    for section, (prefix, status) in SECTIONS.items()
}
EXIT_CODES = {
    "usage": 2, "no_active": 3, "conflict": 4, "frozen": 4,
    "invalid": 5, "io": 6, "partial_failure": 6,
}


class WorklogError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise WorklogError("usage", message)


@dataclass(frozen=True)
class Scope:
    worktree_id: str
    workspace: str
    session_key: str
    project_id: str
    project: str

    @property
    def identity(self):
        return self.worktree_id, self.session_key


@dataclass(frozen=True)
class Entry:
    path: Path
    header: dict

    @property
    def identity(self):
        return self.header["worktree_id"], self.header["session_key"]

    def metadata(self):
        keys = ("date", "session_id", "session_key", "worktree_id",
                "workspace", "project_id", "project", "topic", "status")
        return {"file": self.path.name, "path": str(self.path),
                **{key: self.header[key] for key in keys}}


def now():
    return datetime.now().astimezone()


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError(f"invalid JSON constant {value}")


def decode_json(value):
    return json.loads(value, object_pairs_hook=json_object, parse_constant=invalid_constant)


def dump_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def require(condition, message):
    if not condition:
        raise WorklogError("invalid", message)


def one_line(value, label, limit=300):
    require(isinstance(value, str) and 0 < len(value) <= limit
            and value == value.strip()
            and all(ch.isprintable() for ch in value),
            f"{label} must be nonempty printable text of at most {limit} characters")
    return value


def identifier(value, label, pattern=IDENT):
    require(isinstance(value, str) and pattern.fullmatch(value), f"invalid {label}")
    return value


def calendar_date(value):
    require(isinstance(value, str) and re.fullmatch(DATE, value), "expected YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WorklogError("invalid", f"invalid calendar date: {value}") from exc


def timestamp(value, label):
    text = one_line(value, label)
    try:
        parsed = datetime.fromisoformat(text)
        require(parsed.tzinfo is not None and parsed.isoformat() == text,
                f"{label} must be ISO 8601 with a UTC offset")
    except ValueError as exc:
        raise WorklogError("invalid", f"invalid {label}") from exc
    return parsed


def session_id(value):
    try:
        require(isinstance(value, str) and str(uuid.UUID(value)) == value, "invalid session_id")
    except ValueError as exc:
        raise WorklogError("invalid", "invalid session_id") from exc
    return value


def anchor(value, label):
    require(isinstance(value, dict) and set(value) == {"kind", "value"}, f"invalid {label}")
    require(value["kind"] in ("event", "marker"), f"invalid {label} kind")
    one_line(value["value"], label)
    return value


def validate_header(header):
    require(set(header) == set(FIELDS), "unexpected or missing frontmatter fields")
    require(type(header["schema_version"]) is int and header["schema_version"] == 1,
            "unsupported schema_version")
    started = calendar_date(header["date"])
    session_id(header["session_id"])
    identifier(header["session_key"], "session_key")
    identifier(header["worktree_id"], "worktree_id", SHA)
    identifier(header["project_id"], "project_id")
    for field in ("workspace", "project", "topic"):
        one_line(header[field], field)
    require(header["status"] in ("active", "frozen"), "invalid status")
    created = timestamp(header["created_at"], "created_at")
    updated = timestamp(header["updated_at"], "updated_at")
    require(created.date() == started and updated >= created, "inconsistent timestamps")
    anchor(header["start_anchor"], "start_anchor")
    anchor(header["last_sync_anchor"], "last_sync_anchor")
    items = header["excluded_sessions"]
    require(isinstance(items, list), "invalid excluded_sessions")
    names = set()
    for item in items:
        require(isinstance(item, dict) and set(item) == {"file", "topic"},
                "invalid excluded_sessions entry")
        name = item["file"]
        require(isinstance(name, str) and FILENAME.fullmatch(name)
                and name.startswith(header["date"] + "_") and name not in names,
                "invalid or duplicate excluded session filename")
        one_line(item["topic"], "excluded topic")
        names.add(name)


def read_header(stream):
    require(stream.readline(5) == b"---\n", "missing initial frontmatter delimiter")
    header = {}
    try:
        for raw in iter(stream.readline, b""):
            if raw == b"---\n":
                validate_header(header)
                return header
            key, separator, value = raw.decode("utf-8").partition(": ")
            require(separator and key in FIELDS and key not in header, "invalid frontmatter key")
            header[key] = decode_json(value)
    except ValueError as exc:
        raise WorklogError("invalid", f"invalid frontmatter: {exc}") from exc
    raise WorklogError("invalid", "missing closing frontmatter delimiter")


def validate_prior(lines):
    require(isinstance(lines, list) and len(lines) <= 3, "PRIOR_CTX must be an array of 0–3 lines")
    for line in lines:
        one_line(line, "PRIOR_CTX line", 500)
        require(not line.startswith("#"), "PRIOR_CTX cannot contain headings")


def parse_log(log):
    require(isinstance(log, str), "log must be a Markdown string")
    lines = iter(log.rstrip("\n").split("\n"))
    require(next(lines, None) == "## LOG", "expected ## LOG")
    sections = iter(SECTIONS)
    current = None
    counts = dict.fromkeys(SECTIONS, 0)
    for line in lines:
        if not line:
            continue
        if line.startswith("### "):
            current = next(sections, None)
            require(current is not None and line == f"### {current}", f"unexpected section: {line}")
            continue
        require(current is not None and all(ch.isprintable() for ch in line),
                "invalid LOG line")
        match = ROWS[current].fullmatch(line)
        require(match is not None and all(part.strip() for part in match.groups()),
                f"invalid {current} row: {line[:100]}")
        counts[current] += 1
        require(match[1] == str(counts[current]), f"{current} IDs must be sequential from 1")
    require(next(sections, None) is None, "missing LOG sections")
    return counts


def parse_body(body):
    prefix, separator, log = body.partition("\n## LOG\n")
    require(separator and prefix.startswith("## PRIOR_CTX\n"), "expected PRIOR_CTX and LOG")
    prior = prefix[len("## PRIOR_CTX\n"):].rstrip("\n")
    validate_prior(prior.split("\n") if prior else [])
    log = ("## LOG\n" + log).rstrip("\n")
    return prefix + "\n", log, parse_log(log)


def log_document(header, prior_block, log):
    # JSON values on individual YAML lines eliminate ambiguous scalar coercion.
    frontmatter = "\n".join(f"{key}: {dump_json(header[key])}" for key in FIELDS)
    return f"---\n{frontmatter}\n---\n{prior_block}{log}\n".encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    require(stat.S_ISREG(path.lstat().st_mode), f"expected a regular file: {path}")


def read_entry(path, *, managed, full=False):
    try:
        regular(path)
        if full:
            data = path.read_bytes()
            stream = BytesIO(data)
            header = read_header(stream)
        else:
            # Header-only discovery stops at the delimiter without prefetching LOG bytes.
            with path.open("rb", buffering=0) as stream:
                header = read_header(stream)
        if managed:
            match = FILENAME.fullmatch(path.name)
            require(match and match[1] == header["date"], "filename date differs from frontmatter")
        entry = Entry(path, header)
        if not full:
            return entry
        prior, log, counts = parse_body(stream.read().decode("utf-8"))
        return entry, data, prior, log, counts
    except WorklogError as exc:
        raise WorklogError(exc.code, f"{path}: {exc}") from exc
    except OSError as exc:
        raise WorklogError("io", f"{path}: {exc.strerror}") from exc
    except UnicodeError as exc:
        raise WorklogError("invalid", f"{path}: invalid UTF-8") from exc


def git_value(cwd, option):
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", option], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=True,
        )
    except (FileNotFoundError, PermissionError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return Path(os.fsdecode(result.stdout.rstrip(b"\n")))


def display_name(path):
    return "".join(ch if ch.isprintable() else "�" for ch in path.name).strip() or "root"


def scope_for(cwd, key):
    cwd = resolved_path(cwd)
    require(cwd.is_dir(), f"work directory does not exist: {cwd}")
    identifier(key, "session_key")
    root = git_value(cwd, "--show-toplevel")
    common = git_value(cwd, "--git-common-dir") if root else None
    workspace = (cwd / root).resolve() if root else cwd
    project = (cwd / common).resolve() if common else workspace
    project_root = project.parent if common and project.name == ".git" else workspace
    return Scope(digest(os.fsencode(workspace)), display_name(workspace), key,
                 digest(os.fsencode(project)), display_name(project_root))


def resolved_path(name):
    try:
        return Path(name).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorklogError("invalid", f"could not resolve path {name!r}: {exc}") from exc


@contextmanager
def staged_file(path, data):
    fd, name = tempfile.mkstemp(prefix=".worklog-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def publish(temporary, path):
    os.replace(temporary, path)
    try:
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        return f"{path}: replaced, but directory sync failed: {exc}"
    return None


def atomic_write(path, data):
    with staged_file(path, data) as temporary:
        return publish(temporary, path)


def check_payload(payload, required, optional=()):
    require(isinstance(payload, dict), "expected a JSON object")
    missing = sorted(set(required) - payload.keys())
    unknown = sorted(payload.keys() - set(required) - set(optional))
    require(not missing and not unknown, f"unexpected payload fields: missing={missing}, unknown={unknown}")


def check_revision(entry, data, payload):
    session_id(payload["session_id"])
    identifier(payload["expected_revision"], "expected_revision", SHA)
    if payload["session_id"] != entry.header["session_id"] or payload["expected_revision"] != digest(data):
        raise WorklogError("conflict", "active session or revision changed. Read a fresh snapshot.")


class Store:
    def __init__(self):
        override = os.environ.get("WORKLOG_DIR")
        self.sessions = resolved_path(override or Path.home() / ".agent-worklog" / "sessions")
        self.briefs = self.sessions / "briefs" if override else self.sessions.parent / "briefs"
        self.state = self.sessions / ".state.json"

    @contextmanager
    def lock(self, writable=False):
        # On read-only operations, an absent store has no files to protect.
        if writable:
            self.sessions.mkdir(mode=0o700, parents=True, exist_ok=True)
        elif not self.sessions.exists():
            yield
            return
        path = self.sessions / ".worklog.lock"
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            try:
                fd = os.open(path, flags | os.O_CREAT, 0o600)
            except OSError as exc:
                if writable or exc.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
                    raise
                # A read-only imported store without a lock can still be reported on.
                yield
                return
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode), "store lock must be a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX if writable else fcntl.LOCK_SH)
            yield
        finally:
            os.close(fd)

    def entries(self, day=None):
        entries, errors = [], []
        if not self.sessions.exists():
            return entries, errors
        for path in sorted(self.sessions.iterdir()):
            match = FILENAME.fullmatch(path.name)
            if match and (day is None or match[1] == day):
                try:
                    entries.append(read_entry(path, managed=True))
                except WorklogError as exc:
                    errors.append({"path": str(path), "message": str(exc)})
        return entries, errors

    def inventory(self):
        entries, errors = self.entries()
        active = {}
        for entry in entries:
            if entry.header["status"] == "active":
                previous = active.get(entry.identity)
                if previous:
                    errors.append({"path": str(entry.path), "message":
                                   f"duplicate active scope: {previous.path} and {entry.path}"})
                active[entry.identity] = entry
        if errors:
            raise WorklogError("invalid", "managed inventory is inconsistent", issues=errors)
        return entries, active

    def refresh_state(self, entries):
        state = {"schema_version": 1, "active": {}, "frozen": []}
        for entry in sorted(entries, key=lambda item: item.path.name):
            if entry.header["status"] == "active":
                worktree, key = entry.identity
                state["active"].setdefault(worktree, {})[key] = entry.path.name
            else:
                state["frozen"].append(entry.path.name)
        if not self.sessions.exists():
            return None
        try:
            if os.path.lexists(self.state) and stat.S_ISREG(self.state.lstat().st_mode):
                try:
                    cached = decode_json(self.state.read_text(encoding="utf-8"))
                    if cached == state and type(cached["schema_version"]) is int:
                        return None
                except (ValueError, FileNotFoundError):
                    pass
            return atomic_write(self.state, (dump_json(state) + "\n").encode("utf-8"))
        except OSError as exc:
            return f"could not refresh index: {exc}"

    def inspect(self, command, scope):
        # Status also repairs the cache when the store already exists.
        repair = command == "status" and self.sessions.exists()
        with self.lock(writable=repair):
            entries, active = self.inventory()
            entry = active.get(scope.identity)
            if command == "status":
                warning = self.refresh_state(entries) if repair else None
                return {"active": entry.metadata() if entry else None,
                        "worktree_id": scope.worktree_id, "session_key": scope.session_key,
                        "warnings": [warning] if warning else []}
            if entry is None:
                raise WorklogError("no_active", "no active session for this worktree and key")
            _, data, _, _, _ = read_entry(entry.path, managed=True, full=True)
            return {**entry.metadata(), "revision": digest(data),
                    "last_sync_anchor": entry.header["last_sync_anchor"], "log": data.decode("utf-8")}

    def next_path(self, day, slug):
        candidate = self.sessions / f"{day}_{slug}.md"
        index = 2
        while os.path.lexists(candidate):
            candidate = self.sessions / f"{day}_{slug}_{index}.md"
            index += 1
        return candidate

    def init(self, scope, slug, auto_end, payload):
        required = ["topic", "prior_ctx", "start_anchor"]
        if auto_end:
            required += ["session_id", "expected_revision"]
        check_payload(payload, required, ("project", "project_id"))
        if auto_end:
            session_id(payload["session_id"])
            identifier(payload["expected_revision"], "expected_revision", SHA)
        topic = one_line(payload["topic"], "topic")
        prior = payload["prior_ctx"]
        validate_prior(prior)
        start = anchor(payload["start_anchor"], "start_anchor")
        if slug is None:
            slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:64].rstrip("-") or "work"
        identifier(slug, "slug", SLUG)
        project = one_line(payload.get("project", scope.project), "project")
        project_id = identifier(payload.get("project_id", scope.project_id), "project_id")
        warnings = []
        with self.lock(writable=True):
            entries, active = self.inventory()
            old = active.get(scope.identity)
            for entry in entries:
                if entry.identity == scope.identity and entry.header["start_anchor"] == start:
                    if entry == old:
                        _, data, _, _, _ = read_entry(entry.path, managed=True, full=True)
                        return self.committed(entry, data, entries, warnings, "already_active")
                    raise WorklogError("frozen", "a session with this start anchor is already frozen")
            if old and not auto_end:
                raise WorklogError("conflict", "scope already has an active session", active=old.metadata())
            if auto_end and not old:
                raise WorklogError("no_active", "previous active session is absent. Check status.")
            created = now()
            day = created.date().isoformat()
            excluded = [{"file": entry.path.name, "topic": entry.header["topic"]}
                        for entry in entries if entry.header["date"] == day
                        and (entry.header["status"] == "frozen" or entry == old)]
            header = dict(schema_version=1, date=day, session_id=str(uuid.uuid4()),
                          session_key=scope.session_key, worktree_id=scope.worktree_id,
                          workspace=scope.workspace, project_id=project_id, project=project,
                          topic=topic, status="active", created_at=created.isoformat(),
                          updated_at=created.isoformat(), start_anchor=start,
                          last_sync_anchor=start, excluded_sessions=excluded)
            validate_header(header)
            prior_block = "\n".join(["## PRIOR_CTX", *prior, "", ""])
            log = "\n\n".join(["## LOG", *(f"### {section}" for section in SECTIONS)])
            entry = Entry(self.next_path(day, slug), header)
            data = log_document(header, prior_block, log)
            if old:
                old, original, old_prior, old_log, _ = read_entry(old.path, managed=True, full=True)
                check_revision(old, original, payload)
            frozen = None
            with staged_file(entry.path, data) as temporary:
                try:
                    if old:
                        frozen = self.replace_log(old, old_prior, old_log, {}, True, warnings)
                        entries = [frozen if item == old else item for item in entries]
                        if warnings:
                            raise OSError("old session freeze could not be synced to disk")
                    warning = publish(temporary, entry.path)
                except OSError as exc:
                    if frozen:
                        cache_warning = self.refresh_state(entries)
                        if cache_warning:
                            warnings.append(cache_warning)
                        raise WorklogError("partial_failure", "old session frozen, new session not published",
                                           frozen=frozen.metadata(), warnings=warnings,
                                           cause=str(exc)) from exc
                    raise
                if warning:
                    warnings.append(warning)
            return self.committed(entry, data, [*entries, entry], warnings, "created",
                                  frozen=frozen.metadata() if frozen else None)

    def replace_log(self, entry, prior, log, changes, freeze, warnings):
        header = {**entry.header, **changes}
        header["status"] = "frozen" if freeze else "active"
        header["updated_at"] = max(now(), timestamp(header["updated_at"], "updated_at")).isoformat()
        warning = atomic_write(entry.path, log_document(header, prior, log))
        if warning:
            warnings.append(warning)
        return Entry(entry.path, header)

    def committed(self, entry, data, entries, warnings, result, **extra):
        warning = self.refresh_state(entries)
        if warning:
            warnings.append(warning)
        return {**entry.metadata(), **extra, "revision": digest(data), "result": result,
                "last_sync_anchor": entry.header["last_sync_anchor"],
                "committed": True, "warnings": warnings}

    def change(self, scope, payload, freeze):
        required = ("session_id", "expected_revision")
        updates = ("old_anchor", "new_anchor", "log")
        if freeze:
            check_payload(payload, required, ("final_update",))
            if "final_update" in payload:
                check_payload(payload["final_update"], updates, ("project",))
        else:
            check_payload(payload, required + updates, ("project",))
        target_id = session_id(payload["session_id"])
        identifier(payload["expected_revision"], "expected_revision", SHA)
        with self.lock(writable=True):
            entries, active = self.inventory()
            entry = active.get(scope.identity)
            if not entry or entry.header["session_id"] != target_id:
                if any(item.identity == scope.identity and item.header["session_id"] == target_id
                       and item.header["status"] == "frozen" for item in entries):
                    raise WorklogError("frozen", "target session is frozen")
                if entry is None:
                    raise WorklogError("no_active", "no active session for this worktree and key")
                raise WorklogError("conflict", "different session is active in this scope")
            entry, original, prior, log, counts = read_entry(entry.path, managed=True, full=True)
            check_revision(entry, original, payload)
            changes = {}
            edit = payload.get("final_update") if freeze else payload
            if edit is not None:
                old_anchor = anchor(edit["old_anchor"], "old_anchor")
                new_anchor = anchor(edit["new_anchor"], "new_anchor")
                if old_anchor != entry.header["last_sync_anchor"]:
                    raise WorklogError("conflict", "sync anchor changed. Read a fresh snapshot.")
                new_counts = parse_log(edit["log"])
                require(all(new_counts[section] >= count for section, count in counts.items()),
                        "stable entity IDs cannot be removed")
                new_log = edit["log"].rstrip("\n")
                project = one_line(edit.get("project", entry.header["project"]), "project")
                if list(filter(None, new_log.split("\n"))) != list(filter(None, log.split("\n"))):
                    require(new_anchor != old_anchor, "a changed LOG requires a new sync anchor")
                    require(new_anchor != entry.header["start_anchor"], "cannot return to the start anchor")
                    changes["last_sync_anchor"] = new_anchor
                    log = new_log
                if project != entry.header["project"]:
                    changes["project"] = project
            if not freeze and not changes:
                return {**entry.metadata(), "revision": digest(original), "result": "unchanged",
                        "last_sync_anchor": entry.header["last_sync_anchor"],
                        "committed": False, "warnings": []}
            warnings = []
            updated = self.replace_log(entry, prior, log, changes, freeze, warnings)
            data = log_document(updated.header, prior, log)
            entries = [updated if item.path == entry.path else item for item in entries]
            return self.committed(updated, data, entries, warnings, "frozen" if freeze else "updated")

    def list_by_date(self, day):
        with self.lock():
            entries, issues = self.entries(day)
            return {"date": day, "candidates": [entry.metadata() for entry in entries], "issues": issues}

    def collect(self, day, keys, files, select_all, all_matching_keys):
        if not (keys or files or select_all):
            raise WorklogError("usage", "select at least one --key, --file, or --all")
        for key in keys:
            identifier(key, "session_key")
        with self.lock():
            entries, issues = self.entries(day) if select_all or keys else ([], [])
            if select_all and issues:
                raise WorklogError("invalid", "one or more selected logs are invalid", issues=issues)
            selected = dict.fromkeys(entry.path for entry in entries) if select_all else {}
            for key in dict.fromkeys(keys):
                matches = [entry for entry in entries if entry.header["session_key"] == key]
                if not matches:
                    raise WorklogError("invalid", f"no candidates for key {key!r}", issues=issues)
                if len({entry.identity for entry in matches}) > 1 and not all_matching_keys and not select_all:
                    raise WorklogError("conflict", f"key {key!r} matches multiple worktrees",
                                       candidates=[entry.metadata() for entry in matches])
                selected.update(dict.fromkeys(entry.path for entry in matches))
            sources, failures, seen = [], [], {}
            for name in files:
                try:
                    selected[resolved_path(name)] = None
                except WorklogError as exc:
                    failures.append({"path": name, "message": str(exc)})
            for path in selected:
                try:
                    entry, data, _, _, _ = read_entry(
                        path, managed=path.parent == self.sessions and FILENAME.fullmatch(path.name) is not None,
                        full=True,
                    )
                except WorklogError as exc:
                    failures.append({"path": str(path), "message": str(exc)})
                    continue
                uid = entry.header["session_id"]
                revision = digest(data)
                if uid in seen:
                    first_path, first_revision = seen[uid]
                    if revision != first_revision:
                        failures.append({"path": str(path), "message":
                                         f"content differs from {first_path} with the same session_id"})
                    continue
                seen[uid] = path, revision
                sources.append({**entry.metadata(), "revision": revision, "log": data.decode("utf-8")})
            if failures:
                raise WorklogError("invalid", "one or more selected logs could not be collected", issues=failures)
            return {"date": day, "sources": sources, "issues": issues}

    def save_brief(self, day, text, overwrite):
        require(bool(text.strip()), "brief must contain Markdown")
        data = (text.rstrip("\n") + "\n").encode("utf-8")
        with self.lock(writable=True):
            self.briefs.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = self.briefs / f"{day}_brief.md"
            if os.path.lexists(path):
                regular(path)
                if path.read_bytes() == data:
                    return {"path": str(path), "result": "unchanged", "warnings": []}
                if not overwrite:
                    raise WorklogError("conflict", "brief already exists with different contents", path=str(path))
            warning = atomic_write(path, data)
            return {"path": str(path), "result": "saved", "warnings": [warning] if warning else []}


def parser():
    root = Parser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    scoped = {
        "init": "create a session from JSON on stdin",
        "status": "show the active session and repair the index",
        "get-active": "read the active session and its revision",
        "update": "replace the active LOG with a JSON payload on stdin",
        "freeze": "freeze the active session using a JSON payload on stdin",
    }
    dated = {
        "list-by-date": "list candidate metadata without reading log bodies",
        "collect": "read only the selected logs",
        "save-brief": "save Markdown from stdin",
    }
    for name, help_text in scoped.items():
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--cwd", default=".")
        command.add_argument("--key", default="default")
        if name == "init":
            command.add_argument("slug", nargs="?")
            command.add_argument("--auto-end", action="store_true")
    for name, help_text in dated.items():
        command = commands.add_parser(name, help=help_text)
        command.add_argument("date", nargs="?")
        if name == "collect":
            command.add_argument("--key", action="append", default=[])
            command.add_argument("--file", action="append", default=[])
            command.add_argument("--all", action="store_true")
            command.add_argument("--all-matching-keys", action="store_true")
        if name == "save-brief":
            command.add_argument("--overwrite", action="store_true")
    return root


def input_text():
    return sys.stdin.buffer.read().decode("utf-8")


def payload_from_stdin():
    try:
        return decode_json(input_text())
    except ValueError as exc:
        raise WorklogError("invalid", f"invalid JSON input: {exc}") from exc


def dispatch(args):
    store = Store()
    command = args.command
    if hasattr(args, "cwd"):
        scope = scope_for(args.cwd, args.key)
        if command == "init":
            return store.init(scope, args.slug, args.auto_end, payload_from_stdin())
        if command in ("status", "get-active"):
            return store.inspect(command, scope)
        return store.change(scope, payload_from_stdin(), freeze=command == "freeze")
    day = calendar_date(args.date).isoformat() if args.date is not None else now().date().isoformat()
    if command == "list-by-date":
        return store.list_by_date(day)
    if command == "collect":
        return store.collect(day, args.key, args.file, args.all, args.all_matching_keys)
    return store.save_brief(day, input_text(), args.overwrite)


def output_json(stream, value):
    stream.buffer.write((dump_json(value) + "\n").encode("utf-8", errors="backslashreplace"))


def main():
    try:
        result = dispatch(parser().parse_args())
    except WorklogError as exc:
        output_json(sys.stderr, {"error": exc.code, "message": str(exc), **exc.details})
        return EXIT_CODES[exc.code]
    except (UnicodeError, ValueError) as exc:
        output_json(sys.stderr, {"error": "invalid", "message": str(exc)})
        return EXIT_CODES["invalid"]
    except OSError as exc:
        message = str(exc)
        if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            message += " (check store permissions or set WORKLOG_DIR to a writable directory)"
        output_json(sys.stderr, {"error": "io", "message": message})
        return EXIT_CODES["io"]
    output_json(sys.stdout, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
