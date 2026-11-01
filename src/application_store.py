"""Persistent identities for application channels; names are display labels only."""

import sqlite3
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

    def record_created(self, record, token):
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

    def remove(self, record):
        with self.connection:
            self.connection.execute(
                "DELETE FROM applications WHERE guild_id = ? AND channel_id = ? "
                "AND applicant_id = ? AND message_id IS ?",
                (record.guild_id, record.channel_id, record.applicant_id, record.message_id)
            )

    def close(self):
        self.connection.close()
