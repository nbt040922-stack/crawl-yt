"""Server-rendered local dashboard over existing crawl-yt services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..collectors.channel_collector import ChannelCrawlService
from ..collectors.video_metadata import VideoMetadataService
from ..collectors.ytdlp_channel_video import YtDlpChannelVideoProvider
from ..collectors.ytdlp_video_metadata import YtDlpVideoMetadataProvider
from ..database.models import OperationalBudget
from ..database.repository import TranscriptRepository, VideoRepository
from ..discovery.channel_discovery import DiscoveryService
from ..discovery.ytdlp_provider import YtDlpDiscoveryProvider
from ..operations.planner import OperationalPlanner, WorkPlanExecutor
from ..operations.video_scoring import VideoScoringService
from ..transcripts.provider import TranscriptService
from ..transcripts.ytdlp_provider import YtDlpTranscriptProvider

ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def _page(request: Request, **values: Any) -> dict[str, Any]:
    return {"request": request, **values}


def create_app(
    repository: VideoRepository | None = None,
    discovery_provider: Any | None = None,
    video_provider: Any | None = None,
    metadata_provider: Any | None = None,
    transcript_provider: Any | None = None,
) -> FastAPI:
    database = repository or TranscriptRepository()
    app = FastAPI(title="crawl-yt dashboard")
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    @app.exception_handler(Exception)
    async def friendly_error(request: Request, exc: Exception) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="result.html",
            context={"title": "Operation failed", "result": {"error": str(exc)}},
            status_code=502,
        )

    def render(request: Request, template: str, **values: Any) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name=template, context=values)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        stats = {
            "channels": database.count_channels(),
            "videos": database.count_videos(),
            "metadata_enriched": database.count_enriched_videos(),
            "metadata_pending": database.count_videos_needing_enrichment(),
            "transcripts": _count_transcripts(database),
            "with_transcript": _count_videos_with_transcripts(database),
            "without_transcript": database.count_videos() - _count_videos_with_transcripts(database),
            "discovery_relationships": database.count_discovery_relationships(),
            "channel_tiers": database.count_channels_by_score_tier(),
            "video_tiers": _video_tiers(database),
            "work_plans": database.work_plan_status_counts(),
            "crawl_due": len(database.list_channels_due_for_crawl(100000)),
            "failing": _failing_channels(database),
        }
        return render(request, "dashboard.html", title="Dashboard", stats=stats)

    @app.get("/discovery", response_class=HTMLResponse)
    def discovery_page(request: Request) -> HTMLResponse:
        return render(request, "form.html", title="Discovery", form="discovery")

    @app.post("/discovery", response_class=HTMLResponse)
    def discovery_action(request: Request, keyword: str = Form(...), limit: int = Form(...), dry_run: bool = Form(False)) -> HTMLResponse:
        if not keyword.strip() or not 1 <= limit <= 1000:
            raise HTTPException(400, "Keyword and limit 1-1000 are required")
        provider = discovery_provider or YtDlpDiscoveryProvider()
        report = DiscoveryService(provider, database).discover(keyword.strip(), limit, dry_run=dry_run)
        return render(request, "result.html", title="Discovery result", result=report)

    @app.get("/channels", response_class=HTMLResponse)
    def channels(request: Request, page: int = 1, per_page: int = 50, search: str | None = None, tier: str | None = None, keyword: str | None = None) -> HTMLResponse:
        per_page = min(max(per_page, 1), 100)
        page = max(page, 1)
        total = database.count_channels_page(search, tier, keyword)
        rows = database.list_channels_page(per_page, (page - 1) * per_page, search, tier, keyword)
        return render(request, "list.html", title="Channels", page_kind="channels", rows=rows, page=page, per_page=per_page, total=total)

    @app.get("/channels/{channel_id}", response_class=HTMLResponse)
    def channel_detail(request: Request, channel_id: str) -> HTMLResponse:
        channel = database.get_channel(channel_id)
        if channel is None:
            raise HTTPException(404, "Channel not found")
        return render(request, "detail.html", title=channel.title, kind="channel", channel=channel,
                      score=database.get_channel_score(channel_id), state=database.get_channel_crawl_state(channel_id),
                      discoveries=database.list_discoveries_for_channel(channel_id), videos=database.list_videos_for_channel(channel_id, 20))

    @app.post("/channels/{channel_id}/score")
    def channel_score(request: Request, channel_id: str) -> RedirectResponse:
        from ..discovery.channel_scoring import ChannelScoringService
        if database.get_channel(channel_id) is None:
            raise HTTPException(404, "Channel not found")
        ChannelScoringService(database).score_channel(channel_id)
        return RedirectResponse(f"/channels/{channel_id}", status_code=303)

    @app.post("/channels/{channel_id}/crawl")
    def channel_crawl(channel_id: str, full: bool = Form(False)) -> RedirectResponse:
        service = ChannelCrawlService(video_provider or YtDlpChannelVideoProvider(), database)
        service.crawl(channel_id, full=full)
        return RedirectResponse(f"/channels/{channel_id}", status_code=303)

    @app.post("/channels/{channel_id}/crawl-full")
    def channel_crawl_full(channel_id: str, confirm: bool = Form(False)) -> RedirectResponse:
        if not confirm:
            raise HTTPException(400, "Full crawl requires explicit confirmation")
        service = ChannelCrawlService(video_provider or YtDlpChannelVideoProvider(), database)
        service.crawl(channel_id, full=True)
        return RedirectResponse(f"/channels/{channel_id}", status_code=303)

    @app.get("/videos", response_class=HTMLResponse)
    def videos(request: Request, page: int = 1, per_page: int = 50, channel_id: str | None = None, tier: str | None = None, metadata_pending: bool = False, transcript_pending: bool = False) -> HTMLResponse:
        per_page = min(max(per_page, 1), 100)
        page = max(page, 1)
        total = database.count_videos_page(channel_id, tier, metadata_pending, transcript_pending)
        rows = database.list_videos_page(per_page, (page - 1) * per_page, channel_id, tier, metadata_pending, transcript_pending)
        return render(request, "list.html", title="Videos", page_kind="videos", rows=rows, page=page, per_page=per_page, total=total)

    @app.get("/videos/{video_id}", response_class=HTMLResponse)
    def video_detail(request: Request, video_id: str) -> HTMLResponse:
        video = database.get_video(video_id)
        if video is None:
            raise HTTPException(404, "Video not found")
        transcripts = database.list_transcripts_for_video(video_id) if hasattr(database, "list_transcripts_for_video") else []
        return render(request, "detail.html", title=video.title, kind="video", video=video,
                      score=database.get_video_score(video_id), transcripts=transcripts)

    @app.post("/videos/{video_id}/score")
    def video_score(video_id: str) -> RedirectResponse:
        VideoScoringService(database).score_video(video_id)
        return RedirectResponse(f"/videos/{video_id}", status_code=303)

    @app.post("/videos/{video_id}/enrich")
    def video_enrich(video_id: str) -> RedirectResponse:
        VideoMetadataService(metadata_provider or YtDlpVideoMetadataProvider(), database).enrich(video_id)
        return RedirectResponse(f"/videos/{video_id}", status_code=303)

    @app.post("/videos/{video_id}/transcript")
    def video_transcript(video_id: str, language: str | None = Form(None)) -> RedirectResponse:
        TranscriptService(transcript_provider or YtDlpTranscriptProvider(), database).transcript(video_id, language)
        return RedirectResponse(f"/videos/{video_id}", status_code=303)

    @app.get("/work-plans", response_class=HTMLResponse)
    def work_plans(request: Request, page: int = 1, per_page: int = 20) -> HTMLResponse:
        per_page = min(max(per_page, 1), 100)
        page = max(page, 1)
        rows = database.list_work_plans_page(per_page, (page - 1) * per_page)
        return render(request, "list.html", title="Work Plans", page_kind="work-plans", rows=rows, page=page, per_page=per_page, total=database.count_work_plans())

    @app.get("/work-plans/new", response_class=HTMLResponse)
    def new_work_plan(request: Request) -> HTMLResponse:
        return render(request, "new_work_plan.html", title="Create Work Plan")

    @app.post("/work-plans", response_class=HTMLResponse)
    def create_work_plan(request: Request, max_crawls: int = Form(...), max_enrichments: int = Form(...), max_transcripts: int = Form(...)) -> RedirectResponse:
        if min(max_crawls, max_enrichments, max_transcripts) < 0:
            raise HTTPException(400, "Budgets must be non-negative")
        plan = OperationalPlanner(database).plan(OperationalBudget(max_crawls, max_enrichments, max_transcripts))
        return RedirectResponse(f"/work-plans/{plan.id}", status_code=303)

    @app.get("/work-plans/{plan_id}", response_class=HTMLResponse)
    def work_plan_detail(request: Request, plan_id: int) -> HTMLResponse:
        plan = database.get_work_plan(plan_id)
        if plan is None:
            raise HTTPException(404, "Work plan not found")
        return render(request, "detail.html", title=f"Work Plan {plan_id}", kind="work-plan", plan=plan, items=database.list_work_items(plan_id))

    @app.post("/work-plans/{plan_id}/execute")
    def execute_work_plan(request: Request, plan_id: int, max_items: int | None = Form(None), retry_failed: bool = Form(False)) -> RedirectResponse:
        if max_items is None or max_items < 1:
            raise HTTPException(400, "max_items must be explicit and positive")
        from ..collectors.channel_collector import ChannelCrawlService
        from ..collectors.video_metadata import VideoMetadataService
        from ..transcripts.provider import TranscriptService
        executor = WorkPlanExecutor(database, ChannelCrawlService(video_provider or YtDlpChannelVideoProvider(), database), VideoMetadataService(metadata_provider or YtDlpVideoMetadataProvider(), database), TranscriptService(transcript_provider or YtDlpTranscriptProvider(), database))
        executor.execute(plan_id, max_items, retry_failed)
        return RedirectResponse(f"/work-plans/{plan_id}", status_code=303)

    return app


def _video_tiers(database: VideoRepository) -> dict[str, int]:
    with database._connect() as connection:
        rows = connection.execute("SELECT COALESCE(tier, 'unscored'), COUNT(*) FROM video_scores GROUP BY tier").fetchall()
        total = connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    counts = {"high": 0, "medium": 0, "low": 0, "unscored": int(total)}
    for tier, count in rows:
        counts[str(tier)] = int(count)
        if str(tier) != "unscored":
            counts["unscored"] -= int(count)
    return counts


def _failing_channels(database: VideoRepository) -> int:
    with database._connect() as connection:
        row = connection.execute("SELECT COUNT(*) FROM channel_crawl_state WHERE consecutive_failures > 0").fetchone()
    return int(row[0])


def _count_transcripts(database: VideoRepository) -> int:
    with database._connect() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0])


def _count_videos_with_transcripts(database: VideoRepository) -> int:
    with database._connect() as connection:
        return int(connection.execute("SELECT COUNT(DISTINCT video_id) FROM transcripts").fetchone()[0])
