"""Small sqlite3 repository for Phase 1A."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator

from .models import Channel


class ChannelRepository:
    def __init__(self, database_path: str | Path = "data/crawl_yt.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    channel_url TEXT,
                    subscriber_count INTEGER,
                    video_count INTEGER,
                    view_count INTEGER,
                    discovery_keyword TEXT,
                    discovered_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    discovery_source TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_channels_discovery_keyword
                    ON channels(discovery_keyword);
                """
            )

    def upsert_channel(self, channel: Channel) -> bool:
        """Insert or update a channel, returning True only for a new row."""
        values = self._values(channel)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO channels (
                    channel_id, title, description, channel_url,
                    subscriber_count, video_count, view_count,
                    discovery_keyword, discovered_at, last_checked_at,
                    discovery_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                connection.execute(
                    """
                    UPDATE channels SET
                        title = ?, description = COALESCE(?, description),
                        channel_url = COALESCE(?, channel_url),
                        subscriber_count = COALESCE(?, subscriber_count),
                        video_count = COALESCE(?, video_count),
                        view_count = COALESCE(?, view_count),
                        discovery_keyword = ?, last_checked_at = ?,
                        discovery_source = ?
                    WHERE channel_id = ?
                    """,
                    (
                        channel.title,
                        channel.description,
                        channel.channel_url,
                        channel.subscriber_count,
                        channel.video_count,
                        channel.view_count,
                        channel.discovery_keyword,
                        self._timestamp(channel.last_checked_at),
                        channel.discovery_source,
                        channel.channel_id,
                    ),
                )
        return inserted

    def get_channel(self, channel_id: str) -> Channel | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
            ).fetchone()
        return self._channel(row) if row else None

    def list_channels(
        self, limit: int | None = None, discovery_keyword: str | None = None
    ) -> list[Channel]:
        sql = "SELECT * FROM channels"
        parameters: list[object] = []
        if discovery_keyword is not None:
            sql += " WHERE discovery_keyword = ?"
            parameters.append(discovery_keyword)
        sql += " ORDER BY discovered_at, channel_id"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._channel(row) for row in rows]

    def count_channels(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM channels").fetchone()
        return int(row[0])

    def discovery_keyword_counts(self) -> list[tuple[str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT discovery_keyword, COUNT(*) AS count
                FROM channels
                WHERE discovery_keyword IS NOT NULL
                GROUP BY discovery_keyword
                ORDER BY count DESC, discovery_keyword
                """
            ).fetchall()
        return [(str(row[0]), int(row[1])) for row in rows]

    @classmethod
    def _values(cls, channel: Channel) -> tuple[object, ...]:
        return (
            channel.channel_id,
            channel.title,
            channel.description,
            channel.channel_url,
            channel.subscriber_count,
            channel.video_count,
            channel.view_count,
            channel.discovery_keyword,
            cls._timestamp(channel.discovered_at or datetime.now(timezone.utc)),
            cls._timestamp(channel.last_checked_at),
            channel.discovery_source,
        )

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    @staticmethod
    def _channel(row: sqlite3.Row) -> Channel:
        return Channel(
            channel_id=row["channel_id"],
            title=row["title"],
            description=row["description"],
            channel_url=row["channel_url"],
            subscriber_count=row["subscriber_count"],
            video_count=row["video_count"],
            view_count=row["view_count"],
            discovery_keyword=row["discovery_keyword"],
            discovered_at=datetime.fromisoformat(row["discovered_at"]),
            last_checked_at=(
                datetime.fromisoformat(row["last_checked_at"])
                if row["last_checked_at"]
                else None
            ),
            discovery_source=row["discovery_source"],
        )
