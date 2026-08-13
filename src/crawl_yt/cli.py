"""Command-line interface for channel discovery Phase 1A."""

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

from .config import Config
from .database.repository import ChannelRepository
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
        raise ValueError("Phase 1A only supports sqlite:/// DATABASE_URL values")
    return Path(url.removeprefix(prefix))


def _repository() -> ChannelRepository:
    return ChannelRepository(_database_path())


def doctor(
    _: argparse.Namespace,
    __: ChannelDiscoveryProvider | None = None,
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
    repository: ChannelRepository | None = None,
) -> int:
    database = repository or _repository()
    print(f"Database: {database.database_path}")
    print()
    print(f"Channels: {database.count_channels()}")
    print("Videos: 0")
    print("Transcripts: 0")
    print(f"Discovery relationships: {database.count_discovery_relationships()}")
    keyword_counts = database.discovery_keyword_counts()
    if keyword_counts:
        print()
        print("Discovery keywords:")
        for keyword, count in keyword_counts:
            print(f"  {keyword}: {count}")
    return 0


def placeholder(message: str):
    def run(
        _: argparse.Namespace,
        __: ChannelDiscoveryProvider | None = None,
        ___: ChannelRepository | None = None,
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
        prog="crawl-yt", description="YouTube Intelligence Engine - Phase 1A"
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
    crawl_parser.set_defaults(handler=placeholder("crawl"))

    crawl_all_parser = subparsers.add_parser("crawl-all", help="Thu thap moi kenh")
    crawl_all_parser.set_defaults(handler=placeholder("crawl-all"))

    stats_parser = subparsers.add_parser("stats", help="Hien thi thong ke")
    stats_parser.set_defaults(handler=stats)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    discovery_provider: ChannelDiscoveryProvider | None = None,
    repository: ChannelRepository | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, discovery_provider, repository)


if __name__ == "__main__":
    sys.exit(main())
