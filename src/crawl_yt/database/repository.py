"""Small sqlite3 repository for channels and discovery provenance."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import Channel, ChannelDiscovery


class ChannelRepository:
    def __init__(self, database_path: str | Path = "data/crawl_yt.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(channels)").fetchall()
            }
            if columns & {"discovery_keyword", "discovery_source"}:
                connection.executescript(
                    "DROP TABLE IF EXISTS channel_discoveries; DROP TABLE channels;"
                )
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
                    last_checked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS channel_discoveries (
                    channel_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    source TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
                        ON DELETE CASCADE,
                    UNIQUE (channel_id, keyword, source)
                );
                CREATE INDEX IF NOT EXISTS idx_channel_discoveries_keyword
                    ON channel_discoveries(keyword);
                CREATE INDEX IF NOT EXISTS idx_channel_discoveries_channel_id
                    ON channel_discoveries(channel_id);
                """
            )

    def upsert_channel(self, channel: Channel) -> bool:
        """Upsert canonical metadata, returning True only for a new channel."""
        values = self._values(channel)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO channels (
                    channel_id, title, description, channel_url,
                    subscriber_count, video_count, view_count, last_checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                        last_checked_at = ?
                    WHERE channel_id = ?
                    """,
                    (
                        channel.title,
                        channel.description,
                        channel.channel_url,
                        channel.subscriber_count,
                        channel.video_count,
                        channel.view_count,
                        self._timestamp(channel.last_checked_at),
                        channel.channel_id,
                    ),
                )
        return inserted

    def record_discovery(
        self,
        channel_id: str,
        keyword: str,
        source: str,
        discovered_at: datetime | None = None,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO channel_discoveries
                    (channel_id, keyword, source, discovered_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    channel_id,
                    keyword,
                    source,
                    self._timestamp(discovered_at or datetime.now(timezone.utc)),
                ),
            )
        return cursor.rowcount == 1

    def get_channel(self, channel_id: str) -> Channel | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
            ).fetchone()
        return self._channel(row) if row else None

    def list_channels(self, limit: int | None = None) -> list[Channel]:
        sql = "SELECT * FROM channels ORDER BY channel_id"
        parameters: list[object] = []
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._channel(row) for row in rows]

    def list_discoveries_for_channel(
        self, channel_id: str
    ) -> list[ChannelDiscovery]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT channel_id, keyword, source, discovered_at
                FROM channel_discoveries
                WHERE channel_id = ?
                ORDER BY discovered_at, keyword, source
                """,
                (channel_id,),
            ).fetchall()
        return [self._discovery(row) for row in rows]

    def discovery_exists(self, channel_id: str, keyword: str, source: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM channel_discoveries
                WHERE channel_id = ? AND keyword = ? AND source = ?
                """,
                (channel_id, keyword, source),
            ).fetchone()
        return row is not None

    def count_channels(self) -> int:
        return self._count("channels")

    def count_discovery_relationships(self) -> int:
        return self._count("channel_discoveries")

    def count_channels_for_keyword(self, keyword: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(DISTINCT channel_id)
                FROM channel_discoveries WHERE keyword = ?
                """,
                (keyword,),
            ).fetchone()
        return int(row[0])

    def discovery_keyword_counts(self) -> list[tuple[str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT keyword, COUNT(DISTINCT channel_id) AS count
                FROM channel_discoveries
                GROUP BY keyword
                ORDER BY count DESC, keyword
                """
            ).fetchall()
        return [(str(row[0]), int(row[1])) for row in rows]

    def _count(self, table: str) -> int:
        if table not in {"channels", "channel_discoveries"}:
            raise ValueError("unsupported table")
        with self._connect() as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])

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
            cls._timestamp(channel.last_checked_at),
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
            last_checked_at=(
                datetime.fromisoformat(row["last_checked_at"])
                if row["last_checked_at"]
                else None
            ),
        )

    @staticmethod
    def _discovery(row: sqlite3.Row) -> ChannelDiscovery:
        return ChannelDiscovery(
            channel_id=row["channel_id"],
            keyword=row["keyword"],
            source=row["source"],
            discovered_at=datetime.fromisoformat(row["discovered_at"]),
        )
