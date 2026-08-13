"""Command-line interface for discovery and video enumeration."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import platform
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from .collectors.channel_collector import (
    ChannelCrawlService,
    ChannelVideoProvider,
    CrawlReport,
    UnknownChannelError,
)
from .collectors.video_metadata import (
    EnrichmentBatchReport,
    VideoMetadataProvider,
    VideoMetadataService,
)
from .collectors.ytdlp_channel_video import YtDlpChannelVideoProvider
from .collectors.ytdlp_video_metadata import YtDlpVideoMetadataProvider
from .config import Config
from .database.repository import ChannelRepository, TranscriptRepository, VideoRepository
from .discovery.channel_discovery import ChannelDiscoveryProvider, DiscoveryService
from .discovery.channel_scoring import ChannelScoringService, CrawlPriorityPolicy
from .discovery.expansion import DiscoveryExpansionService
from .discovery.ytdlp_provider import YtDlpDiscoveryProvider
from .transcripts.opencli_provider import OpenCliTranscriptProvider
from .transcripts.provider import (
    TranscriptBatchReport,
    TranscriptPipeline,
    TranscriptProvider,
    TranscriptService,
)
from .transcripts.whisper_provider import LocalWhisperTranscriptProvider
from .transcripts.ytdlp_provider import YtDlpTranscriptProvider


def _version(command: str, *args: str) -> str | None:
    executable = shutil.which(command)
    if executable is None:
        return None
    result = subprocess.run(
        [executable, *args], capture_output=True, text=True, check=False
    )
    output = result.stdout.strip() or result.stderr.strip()
    return output.splitlines()[0] if output else None


def _database_path() -> Path:
    url = Config.from_env().database_url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("Only sqlite:/// DATABASE_URL values are supported")
    return Path(url.removeprefix(prefix))


def _repository() -> ChannelRepository:
    return ChannelRepository(_database_path())


def _video_repository(
    repository: ChannelRepository | None = None,
) -> VideoRepository:
    if isinstance(repository, VideoRepository):
        return repository
    return VideoRepository(repository.database_path if repository else _database_path())


def _transcript_repository(
    repository: ChannelRepository | None = None,
) -> TranscriptRepository:
    if isinstance(repository, TranscriptRepository):
        return repository
    return TranscriptRepository(
        repository.database_path if repository else _database_path()
    )


def doctor(
    _: argparse.Namespace,
    __: ChannelDiscoveryProvider | None = None,
    ___: ChannelVideoProvider | None = None,
    ____: VideoMetadataProvider | None = None,
    _____: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    try:
        yt_dlp_version = importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        yt_dlp_version = None
    try:
        database = repository or _repository()
        database_status = f"writable ({database.database_path})"
    except (OSError, sqlite3.Error, ValueError) as error:
        database_status = None
        database_error = str(error)
    else:
        database_error = ""
    required_checks = (
        ("Python", platform.python_version()),
        ("FFmpeg", _version("ffmpeg", "-version")),
        ("FFprobe", _version("ffprobe", "-version")),
        ("yt-dlp captions", yt_dlp_version),
        ("SQLite", sqlite3.sqlite_version),
        ("Database", database_status),
    )
    optional_checks = (
        ("OpenCLI", _version("opencli", "--version")),
        (
            "Local ASR (faster-whisper)",
            "installed" if importlib.util.find_spec("faster_whisper") else None,
        ),
        ("NVIDIA GPU", _version("nvidia-smi", "--query-gpu=name", "--format=csv,noheader")),
    )
    print("Kiem tra moi truong crawl-yt:")
    for name, value in required_checks:
        status = "PASS" if value else "FAIL"
        detail = value or database_error or "khong tim thay"
        print(f"[{status}] {name}: {detail}")
    for name, value in optional_checks:
        status = "PASS" if value else "OPTIONAL"
        print(f"[{status}] {name}: {value or 'khong cai dat'}")
    return 0 if all(value for _, value in required_checks) else 1


def discover(
    args: argparse.Namespace,
    provider: ChannelDiscoveryProvider | None = None,
    _: ChannelVideoProvider | None = None,
    __: VideoMetadataProvider | None = None,
    ___: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    service = DiscoveryService(
        provider or YtDlpDiscoveryProvider(), repository or _repository()
    )
    try:
        report = service.discover(args.keyword, args.limit, args.dry_run)
    except Exception as error:  # yt-dlp errors vary by extractor and network state
        print(f"Discovery failed: {error}", file=sys.stderr)
        return 1
    print(f"Search results: {report.search_results}")
    print(f"Unique channels in search: {report.unique_channels_in_search}")
    print(f"Duplicate results in search: {report.duplicate_results_in_search}")
    print(f"New channels: {report.new_channels}")
    print(f"Existing channels: {report.existing_channels}")
    print(f"New discovery relationships: {report.new_discovery_relationships}")
    print(
        "Existing discovery relationships: "
        f"{report.existing_discovery_relationships}"
    )
    if args.dry_run:
        print("Dry run: database was not changed")
    return 0


def _print_expansion(report, channel_budget: int, query_budget: int) -> None:
    print(f"Seed: {report.seed_keyword}")
    print(f"Status: {report.status}")
    if report.run_id is not None:
        print(f"Run ID: {report.run_id}")
    print(f"Queries executed: {report.queries_executed}/{query_budget}")
    print(f"New channels: {report.new_channels}/{channel_budget}")
    print(f"Existing channels rediscovered: {report.existing_channels}")
    print(f"Max depth reached: {report.max_depth_reached}")
    print(f"Generated queries: {len(report.generated_queries)}")
    for query in report.generated_queries[:10]:
        print(f"  {query}")
    if report.top_channels:
        print("Top discovered channels:")
        for title, score, tier in report.top_channels:
            print(f"  {score:5.1f} {tier:<7} {title}")
    if report.failures:
        print(f"Failed queries: {len(report.failures)}")


def expand(
    args: argparse.Namespace,
    provider: ChannelDiscoveryProvider | None = None,
    _: ChannelVideoProvider | None = None,
    __: VideoMetadataProvider | None = None,
    ___: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    service = DiscoveryExpansionService(
        provider or YtDlpDiscoveryProvider(), _video_repository(repository)
    )
    report = service.expand(
        args.seed_keyword,
        max_depth=args.max_depth,
        channel_budget=args.channel_budget,
        query_budget=args.query_budget,
        results_per_query=args.results_per_query,
        dry_run=args.dry_run,
    )
    _print_expansion(report, args.channel_budget, args.query_budget)
    if args.dry_run:
        print("Dry run: no provider calls and no database writes")
    return 1 if report.status == "failed" else 0


def discovery_runs(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    ___: VideoMetadataProvider | None = None,
    ____: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    rows = _video_repository(repository).list_discovery_runs(args.limit)
    print("ID  Status          Queries  New channels  Seed")
    for run in rows:
        print(
            f"{run.id:<3} {run.status:<15} {run.queries_executed:>7} "
            f"{run.channels_discovered:>13}  {run.seed_keyword}"
        )
    return 0


def discovery_run(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    ___: VideoMetadataProvider | None = None,
    ____: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    database = _video_repository(repository)
    run = database.get_discovery_run(args.run_id)
    if run is None:
        print(f"Discovery run {args.run_id} not found", file=sys.stderr)
        return 1
    print(f"Run ID: {run.id}")
    print(f"Seed: {run.seed_keyword}")
    print(f"Status: {run.status}")
    print(f"Queries executed: {run.queries_executed}/{run.query_budget}")
    print(f"New channels: {run.channels_discovered}/{run.channel_budget}")
    print(f"Max depth: {run.max_depth}")
    print("Queries:")
    for query in database.list_discovery_queries(run.id):
        print(
            f"  d{query.depth} {query.status:<9} {query.query} "
            f"(found={query.channels_found}, new={query.new_channels}, source={query.source})"
        )
    return 0


def stats(
    _: argparse.Namespace,
    __: ChannelDiscoveryProvider | None = None,
    ___: ChannelVideoProvider | None = None,
    ____: VideoMetadataProvider | None = None,
    _____: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    database = _transcript_repository(repository)
    print(f"Database: {database.database_path}")
    print()
    print(f"Channels: {database.count_channels()}")
    print(f"Videos: {database.count_videos()}")
    crawl_counts = database.crawl_state_counts()
    print("Crawl state:")
    print(f"  never crawled: {crawl_counts['never_crawled']}")
    print(f"  due: {crawl_counts['due']}")
    print(f"  healthy: {crawl_counts['healthy']}")
    print(f"  failing: {crawl_counts['failing']}")
    score_counts = database.count_channels_by_score_tier()
    print("Channel scores:")
    for tier in ("high", "medium", "low", "unscored"):
        print(f"  {tier}: {score_counts[tier]}")
    print(f"Metadata enriched: {database.count_enriched_videos()}")
    print(f"Metadata pending: {database.count_videos_needing_enrichment()}")
    print(f"Transcripts: {database.count_transcripts()}")
    print(f"Videos with transcript: {database.count_videos_with_transcripts()}")
    print(
        f"Videos without transcript: {database.count_videos_without_transcripts()}"
    )
    source_counts = database.transcript_source_counts()
    if source_counts:
        print("Transcript sources:")
        for source, count in source_counts:
            print(f"  {source}: {count}")
    attempt_counts = database.transcript_attempt_status_counts()
    if attempt_counts:
        print("Transcript attempts:")
        for status, count in attempt_counts:
            print(f"  {status}: {count}")
    print(f"Discovery relationships: {database.count_discovery_relationships()}")
    keyword_counts = database.discovery_keyword_counts()
    if keyword_counts:
        print()
        print("Discovery keywords:")
        for keyword, count in keyword_counts:
            print(f"  {keyword}: {count}")
    return 0


def _channel_id(value: str) -> str:
    if value.startswith("UC"):
        return value
    marker = "/channel/"
    if marker in value:
        return value.split(marker, 1)[1].split("/", 1)[0]
    return value


def _print_crawl(report: CrawlReport) -> None:
    print(f"Channel: {report.channel_title} ({report.channel_id})")
    print(f"Mode: {report.mode}")
    print(f"Enumerated entries: {report.enumerated_entries}")
    print(f"Unique videos: {report.unique_videos}")
    print(f"New videos: {report.new_videos}")
    print(f"Existing videos: {report.existing_videos}")
    print(f"Skipped: {report.skipped_entries}")
    print(f"Stopped early: {'yes' if report.stopped_early else 'no'}")
    if report.stop_reason:
        print(f"Stop reason: {report.stop_reason}")
    print(f"Consecutive known at stop: {report.consecutive_known_at_stop}")
    print(f"Elapsed time: {report.elapsed_seconds:.2f}s")


def crawl(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    provider: ChannelVideoProvider | None = None,
    __: VideoMetadataProvider | None = None,
    ___: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    service = ChannelCrawlService(
        provider or YtDlpChannelVideoProvider(), _video_repository(repository)
    )
    try:
        report = service.crawl(
            _channel_id(args.channel),
            args.limit,
            full=args.full,
            known_stop_threshold=args.known_stop_threshold,
        )
    except UnknownChannelError as error:
        print(f"Crawl failed: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # yt-dlp errors vary by extractor and network state
        print(f"Crawl failed: {error}", file=sys.stderr)
        return 1
    _print_crawl(report)
    return 0


def crawl_all(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    provider: ChannelVideoProvider | None = None,
    __: VideoMetadataProvider | None = None,
    ___: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    service = ChannelCrawlService(
        provider or YtDlpChannelVideoProvider(), _video_repository(repository)
    )
    report = service.crawl_all(args.max_channels, args.limit_per_channel)
    print(f"Channels attempted: {report.channels_attempted}")
    print(f"Channels succeeded: {report.channels_succeeded}")
    print(f"Enumerated entries: {report.enumerated_entries}")
    print(f"Unique videos: {report.unique_videos}")
    print(f"New videos: {report.new_videos}")
    print(f"Existing videos: {report.existing_videos}")
    print(f"Skipped: {report.skipped_entries}")
    print(f"Failures: {len(report.failures)}")
    for channel_id, error in report.failures:
        print(f"  {channel_id}: {error}")
    return 1 if report.failures else 0


def crawl_due(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    provider: ChannelVideoProvider | None = None,
    __: VideoMetadataProvider | None = None,
    ___: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    service = ChannelCrawlService(
        provider or YtDlpChannelVideoProvider(), _video_repository(repository)
    )
    report = service.crawl_due(args.limit)
    print(f"Channels attempted: {report.channels_attempted}")
    print(f"Channels succeeded: {report.channels_succeeded}")
    print(f"Failures: {len(report.failures)}")
    for channel_id, error in report.failures:
        print(f"  {channel_id}: {error}")
    return 1 if report.failures else 0


def _print_score(channel_title: str, score) -> None:
    print(f"Channel: {channel_title} ({score.channel_id})")
    print(f"Score: {score.score:.2f}")
    print(f"Tier: {score.tier}")
    print(
        "Components: "
        f"relevance={score.relevance_score:.2f}, "
        f"activity={score.activity_score:.2f}, "
        f"traction={score.traction_score:.2f}, "
        f"confidence={score.confidence_score:.2f}"
    )
    print(
        f"Crawl interval: {CrawlPriorityPolicy().interval_for(score.tier).total_seconds() / 3600:.0f}h"
    )
    notes = score.reasons.get("notes", [])
    if notes:
        print(f"Reasons: {'; '.join(notes)}")


def score_channel(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    ___: VideoMetadataProvider | None = None,
    ____: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    database = _video_repository(repository)
    channel_id = _channel_id(args.channel)
    try:
        score = ChannelScoringService(database).score_channel(channel_id)
    except ValueError as error:
        print(f"Scoring failed: {error}", file=sys.stderr)
        return 1
    _print_score(database.get_channel(channel_id).title, score)
    return 0


def score_all(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    ___: VideoMetadataProvider | None = None,
    ____: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    scores = ChannelScoringService(_video_repository(repository)).score_all(args.limit)
    print(f"Channels scored: {len(scores)}")
    return 0


def top_channels(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    ___: VideoMetadataProvider | None = None,
    ____: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    rows = _video_repository(repository).list_top_channels(args.limit)
    print("Score  Tier    Channel")
    for channel, score in rows:
        print(f"{score.score:5.1f}  {score.tier:<7} {channel.title}")
    return 0


def _metadata_service(
    provider: VideoMetadataProvider | None,
    repository: ChannelRepository | None,
) -> VideoMetadataService:
    return VideoMetadataService(
        provider or YtDlpVideoMetadataProvider(), _video_repository(repository)
    )


def _print_enrichment_batch(report: EnrichmentBatchReport) -> None:
    print(f"Attempted: {report.attempted}")
    print(f"Succeeded: {report.succeeded}")
    print(f"Failed: {report.failed}")
    for result in report.results:
        if not result.success:
            print(f"  {result.video_id}: {result.error}")


def enrich(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    provider: VideoMetadataProvider | None = None,
    ___: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    result = _metadata_service(provider, repository).enrich(args.video_id)
    print(f"Video: {result.video_id}")
    print(f"Enriched: {'yes' if result.success else 'no'}")
    if result.error:
        print(f"Error: {result.error}")
    return 0 if result.success else 1


def enrich_channel(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    provider: VideoMetadataProvider | None = None,
    ___: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    try:
        report = _metadata_service(provider, repository).enrich_channel(
            _channel_id(args.channel), args.limit
        )
    except ValueError as error:
        print(f"Enrichment failed: {error}", file=sys.stderr)
        return 1
    _print_enrichment_batch(report)
    return 1 if report.failed else 0


def enrich_pending(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    provider: VideoMetadataProvider | None = None,
    ___: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    report = _metadata_service(provider, repository).enrich_pending(args.limit)
    _print_enrichment_batch(report)
    return 1 if report.failed else 0


def _transcript_service(
    provider: TranscriptProvider | None,
    repository: ChannelRepository | None,
) -> TranscriptService:
    transcript_repository = _transcript_repository(repository)
    if provider is not None:
        return TranscriptService(provider, transcript_repository)
    pipeline = TranscriptPipeline(
        YtDlpTranscriptProvider(),
        transcript_repository,
        OpenCliTranscriptProvider(),
        LocalWhisperTranscriptProvider(),
    )
    return TranscriptService(pipeline, transcript_repository)


def _print_transcript_batch(report: TranscriptBatchReport) -> None:
    print(f"Attempted: {report.attempted}")
    print(f"Succeeded: {report.succeeded}")
    print(f"Failed: {report.failed}")
    for result in report.results:
        if not result.success:
            print(f"  {result.video_id}: {result.error}")


def transcript(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    ___: VideoMetadataProvider | None = None,
    provider: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    result = _transcript_service(provider, repository).transcript(
        args.video_id,
        args.lang,
        args.force,
        fallback=args.fallback,
        allow_audio=args.allow_audio,
    )
    print(f"Video: {result.video_id}")
    if not result.success:
        print(f"Transcript: failed")
        print(f"Error: {result.error}")
        return 1
    item = result.transcript
    print(f"Transcript: {'cached' if result.cached else 'stored'}")
    print(f"Language: {item.language}")
    print(f"Source: {item.source}")
    print(f"Segments: {len(item.segments)}")
    print(f"Text length: {len(item.text)}")
    print(f"Provider attempts: {result.attempts}")
    return 0


def transcript_channel(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    ___: VideoMetadataProvider | None = None,
    provider: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    try:
        report = _transcript_service(provider, repository).transcript_channel(
            _channel_id(args.channel),
            args.limit,
            args.lang,
            fallback=args.fallback,
            allow_audio=args.allow_audio,
        )
    except ValueError as error:
        print(f"Transcript failed: {error}", file=sys.stderr)
        return 1
    _print_transcript_batch(report)
    return 1 if report.failed else 0


def transcript_pending(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    __: ChannelVideoProvider | None = None,
    ___: VideoMetadataProvider | None = None,
    provider: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    report = _transcript_service(provider, repository).transcript_pending(
        args.limit,
        args.lang,
        fallback=args.fallback,
        allow_audio=args.allow_audio,
    )
    _print_transcript_batch(report)
    return 1 if report.failed else 0


def placeholder(message: str):
    def run(
        _: argparse.Namespace,
        __: ChannelDiscoveryProvider | None = None,
        ___: ChannelVideoProvider | None = None,
        ____: VideoMetadataProvider | None = None,
        _____: TranscriptProvider | None = None,
        ______: ChannelRepository | None = None,
    ) -> int:
        print(f"Chua trien khai: {message}")
        return 0

    return run


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _add_fallback_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fallback", action="store_true", help="Cho phep fallback khong audio"
    )
    parser.add_argument(
        "--allow-audio",
        action="store_true",
        help="Cho phep tai audio va ASR cuc bo (can --fallback)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crawl-yt", description="YouTube Intelligence Engine - Phase 1E"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Kiem tra moi truong cuc bo")
    doctor_parser.set_defaults(handler=doctor)

    discover_parser = subparsers.add_parser("discover", help="Tim kenh theo chu de")
    discover_parser.add_argument("keyword")
    discover_parser.add_argument("--limit", type=positive_int, default=50)
    discover_parser.add_argument("--dry-run", action="store_true")
    discover_parser.set_defaults(handler=discover)

    expand_parser = subparsers.add_parser(
        "expand", help="Mo rong discovery co gioi han"
    )
    expand_parser.add_argument("seed_keyword")
    expand_parser.add_argument("--max-depth", type=positive_int, required=True)
    expand_parser.add_argument("--channel-budget", type=positive_int, required=True)
    expand_parser.add_argument("--query-budget", type=positive_int, required=True)
    expand_parser.add_argument("--results-per-query", type=positive_int, default=20)
    expand_parser.add_argument("--dry-run", action="store_true")
    expand_parser.set_defaults(handler=expand)

    runs_parser = subparsers.add_parser(
        "discovery-runs", help="Liet ke cac lan mo rong discovery"
    )
    runs_parser.add_argument("--limit", type=positive_int, required=True)
    runs_parser.set_defaults(handler=discovery_runs)

    run_parser = subparsers.add_parser(
        "discovery-run", help="Chi tiet mot lan mo rong discovery"
    )
    run_parser.add_argument("run_id", type=positive_int)
    run_parser.set_defaults(handler=discovery_run)

    add_parser = subparsers.add_parser("add-channel", help="Them mot kenh")
    add_parser.add_argument("youtube_channel_url")
    add_parser.set_defaults(handler=placeholder("add-channel"))

    crawl_parser = subparsers.add_parser("crawl", help="Thu thap mot kenh")
    crawl_parser.add_argument("channel")
    crawl_parser.add_argument("--limit", type=positive_int)
    crawl_parser.add_argument("--full", action="store_true")
    crawl_parser.add_argument(
        "--known-stop-threshold", type=positive_int, default=5
    )
    crawl_parser.set_defaults(handler=crawl)

    crawl_due_parser = subparsers.add_parser(
        "crawl-due", help="Thu thap cac kenh da den lich"
    )
    crawl_due_parser.add_argument("--limit", type=positive_int, required=True)
    crawl_due_parser.set_defaults(handler=crawl_due)

    crawl_all_parser = subparsers.add_parser("crawl-all", help="Thu thap moi kenh")
    crawl_all_parser.add_argument("--max-channels", type=positive_int)
    crawl_all_parser.add_argument("--limit-per-channel", type=positive_int)
    crawl_all_parser.set_defaults(handler=crawl_all)

    score_channel_parser = subparsers.add_parser(
        "score-channel", help="Tinh diem mot kenh"
    )
    score_channel_parser.add_argument("channel")
    score_channel_parser.set_defaults(handler=score_channel)

    score_all_parser = subparsers.add_parser("score-all", help="Tinh diem cac kenh")
    score_all_parser.add_argument("--limit", type=positive_int, required=True)
    score_all_parser.set_defaults(handler=score_all)

    top_channels_parser = subparsers.add_parser(
        "top-channels", help="Hien thi kenh co diem cao"
    )
    top_channels_parser.add_argument("--limit", type=positive_int, required=True)
    top_channels_parser.set_defaults(handler=top_channels)

    enrich_parser = subparsers.add_parser("enrich", help="Bo sung metadata video")
    enrich_parser.add_argument("video_id")
    enrich_parser.set_defaults(handler=enrich)

    enrich_channel_parser = subparsers.add_parser(
        "enrich-channel", help="Bo sung metadata video cua mot kenh"
    )
    enrich_channel_parser.add_argument("channel")
    enrich_channel_parser.add_argument("--limit", type=positive_int, required=True)
    enrich_channel_parser.set_defaults(handler=enrich_channel)

    enrich_pending_parser = subparsers.add_parser(
        "enrich-pending", help="Bo sung metadata video dang cho"
    )
    enrich_pending_parser.add_argument("--limit", type=positive_int, required=True)
    enrich_pending_parser.set_defaults(handler=enrich_pending)

    transcript_parser = subparsers.add_parser(
        "transcript", help="Lay subtitle cua mot video"
    )
    transcript_parser.add_argument("video_id")
    transcript_parser.add_argument("--lang")
    transcript_parser.add_argument("--force", action="store_true")
    _add_fallback_flags(transcript_parser)
    transcript_parser.set_defaults(handler=transcript)

    transcript_channel_parser = subparsers.add_parser(
        "transcript-channel", help="Lay subtitle video cua mot kenh"
    )
    transcript_channel_parser.add_argument("channel")
    transcript_channel_parser.add_argument(
        "--limit", type=positive_int, required=True
    )
    transcript_channel_parser.add_argument("--lang")
    _add_fallback_flags(transcript_channel_parser)
    transcript_channel_parser.set_defaults(handler=transcript_channel)

    transcript_pending_parser = subparsers.add_parser(
        "transcript-pending", help="Lay subtitle video dang cho"
    )
    transcript_pending_parser.add_argument(
        "--limit", type=positive_int, required=True
    )
    transcript_pending_parser.add_argument("--lang")
    _add_fallback_flags(transcript_pending_parser)
    transcript_pending_parser.set_defaults(handler=transcript_pending)

    stats_parser = subparsers.add_parser("stats", help="Hien thi thong ke")
    stats_parser.set_defaults(handler=stats)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    discovery_provider: ChannelDiscoveryProvider | None = None,
    video_provider: ChannelVideoProvider | None = None,
    metadata_provider: VideoMetadataProvider | None = None,
    transcript_provider: TranscriptProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "allow_audio", False) and not getattr(args, "fallback", False):
        print("--allow-audio requires --fallback", file=sys.stderr)
        return 2
    return args.handler(
        args,
        discovery_provider,
        video_provider,
        metadata_provider,
        transcript_provider,
        repository,
    )


if __name__ == "__main__":
    sys.exit(main())
