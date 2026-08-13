"""Command-line interface for discovery and video enumeration."""

from __future__ import annotations

import argparse
import importlib.metadata
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
from .database.repository import ChannelRepository, VideoRepository
from .discovery.channel_discovery import ChannelDiscoveryProvider, DiscoveryService
from .discovery.ytdlp_provider import YtDlpDiscoveryProvider


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


def doctor(
    _: argparse.Namespace,
    __: ChannelDiscoveryProvider | None = None,
    ___: ChannelVideoProvider | None = None,
    ____: VideoMetadataProvider | None = None,
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
    checks = (
        ("Python", platform.python_version()),
        ("FFmpeg", _version("ffmpeg", "-version")),
        ("FFprobe", _version("ffprobe", "-version")),
        ("yt-dlp", yt_dlp_version),
        ("SQLite", sqlite3.sqlite_version),
        ("Database", database_status),
    )
    print("Kiem tra moi truong crawl-yt:")
    for name, value in checks:
        status = "PASS" if value else "FAIL"
        detail = value or database_error or "khong tim thay"
        print(f"[{status}] {name}: {detail}")
    return 0 if all(value for _, value in checks) else 1


def discover(
    args: argparse.Namespace,
    provider: ChannelDiscoveryProvider | None = None,
    _: ChannelVideoProvider | None = None,
    __: VideoMetadataProvider | None = None,
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


def stats(
    _: argparse.Namespace,
    __: ChannelDiscoveryProvider | None = None,
    ___: ChannelVideoProvider | None = None,
    ____: VideoMetadataProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    database = _video_repository(repository)
    print(f"Database: {database.database_path}")
    print()
    print(f"Channels: {database.count_channels()}")
    print(f"Videos: {database.count_videos()}")
    print(f"Metadata enriched: {database.count_enriched_videos()}")
    print(f"Metadata pending: {database.count_videos_needing_enrichment()}")
    print("Transcripts: 0")
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
    print(f"Enumerated entries: {report.enumerated_entries}")
    print(f"Unique videos: {report.unique_videos}")
    print(f"New videos: {report.new_videos}")
    print(f"Existing videos: {report.existing_videos}")
    print(f"Skipped: {report.skipped_entries}")


def crawl(
    args: argparse.Namespace,
    _: ChannelDiscoveryProvider | None = None,
    provider: ChannelVideoProvider | None = None,
    __: VideoMetadataProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    service = ChannelCrawlService(
        provider or YtDlpChannelVideoProvider(), _video_repository(repository)
    )
    try:
        report = service.crawl(_channel_id(args.channel), args.limit)
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
    repository: ChannelRepository | None = None,
) -> int:
    report = _metadata_service(provider, repository).enrich_pending(args.limit)
    _print_enrichment_batch(report)
    return 1 if report.failed else 0


def placeholder(message: str):
    def run(
        _: argparse.Namespace,
        __: ChannelDiscoveryProvider | None = None,
        ___: ChannelVideoProvider | None = None,
        ____: VideoMetadataProvider | None = None,
        _____: ChannelRepository | None = None,
    ) -> int:
        print(f"Chua trien khai: {message}")
        return 0

    return run


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crawl-yt", description="YouTube Intelligence Engine - Phase 1C"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Kiem tra moi truong cuc bo")
    doctor_parser.set_defaults(handler=doctor)

    discover_parser = subparsers.add_parser("discover", help="Tim kenh theo chu de")
    discover_parser.add_argument("keyword")
    discover_parser.add_argument("--limit", type=positive_int, default=50)
    discover_parser.add_argument("--dry-run", action="store_true")
    discover_parser.set_defaults(handler=discover)

    add_parser = subparsers.add_parser("add-channel", help="Them mot kenh")
    add_parser.add_argument("youtube_channel_url")
    add_parser.set_defaults(handler=placeholder("add-channel"))

    crawl_parser = subparsers.add_parser("crawl", help="Thu thap mot kenh")
    crawl_parser.add_argument("channel")
    crawl_parser.add_argument("--limit", type=positive_int)
    crawl_parser.set_defaults(handler=crawl)

    crawl_all_parser = subparsers.add_parser("crawl-all", help="Thu thap moi kenh")
    crawl_all_parser.add_argument("--max-channels", type=positive_int)
    crawl_all_parser.add_argument("--limit-per-channel", type=positive_int)
    crawl_all_parser.set_defaults(handler=crawl_all)

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

    stats_parser = subparsers.add_parser("stats", help="Hien thi thong ke")
    stats_parser.set_defaults(handler=stats)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    discovery_provider: ChannelDiscoveryProvider | None = None,
    video_provider: ChannelVideoProvider | None = None,
    metadata_provider: VideoMetadataProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(
        args, discovery_provider, video_provider, metadata_provider, repository
    )


if __name__ == "__main__":
    sys.exit(main())
