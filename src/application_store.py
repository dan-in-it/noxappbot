"""Persistent identities for application channels; names are display labels only."""

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class ApplicationRecord:
    guild_id: int
    channel_id: int
    applicant_id: int
    message_id: int | None = None


class ApplicationStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        with self.connection:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    applicant_id INTEGER NOT NULL,
                    message_id INTEGER,
                    PRIMARY KEY (guild_id, channel_id)
                )
            """)
            # Legacy installations can already have several channels per user.
            # Retain every one so none can disappear from duplicate checks.
            self.connection.execute("""
                CREATE INDEX IF NOT EXISTS applications_by_applicant
                ON applications (guild_id, applicant_id)
            """)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS application_reservations (
                    guild_id INTEGER NOT NULL,
                    applicant_id INTEGER NOT NULL,
                    token TEXT NOT NULL,
                    PRIMARY KEY (guild_id, applicant_id)
                )
            """)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS application_drafts (
                    applicant_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    payload TEXT NOT NULL
                )
            """)
            # Keep responses and decisions after channel cleanup. The applications
            # table remains the authoritative registry of retained channels.
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS application_workflows (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    applicant_id INTEGER NOT NULL,
                    questions TEXT NOT NULL DEFAULT '[]',
                    answers TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewer_id INTEGER,
                    decision_note TEXT,
                    decided_at REAL,
                    delete_after INTEGER,
                    delete_at REAL,
                    approval_role_ids TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (guild_id, channel_id)
                )
            """)
            self.connection.execute("""
                INSERT OR IGNORE INTO application_workflows (guild_id, channel_id, applicant_id)
                SELECT guild_id, channel_id, applicant_id FROM applications
            """)

    def save_draft(self, guild_id, applicant_id, payload):
        with self.connection:
            self.connection.execute(
                "INSERT INTO application_drafts (applicant_id, guild_id, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(applicant_id) DO UPDATE SET guild_id = excluded.guild_id, payload = excluded.payload",
                (applicant_id, guild_id, json.dumps(payload))
            )

    def get_draft(self, applicant_id):
        row = self.connection.execute(
            "SELECT guild_id, payload FROM application_drafts WHERE applicant_id = ?", (applicant_id,)
        ).fetchone()
        return {"guild_id": row["guild_id"], **json.loads(row["payload"])} if row else None

    def delete_draft(self, guild_id, applicant_id):
        with self.connection:
            self.connection.execute(
                "DELETE FROM application_drafts WHERE guild_id = ? AND applicant_id = ?", (guild_id, applicant_id)
            )

    def _new_workflow(self, record, questions=(), answers=()):
        self.connection.execute(
            "INSERT OR REPLACE INTO application_workflows "
            "(guild_id, channel_id, applicant_id, questions, answers) VALUES (?, ?, ?, ?, ?)",
            (record.guild_id, record.channel_id, record.applicant_id, json.dumps(questions), json.dumps(answers))
        )

    def workflow(self, record):
        row = self.connection.execute(
            "SELECT * FROM application_workflows WHERE guild_id = ? AND channel_id = ? AND applicant_id = ?",
            (record.guild_id, record.channel_id, record.applicant_id)
        ).fetchone()
        if row is None:
            raise RuntimeError("Application workflow is missing")
        result = dict(row)
        for key in ("questions", "answers", "approval_role_ids"):
            result[key] = json.loads(result[key])
        return result

    def begin_decision(self, record, status, reviewer_id, note, delete_after, role_ids=()):
        if status not in ("approving", "rejecting"):
            raise ValueError("Invalid decision")
        with self.connection:
            if self.get(record.guild_id, record.channel_id) != record or record.message_id is None:
                raise RuntimeError("Application record changed during decision")
            cursor = self.connection.execute(
                "UPDATE application_workflows SET status = ?, reviewer_id = ?, decision_note = ?, "
                "delete_after = ?, approval_role_ids = ? WHERE guild_id = ? AND channel_id = ? "
                "AND applicant_id = ? AND status = 'pending'",
                (status, reviewer_id, note, delete_after, json.dumps(role_ids),
                 record.guild_id, record.channel_id, record.applicant_id)
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Application already has a decision")

    def finish_decision(self, record, status):
        previous = {"approved": "approving", "rejected": "rejecting"}[status]
        now = time.time()
        with self.connection:
            if self.get(record.guild_id, record.channel_id) != record:
                raise RuntimeError("Application record changed during decision")
            cursor = self.connection.execute(
                "UPDATE application_workflows SET status = ?, decided_at = ?, "
                "delete_at = CASE WHEN delete_after IS NULL THEN NULL ELSE ? + delete_after END "
                "WHERE guild_id = ? AND channel_id = ? AND applicant_id = ? AND status = ?",
                (status, now, now, record.guild_id, record.channel_id, record.applicant_id, previous)
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Application decision changed")
        return self.workflow(record)

    def scheduled_deletions(self, guild_id):
        rows = self.connection.execute(
            "SELECT a.guild_id, a.channel_id, a.applicant_id, a.message_id FROM applications a "
            "JOIN application_workflows w USING (guild_id, channel_id) WHERE a.guild_id = ? "
            "AND a.message_id IS NOT NULL AND w.status IN ('approved', 'rejected') AND w.delete_at IS NOT NULL",
            (guild_id,)
        ).fetchall()
        return [ApplicationRecord(*row) for row in rows]

    def get(self, guild_id, channel_id):
        row = self.connection.execute(
            "SELECT guild_id, channel_id, applicant_id, message_id FROM applications "
            "WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id)
        ).fetchone()
        return ApplicationRecord(*row) if row else None

    def for_applicant(self, guild_id, applicant_id):
        rows = self.connection.execute(
            "SELECT guild_id, channel_id, applicant_id, message_id FROM applications "
            "WHERE guild_id = ? AND applicant_id = ?", (guild_id, applicant_id)
        ).fetchall()
        return [ApplicationRecord(*row) for row in rows]

    def add(self, record):
        with self.connection:
            self.connection.execute(
                "INSERT INTO applications (guild_id, channel_id, applicant_id, message_id) "
                "VALUES (?, ?, ?, ?)",
                (record.guild_id, record.channel_id, record.applicant_id, record.message_id)
            )
            self._new_workflow(record)

    def has_reservation(self, guild_id, applicant_id):
        return self.connection.execute(
            "SELECT 1 FROM application_reservations WHERE guild_id = ? AND applicant_id = ?",
            (guild_id, applicant_id)
        ).fetchone() is not None

    def reserve(self, guild_id, applicant_id):
        """Commit a duplicate guard before the non-transactional Discord request."""
        token = uuid4().hex
        with self.connection:
            if self.for_applicant(guild_id, applicant_id):
                raise RuntimeError("Applicant already has a retained application")
            self.connection.execute(
                "INSERT INTO application_reservations (guild_id, applicant_id, token) VALUES (?, ?, ?)",
                (guild_id, applicant_id, token)
            )
        return token

    def record_created(self, record, token, questions=(), answers=()):
        """Atomically replace our reservation with the confirmed channel identity."""
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM application_reservations WHERE guild_id = ? AND applicant_id = ? AND token = ?",
                (record.guild_id, record.applicant_id, token)
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Application reservation changed during creation")
            self.connection.execute(
                "INSERT INTO applications (guild_id, channel_id, applicant_id, message_id) VALUES (?, ?, ?, ?)",
                (record.guild_id, record.channel_id, record.applicant_id, record.message_id)
            )
            self._new_workflow(record, questions, answers)

    def release_reservation(self, guild_id, applicant_id, token):
        with self.connection:
            self.connection.execute(
                "DELETE FROM application_reservations WHERE guild_id = ? AND applicant_id = ? AND token = ?",
                (guild_id, applicant_id, token)
            )

    def mark_submitted(self, record, message_id):
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE applications SET message_id = ? WHERE guild_id = ? "
                "AND channel_id = ? AND applicant_id = ? AND message_id IS NULL",
                (message_id, record.guild_id, record.channel_id, record.applicant_id)
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Application record changed during submission")
            self.connection.execute(
                "DELETE FROM application_drafts WHERE guild_id = ? AND applicant_id = ?",
                (record.guild_id, record.applicant_id)
            )

    def remove(self, record):
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM applications WHERE guild_id = ? AND channel_id = ? "
                "AND applicant_id = ? AND message_id IS ?",
                (record.guild_id, record.channel_id, record.applicant_id, record.message_id)
            )
            if cursor.rowcount:
                self.connection.execute(
                    "UPDATE application_workflows SET delete_at = NULL WHERE guild_id = ? AND channel_id = ?",
                    (record.guild_id, record.channel_id)
                )

    def close(self):
        self.connection.close()
