"""Small sqlite3 repository for channels and discovery provenance."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import Channel, ChannelDiscovery, Transcript, Video


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
                raise sqlite3.DatabaseError(
                    "Legacy Phase 1A schema detected; migrate or reset it explicitly"
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
                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    published_at TEXT,
                    duration_seconds INTEGER,
                    view_count INTEGER,
                    like_count INTEGER,
                    comment_count INTEGER,
                    thumbnail_url TEXT,
                    webpage_url TEXT,
                    availability TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    metadata_source TEXT,
                    tags_json TEXT,
                    categories_json TEXT,
                    language TEXT,
                    metadata_enriched_at TEXT,
                    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_videos_channel_id
                    ON videos(channel_id);
                CREATE INDEX IF NOT EXISTS idx_videos_published_at
                    ON videos(published_at);
                CREATE TABLE IF NOT EXISTS transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    language TEXT NOT NULL,
                    source TEXT NOT NULL,
                    text TEXT NOT NULL,
                    segments_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                        ON DELETE CASCADE,
                    UNIQUE (video_id, language, source)
                );
                CREATE INDEX IF NOT EXISTS idx_transcripts_video_id
                    ON transcripts(video_id);
                CREATE INDEX IF NOT EXISTS idx_transcripts_language
                    ON transcripts(language);
                CREATE INDEX IF NOT EXISTS idx_transcripts_source
                    ON transcripts(source);
                """
            )
            video_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(videos)").fetchall()
            }
            for name in (
                "tags_json",
                "categories_json",
                "language",
                "metadata_enriched_at",
            ):
                if name not in video_columns:
                    connection.execute(f"ALTER TABLE videos ADD COLUMN {name} TEXT")

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


class VideoRepository(ChannelRepository):
    def upsert_video(self, video: Video) -> bool:
        """Upsert video metadata while preserving the original first_seen_at."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO videos (
                    video_id, channel_id, title, description, published_at,
                    duration_seconds, view_count, like_count, comment_count,
                    thumbnail_url, webpage_url, availability, first_seen_at,
                    last_checked_at, metadata_source, tags_json,
                    categories_json, language, metadata_enriched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._video_values(video),
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                connection.execute(
                    """
                    UPDATE videos SET
                        title = ?,
                        description = COALESCE(?, description),
                        published_at = COALESCE(?, published_at),
                        duration_seconds = COALESCE(?, duration_seconds),
                        view_count = COALESCE(?, view_count),
                        like_count = COALESCE(?, like_count),
                        comment_count = COALESCE(?, comment_count),
                        thumbnail_url = COALESCE(?, thumbnail_url),
                        webpage_url = COALESCE(?, webpage_url),
                        availability = COALESCE(?, availability),
                        last_checked_at = COALESCE(?, last_checked_at),
                        metadata_source = COALESCE(?, metadata_source),
                        tags_json = COALESCE(?, tags_json),
                        categories_json = COALESCE(?, categories_json),
                        language = COALESCE(?, language),
                        metadata_enriched_at = COALESCE(?, metadata_enriched_at)
                    WHERE video_id = ?
                    """,
                    (
                        video.title,
                        video.description,
                        self._timestamp(video.published_at),
                        video.duration_seconds,
                        video.view_count,
                        video.like_count,
                        video.comment_count,
                        video.thumbnail_url,
                        video.webpage_url,
                        video.availability,
                        self._timestamp(video.last_checked_at),
                        video.metadata_source,
                        self._json(video.tags),
                        self._json(video.categories),
                        video.language,
                        self._timestamp(video.metadata_enriched_at),
                        video.video_id,
                    ),
                )
        return inserted

    def get_video(self, video_id: str) -> Video | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM videos WHERE video_id = ?", (video_id,)
            ).fetchone()
        return self._video(row) if row else None

    def video_exists(self, video_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM videos WHERE video_id = ?", (video_id,)
            ).fetchone()
        return row is not None

    def count_videos(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM videos").fetchone()
        return int(row[0])

    def count_videos_for_channel(self, channel_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM videos WHERE channel_id = ?", (channel_id,)
            ).fetchone()
        return int(row[0])

    def count_videos_needing_enrichment(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM videos WHERE metadata_enriched_at IS NULL"
            ).fetchone()
        return int(row[0])

    def count_enriched_videos(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM videos WHERE metadata_enriched_at IS NOT NULL"
            ).fetchone()
        return int(row[0])

    def list_videos_needing_enrichment(
        self, channel_id: str | None = None, limit: int | None = None
    ) -> list[Video]:
        sql = "SELECT * FROM videos WHERE metadata_enriched_at IS NULL"
        parameters: list[object] = []
        if channel_id is not None:
            sql += " AND channel_id = ?"
            parameters.append(channel_id)
        sql += " ORDER BY first_seen_at, video_id"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._video(row) for row in rows]

    def list_videos_for_channel(
        self, channel_id: str, limit: int | None = None
    ) -> list[Video]:
        sql = """
            SELECT * FROM videos WHERE channel_id = ?
            ORDER BY published_at IS NULL, published_at DESC, video_id
        """
        parameters: list[object] = [channel_id]
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._video(row) for row in rows]

    @classmethod
    def _video_values(cls, video: Video) -> tuple[object, ...]:
        return (
            video.video_id,
            video.channel_id,
            video.title,
            video.description,
            cls._timestamp(video.published_at),
            video.duration_seconds,
            video.view_count,
            video.like_count,
            video.comment_count,
            video.thumbnail_url,
            video.webpage_url,
            video.availability,
            cls._timestamp(video.first_seen_at),
            cls._timestamp(video.last_checked_at),
            video.metadata_source,
            cls._json(video.tags),
            cls._json(video.categories),
            video.language,
            cls._timestamp(video.metadata_enriched_at),
        )

    @staticmethod
    def _json(value: list[str] | None) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None

    @staticmethod
    def _video(row: sqlite3.Row) -> Video:
        return Video(
            video_id=row["video_id"],
            channel_id=row["channel_id"],
            title=row["title"],
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            description=row["description"],
            published_at=(
                datetime.fromisoformat(row["published_at"])
                if row["published_at"]
                else None
            ),
            duration_seconds=row["duration_seconds"],
            view_count=row["view_count"],
            like_count=row["like_count"],
            comment_count=row["comment_count"],
            thumbnail_url=row["thumbnail_url"],
            webpage_url=row["webpage_url"],
            availability=row["availability"],
            last_checked_at=(
                datetime.fromisoformat(row["last_checked_at"])
                if row["last_checked_at"]
                else None
            ),
            metadata_source=row["metadata_source"],
            tags=json.loads(row["tags_json"]) if row["tags_json"] else None,
            categories=(
                json.loads(row["categories_json"])
                if row["categories_json"]
                else None
            ),
            language=row["language"],
            metadata_enriched_at=(
                datetime.fromisoformat(row["metadata_enriched_at"])
                if row["metadata_enriched_at"]
                else None
            ),
        )


class TranscriptRepository(VideoRepository):
    def upsert_transcript(self, transcript: Transcript) -> bool:
        now = datetime.now(timezone.utc)
        created_at = transcript.created_at or now
        updated_at = transcript.updated_at or now
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO transcripts (
                    video_id, language, source, text, segments_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transcript.video_id,
                    transcript.language,
                    transcript.source,
                    transcript.text,
                    json.dumps(transcript.segments, ensure_ascii=False),
                    self._timestamp(created_at),
                    self._timestamp(updated_at),
                ),
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                connection.execute(
                    """
                    UPDATE transcripts SET text = ?, segments_json = ?, updated_at = ?
                    WHERE video_id = ? AND language = ? AND source = ?
                    """,
                    (
                        transcript.text,
                        json.dumps(transcript.segments, ensure_ascii=False),
                        self._timestamp(updated_at),
                        transcript.video_id,
                        transcript.language,
                        transcript.source,
                    ),
                )
        return inserted

    def get_transcript(
        self,
        video_id: str,
        language: str | None = None,
        source: str | None = None,
    ) -> Transcript | None:
        sql = "SELECT * FROM transcripts WHERE video_id = ?"
        parameters: list[object] = [video_id]
        if language is not None:
            sql += " AND language = ?"
            parameters.append(language)
        if source is not None:
            sql += " AND source = ?"
            parameters.append(source)
        sql += """
            ORDER BY CASE source WHEN 'youtube_manual' THEN 0 ELSE 1 END,
                     updated_at DESC LIMIT 1
        """
        with self._connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return self._transcript(row) if row else None

    def list_transcripts_for_video(self, video_id: str) -> list[Transcript]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM transcripts WHERE video_id = ?
                ORDER BY language,
                    CASE source WHEN 'youtube_manual' THEN 0 ELSE 1 END
                """,
                (video_id,),
            ).fetchall()
        return [self._transcript(row) for row in rows]

    def transcript_exists(
        self,
        video_id: str,
        language: str | None = None,
        source: str | None = None,
    ) -> bool:
        return self.get_transcript(video_id, language, source) is not None

    def count_transcripts(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM transcripts").fetchone()
        return int(row[0])

    def count_videos_with_transcripts(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(DISTINCT video_id) FROM transcripts"
            ).fetchone()
        return int(row[0])

    def count_videos_without_transcripts(self) -> int:
        return self.count_videos() - self.count_videos_with_transcripts()

    def list_videos_needing_transcript(
        self,
        channel_id: str | None = None,
        limit: int | None = None,
        languages: tuple[str, ...] | None = None,
    ) -> list[Video]:
        sql = "SELECT v.* FROM videos AS v WHERE NOT EXISTS (SELECT 1 FROM transcripts AS t WHERE t.video_id = v.video_id"
        parameters: list[object] = []
        if languages:
            language_clauses: list[str] = []
            seen: set[tuple[str, str]] = set()
            for language in languages:
                normalized = language.lower()
                pair = (normalized, f"{normalized.split('-', 1)[0]}-%")
                if pair not in seen:
                    seen.add(pair)
                    language_clauses.append(
                        "(lower(t.language) = ? OR lower(t.language) LIKE ?)"
                    )
                    parameters.extend(pair)
            sql += " AND (" + " OR ".join(language_clauses) + ")"
        sql += ")"
        if channel_id is not None:
            sql += " AND v.channel_id = ?"
            parameters.append(channel_id)
        sql += " ORDER BY v.first_seen_at, v.video_id"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._video(row) for row in rows]

    @staticmethod
    def _transcript(row: sqlite3.Row) -> Transcript:
        return Transcript(
            video_id=row["video_id"],
            language=row["language"],
            source=row["source"],
            text=row["text"],
            segments=json.loads(row["segments_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
