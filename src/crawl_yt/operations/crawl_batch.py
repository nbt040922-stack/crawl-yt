"""Durable, sequential execution for manual full-crawl batches."""

from __future__ import annotations

from typing import Any

from ..collectors.channel_collector import ChannelCrawlService
from ..collectors.ytdlp_channel_video import YtDlpChannelVideoProvider
from ..collectors.ytdlp_video_metadata import YtDlpVideoMetadataProvider
from ..collectors.ytdlp_channel_metadata import YtDlpChannelMetadataProvider


class CrawlBatchService:
    def __init__(self, repository, video_provider: Any | None = None, metadata_provider: Any | None = None, channel_metadata_provider: Any | None = None) -> None:
        self.repository = repository
        self.video_provider = video_provider or YtDlpChannelVideoProvider()
        self.metadata_provider = metadata_provider or (
            YtDlpVideoMetadataProvider() if video_provider is None else None
        )
        self.channel_metadata_provider = channel_metadata_provider or (
            YtDlpChannelMetadataProvider() if video_provider is None else None
        )

    def create(self, filters: dict[str, object], sort: str, limit: int):
        return self.repository.create_crawl_batch(filters, sort, limit)

    def run_next(self, batch_id: int, chunk_size: int = 5):
        if not 1 <= chunk_size <= 20:
            raise ValueError("chunk size must be between 1 and 20")
        batch = self.repository.get_crawl_batch(batch_id)
        if batch is None:
            raise ValueError("crawl batch not found")
        self.repository.recover_running_crawl_batch_items(batch_id)
        items = self.repository.claim_crawl_batch_items(batch_id, chunk_size)
        for item in items:
            try:
                ChannelCrawlService(self.video_provider, self.repository, cadence_metadata_provider=self.metadata_provider, channel_metadata_provider=self.channel_metadata_provider).crawl(
                    item.channel_id, full=True
                )
            except Exception as error:
                self.repository.mark_crawl_batch_item(item.id, "failed", str(error))
            else:
                self.repository.mark_crawl_batch_item(item.id, "success")
        return self.repository.get_crawl_batch(batch_id)

    def retry_failed(self, batch_id: int):
        if self.repository.get_crawl_batch(batch_id) is None:
            raise ValueError("crawl batch not found")
        self.repository.retry_failed_crawl_batch_items(batch_id)
        return self.repository.get_crawl_batch(batch_id)
