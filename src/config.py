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
#   June 10 (298 min): DER 0.0060, 41 speakers vs single-pass's 41 (drift
#     0.0%), 1 person fragmented — the SAME person single-pass itself splits —
#     and 0 conflated. Slowest window 109s vs 7100s single-pass (65x).
#   May 6 (244 min): DER 0.0087, 43 speakers vs 42 (drift +2.4%), 2 people
#     fragmented — same as single-pass — and 3 conflated, ALL THREE inherited
#     from pyannote's own within-window labels (single-pass conflates 1 here).
#   July 29 (82 min): does not chunk at 60 min (a meeting under ~90 minutes is
#     one window, i.e. byte-identical single-pass output, zero risk). Checked
#     separately at 45 min: 0 people fragmented where single-pass fragments 2.
# People fragmented therefore equals or beats single-pass on all three, and the
# identity pass introduces no cross-window conflation on any of them (see
# global_identity.MIN_SEAM_OVERLAP_SECONDS for the defect that had to be fixed
# before that was true).
# Before this change the same meetings gave 49 and 43 labels with 6 people
# fragmented on June 10. Fragmentation is what blocked the default, and it is
# gone.
DIARIZE_CHUNK_MINUTES = 60
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
# MEASURED 2026-08-03 by scripts/sweep_chunk_thresholds.py over per-turn
# wespeaker vectors, using each meeting's human-reviewed transcript as truth.
# The scale is much lower than the centroid path's 0.50 because these are
# means over pairs of INDIVIDUAL TURN embeddings, not over per-window means:
# same-person cross-window node pairs score p05 0.273 / median 0.427 while
# different-person pairs top out at 0.322.
#
#   0.36-0.40  fragments slightly (June 10: 44 speakers for 41 people)
#   0.30-0.32  FLAT and correct (June 10: 41 speakers, DER 0.0060, 1 person
#              fragmented — the same one single-pass splits — 0 conflated;
#              May 6: 41 vs 42, DER 0.0210; July 29 @45min: 0 fragmented)
#   0.28       CONFLATION CLIFF, independently on both long meetings: June 10
#              merges Paul Gillard (140.4s, 44.6% of the label) into another
#              speaker, May 6 merges Steve Volin (168.5s, 47.3%). DER rises
#              with it (0.0060 -> 0.0150).
#
# 0.32 is the TOP of the flat basin, one full step above a cliff that two
# meetings agree on. Do not lower it without re-running the sweep. Note that
# the DER + speaker-count gate PASSES even at 0.22 (4 real people conflated on
# June 10), so DER alone cannot defend this value — the reviewed-names check in
# bench/identity_score.py is what discriminates.
DIARIZE_CHUNK_CLUSTER_THRESHOLD = 0.32
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
