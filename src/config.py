"""Configuration constants and paths for CouncilScribe."""

import os
from pathlib import Path

# --- Data root (auto-detect Colab vs local) ---
_DEFAULT_LOCAL = Path.home() / "CouncilScribe"
_DEFAULT_COLAB = Path("/content/drive/MyDrive/CouncilScribe")

def _detect_root() -> Path:
    """Resolve data root: CS_DATA_DIR env var > Colab Drive > ~/CouncilScribe."""
    env = os.environ.get("CS_DATA_DIR")
    if env:
        return Path(env)
    if _DEFAULT_COLAB.exists():
        return _DEFAULT_COLAB
    return _DEFAULT_LOCAL

DRIVE_ROOT = _detect_root()
MEETINGS_DIR = DRIVE_ROOT / "meetings"
PROFILES_DIR = DRIVE_ROOT / "profiles"
CONFIG_DIR = DRIVE_ROOT / "config"

# --- Audio parameters ---
SAMPLE_RATE = 16000
CHANNELS = 1  # mono

# --- Model identifiers ---
DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"
# WeSpeaker ResNet34 — higher-quality embeddings than pyannote/embedding (256-dim).
# NOTE: changing this invalidates stored voice profiles (different dimension).
# PROFILE_SCHEMA_VERSION is bumped when the embedding model OR the stored profile
# structure changes, so load_profiles() can detect and discard stale profiles
# instead of silently mis-matching or unpickling an incompatible shape.
EMBEDDING_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"
WHISPER_MODEL_GPU = "large-v3"
WHISPER_MODEL_CPU = "medium"
WHISPER_COMPUTE_GPU = "float16"
WHISPER_COMPUTE_CPU = "int8"

# Which client the meeting pipeline's Anthropic-shaped call sites use.
# "openrouter" routes the SAME Claude models through OpenRouter billing
# (model ids mapped in llm_providers._OPENROUTER_MODEL_MAP); "anthropic"
# is the direct path (needs ANTHROPIC_API_KEY credit).
LLM_CLIENT_BACKEND = "openrouter"

# --- Summary generation (Anthropic API) ---
SUMMARY_CLASSIFY_MODEL = "claude-haiku-4-5-20251001"    # Section classification
SUMMARY_SYNTHESIZE_MODEL = "claude-sonnet-4-5"  # Discussion summaries & executive summary
SUMMARY_MAX_TOKENS_CLASSIFY = 4096
SUMMARY_MAX_TOKENS_SYNTHESIZE = 4096
SUMMARY_CHUNK_SIZE = 150  # Max segments per classification chunk

# Agenda interpretation (Pass A of item-centric coverage). Sonnet: citizens
# act on these summaries; the groundedness gate rejects rather than repairs.
AGENDA_INTERPRET_MODEL = "claude-sonnet-4-5"
AGENDA_INTERPRET_MAX_TOKENS = 600

# Agenda item -> video alignment (Pass B). Sonnet bounds spans between
# mechanical anchors and reads outcomes; validate_spans + the legislation
# oracle gate everything it says. 4000 tokens covers a full agenda's spans.
AGENDA_ALIGN_MODEL = "claude-sonnet-4-5"
AGENDA_ALIGN_MAX_TOKENS = 4000

# --- Layer-3 speaker identification (LLM) ---
# Layer-3 LLM speaker-ID master switch. OFF BY DELIBERATE CHOICE (2026-08-06):
# the 88-interview eval measured ~13-16% correct-name coverage across five
# models (transcript-anchor is the ceiling — guests are rarely full-named on
# air), and in practice names come from human review. Layers 1-2 (voice
# profiles/embeddings) and the CREC oracle are unaffected. The eval harness
# (scripts/eval_speaker_id.py) bypasses this switch by calling the provider
# directly. Flip to True to re-enable (interview kinds stay excluded).
SPEAKER_ID_LLM_ENABLED = False

# Production model key; the eval harness (scripts/eval_speaker_id.py) decides the
# final value. "haiku-or" is the same Claude weights as "haiku", billed through
# OpenRouter instead of direct Anthropic — needs OPENROUTER_API_KEY, not
# ANTHROPIC_API_KEY (the account has no direct-Anthropic credit). Note this
# layer no longer runs at all on interview-kind meetings (news_clip,
# press_conference, podcast) as of 2026-08-05 — see run_local.should_run_llm —
# so this key now only governs civic-kind and debate/forum runs, and only when
# SPEAKER_ID_LLM_ENABLED is True.
SPEAKER_ID_ACTIVE = "haiku-or"
SPEAKER_ID_MAX_TOKENS = 150
_OPENROUTER_URL = "https://openrouter.ai/api/v1"

SPEAKER_ID_MODELS = {
    "haiku":  {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    "sonnet": {"provider": "anthropic", "model": "claude-sonnet-4-5"},
    # OpenRouter: one OpenAI-compatible endpoint + one key (OPENROUTER_API_KEY)
    # for many models. Model ids + prices ($/M in / out) verified against the
    # live catalog 2026-08-05. "haiku-or" is the SAME weights as "haiku",
    # billed through OpenRouter — the same-model reference for eval runs.
    "haiku-or": {"provider": "openai_compat", "model": "anthropic/claude-haiku-4.5",
                 "base_url": _OPENROUTER_URL, "api_key_env": "OPENROUTER_API_KEY"},  # 1.00/5.00
    "gemini-flash": {"provider": "openai_compat", "model": "google/gemini-2.5-flash",
                     "base_url": _OPENROUTER_URL, "api_key_env": "OPENROUTER_API_KEY"},  # 0.30/2.50
    "gemini-flash-lite": {"provider": "openai_compat", "model": "google/gemini-2.5-flash-lite",
                          "base_url": _OPENROUTER_URL, "api_key_env": "OPENROUTER_API_KEY"},  # 0.10/0.40
    "gpt5-mini": {"provider": "openai_compat", "model": "openai/gpt-5-mini",
                  "base_url": _OPENROUTER_URL, "api_key_env": "OPENROUTER_API_KEY"},  # 0.25/2.00
    "gpt5-nano": {"provider": "openai_compat", "model": "openai/gpt-5-nano",
                  "base_url": _OPENROUTER_URL, "api_key_env": "OPENROUTER_API_KEY"},  # 0.05/0.40
    "deepseek": {"provider": "openai_compat", "model": "deepseek/deepseek-chat-v3.1",
                 "base_url": _OPENROUTER_URL, "api_key_env": "OPENROUTER_API_KEY"},  # 0.25/0.95
    "qwen3-30b": {"provider": "openai_compat", "model": "qwen/qwen3-30b-a3b-instruct-2507",
                  "base_url": _OPENROUTER_URL, "api_key_env": "OPENROUTER_API_KEY"},  # 0.048/0.193
}

# --- Source discovery (docs/superpowers/specs/2026-08-02-source-discovery-design.md) ---
DISCOVERY_DIR = DRIVE_ROOT / "discovery"        # poll.log + caption cache
# Key into SPEAKER_ID_MODELS. Switched haiku -> deepseek 2026-08-05 on the
# 25-fixture eval: recall 0.95 vs 0.85, Brier 0.043 vs 0.113, route 14/15 and
# tier 0.90 tied, 0 parse failures, ~4x cheaper. Every verdict is still
# human-triaged; re-check after each harvest grows the eval set.
DISCOVERY_MODEL_ACTIVE = "deepseek"
DISCOVERY_CLASSIFY_MAX_TOKENS = 500
DISCOVERY_CLASSIFY_CAP_PER_RUN = 200            # spend cap; truncation is logged loudly
DISCOVERY_CONFIDENCE_FLOOR = 0.30               # below -> stored as auto_filtered
DISCOVERY_CAPTIONS_BAND = (0.35, 0.75)          # mid-confidence band triggers the stage-2 peek (captions or page text)
DISCOVERY_SEARCH_RESULTS_PER_QUERY = 10         # ytsearchN
DISCOVERY_SEARCH_SLEEP_SECONDS = 2.0            # politeness between searches
DISCOVERY_SHORT_CLIP_MAX_SECONDS = 8 * 60       # < this from a news channel = likely package
DISCOVERY_FULL_EVENT_MIN_SECONDS = 25 * 60      # >= this = likely full event
# Recency filter: > this = stale/old-cycle. 630 reaches back past the PREVIOUS
# general election (2024-11-05). Calibrated 2026-08-04: observed stale rejects
# were all >=1622 days; high-confidence queued content reached 588.
DISCOVERY_MAX_ITEM_AGE_DAYS = 630
DISCOVERY_BACKOFF_RETRIES = 3                   # yt-dlp bot-check/429 retries per query
DISCOVERY_BACKOFF_BASE_SECONDS = 5.0
DISCOVERY_WEB_FETCH_SLEEP_SECONDS = 2.0         # per-domain politeness for web_rss
DISCOVERY_SWEEP_ABORT_AFTER = 5                 # consecutive exhausted searches -> abort sweep phase

# --- Thresholds ---
VOICE_MATCH_THRESHOLD = 0.85          # Auto-enroll: voice match or high-confidence ID
SOFT_MATCH_THRESHOLD = 0.50           # Show as hint during pre-identification
ENROLLMENT_PROMPT_THRESHOLD = 0.70    # Prompt for enrollment confirmation (interactive mode)
CONFIDENCE_REVIEW_THRESHOLD = 0.70    # Flag for speaker ID review below this
RETURNING_SPEAKER_THRESHOLD_2 = 0.78  # Lowered match threshold for profiles seen in 2 meetings
RETURNING_SPEAKER_THRESHOLD_3 = 0.70  # Lowered match threshold for profiles seen in 3+ meetings

# --- Diarization tuning ---
MERGE_GAP_SECONDS = 0.5  # merge adjacent same-speaker segments closer than this
SPEAKER_MERGE_THRESHOLD = 0.80  # merge diarized speakers with embedding similarity above this

# Chunked diarization: split a meeting into 60-minute windows, diarize them
# concurrently on separate GPUs, then resolve identity globally. See
# docs/superpowers/specs/2026-07-31-chunked-parallel-diarization-design.md for
# the chunking itself and .../2026-08-03-global-identity-clustering-design.md
# for the identity pass.
#
# 60 = ON. This was 0 (off) until 2026-08-03, because chunking reliably produced
# MORE speaker labels than there were people (June 10: 49 vs 41) when
# cross-window identity rested on per-window CENTROIDS. That is not cosmetic:
# identify._dedupe_identities treats two labels naming one person as a mis-ID
# and demotes the loser to unnamed+needs_review, so an unmerged fragment
# publishes a real person's remarks attributed to NOBODY unless a reviewer
# catches it. The fix was architectural, not another threshold — chunk for
# SEGMENTATION (where the ~quadratic cost lives), then cluster identity
# globally over PER-TURN embeddings (DIARIZE_CHUNK_IDENTITY below).
#
# Calibrated at 60 min / average linkage / 0.32 / wespeaker against
# human-reviewed named transcripts, not just against single-pass output:
#   June 10 (298 min): 44 speakers vs single-pass's 41, 2 people fragmented, 0
#     conflated. Slowest window 109s vs 7100s single-pass (65x). At the
#     original 0.32 it was 41 speakers / 1 fragmented / DER 0.0060; the extra
#     labels are the measured price of the higher, venue-safe threshold.
#   May 6 (244 min): 44 speakers vs 42, 3 people fragmented, 3 conflated — all
#     three inherited from pyannote's own within-window labels (single-pass
#     conflates 1 here). At the original 0.32: 43 speakers, 2 fragmented.
#   July 29 (82 min): does not chunk at 60 min (a meeting under ~90 minutes is
#     one window, i.e. byte-identical single-pass output, zero risk). Checked
#     separately at 45 min: 0 people fragmented, 0 conflated.
#   July 16 House floor (200 min, 38 people): 38 speakers, 3 fragmented, 2
#     conflated — both 4s within-window bleeds, no cross-window merge.
#   VALIDATED VENUES are long civic meetings: council and legislative floor.
#     A dense broadcast debate (LA mayoral, 106 min, ~33 people) under-separates
#     badly — 29 labels for 33 people, and 14 of its window-local labels ALREADY
#     span two people before any cross-window step, so this is pyannote inside a
#     60-min window, not the identity pass. Prefer single-pass for dense
#     multi-speaker debate/forum audio; most of it is under 90 min and therefore
#     never chunks anyway.
# People fragmented therefore equals or beats single-pass on all three, and the
# identity pass introduces no cross-window conflation on any of them (see
# global_identity.MIN_SEAM_OVERLAP_SECONDS for the defect that had to be fixed
# before that was true).
# Before this change the same meetings gave 49 and 43 labels with 6 people
# fragmented on June 10. Fragmentation is what blocked the default, and it is
# gone.
DIARIZE_CHUNK_MINUTES = 60
DIARIZE_CHUNK_OVERLAP_SECONDS = 60
# Meeting kinds where chunking is allowed. Chunking is a SPEED optimisation, so
# declining to chunk costs only time and can never be a correctness regression —
# an unlisted kind simply takes the untouched single-pass path, exactly as it did
# before chunking existed. An explicit `run_local.py --diarize-chunk-minutes N`
# still overrides this, loudly, for deliberate experiments.
#
# MEASURED GOOD: "council" (three Bloomington meetings, 82-298 min) and "floor"
# (July 16 House floor, 200 min, 38 labels for 38 reviewed people).
#
# MEASURED BAD: "debate". The LA mayoral debate (~33 people in 106 min) produced
# 29 labels for 33 people, and NO threshold helped — 14 of its window-local
# labels already spanned two people before any cross-window step. The mechanism
# is pyannote's own clustering inside one 60-minute window: when many speakers
# each hold little speech in a window, it merges them. That mechanism depends on
# speaker DENSITY and turn brevity, not on the kind label, which is why "forum"
# is excluded too — same moderated, many-voice, fast-turn shape.
#
# "school_board" and "community_meeting" are included on that same mechanism
# argument, not on measurement: they are single-room civic meetings with
# sequential speakers, i.e. the low-density shape that was validated. Score one
# of each against a human-reviewed transcript when the chance comes.
DIARIZE_CHUNK_EVENT_KINDS = ("council", "school_board", "community_meeting", "floor")
# Cosine similarity required to call a chunk-local speaker the same person as
# an already-seen global speaker across windows. Deliberately LOWER than
# src.speaker_reconcile.EMBEDDING_MATCH_THRESHOLD (0.75, tuned for VibeVoice's
# 50-min windows): per-window pyannote centroids average over fewer turns, so
# the same person scores as low as ~0.55 across a seam and 0.75 fragments them
# badly (June 10: 56 speakers vs 41). Do not lower this further without
# re-running scripts/sweep_chunk_thresholds.py — below 0.50 the DER starts
# climbing again as genuinely different people begin merging, and conflation
# is far worse than fragmentation (a human reviewer sees an extra unnamed
# speaker, but silently merged speakers misattribute quotes).
#
# WARNING: 0.50 was calibrated on cross-window pyannote/embedding (512-dim)
# centroid similarity specifically. This threshold is only in its calibrated
# regime when a payload's `centroids` come from that model. New chunk-worker
# payloads default to computing centroids from wespeaker first (see
# DIARIZE_CHUNK_EMBEDDER below), so a sequential-path run over them is NOT
# the shipped/measured baseline — re-calibrate before trusting DER out of
# that combination.
DIARIZE_CHUNK_STITCH_THRESHOLD = 0.50

# How chunked diarization turns window-local speaker labels into meeting-wide
# ones. "global" (src/global_identity.py) runs ONE constrained agglomerative
# clustering over PER-TURN embeddings at full-meeting scope — the same
# information single-pass clustering uses. "sequential"
# (src/speaker_reconcile.reconcile_chunks) is the older per-window CENTROID
# matcher, kept as an escape hatch and for stitching payloads cached before
# per-turn embeddings existed.
#
# Why global: per-window centroids fragment people (June 10: 49 labels for 41
# people) and the cause is structural, not a threshold. Measured on that
# meeting's human-reviewed transcript: its 86 window-local speakers map onto
# exactly 40 real people, so grouping them is SUFFICIENT; 7 of 86 centroids
# were non-finite (unfiltered NaN turns) and so could never match at all; and
# at the sequential path's 0.50 threshold, centroid matching recovers only
# 83.3% of same-person cross-window pairs while producing ZERO false
# positives — headroom that greedy one-to-one matching against a running mean
# cannot exploit safely.
DIARIZE_CHUNK_IDENTITY = "global"
# Cosine similarity required to merge two clusters of per-turn embeddings.
# NOT interchangeable with DIARIZE_CHUNK_STITCH_THRESHOLD (0.50, per-window
# centroids) or speaker_reconcile.EMBEDDING_MATCH_THRESHOLD (0.75, VibeVoice's
# 50-minute windows): three matchers over three different signals, and reusing
# a value measured on one of them elsewhere is how a tuned number ends up
# somewhere it was never calibrated. Conflation (silent quote misattribution)
# is far worse than fragmentation (an extra unnamed speaker the review gate
# catches), so ties break toward the HIGHER value.
#
# MEASURED. The scale is much lower than the centroid path's 0.50 because these
# are means over pairs of INDIVIDUAL TURN embeddings, not over per-window means:
# on June 10, same-person cross-window node pairs score p05 0.273 / median 0.427
# while different-person pairs top out at 0.322. (That the value below happens to
# equal DIARIZE_CHUNK_STITCH_THRESHOLD is a coincidence — different signal.)
#
# 0.32 shipped first, calibrated on three Bloomington council meetings where
# nothing conflated anywhere in 0.30-0.40. Validating on two OTHER venues then
# found 0.32 too low: on the July 16 House floor it merged Rep. Lauren Underwood
# (82s) with Rep. Emilia Sykes (60s) into one speaker — a 42.2% minority share,
# i.e. two real people, not a boundary bleed. The reviewed transcript shows
# single-pass kept them apart, so that was a genuine regression.
#
#   <=0.46  House floor merges Underwood+Sykes (42.2% / 60s)
#   >=0.48  keeps them apart; 0.48-0.60 identical there (38 labels = 38 people)
#   0.44-0.60 identical on every council meeting tested
#
# 0.50 is two steps above that cliff and inside the wide flat plateau, and
# 0.55+ starts costing fragmentation on dense content. Raising 0.32 -> 0.50
# costs ~3 extra labels and 1 extra fragmented person on a 5-hour council
# meeting and removes the only cross-window conflation the identity pass was
# creating anywhere. That is the intended trade: fragmentation surfaces as an
# extra unnamed speaker at the review gate, conflation misattributes quotes
# silently. Do not lower this without re-running the validation over BOTH
# venue classes — a council-only sweep is what missed the floor regression.
DIARIZE_CHUNK_CLUSTER_THRESHOLD = 0.50
# Cluster-distance linkage. MEASURED: "average" (mean pairwise turn
# similarity) is the only workable choice. "complete" scores a candidate by its
# WORST turn pair, and a real person's worst pair is often anti-correlated
# (same-person median -0.125), so it merges almost nothing. "centroid" pools
# each cluster into one mean and throws away the distribution that makes
# per-turn embeddings worth having — it conflated 2 real people at the most
# conservative threshold tested, on both long meetings.
DIARIZE_CHUNK_LINKAGE = "average"
# Which embedder's per-turn vectors the global pass clusters. MEASURED: on
# June 10's reviewed reference, wespeaker separates same-person from
# different-person cross-window pairs at J=0.953 (90.3% recall at 4 false
# pairs in 2810) versus pyannote/embedding's J=0.919 at a 10x higher false
# rate. It is also what pyannote 3.1 clusters on internally AND what voice
# profiles are built on (config.EMBEDDING_MODEL), so the centroids this path
# returns are profile-compatible and skip run_local's re-extraction guard.
DIARIZE_CHUNK_EMBEDDER = EMBEDDING_MODEL

# --- Post-identification segment merging ---
SEGMENT_MERGE_GAP = 2.0  # merge adjacent same-speaker segments with gap < this (seconds)

# --- Roster surname gating (Layer 2 pattern matcher) ---
ROSTER_SURNAME_THRESHOLD = 0.80  # Reject pattern match if surname similarity below this

# --- Checkpoint ---
CHECKPOINT_EVERY_N_SEGMENTS = 50

# --- Topic classification (Phase 6) ---
TOPIC_CLASSIFY_MODEL = "claude-haiku-4-5-20251001"
# Section types worth tagging with a topic (procedural/roll_call/opening/closing skipped).
# "topic" is the type produced by the interview/media summary path
# (event_kind in news_clip/press_conference) — without it, interviews never tag.
SUBSTANTIVE_SECTION_TYPES = ("discussion", "public_comment", "consent_agenda", "vote", "topic")

# --- Profile DB ---
PROFILE_DB_FILENAME = "speaker_profiles.pkl"
# Bumped to 2 on 2026-04-10 when EMBEDDING_MODEL switched from pyannote/embedding
# (512-dim) to pyannote/wespeaker-voxceleb-resnet34-LM (256-dim). Profiles with
# older schema versions are discarded on load and must be re-enrolled.
# Bumped to 3 on 2026-04-12: added politician_slug and politician_id identity
# fields to StoredProfile for essentials-keyed enrollment.
# Bumped to 4 on 2026-06-16: embeddings are now EmbeddingRecord (vector +
# meeting_id + seg_count) instead of bare np.ndarray, enabling embedding-level
# leave-one-out provenance in calibration.
# Bumped to 5 on 2026-06-26: essentials-linked profiles are now keyed
# essentials:<politician_id> instead of essentials:<politician_slug> (slug is
# NULL for ~99.4% of essentials.politicians). Old slug-keyed profiles are a
# different key space, so load_profiles() discards (and backs up) the old DB on
# load; re-enroll via reenroll_profiles.py to rebuild under the id keys.
PROFILE_SCHEMA_VERSION = 5

# --- Meeting confidence gate (Phase A) ---
# Probable-tier coverage (returning-speaker voice matches at the lowered
# threshold) counts toward the verdict at this discount vs. trusted coverage.
GATE_PROBABLE_DISCOUNT = 0.5

# Speakers whose total speech-time is below this are treated as incidental
# (e.g. public commenters) and excluded from the coverage denominator, UNLESS
# excluding them would leave no eligible speakers (then all are kept).
GATE_SPEECH_FLOOR_SECONDS = 60.0

# Per-event-kind verdict thresholds on the (discounted) effective coverage.
# verdict: effective >= high -> pass; high > effective >= low -> review;
#          effective < low -> failed.
# SEED VALUES — provisional and conservative; recalibrate with
# bench/calibrate_gate.py once one meeting of each kind has been reviewed.
GATE_THRESHOLDS = {
    "default":          {"high": 0.90, "low": 0.50},
    "council":          {"high": 0.90, "low": 0.50},
    "school_board":     {"high": 0.90, "low": 0.50},
    "debate":           {"high": 0.95, "low": 0.60},
    "forum":            {"high": 0.90, "low": 0.55},
    "community_meeting":{"high": 0.70, "low": 0.40},
    "floor":            {"high": 0.70, "low": 0.40},
    "news_clip":        {"high": 0.90, "low": 0.50},
    "press_conference": {"high": 0.90, "low": 0.50},
    "podcast":          {"high": 0.90, "low": 0.50},
    "other":            {"high": 0.90, "low": 0.50},
}
