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
SPEAKER_ID_MODELS = {
    "haiku":  {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    "sonnet": {"provider": "anthropic", "model": "claude-sonnet-4-5"},
    # OpenAI-compatible endpoints. Model ids / base_urls are placeholders to be
    # verified against each provider's current docs before first use; they are
    # only reachable when their api_key_env is set (the eval skips the rest).
    "gemini-flash": {"provider": "openai_compat", "model": "gemini-2.5-flash",
                     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                     "api_key_env": "GEMINI_API_KEY"},
    "deepseek": {"provider": "openai_compat", "model": "deepseek-chat",
                 "base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
    "kimi": {"provider": "openai_compat", "model": "moonshot-v1-8k",
             "base_url": "https://api.moonshot.ai/v1", "api_key_env": "MOONSHOT_API_KEY"},
    "glm": {"provider": "openai_compat", "model": "glm-4-flash",
            "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key_env": "ZHIPU_API_KEY"},
}

# --- Source discovery (docs/superpowers/specs/2026-08-02-source-discovery-design.md) ---
DISCOVERY_DIR = DRIVE_ROOT / "discovery"        # poll.log + caption cache
DISCOVERY_MODEL_ACTIVE = "haiku"                # key into SPEAKER_ID_MODELS registry
DISCOVERY_CLASSIFY_MAX_TOKENS = 500
DISCOVERY_CLASSIFY_CAP_PER_RUN = 200            # spend cap; truncation is logged loudly
DISCOVERY_CONFIDENCE_FLOOR = 0.30               # below -> stored as auto_filtered
DISCOVERY_CAPTIONS_BAND = (0.35, 0.75)          # mid-confidence band triggers captions peek
DISCOVERY_SEARCH_RESULTS_PER_QUERY = 10         # ytsearchN
DISCOVERY_SEARCH_SLEEP_SECONDS = 2.0            # politeness between searches
DISCOVERY_SHORT_CLIP_MAX_SECONDS = 8 * 60       # < this from a news channel = likely package
DISCOVERY_FULL_EVENT_MIN_SECONDS = 25 * 60      # >= this = likely full event

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
