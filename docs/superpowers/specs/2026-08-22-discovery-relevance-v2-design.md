# Discovery Relevance v2 Design

## Goal

Make search results candidates only; accept a channel only when a bounded,
deterministic channel-wide topic check passes.

## Architecture

`DiscoveryService` will normalize the primary query and related terms, deduplicate
search candidates, verify each candidate once through a provider method, and apply
a centralized `Strict`/`Balanced`/`Broad` policy. Verification evidence is kept
in the report for UI/audit. Only accepted candidates may be persisted as new
channels or discovery provenance. Existing channels remain intact but receive no
new provenance when rejected.

The provider boundary exposes lightweight channel verification (identity text and
up to 20 recent video titles). No full crawl, transcripts, comments, API calls,
scoring inputs, or semantic models are introduced.

## Relevance policy

- Sample size: `DISCOVERY_TOPIC_SAMPLE_SIZE = 20`.
- Candidate cap: `MAX_CANDIDATE_MULTIPLIER = 5` times the requested accepted limit.
- Coverage thresholds: Strict 0.60, Balanced 0.40, Broad 0.25.
- Identity exception floors: Strict 0.40, Balanced 0.25, Broad 0.15.
- Acceptance is coverage threshold, or strong identity plus identity floor; near-zero
  coverage never passes via identity.

Matching uses Unicode casefolding, collapsed whitespace, phrase matching, multiple
meaningful query terms, or explicit related phrases. Generic single tokens do not
count by themselves.

## Data and UI

`DiscoveryReport` gains mode, related terms, candidate/accepted/rejected counts,
accepted evidence, rejected evidence, and verification failures. Existing discovery
tables are preserved; no legacy channel or provenance rows are removed. The form
adds mode and related terms. The result page displays accepted/rejected evidence,
coverage and identity, and dry-run status without raw dataclass output.

## Failure and compatibility

Verification failures are recorded as rejected and processing continues. Existing
scoring, crawl, scheduling, batch, and Excel behavior is unchanged. Dry-run performs
search and verification but writes nothing.
