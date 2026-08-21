"""Small sqlite3 repository for channels and discovery provenance."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import (
    Channel,
    ChannelCrawlState,
    ChannelScore,
    ChannelDiscovery,
    DiscoveryQuery,
    DiscoveryRun,
    OperationalBudget,
    Transcript,
    TranscriptAttempt,
    Video,
    VideoScore,
    WorkItem,
    WorkPlan,
)
from ..discovery.normalization import normalize_discovery_keyword


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
                    normalized_keyword TEXT,
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
                CREATE TABLE IF NOT EXISTS transcript_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    requested_language TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN ('success', 'failed', 'unavailable', 'skipped')
                    ),
                    error_type TEXT,
                    error_message TEXT,
                    attempted_at TEXT NOT NULL,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_transcript_attempts_video_id
                    ON transcript_attempts(video_id);
                CREATE INDEX IF NOT EXISTS idx_transcript_attempts_provider
                    ON transcript_attempts(provider);
                CREATE INDEX IF NOT EXISTS idx_transcript_attempts_attempted_at
                    ON transcript_attempts(attempted_at);
                CREATE TABLE IF NOT EXISTS channel_crawl_state (
                    channel_id TEXT PRIMARY KEY,
                    last_crawl_started_at TEXT,
                    last_crawl_completed_at TEXT,
                    last_success_at TEXT,
                    last_error_at TEXT,
                    last_error TEXT,
                    last_seen_video_id TEXT,
                    last_seen_published_at TEXT,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    total_crawls INTEGER NOT NULL DEFAULT 0,
                    next_crawl_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_channel_crawl_state_next_crawl_at
                    ON channel_crawl_state(next_crawl_at);
                CREATE INDEX IF NOT EXISTS idx_channel_crawl_state_last_success_at
                    ON channel_crawl_state(last_success_at);
                CREATE TABLE IF NOT EXISTS channel_scores (
                    channel_id TEXT PRIMARY KEY,
                    score REAL NOT NULL,
                    relevance_score REAL NOT NULL,
                    activity_score REAL NOT NULL,
                    traction_score REAL NOT NULL,
                    confidence_score REAL NOT NULL,
                    tier TEXT NOT NULL CHECK (tier IN ('high', 'medium', 'low', 'unscored')),
                    reason_json TEXT NOT NULL,
                    scored_at TEXT NOT NULL,
                    scoring_version TEXT NOT NULL,
                    cadence_score REAL,
                    videos_per_week_30d REAL,
                    videos_per_week_90d REAL,
                    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_channel_scores_score
                    ON channel_scores(score);
                CREATE INDEX IF NOT EXISTS idx_channel_scores_tier
                    ON channel_scores(tier);
                CREATE INDEX IF NOT EXISTS idx_channel_scores_scored_at
                    ON channel_scores(scored_at);
                CREATE TABLE IF NOT EXISTS discovery_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seed_keyword TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN ('running', 'completed', 'failed', 'stopped_budget')
                    ),
                    max_depth INTEGER NOT NULL,
                    channel_budget INTEGER NOT NULL,
                    query_budget INTEGER NOT NULL,
                    channels_discovered INTEGER NOT NULL DEFAULT 0,
                    queries_executed INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT
                );
                CREATE TABLE IF NOT EXISTS discovery_queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    query TEXT NOT NULL,
                    depth INTEGER NOT NULL,
                    parent_query TEXT,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'completed', 'failed')
                    ),
                    channels_found INTEGER NOT NULL DEFAULT 0,
                    new_channels INTEGER NOT NULL DEFAULT 0,
                    executed_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES discovery_runs(id)
                        ON DELETE CASCADE,
                    UNIQUE (run_id, query)
                );
                CREATE INDEX IF NOT EXISTS idx_discovery_queries_run_id
                    ON discovery_queries(run_id);
                CREATE INDEX IF NOT EXISTS idx_discovery_queries_depth
                    ON discovery_queries(depth);
                CREATE INDEX IF NOT EXISTS idx_discovery_queries_status
                    ON discovery_queries(status);
                CREATE TABLE IF NOT EXISTS work_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('planned', 'running', 'completed', 'partial', 'failed')
                    ),
                    budget_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS work_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL,
                    item_type TEXT NOT NULL CHECK (
                        item_type IN ('crawl_channel', 'enrich_video',
                                      'transcript_video', 'discovery_expand')
                    ),
                    target_id TEXT,
                    priority REAL NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'running', 'completed', 'failed', 'skipped')
                    ),
                    reason_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error_message TEXT,
                    FOREIGN KEY (plan_id) REFERENCES work_plans(id)
                        ON DELETE CASCADE,
                    UNIQUE (plan_id, item_type, target_id)
                );
                CREATE INDEX IF NOT EXISTS idx_work_items_plan_id
                    ON work_items(plan_id);
                CREATE INDEX IF NOT EXISTS idx_work_items_item_type
                    ON work_items(item_type);
                CREATE INDEX IF NOT EXISTS idx_work_items_status
                    ON work_items(status);
                CREATE INDEX IF NOT EXISTS idx_work_items_priority
                    ON work_items(priority);
                CREATE TABLE IF NOT EXISTS video_scores (
                    video_id TEXT PRIMARY KEY,
                    score REAL NOT NULL,
                    recency_score REAL NOT NULL,
                    channel_score REAL NOT NULL,
                    traction_score REAL NOT NULL,
                    metadata_value_score REAL NOT NULL,
                    transcript_value_score REAL NOT NULL,
                    confidence_score REAL NOT NULL,
                    tier TEXT NOT NULL,
                    reason_json TEXT NOT NULL,
                    scored_at TEXT NOT NULL,
                    scoring_version TEXT NOT NULL,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_video_scores_score
                    ON video_scores(score);
                CREATE INDEX IF NOT EXISTS idx_video_scores_tier
                    ON video_scores(tier);
                CREATE INDEX IF NOT EXISTS idx_video_scores_scored_at
                    ON video_scores(scored_at);
                """
            )
            score_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(channel_scores)").fetchall()
            }
            for name in ("cadence_score", "videos_per_week_30d", "videos_per_week_90d"):
                if name not in score_columns:
                    connection.execute(f"ALTER TABLE channel_scores ADD COLUMN {name} REAL")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_channel_scores_cadence_30d "
                "ON channel_scores(videos_per_week_30d)"
            )
            discovery_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(channel_discoveries)").fetchall()
            }
            if "normalized_keyword" not in discovery_columns:
                connection.execute("ALTER TABLE channel_discoveries ADD COLUMN normalized_keyword TEXT")
            legacy_rows = connection.execute(
                "SELECT rowid, keyword FROM channel_discoveries WHERE normalized_keyword IS NULL OR normalized_keyword = ''"
            ).fetchall()
            for row in legacy_rows:
                connection.execute(
                    "UPDATE channel_discoveries SET normalized_keyword = ? WHERE rowid = ?",
                    (normalize_discovery_keyword(row["keyword"]), row["rowid"]),
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_channel_discoveries_normalized_keyword ON channel_discoveries(normalized_keyword)"
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
        canonical = normalize_discovery_keyword(keyword)
        if not canonical:
            raise ValueError("discovery keyword must not be empty")
        if self.discovery_exists(channel_id, canonical, source):
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO channel_discoveries
                    (channel_id, keyword, normalized_keyword, source, discovered_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    canonical,
                    canonical,
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

    def list_channels_page(
        self, limit: int, offset: int = 0, search: str | None = None,
        tier: str | None = None, keyword: str | None = None,
        min_videos_per_week: float | None = None, sort: str = "score",
    ) -> list[dict[str, object]]:
        clauses = ["1=1"]
        parameters: list[object] = []
        if search:
            clauses.append("(c.title LIKE ? OR c.channel_id LIKE ?)")
            pattern = f"%{search}%"
            parameters.extend((pattern, pattern))
        if tier:
            clauses.append("COALESCE(cs.tier, 'unscored') = ?")
            parameters.append(tier)
        if keyword:
            clauses.append("EXISTS (SELECT 1 FROM channel_discoveries cd WHERE cd.channel_id = c.channel_id AND cd.normalized_keyword = ?)")
            parameters.append(normalize_discovery_keyword(keyword))
        if min_videos_per_week is not None:
            clauses.append(
                "json_extract(cs.reason_json, '$.observation_coverage_30d') = 1 "
                "AND cs.videos_per_week_30d >= ?"
            )
            parameters.append(min_videos_per_week)
        order_by = {
            "score": "cs.score IS NULL, cs.score DESC, c.channel_id",
            "cadence": "cs.videos_per_week_30d IS NULL, cs.videos_per_week_30d DESC, c.channel_id",
            "subscribers": "c.subscriber_count IS NULL, c.subscriber_count DESC, c.channel_id",
        }.get(sort, "cs.score IS NULL, cs.score DESC, c.channel_id")
        parameters.extend((limit, offset))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT c.*, cs.score AS score, cs.tier AS tier,
                       s.last_success_at, s.next_crawl_at, s.consecutive_failures,
                       CASE WHEN json_extract(cs.reason_json, '$.observation_coverage_30d') = 1
                            THEN cs.videos_per_week_30d END AS videos_per_week_30d,
                       CASE WHEN json_extract(cs.reason_json, '$.observation_coverage_30d') = 1
                            THEN json_extract(cs.reason_json, '$.cadence_fit') END AS cadence_fit,
                       (SELECT COUNT(*) FROM videos v WHERE v.channel_id = c.channel_id) AS observed_videos,
                       (SELECT GROUP_CONCAT(DISTINCT cd.keyword) FROM channel_discoveries cd
                        WHERE cd.channel_id = c.channel_id) AS discovery_keywords
                FROM channels c
                LEFT JOIN channel_scores cs ON cs.channel_id = c.channel_id
                LEFT JOIN channel_crawl_state s ON s.channel_id = c.channel_id
                WHERE {' AND '.join(clauses)}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def count_channels_page(
        self, search: str | None = None, tier: str | None = None, keyword: str | None = None,
        min_videos_per_week: float | None = None,
    ) -> int:
        clauses = ["1=1"]
        parameters: list[object] = []
        if search:
            clauses.append("(c.title LIKE ? OR c.channel_id LIKE ?)")
            pattern = f"%{search}%"
            parameters.extend((pattern, pattern))
        if tier:
            clauses.append("COALESCE(cs.tier, 'unscored') = ?")
            parameters.append(tier)
        if keyword:
            clauses.append("EXISTS (SELECT 1 FROM channel_discoveries cd WHERE cd.channel_id = c.channel_id AND cd.normalized_keyword = ?)")
            parameters.append(normalize_discovery_keyword(keyword))
        if min_videos_per_week is not None:
            clauses.append(
                "json_extract(cs.reason_json, '$.observation_coverage_30d') = 1 "
                "AND cs.videos_per_week_30d >= ?"
            )
            parameters.append(min_videos_per_week)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM channels c LEFT JOIN channel_scores cs ON cs.channel_id = c.channel_id WHERE {' AND '.join(clauses)}",
                parameters,
            ).fetchone()
        return int(row[0])

    def list_discoveries_for_channel(
        self, channel_id: str
    ) -> list[ChannelDiscovery]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT channel_id, normalized_keyword AS keyword, source, discovered_at
                FROM channel_discoveries
                WHERE channel_id = ?
                ORDER BY discovered_at, keyword, source
                """,
                (channel_id,),
            ).fetchall()
        return [self._discovery(row) for row in rows]

    def discovery_exists(self, channel_id: str, keyword: str, source: str) -> bool:
        canonical = normalize_discovery_keyword(keyword)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM channel_discoveries
                WHERE channel_id = ? AND normalized_keyword = ? AND source = ?
                """,
                (channel_id, canonical, source),
            ).fetchone()
        return row is not None

    def count_channels(self) -> int:
        return self._count("channels")

    def count_channels_due_for_crawl(self, now: datetime | None = None) -> int:
        current = self._timestamp(now or datetime.now(timezone.utc))
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) FROM channel_crawl_state
                   WHERE next_crawl_at IS NOT NULL AND next_crawl_at <= ?""",
                (current,),
            ).fetchone()
        return int(row[0])

    def count_failing_channels(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM channel_crawl_state WHERE consecutive_failures > 0"
            ).fetchone()
        return int(row[0])

    def count_discovery_relationships(self) -> int:
        return self._count("channel_discoveries")

    def count_channels_for_keyword(self, keyword: str) -> int:
        canonical = normalize_discovery_keyword(keyword)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(DISTINCT channel_id)
                FROM channel_discoveries WHERE normalized_keyword = ?
                """,
                (canonical,),
            ).fetchone()
        return int(row[0])

    def discovery_keyword_counts(self) -> list[tuple[str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT normalized_keyword, COUNT(DISTINCT channel_id) AS count
                FROM channel_discoveries
                GROUP BY normalized_keyword
                ORDER BY count DESC, normalized_keyword
                """
            ).fetchall()
        return [(str(row[0]), int(row[1])) for row in rows]

    def list_discovery_keyword_summaries(self) -> list[dict[str, object]]:
        """Return normalized keyword provenance summaries for the history UI."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT normalized_keyword AS keyword,
                          COUNT(DISTINCT channel_id) AS channel_count,
                          MIN(discovered_at) AS first_discovered,
                          MAX(discovered_at) AS last_discovered
                   FROM channel_discoveries
                   GROUP BY normalized_keyword
                   ORDER BY last_discovered DESC, normalized_keyword"""
            ).fetchall()
        return [dict(row) for row in rows]

    def list_discovery_keywords(self) -> list[str]:
        return [str(item["keyword"]) for item in self.list_discovery_keyword_summaries()]

    def list_channels_for_discovery_keyword(self, keyword: str, limit: int, offset: int = 0) -> list[dict[str, object]]:
        canonical = normalize_discovery_keyword(keyword)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT c.*, cs.score, cs.tier,
                    (SELECT COUNT(*) FROM videos v WHERE v.channel_id = c.channel_id) AS observed_videos,
                    MIN(cd.discovered_at) AS first_discovered, MAX(cd.discovered_at) AS last_discovered,
                    (SELECT GROUP_CONCAT(DISTINCT other.keyword) FROM channel_discoveries other
                     WHERE other.channel_id = c.channel_id
                       AND other.normalized_keyword <> ?) AS other_keywords
                   FROM channels c
                   JOIN channel_discoveries cd ON cd.channel_id = c.channel_id
                   LEFT JOIN channel_scores cs ON cs.channel_id = c.channel_id
                   WHERE cd.normalized_keyword = ?
                   GROUP BY c.channel_id
                   ORDER BY cs.score IS NULL, cs.score DESC, c.channel_id
                   LIMIT ? OFFSET ?""",
                (canonical, canonical, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_channels_for_discovery_keyword(self, keyword: str) -> int:
        canonical = normalize_discovery_keyword(keyword)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(DISTINCT channel_id) FROM channel_discoveries
                   WHERE normalized_keyword = ?""",
                (canonical,),
            ).fetchone()
        return int(row[0])

    def get_discovery_keywords_for_channels(self, channel_ids: list[str]) -> dict[str, list[str]]:
        if not channel_ids:
            return {}
        placeholders = ",".join("?" for _ in channel_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT channel_id, normalized_keyword FROM channel_discoveries WHERE channel_id IN ({placeholders}) ORDER BY channel_id, normalized_keyword",
                channel_ids,
            ).fetchall()
        result = {channel_id: [] for channel_id in channel_ids}
        for row in rows:
            keyword = str(row["normalized_keyword"])
            if keyword not in result[str(row["channel_id"])]:
                result[str(row["channel_id"])].append(keyword)
        return result

    def list_channel_ids_for_keyword(self, keyword: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT channel_id FROM channel_discoveries
                WHERE normalized_keyword = ? ORDER BY channel_id
                """,
                (normalize_discovery_keyword(keyword),),
            ).fetchall()
        return [str(row[0]) for row in rows]

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
    def upsert_video_score(self, score: VideoScore) -> VideoScore:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO video_scores (
                    video_id, score, recency_score, channel_score, traction_score,
                    metadata_value_score, transcript_value_score, confidence_score,
                    tier, reason_json, scored_at, scoring_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    score=excluded.score,
                    recency_score=excluded.recency_score,
                    channel_score=excluded.channel_score,
                    traction_score=excluded.traction_score,
                    metadata_value_score=excluded.metadata_value_score,
                    transcript_value_score=excluded.transcript_value_score,
                    confidence_score=excluded.confidence_score,
                    tier=excluded.tier,
                    reason_json=excluded.reason_json,
                    scored_at=excluded.scored_at,
                    scoring_version=excluded.scoring_version
                """,
                (
                    score.video_id, score.score, score.recency_score,
                    score.channel_score, score.traction_score,
                    score.metadata_value_score, score.transcript_value_score,
                    score.confidence_score, score.tier, score.reason_json,
                    self._timestamp(score.scored_at), score.scoring_version,
                ),
            )
        return score

    def get_video_score(self, video_id: str) -> VideoScore | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM video_scores WHERE video_id = ?", (video_id,)
            ).fetchone()
        return self._video_score(row) if row else None

    def get_video_scoring_input(self, video_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT v.*, c.subscriber_count AS channel_subscriber_count,
                       cs.score AS channel_score,
                       EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.video_id)
                           AS transcript_present
                FROM videos v
                JOIN channels c ON c.channel_id = v.channel_id
                LEFT JOIN channel_scores cs ON cs.channel_id = v.channel_id
                WHERE v.video_id = ?
                """,
                (video_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "video": self._video(row),
            "channel_subscribers": row["channel_subscriber_count"],
            "channel_score": row["channel_score"],
            "transcript_present": bool(row["transcript_present"]),
        }

    def list_video_ids(self, limit: int) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT video_id FROM videos ORDER BY first_seen_at, video_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [str(row["video_id"]) for row in rows]

    def list_top_video_scores(self, limit: int) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*, v.title, v.channel_id, c.title AS channel_title,
                       v.published_at, v.view_count
                FROM video_scores s
                JOIN videos v ON v.video_id = s.video_id
                JOIN channels c ON c.channel_id = v.channel_id
                ORDER BY s.score DESC, s.video_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_work_plan(self, plan: WorkPlan, items: list[WorkItem]) -> WorkPlan:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO work_plans (
                    created_at, status, budget_json, summary_json, completed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    self._timestamp(plan.created_at),
                    plan.status,
                    json.dumps(
                        {
                            "max_channel_crawls": plan.budget.max_channel_crawls,
                            "max_video_enrichments": plan.budget.max_video_enrichments,
                            "max_transcripts": plan.budget.max_transcripts,
                            "max_discovery_queries": plan.budget.max_discovery_queries,
                        }
                    ),
                    json.dumps(plan.summary),
                    self._timestamp(plan.completed_at),
                ),
            )
            plan.id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO work_items (
                    plan_id, item_type, target_id, priority, status,
                    reason_json, created_at, started_at, completed_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        plan.id,
                        item.item_type,
                        item.target_id,
                        item.priority,
                        item.status,
                        json.dumps(item.reasons, ensure_ascii=False),
                        self._timestamp(item.created_at),
                        self._timestamp(item.started_at),
                        self._timestamp(item.completed_at),
                        item.error_message,
                    )
                    for item in items
                ],
            )
        return plan

    def get_work_plan(self, plan_id: int) -> WorkPlan | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM work_plans WHERE id = ?", (plan_id,)
            ).fetchone()
        return self._work_plan(row) if row else None

    def list_work_plans(self, limit: int) -> list[WorkPlan]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM work_plans ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._work_plan(row) for row in rows]

    def list_work_plans_page(self, limit: int, offset: int = 0) -> list[WorkPlan]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM work_plans ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._work_plan(row) for row in rows]

    def count_work_plans(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM work_plans").fetchone()
        return int(row[0])

    def list_work_items(
        self,
        plan_id: int,
        statuses: tuple[str, ...] | None = None,
        limit: int | None = None,
        item_types: tuple[str, ...] | None = None,
    ) -> list[WorkItem]:
        sql = "SELECT * FROM work_items WHERE plan_id = ?"
        parameters: list[object] = [plan_id]
        if statuses:
            sql += f" AND status IN ({','.join('?' for _ in statuses)})"
            parameters.extend(statuses)
        if item_types:
            sql += f" AND item_type IN ({','.join('?' for _ in item_types)})"
            parameters.extend(item_types)
        sql += " ORDER BY priority DESC, item_type, target_id, id"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._work_item(row) for row in rows]

    def mark_work_item_running(
        self, item_id: int, now: datetime | None = None
    ) -> None:
        timestamp = self._timestamp(now or datetime.now(timezone.utc))
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE work_items SET status = 'running', started_at = ?,
                    completed_at = NULL, error_message = NULL WHERE id = ?
                """,
                (timestamp, item_id),
            )

    def finish_work_item(
        self,
        item_id: int,
        status: str,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE work_items SET status = ?, completed_at = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    status,
                    self._timestamp(now or datetime.now(timezone.utc)),
                    error_message,
                    item_id,
                ),
            )

    def update_work_plan_status(
        self, plan_id: int, status: str, now: datetime | None = None
    ) -> None:
        terminal = status in {"completed", "failed"}
        with self._connect() as connection:
            connection.execute(
                "UPDATE work_plans SET status = ?, completed_at = ? WHERE id = ?",
                (
                    status,
                    self._timestamp(now or datetime.now(timezone.utc)) if terminal else None,
                    plan_id,
                ),
            )

    def refresh_work_plan_status(self, plan_id: int) -> str:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM work_items WHERE plan_id = ? GROUP BY status",
                (plan_id,),
            ).fetchall()
        counts = {str(row[0]): int(row[1]) for row in rows}
        if not counts or set(counts) <= {"completed", "skipped"}:
            status = "completed"
        else:
            status = "partial"
        self.update_work_plan_status(plan_id, status)
        return status

    def work_plan_status_counts(self) -> dict[str, int]:
        counts = {key: 0 for key in ("planned", "running", "partial", "completed", "failed")}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM work_plans GROUP BY status"
            ).fetchall()
        for status, count in rows:
            counts[str(status)] = int(count)
        return counts

    def pending_work_item_counts(self) -> dict[str, int]:
        counts = {key: 0 for key in ("crawl_channel", "enrich_video", "transcript_video", "discovery_expand")}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT item_type, COUNT(*) FROM work_items
                WHERE status = 'pending' GROUP BY item_type
                """
            ).fetchall()
        for item_type, count in rows:
            counts[str(item_type)] = int(count)
        return counts

    def list_crawl_work_candidates(
        self, limit: int, now: datetime
    ) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.channel_id, c.title, s.next_crawl_at, s.last_success_at,
                       s.consecutive_failures, cs.score, cs.tier
                FROM channel_crawl_state AS s
                JOIN channels AS c ON c.channel_id = s.channel_id
                LEFT JOIN channel_scores AS cs ON cs.channel_id = c.channel_id
                WHERE s.next_crawl_at <= ?
                  AND NOT EXISTS (
                    SELECT 1 FROM work_items wi JOIN work_plans wp ON wp.id = wi.plan_id
                    WHERE wi.item_type = 'crawl_channel' AND wi.target_id = c.channel_id
                      AND wi.status IN ('pending', 'running')
                      AND wp.status IN ('planned', 'running', 'partial')
                  )
                ORDER BY (
                    CASE COALESCE(cs.tier, 'unscored')
                        WHEN 'high' THEN 300 WHEN 'medium' THEN 200
                        WHEN 'unscored' THEN 150 ELSE 100 END
                    + MIN(50.0, MAX(0.0, julianday(?) - julianday(s.next_crawl_at)))
                    - s.consecutive_failures * 20
                ) DESC, c.channel_id
                LIMIT ?
                """,
                (self._timestamp(now), self._timestamp(now), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_video_work_candidates(
        self, item_type: str, limit: int, now: datetime
    ) -> list[dict[str, object]]:
        if item_type not in {"enrich_video", "transcript_video"}:
            raise ValueError("unsupported video work type")
        pending = (
            "v.metadata_enriched_at IS NULL"
            if item_type == "enrich_video"
            else "NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.video_id)"
        )
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT v.video_id, v.channel_id, v.published_at,
                       v.metadata_enriched_at, cs.score, cs.tier
                FROM videos AS v
                LEFT JOIN channel_scores AS cs ON cs.channel_id = v.channel_id
                WHERE {pending}
                  AND NOT EXISTS (
                    SELECT 1 FROM work_items wi JOIN work_plans wp ON wp.id = wi.plan_id
                    WHERE wi.item_type = ? AND wi.target_id = v.video_id
                      AND wi.status IN ('pending', 'running')
                      AND wp.status IN ('planned', 'running', 'partial')
                  )
                ORDER BY (
                    COALESCE(cs.score, 0) * 2
                    + CASE WHEN v.published_at IS NULL THEN 0
                        ELSE MAX(0.0, 100.0 -
                            (julianday(?) - julianday(v.published_at)) / 3.0)
                      END
                    + CASE WHEN ? = 'transcript_video'
                              AND v.metadata_enriched_at IS NOT NULL
                           THEN 20 ELSE 0 END
                ) DESC, v.video_id
                LIMIT ?
                """,
                (item_type, self._timestamp(now), item_type, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _video_score(row: sqlite3.Row) -> VideoScore:
        return VideoScore(
            video_id=row["video_id"],
            score=float(row["score"]),
            recency_score=float(row["recency_score"]),
            channel_score=float(row["channel_score"]),
            traction_score=float(row["traction_score"]),
            metadata_value_score=float(row["metadata_value_score"]),
            transcript_value_score=float(row["transcript_value_score"]),
            confidence_score=float(row["confidence_score"]),
            tier=row["tier"],
            reason_json=row["reason_json"],
            scored_at=datetime.fromisoformat(row["scored_at"]),
            scoring_version=row["scoring_version"],
        )

    @staticmethod
    def _work_plan(row: sqlite3.Row) -> WorkPlan:
        budget = json.loads(row["budget_json"])
        return WorkPlan(
            id=int(row["id"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            status=row["status"],
            budget=OperationalBudget(**budget),
            summary=json.loads(row["summary_json"]),
            completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
        )

    @staticmethod
    def _work_item(row: sqlite3.Row) -> WorkItem:
        return WorkItem(
            id=int(row["id"]),
            plan_id=int(row["plan_id"]),
            item_type=row["item_type"],
            target_id=row["target_id"],
            priority=float(row["priority"]),
            status=row["status"],
            reasons=json.loads(row["reason_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=(datetime.fromisoformat(row["started_at"]) if row["started_at"] else None),
            completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
            error_message=row["error_message"],
        )

    def create_discovery_run(self, run: DiscoveryRun) -> DiscoveryRun:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO discovery_runs (
                    seed_keyword, started_at, completed_at, status, max_depth,
                    channel_budget, query_budget, channels_discovered,
                    queries_executed, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.seed_keyword,
                    self._timestamp(run.started_at),
                    self._timestamp(run.completed_at),
                    run.status,
                    run.max_depth,
                    run.channel_budget,
                    run.query_budget,
                    run.channels_discovered,
                    run.queries_executed,
                    run.error_message,
                ),
            )
            run.id = int(cursor.lastrowid)
        return run

    def finish_discovery_run(
        self,
        run_id: int,
        status: str,
        channels_discovered: int,
        queries_executed: int,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE discovery_runs SET completed_at = ?, status = ?,
                    channels_discovered = ?, queries_executed = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    self._timestamp(completed_at or datetime.now(timezone.utc)),
                    status,
                    channels_discovered,
                    queries_executed,
                    error_message,
                    run_id,
                ),
            )

    def get_discovery_run(self, run_id: int) -> DiscoveryRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM discovery_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return self._discovery_run(row) if row else None

    def list_discovery_runs(self, limit: int) -> list[DiscoveryRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM discovery_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._discovery_run(row) for row in rows]

    def add_discovery_query(self, query: DiscoveryQuery) -> DiscoveryQuery:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO discovery_queries (
                    run_id, query, depth, parent_query, source, status,
                    channels_found, new_channels, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query.run_id,
                    query.query,
                    query.depth,
                    query.parent_query,
                    query.source,
                    query.status,
                    query.channels_found,
                    query.new_channels,
                    self._timestamp(query.executed_at),
                ),
            )
            query.id = int(cursor.lastrowid)
        return query

    def finish_discovery_query(
        self,
        query_id: int,
        status: str,
        channels_found: int,
        new_channels: int,
        executed_at: datetime | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE discovery_queries SET status = ?, channels_found = ?,
                    new_channels = ?, executed_at = ? WHERE id = ?
                """,
                (
                    status,
                    channels_found,
                    new_channels,
                    self._timestamp(executed_at or datetime.now(timezone.utc)),
                    query_id,
                ),
            )

    def list_discovery_queries(self, run_id: int) -> list[DiscoveryQuery]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM discovery_queries WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [self._discovery_query(row) for row in rows]

    def get_expansion_inputs(self, channel_id: str) -> dict[str, object]:
        channel = self.get_channel(channel_id)
        if channel is None:
            raise ValueError(f"channel {channel_id} is not in the database")
        discoveries = [
            item.keyword for item in self.list_discoveries_for_channel(channel_id)
        ]
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT title, tags_json, categories_json FROM videos
                WHERE channel_id = ?
                ORDER BY published_at IS NULL, published_at DESC, first_seen_at DESC
                LIMIT 10
                """,
                (channel_id,),
            ).fetchall()
        tags: list[str] = []
        titles: list[str] = []
        for row in rows:
            titles.append(str(row["title"]))
            tags.extend(json.loads(row["tags_json"]) if row["tags_json"] else [])
            tags.extend(
                json.loads(row["categories_json"])
                if row["categories_json"]
                else []
            )
        return {
            "channel_title": channel.title,
            "discovery_keywords": discoveries,
            "video_titles": titles,
            "tags": tags,
        }

    @staticmethod
    def _discovery_run(row: sqlite3.Row) -> DiscoveryRun:
        return DiscoveryRun(
            id=int(row["id"]),
            seed_keyword=row["seed_keyword"],
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            status=row["status"],
            max_depth=int(row["max_depth"]),
            channel_budget=int(row["channel_budget"]),
            query_budget=int(row["query_budget"]),
            channels_discovered=int(row["channels_discovered"]),
            queries_executed=int(row["queries_executed"]),
            error_message=row["error_message"],
        )

    @staticmethod
    def _discovery_query(row: sqlite3.Row) -> DiscoveryQuery:
        return DiscoveryQuery(
            id=int(row["id"]),
            run_id=int(row["run_id"]),
            query=row["query"],
            depth=int(row["depth"]),
            parent_query=row["parent_query"],
            source=row["source"],
            status=row["status"],
            channels_found=int(row["channels_found"]),
            new_channels=int(row["new_channels"]),
            executed_at=(
                datetime.fromisoformat(row["executed_at"])
                if row["executed_at"]
                else None
            ),
        )

    def upsert_channel_score(self, score: ChannelScore) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO channel_scores (
                    channel_id, score, relevance_score, activity_score,
                    traction_score, confidence_score, tier, reason_json,
                    scored_at, scoring_version, cadence_score,
                    videos_per_week_30d, videos_per_week_90d
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    score = excluded.score,
                    relevance_score = excluded.relevance_score,
                    activity_score = excluded.activity_score,
                    traction_score = excluded.traction_score,
                    confidence_score = excluded.confidence_score,
                    tier = excluded.tier,
                    reason_json = excluded.reason_json,
                    scored_at = excluded.scored_at,
                    scoring_version = excluded.scoring_version,
                    cadence_score = excluded.cadence_score,
                    videos_per_week_30d = excluded.videos_per_week_30d,
                    videos_per_week_90d = excluded.videos_per_week_90d
                """,
                (
                    score.channel_id,
                    score.score,
                    score.relevance_score,
                    score.activity_score,
                    score.traction_score,
                    score.confidence_score,
                    score.tier,
                    json.dumps(score.reasons, ensure_ascii=False),
                    self._timestamp(score.scored_at),
                    score.scoring_version,
                    score.cadence_score
                    if score.cadence_score is not None
                    else (score.activity_score if score.scoring_version == "v2" else None),
                    score.videos_per_week_30d,
                    score.videos_per_week_90d,
                ),
            )

    def get_channel_score(self, channel_id: str) -> ChannelScore | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM channel_scores WHERE channel_id = ?", (channel_id,)
            ).fetchone()
        return self._channel_score(row) if row else None

    def list_top_channels(
        self, limit: int, tier: str | None = None
    ) -> list[tuple[Channel, ChannelScore]]:
        sql = """
            SELECT c.*, s.score AS scored_score, s.relevance_score,
                   s.activity_score, s.traction_score, s.confidence_score,
                   s.tier, s.reason_json, s.scored_at, s.scoring_version,
                   s.cadence_score, s.videos_per_week_30d, s.videos_per_week_90d
            FROM channel_scores AS s
            JOIN channels AS c ON c.channel_id = s.channel_id
        """
        parameters: list[object] = []
        if tier is not None:
            sql += " WHERE s.tier = ?"
            parameters.append(tier)
        sql += " ORDER BY s.score DESC, c.channel_id LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [
            (self._channel(row), self._channel_score(row, score_column="scored_score"))
            for row in rows
        ]

    def count_channels_by_score_tier(self) -> dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0, "unscored": 0}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT tier, COUNT(*) FROM channel_scores GROUP BY tier"
            ).fetchall()
            total = connection.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        for tier, count in rows:
            counts[str(tier)] = int(count)
        counts["unscored"] += int(total) - sum(int(row[1]) for row in rows)
        return counts

    def list_unscored_channels(self, limit: int) -> list[Channel]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.* FROM channels AS c
                LEFT JOIN channel_scores AS s ON s.channel_id = c.channel_id
                WHERE s.channel_id IS NULL
                ORDER BY c.channel_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._channel(row) for row in rows]

    def get_channel_scoring_signals(
        self, channel_id: str, now: datetime
    ) -> dict[str, object] | None:
        cutoff_30 = self._timestamp(now - timedelta(days=30))
        cutoff_90 = self._timestamp(now - timedelta(days=90))
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.*,
                    (SELECT COUNT(DISTINCT keyword) FROM channel_discoveries
                     WHERE channel_id = c.channel_id) AS discovery_keywords,
                    (SELECT COUNT(*) FROM videos
                     WHERE channel_id = c.channel_id) AS observed_videos,
                    (SELECT COUNT(*) FROM videos WHERE channel_id = c.channel_id
                     AND published_at IS NOT NULL) AS published_videos,
                    (SELECT MAX(published_at) FROM videos
                     WHERE channel_id = c.channel_id) AS latest_published_at,
                    (SELECT COUNT(*) FROM videos WHERE channel_id = c.channel_id
                     AND published_at >= ?) AS videos_last_30d,
                    (SELECT COUNT(*) FROM videos WHERE channel_id = c.channel_id
                     AND published_at >= ?) AS videos_last_90d,
                    (SELECT COUNT(*) FROM videos WHERE channel_id = c.channel_id
                     AND metadata_enriched_at IS NOT NULL) AS enriched_videos
                FROM channels AS c WHERE c.channel_id = ?
                """,
                (cutoff_30, cutoff_90, channel_id),
            ).fetchone()
            if row is None:
                return None
            published_dates = [
                datetime.fromisoformat(item[0])
                for item in connection.execute(
                    "SELECT published_at FROM videos "
                    "WHERE channel_id = ? AND published_at IS NOT NULL "
                    "ORDER BY published_at",
                    (channel_id,),
                ).fetchall()
            ]
            views = [
                int(item[0])
                for item in connection.execute(
                    """
                    SELECT view_count FROM videos
                    WHERE channel_id = ? AND metadata_enriched_at IS NOT NULL
                      AND view_count IS NOT NULL
                    """,
                    (channel_id,),
                ).fetchall()
            ]
            recent_views = [
                int(item[0])
                for item in connection.execute(
                    "SELECT view_count FROM videos "
                    "WHERE channel_id = ? AND metadata_enriched_at IS NOT NULL "
                    "AND view_count IS NOT NULL AND published_at >= ?",
                    (channel_id, cutoff_30),
                ).fetchall()
            ]
        return {
            "channel": self._channel(row),
            "discovery_keywords": int(row["discovery_keywords"]),
            "observed_videos": int(row["observed_videos"]),
            "published_videos": int(row["published_videos"]),
            "latest_published_at": (
                datetime.fromisoformat(row["latest_published_at"])
                if row["latest_published_at"]
                else None
            ),
            "videos_last_30d": int(row["videos_last_30d"]),
            "videos_last_90d": int(row["videos_last_90d"]),
            "published_dates": published_dates,
            "enriched_videos": int(row["enriched_videos"]),
            "enriched_view_counts": views,
            "recent_enriched_view_counts": recent_views,
        }

    @staticmethod
    def _channel_score(
        row: sqlite3.Row, score_column: str = "score"
    ) -> ChannelScore:
        return ChannelScore(
            channel_id=row["channel_id"],
            score=float(row[score_column]),
            relevance_score=float(row["relevance_score"]),
            activity_score=float(row["activity_score"]),
            traction_score=float(row["traction_score"]),
            confidence_score=float(row["confidence_score"]),
            tier=row["tier"],
            reasons=json.loads(row["reason_json"]),
            scored_at=datetime.fromisoformat(row["scored_at"]),
            scoring_version=row["scoring_version"],
            cadence_score=(
                float(row["cadence_score"])
                if row["cadence_score"] is not None
                else None
            ),
            videos_per_week_30d=(
                float(row["videos_per_week_30d"])
                if row["videos_per_week_30d"] is not None
                else None
            ),
            videos_per_week_90d=(
                float(row["videos_per_week_90d"])
                if row["videos_per_week_90d"] is not None
                else None
            ),
        )

    def get_channel_crawl_state(
        self, channel_id: str
    ) -> ChannelCrawlState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM channel_crawl_state WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
        return self._crawl_state(row) if row else None

    def ensure_channel_crawl_state(self, channel_id: str) -> ChannelCrawlState:
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO channel_crawl_state (channel_id, updated_at)
                VALUES (?, ?)
                """,
                (channel_id, self._timestamp(now)),
            )
        state = self.get_channel_crawl_state(channel_id)
        if state is None:  # Only possible if the channel foreign key is invalid.
            raise sqlite3.IntegrityError(f"channel {channel_id} does not exist")
        return state

    def mark_crawl_started(
        self, channel_id: str, now: datetime | None = None
    ) -> None:
        started_at = now or datetime.now(timezone.utc)
        self.ensure_channel_crawl_state(channel_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE channel_crawl_state
                SET last_crawl_started_at = ?, updated_at = ?
                WHERE channel_id = ?
                """,
                (self._timestamp(started_at), self._timestamp(started_at), channel_id),
            )

    def mark_crawl_success(
        self,
        channel_id: str,
        last_seen_video_id: str | None,
        last_seen_published_at: datetime | None,
        now: datetime | None = None,
        crawl_interval: timedelta = timedelta(hours=24),
    ) -> None:
        completed_at = now or datetime.now(timezone.utc)
        self.ensure_channel_crawl_state(channel_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE channel_crawl_state SET
                    last_crawl_completed_at = ?, last_success_at = ?,
                    last_error = NULL,
                    last_seen_video_id = COALESCE(?, last_seen_video_id),
                    last_seen_published_at = COALESCE(?, last_seen_published_at),
                    consecutive_failures = 0,
                    total_crawls = total_crawls + 1, next_crawl_at = ?, updated_at = ?
                WHERE channel_id = ?
                """,
                (
                    self._timestamp(completed_at),
                    self._timestamp(completed_at),
                    last_seen_video_id,
                    self._timestamp(last_seen_published_at),
                    self._timestamp(completed_at + crawl_interval),
                    self._timestamp(completed_at),
                    channel_id,
                ),
            )

    def mark_crawl_failure(
        self,
        channel_id: str,
        error: str,
        now: datetime | None = None,
        crawl_interval: timedelta = timedelta(hours=24),
    ) -> None:
        failed_at = now or datetime.now(timezone.utc)
        self.ensure_channel_crawl_state(channel_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE channel_crawl_state SET
                    last_error_at = ?, last_error = ?,
                    consecutive_failures = consecutive_failures + 1,
                    total_crawls = total_crawls + 1, next_crawl_at = ?, updated_at = ?
                WHERE channel_id = ?
                """,
                (
                    self._timestamp(failed_at),
                    error,
                    self._timestamp(failed_at + crawl_interval),
                    self._timestamp(failed_at),
                    channel_id,
                ),
            )

    def list_channels_due_for_crawl(
        self, limit: int | None = None, now: datetime | None = None
    ) -> list[Channel]:
        sql = """
            SELECT c.* FROM channels AS c
            JOIN channel_crawl_state AS s ON s.channel_id = c.channel_id
            WHERE s.next_crawl_at IS NOT NULL AND s.next_crawl_at <= ?
            ORDER BY s.next_crawl_at, c.channel_id
        """
        parameters: list[object] = [
            self._timestamp(now or datetime.now(timezone.utc))
        ]
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._channel(row) for row in rows]

    def crawl_state_counts(
        self, now: datetime | None = None
    ) -> dict[str, int]:
        current = self._timestamp(now or datetime.now(timezone.utc))
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM channels) - COUNT(*) AS never_crawled,
                    SUM(CASE WHEN next_crawl_at <= ? THEN 1 ELSE 0 END) AS due,
                    SUM(CASE WHEN consecutive_failures = 0 THEN 1 ELSE 0 END) AS healthy,
                    SUM(CASE WHEN consecutive_failures > 0 THEN 1 ELSE 0 END) AS failing
                FROM channel_crawl_state
                """,
                (current,),
            ).fetchone()
        return {
            "never_crawled": int(row[0] or 0),
            "due": int(row[1] or 0),
            "healthy": int(row[2] or 0),
            "failing": int(row[3] or 0),
        }

    @staticmethod
    def _crawl_state(row: sqlite3.Row) -> ChannelCrawlState:
        def timestamp(name: str) -> datetime | None:
            return datetime.fromisoformat(row[name]) if row[name] else None

        return ChannelCrawlState(
            channel_id=row["channel_id"],
            last_crawl_started_at=timestamp("last_crawl_started_at"),
            last_crawl_completed_at=timestamp("last_crawl_completed_at"),
            last_success_at=timestamp("last_success_at"),
            last_error_at=timestamp("last_error_at"),
            last_error=row["last_error"],
            last_seen_video_id=row["last_seen_video_id"],
            last_seen_published_at=timestamp("last_seen_published_at"),
            consecutive_failures=row["consecutive_failures"],
            total_crawls=row["total_crawls"],
            next_crawl_at=timestamp("next_crawl_at"),
            updated_at=timestamp("updated_at"),
        )

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

    def count_video_score_tiers(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT COALESCE(tier, 'unscored'), COUNT(*) FROM video_scores GROUP BY tier"
            ).fetchall()
            total = connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        counts = {"high": 0, "medium": 0, "low": 0, "unscored": int(total)}
        for tier, count in rows:
            tier_name = str(tier)
            counts[tier_name] = int(count)
            if tier_name != "unscored":
                counts["unscored"] -= int(count)
        return counts

    def list_videos_page(
        self, limit: int, offset: int = 0, channel_id: str | None = None,
        tier: str | None = None, metadata_pending: bool = False,
        transcript_pending: bool = False,
    ) -> list[dict[str, object]]:
        clauses = ["1=1"]
        parameters: list[object] = []
        if channel_id:
            clauses.append("v.channel_id = ?")
            parameters.append(channel_id)
        if tier:
            clauses.append("COALESCE(s.tier, 'unscored') = ?")
            parameters.append(tier)
        if metadata_pending:
            clauses.append("v.metadata_enriched_at IS NULL")
        if transcript_pending:
            clauses.append("NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.video_id)")
        parameters.extend((limit, offset))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT v.*, c.title AS channel_title, s.score, s.tier,
                       s.recency_score, s.transcript_value_score,
                       EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.video_id) AS transcript_present
                FROM videos v JOIN channels c ON c.channel_id = v.channel_id
                LEFT JOIN video_scores s ON s.video_id = v.video_id
                WHERE {' AND '.join(clauses)}
                ORDER BY s.score IS NULL, s.score DESC, v.published_at DESC, v.video_id
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def count_videos_page(
        self, channel_id: str | None = None, tier: str | None = None,
        metadata_pending: bool = False, transcript_pending: bool = False,
    ) -> int:
        clauses = ["1=1"]
        parameters: list[object] = []
        if channel_id:
            clauses.append("v.channel_id = ?")
            parameters.append(channel_id)
        if tier:
            clauses.append("COALESCE(s.tier, 'unscored') = ?")
            parameters.append(tier)
        if metadata_pending:
            clauses.append("v.metadata_enriched_at IS NULL")
        if transcript_pending:
            clauses.append("NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.video_id)")
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM videos v LEFT JOIN video_scores s ON s.video_id = v.video_id WHERE {' AND '.join(clauses)}",
                parameters,
            ).fetchone()
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
    def record_transcript_attempt(self, attempt: TranscriptAttempt) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO transcript_attempts (
                    video_id, provider, requested_language, status,
                    error_type, error_message, attempted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.video_id,
                    attempt.provider,
                    attempt.requested_language,
                    attempt.status,
                    attempt.error_type,
                    attempt.error_message,
                    self._timestamp(attempt.attempted_at),
                ),
            )

    def list_transcript_attempts(self, video_id: str) -> list[TranscriptAttempt]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT video_id, provider, requested_language, status,
                       error_type, error_message, attempted_at
                FROM transcript_attempts WHERE video_id = ?
                ORDER BY id
                """,
                (video_id,),
            ).fetchall()
        return [
            TranscriptAttempt(
                video_id=row["video_id"],
                provider=row["provider"],
                requested_language=row["requested_language"],
                status=row["status"],
                error_type=row["error_type"],
                error_message=row["error_message"],
                attempted_at=datetime.fromisoformat(row["attempted_at"]),
            )
            for row in rows
        ]

    def transcript_source_counts(self) -> list[tuple[str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source, COUNT(*) FROM transcripts
                GROUP BY source ORDER BY COUNT(*) DESC, source
                """
            ).fetchall()
        return [(str(row[0]), int(row[1])) for row in rows]

    def transcript_attempt_status_counts(self) -> list[tuple[str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) FROM transcript_attempts
                GROUP BY status ORDER BY status
                """
            ).fetchall()
        return [(str(row[0]), int(row[1])) for row in rows]

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
