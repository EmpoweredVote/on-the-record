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
# Production model key; the eval harness (scripts/eval_speaker_id.py) decides the
# final value. Default "haiku" needs only the already-present ANTHROPIC_API_KEY.
SPEAKER_ID_ACTIVE = "haiku"
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

# Chunked diarization (see docs/superpowers/specs/2026-07-31-chunked-parallel-diarization-design.md).
# 0 = OFF: single-pass diarization, the long-standing behaviour. This is the
# DEFAULT BY DELIBERATE CHOICE, not because chunking is unbuilt or unmeasured.
#
# Chunking works and is fast (60-min windows: 64x on the 5-hour June 10
# meeting, 33x on May 6, DER 0.044/0.059 vs single-pass). But it reliably
# produces MORE speaker labels than there are people (June 10: 49 vs 41),
# because a window sees only a slice of each voice. identify._dedupe then
# treats two labels naming one person as a mis-ID and demotes the loser to
# unnamed+needs_review — so an unmerged fragment publishes a real person's
# remarks attributed to NOBODY unless the reviewer catches it in the GUI.
# Since the 118-minute single-pass cost is UNATTENDED machine time while the
# fragmentation lands on the human review step, the trade is not worth it for
# accuracy-first processing. Set to 60 (or pass --diarize-chunk-minutes 60)
# when a long meeting genuinely needs fast turnaround, and watch the speaker
# count during review. The fix that would make this default-on is
# architectural, not a threshold: chunk for segmentation, then re-cluster
# identity globally over per-turn embeddings (~20s for 2811 segments).
DIARIZE_CHUNK_MINUTES = 0
DIARIZE_CHUNK_OVERLAP_SECONDS = 60
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
DIARIZE_CHUNK_STITCH_THRESHOLD = 0.50

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
