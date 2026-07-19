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

# --- LLM (Layer 3 speaker identification) ---
LLM_REPO = "bartowski/Qwen2.5-7B-Instruct-GGUF"
LLM_FILENAME = "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
LLM_CONTEXT_TOKENS = 8192

# --- Summary generation (Anthropic API) ---
SUMMARY_CLASSIFY_MODEL = "claude-haiku-4-5-20251001"    # Section classification
SUMMARY_SYNTHESIZE_MODEL = "claude-sonnet-4-5"  # Discussion summaries & executive summary
SUMMARY_MAX_TOKENS_CLASSIFY = 4096
SUMMARY_MAX_TOKENS_SYNTHESIZE = 4096
SUMMARY_CHUNK_SIZE = 150  # Max segments per classification chunk

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
    "news_clip":        {"high": 0.90, "low": 0.50},
    "press_conference": {"high": 0.90, "low": 0.50},
    "podcast":          {"high": 0.90, "low": 0.50},
    "other":            {"high": 0.90, "low": 0.50},
}
