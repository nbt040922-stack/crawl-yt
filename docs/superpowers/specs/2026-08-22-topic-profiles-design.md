# Discovery Topic Profiles Design

## Goal

Let users define reusable, deterministic topic concepts separately from the
YouTube search query, while keeping channel-wide coverage gates unchanged.

## Data model

`topic_profiles` stores a user-controlled name, description, normalized concept
phrases, and timestamps. Discovery relevance runs snapshot the selected profile
identity and effective concepts so later profile edits cannot change historical
evidence. Candidate evidence stores bounded sampled-title matches for audit.

## Matching

The primary search query continues to find candidates. Verification matches
sampled video titles and channel identity against effective concepts assembled
from the selected profile, run-specific extra concepts, and the primary query.
Multi-word phrases match by normalized substring or conservative all-token
matching regardless of order. Generic single-token protection remains in force.
Every title records the matched concept phrases.

## Discovery behavior

Coverage remains matched usable titles divided by sampled usable titles.
Strict/Balanced/Broad thresholds and identity floors are unchanged. Accepted
provenance uses only the primary search keyword. Profiles add no network calls.

## Web UI

Server-rendered profile list/create/edit/delete pages manage one-phrase-per-line
concepts. Discovery can select a profile or run without one, and accepts extra
concepts. Results show the profile snapshot, effective concept count, top matched
concepts, and expandable per-title evidence for accepted and rejected candidates.
