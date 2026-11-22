"""Local, explicitly scoped facts and playback receipts. No model or audio calls."""
from __future__ import annotations

import os
import sqlite3
import stat

from research_runner.core import Blocked, check_int, digest, no_links


_APPLICATION_ID = int.from_bytes(b"FGCM", "big")
_SCHEMA_VERSION = 2


def _text(value, field, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise Blocked(f"INVALID_{field.upper()}")
    return value


def _require_private_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise Blocked("CONVERSATION_FILE_NOT_PRIVATE")


class ConversationMemory:
    """The trusted caller binds a user and project before serving any conversation.

    Playback callbacks acknowledge complete, sequential segments after delivery.
    Their exact text, including boundary spaces, is retained. Acknowledgement is
    a playback receipt, not proof that a person listened. Historical speech is
    separate from the current fact store. The host must control the database
    directory for the entire lifetime of the connection.
    """

    def __init__(self, path, user, project):
        self._scope = (_text(user, "user", 128), _text(project, "project", 128))
        path = no_links(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                info = path.with_name(path.name + suffix).lstat()
            except FileNotFoundError:
                continue
            _require_private_file(info)
        # SQLite normally creates files under umask. Explicitly create our
        # store owner-only even when the caller uses the system default umask.
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            _require_private_file(os.fstat(fd))
        finally:
            os.close(fd)
        self._db = sqlite3.connect(path, timeout=10)
        try:
            self._db.execute("PRAGMA foreign_keys=ON")
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                app_id = self._db.execute("PRAGMA application_id").fetchone()[0]
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                if app_id == _APPLICATION_ID and version == _SCHEMA_VERSION:
                    return
                if app_id == _APPLICATION_ID and version == 1:
                    self._add_proposals()
                    return
                previous = self._db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
                if app_id or version or previous:
                    raise Blocked("NOT_A_SUPPORTED_CONVERSATION_DATABASE")
                self._db.execute("""
                    CREATE TABLE facts (
                        user TEXT NOT NULL, project TEXT NOT NULL,
                        key TEXT NOT NULL, value TEXT NOT NULL,
                        PRIMARY KEY (user, project, key)
                    ) WITHOUT ROWID""")
                self._db.execute("""
                    CREATE TABLE turns (
                        id INTEGER PRIMARY KEY, user TEXT NOT NULL, project TEXT NOT NULL,
                        turn_id TEXT NOT NULL, status TEXT NOT NULL,
                        UNIQUE (user, project, turn_id)
                    )""")
                self._db.execute("""
                    CREATE TABLE chunks (
                        turn INTEGER NOT NULL REFERENCES turns(id), sequence INTEGER NOT NULL,
                        text TEXT NOT NULL, PRIMARY KEY (turn, sequence)
                    ) WITHOUT ROWID""")
                self._db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                self._add_proposals()
        except BaseException:
            self._db.close()
            raise

    def _add_proposals(self):
        self._db.execute("""
            CREATE TABLE proposals (
                user TEXT NOT NULL, project TEXT NOT NULL, proposal_id TEXT NOT NULL,
                key TEXT NOT NULL, request_hash TEXT NOT NULL, value TEXT, base_hash TEXT,
                status TEXT NOT NULL CHECK(status IN ('pending','applied','rejected','stale')),
                PRIMARY KEY (user, project, proposal_id)
            ) WITHOUT ROWID""")
        self._db.execute("""
            CREATE INDEX pending_fact_proposals ON proposals(user, project, key)
            WHERE status='pending'""")
        # A v1 connection that was already open during migration can still
        # write facts. The invalidation must happen inside the database for
        # those writes as well, including a change followed by a change back.
        for event, row in (("INSERT", "NEW"), ("UPDATE", "OLD"), ("DELETE", "OLD")):
            self._db.execute(f"""
                CREATE TRIGGER stale_proposals_after_{event} AFTER {event} ON facts
                BEGIN
                    UPDATE proposals SET status='stale', value=NULL, base_hash=NULL
                    WHERE user={row}.user AND project={row}.project AND key={row}.key
                        AND status='pending';
                END""")
        self._db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        self._db.close()

    def remember(self, key, value):
        """Store a confirmed fact; a repeated confirmation replaces its value."""
        key = _text(key, "key", 128)
        value = _text(value, "value", 4096)
        with self._db:
            self._set_fact(key, value)

    def _set_fact(self, key, value):
        self._db.execute("""
            INSERT INTO facts VALUES (?, ?, ?, ?)
            ON CONFLICT(user, project, key) DO UPDATE SET value=excluded.value
        """, (*self._scope, key, value))

    def _stale_proposals(self, key):
        self._db.execute("""
            UPDATE proposals SET status='stale', value=NULL, base_hash=NULL
            WHERE user=? AND project=? AND key=? AND status='pending'
        """, (*self._scope, key))

    def recall(self, key):
        key = _text(key, "key", 128)
        row = self._db.execute("SELECT value FROM facts WHERE user=? AND project=? AND key=?",
                               (*self._scope, key)).fetchone()
        return row[0] if row else None

    def correct(self, key, old_value, new_value):
        """Update only if the current value still matches the one being corrected."""
        key = _text(key, "key", 128)
        old_value = _text(old_value, "old_value", 4096)
        new_value = _text(new_value, "new_value", 4096)
        with self._db:
            result = self._db.execute("""
                UPDATE facts SET value=? WHERE user=? AND project=? AND key=? AND value=?
            """, (new_value, *self._scope, key, old_value))
            return bool(result.rowcount)

    def forget(self, key):
        """Remove the scoped fact from future recall."""
        key = _text(key, "key", 128)
        with self._db:
            result = self._db.execute("DELETE FROM facts WHERE user=? AND project=? AND key=?",
                                      (*self._scope, key))
            if not result.rowcount:
                self._stale_proposals(key)
            return bool(result.rowcount)

    def clear_scope(self):
        """Atomically remove this user's project facts and playback history."""
        with self._db:
            self._db.execute("""
                DELETE FROM chunks
                WHERE turn IN (SELECT id FROM turns WHERE user=? AND project=?)
            """, self._scope)
            self._db.execute("DELETE FROM turns WHERE user=? AND project=?", self._scope)
            self._db.execute("DELETE FROM facts WHERE user=? AND project=?", self._scope)
            self._db.execute("DELETE FROM proposals WHERE user=? AND project=?", self._scope)

    @staticmethod
    def _proposal_receipt(proposal_id, key, value, status):
        result = {"proposal_id": proposal_id, "key": key, "status": status}
        if status == "pending":
            result["value"] = value
        return result

    def _proposal_row(self, proposal_id):
        return self._db.execute("""
            SELECT key, value, status, request_hash, base_hash FROM proposals
            WHERE user=? AND project=? AND proposal_id=?
        """, (*self._scope, proposal_id)).fetchone()

    def proposal(self, proposal_id):
        """Preview a change for review, including after the reviewing client restarts."""
        proposal_id = _text(proposal_id, "proposal_id", 128)
        row = self._proposal_row(proposal_id)
        if row is None:
            raise Blocked("UNKNOWN_PROPOSAL")
        return self._proposal_receipt(proposal_id, *row[:3])

    def propose(self, proposal_id, key, value):
        """Record a possible change for explicit host confirmation. None requests removal."""
        proposal_id = _text(proposal_id, "proposal_id", 128)
        key = _text(key, "key", 128)
        if value is not None:
            value = _text(value, "value", 4096)
        request_hash = digest([key, value])
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._proposal_row(proposal_id)
            if row is not None:
                if row[3] != request_hash:
                    raise Blocked("CONFLICTING_PROPOSAL_ID")
                return self._proposal_receipt(proposal_id, *row[:3])
            self._db.execute("""
                INSERT INTO proposals(user, project, proposal_id, key, request_hash,
                                      value, base_hash, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (*self._scope, proposal_id, key, request_hash, value, digest(self.recall(key))))
            return self._proposal_receipt(proposal_id, key, value, "pending")

    def decide(self, proposal_id, accept):
        """Finalize a specific proposal using an independent trusted confirmation."""
        proposal_id = _text(proposal_id, "proposal_id", 128)
        if type(accept) is not bool:
            raise Blocked("INVALID_PROPOSAL_DECISION")
        requested = "applied" if accept else "rejected"
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._proposal_row(proposal_id)
            if row is None:
                raise Blocked("UNKNOWN_PROPOSAL")
            key, value, status, _, base_hash = row
            if status != "pending":
                if status not in (requested, "stale"):
                    raise Blocked("CONFLICTING_PROPOSAL_DECISION")
                return self._proposal_receipt(proposal_id, key, None, status)
            outcome = requested if digest(self.recall(key)) == base_hash else "stale"
            self._db.execute("""
                UPDATE proposals SET status=?, value=NULL, base_hash=NULL
                WHERE user=? AND project=? AND proposal_id=?
            """, (outcome, *self._scope, proposal_id))
            if outcome == "applied":
                if value is None:
                    removed = self._db.execute("DELETE FROM facts WHERE user=? AND project=? AND key=?",
                                               (*self._scope, key)).rowcount
                    if not removed:
                        self._stale_proposals(key)
                else:
                    self._set_fact(key, value)
            return self._proposal_receipt(proposal_id, key, None, outcome)

    def start_turn(self, turn_id):
        """A new reply interrupts its predecessor; retries never reopen old turns."""
        turn_id = _text(turn_id, "turn_id", 128)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            added = self._db.execute("""
                INSERT INTO turns(user, project, turn_id, status) VALUES (?, ?, ?, 'playing')
                ON CONFLICT(user, project, turn_id) DO NOTHING
            """, (*self._scope, turn_id)).rowcount
            if added:
                self._db.execute("""
                    UPDATE turns SET status='interrupted'
                    WHERE user=? AND project=? AND turn_id<>? AND status='playing'
                """, (*self._scope, turn_id))

    def _turn_row(self, turn_id):
        return self._db.execute("""
            SELECT id, status FROM turns WHERE user=? AND project=? AND turn_id=?
        """, (*self._scope, _text(turn_id, "turn_id", 128))).fetchone()

    def acknowledge(self, turn_id, sequence, text):
        """Append the next played chunk atomically; identical redeliveries are safe."""
        check_int(sequence, 1, 256)
        text = _text(text, "chunk", 2048)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._turn_row(turn_id)
            if row is None:
                raise Blocked("UNKNOWN_TURN")
            turn, status = row
            prior = self._db.execute("SELECT text FROM chunks WHERE turn=? AND sequence=?",
                                     (turn, sequence)).fetchone()
            if prior is not None:
                if prior[0] != text:
                    raise Blocked("CONFLICTING_PLAYBACK_RECEIPT")
                return
            last = self._db.execute("SELECT COALESCE(MAX(sequence),0) FROM chunks WHERE turn=?",
                                    (turn,)).fetchone()[0]
            if status != "playing" or sequence != last + 1:
                raise Blocked("PLAYBACK_CLOSED_OR_OUT_OF_ORDER")
            self._db.execute("INSERT INTO chunks VALUES (?, ?, ?)", (turn, sequence, text))

    def finish_turn(self, turn_id, *, interrupted):
        if type(interrupted) is not bool:
            raise Blocked("INVALID_INTERRUPTED_FLAG")
        status = "interrupted" if interrupted else "completed"
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._turn_row(turn_id)
            if row is None or row[1] not in ("playing", status):
                raise Blocked("UNKNOWN_OR_CLOSED_TURN")
            self._db.execute("UPDATE turns SET status=? WHERE id=?", (status, row[0]))

    def turn(self, turn_id):
        """Read status and exact played prefix from a single database snapshot."""
        turn_id = _text(turn_id, "turn_id", 128)
        rows = self._db.execute("""
            SELECT t.status, c.text FROM turns t LEFT JOIN chunks c ON t.id=c.turn
            WHERE t.user=? AND t.project=? AND t.turn_id=? ORDER BY c.sequence
        """, (*self._scope, turn_id)).fetchall()
        if not rows:
            return None
        texts = [text for _, text in rows if text is not None]
        return {"turn_id": turn_id, "status": rows[0][0], "acknowledged_chunks": len(texts),
                "spoken_text": "".join(texts)}

    def latest_turn(self):
        row = self._db.execute("""
            SELECT turn_id FROM turns WHERE user=? AND project=? ORDER BY id DESC LIMIT 1
        """, self._scope).fetchone()
        return self.turn(row[0]) if row else None

    def snapshot(self, keys):
        """Fetch a bounded set of current facts and the last reply in one snapshot."""
        if not isinstance(keys, (tuple, list)) or len(keys) > 16:
            raise Blocked("INVALID_CONTEXT_KEYS")
        with self._db:
            self._db.execute("BEGIN")
            return {"facts": {key: self.recall(key) for key in keys},
                    "latest_turn": self.latest_turn()}
