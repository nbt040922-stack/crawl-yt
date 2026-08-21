# Phase 2E Video Prioritization

Phase 2E adds deterministic local video scoring. `VideoScoringService` reads video and channel signals, persists versioned `VideoScore` rows, and supplies operational metadata/transcript priorities without network calls. The planner scores only a bounded candidate pool and refreshes scores older than 24 hours.

The v1 formula is:

- metadata priority = 0.40 recency + 0.30 channel + 0.15 traction + 0.15 confidence
- transcript priority = 0.30 recency + 0.30 channel + 0.20 traction + 0.10 metadata value + 0.10 confidence
- operational score = max(metadata priority, transcript priority), clamped to 0-100

Scores use recency buckets, neutral values for missing data, tiers high (>=70), medium (>=40), and low (<40), and human-readable reason JSON. Existing Phase 2A-2D behavior remains network-free and budget-limited.
