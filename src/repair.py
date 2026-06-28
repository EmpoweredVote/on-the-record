"""Repair transcript artifacts from diarization and caption data."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .export import export_all
from .identify import apply_mappings_to_segments, merge_adjacent_segments
from .models import Meeting, Segment
from .transcribe import remove_segment_overlaps
from .vtt_align import align_vtt_to_segments, parse_vtt


class RepairError(RuntimeError):
    """Raised when a transcript cannot be repaired safely."""


@dataclass(frozen=True)
class RepairResult:
    meeting_id: str
    segment_count: int
    backup_dir: Path
    exports: dict[str, Path]


_REQUIRED_FILES = (
    "pipeline_state.json",
    "diarization.json",
    "captions.vtt",
    "transcript_named.json",
)


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RepairError(
            f"Invalid repair input: {path.name} must contain valid JSON: {exc}"
        ) from exc


def _load_inputs(meeting_dir: Path) -> tuple[list[Segment], Meeting]:
    missing = [name for name in _REQUIRED_FILES if not (meeting_dir / name).is_file()]
    if missing:
        raise RepairError(f"Missing required meeting files: {', '.join(missing)}")

    try:
        pipeline_state = _load_json(meeting_dir / "pipeline_state.json")
        if not isinstance(pipeline_state, dict):
            raise ValueError("pipeline_state.json must contain a JSON object")

        diarization_data = _load_json(meeting_dir / "diarization.json")
        if not isinstance(diarization_data, list):
            raise ValueError("diarization.json must contain a JSON array")
        segments = [Segment.from_dict(item) for item in diarization_data]

        named_data = _load_json(meeting_dir / "transcript_named.json")
        if not isinstance(named_data, dict):
            raise ValueError("transcript_named.json must contain a JSON object")
        meeting = Meeting.from_dict(named_data)

        if not parse_vtt(meeting_dir / "captions.vtt"):
            raise ValueError("captions.vtt contains no usable cues")
    except (
        OSError,
        UnicodeError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise RepairError(f"Invalid repair input: {exc}") from exc

    return segments, meeting


def _serialize_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _create_backup(
    meeting_dir: Path,
    backup_dir: Path,
    existing_live_paths: set[Path],
) -> None:
    created = False
    try:
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        backup_dir.mkdir(exist_ok=False)
        created = True
        for relative_path in existing_live_paths:
            source = meeting_dir / relative_path
            destination = backup_dir / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    except Exception as exc:
        if created:
            shutil.rmtree(backup_dir, ignore_errors=True)
        raise RepairError(f"Could not create repair backup: {exc}") from exc


def _restore_from_backup(backup_path: Path, live_path: Path) -> None:
    fd, temporary_path = tempfile.mkstemp(
        prefix=".repair-rollback-",
        dir=live_path.parent,
    )
    os.close(fd)
    temporary = Path(temporary_path)
    try:
        shutil.copy2(backup_path, temporary)
        os.replace(temporary, live_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _install_transaction(
    meeting_dir: Path,
    backup_dir: Path,
    staged_files: dict[Path, Path | None],
    existing_live_paths: set[Path],
) -> None:
    changed: list[Path] = []

    try:
        for relative_path, staged_path in staged_files.items():
            live_path = meeting_dir / relative_path
            if staged_path is None:
                if live_path.exists():
                    live_path.unlink()
                    changed.append(relative_path)
                continue

            live_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_path, live_path)
            changed.append(relative_path)
    except BaseException as install_error:
        rollback_errors = []
        for relative_path in reversed(changed):
            live_path = meeting_dir / relative_path
            try:
                if relative_path in existing_live_paths:
                    _restore_from_backup(
                        backup_dir / relative_path,
                        live_path,
                    )
                elif live_path.exists():
                    live_path.unlink()
            except BaseException as rollback_error:
                rollback_errors.append(f"{relative_path}: {rollback_error}")

        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise RepairError(
                f"Could not install repaired transcript: {install_error}; "
                f"rollback also failed: {details}"
            ) from install_error
        if isinstance(install_error, Exception):
            raise RepairError(
                f"Could not install repaired transcript: {install_error}"
            ) from install_error
        raise


def repair_transcript(
    meeting_dir: str | Path,
    *,
    now: datetime | None = None,
) -> RepairResult:
    """Rebuild transcript artifacts from the original diarization and captions."""
    meeting_dir = Path(meeting_dir)
    if not meeting_dir.is_dir():
        raise RepairError(f"Meeting directory does not exist: {meeting_dir}")

    diarized_segments, meeting = _load_inputs(meeting_dir)

    try:
        raw_segments = remove_segment_overlaps(diarized_segments)
        # Clip meetings store full-source captions but clip-local diarization;
        # rebase cue times by the clip start, mirroring the live pipeline so
        # in-window text aligns and out-of-window cues drop.
        raw_segments = align_vtt_to_segments(
            meeting_dir / "captions.vtt",
            raw_segments,
            clip_offset=meeting.clip_start_seconds or 0.0,
        )
    except Exception as exc:
        raise RepairError(f"Could not align captions: {exc}") from exc

    if not any(segment.text.strip() for segment in raw_segments):
        raise RepairError("No caption text aligned to diarization segments")

    for segment in raw_segments:
        segment.speaker_name = None
        segment.confidence = None
        segment.id_method = None

    named_segments = [
        Segment.from_dict(segment.to_dict()) for segment in raw_segments
    ]
    apply_mappings_to_segments(named_segments, meeting.speakers)
    meeting.segments = merge_adjacent_segments(named_segments)

    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup_dir = (
        meeting_dir / "backups" / f"transcript-repair-{timestamp}"
    )
    try:
        staging_dir = Path(
            tempfile.mkdtemp(prefix=".transcript-repair-", dir=meeting_dir)
        )
    except Exception as exc:
        raise RepairError(f"Could not create transcript repair staging: {exc}") from exc

    try:
        staged_raw = staging_dir / "transcript_raw.json"
        staged_named = staging_dir / "transcript_named.json"
        staged_exports_dir = staging_dir / "exports"

        try:
            _serialize_json(
                staged_raw,
                [segment.to_dict() for segment in raw_segments],
            )
            _serialize_json(staged_named, meeting.to_dict())
            staged_exports = export_all(meeting, staged_exports_dir)

            staged_files: dict[Path, Path | None] = {
                staged_raw.relative_to(staging_dir): staged_raw,
                staged_named.relative_to(staging_dir): staged_named,
            }
            live_exports = {}
            for export_type, staged_path in staged_exports.items():
                relative_path = staged_path.relative_to(staging_dir)
                staged_files[relative_path] = staged_path
                live_exports[export_type] = meeting_dir / relative_path

            summary_path = Path("exports/summary.md")
            if (
                "summary" not in staged_exports
                and (meeting_dir / summary_path).is_file()
            ):
                staged_files[summary_path] = None

            existing_live_paths = {
                relative_path
                for relative_path in staged_files
                if (meeting_dir / relative_path).is_file()
            }
        except RepairError:
            raise
        except Exception as exc:
            raise RepairError(f"Transcript repair failed: {exc}") from exc

        _create_backup(meeting_dir, backup_dir, existing_live_paths)
        _install_transaction(
            meeting_dir,
            backup_dir,
            staged_files,
            existing_live_paths,
        )
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return RepairResult(
        meeting_id=meeting.meeting_id,
        segment_count=len(meeting.segments),
        backup_dir=backup_dir,
        exports=live_exports,
    )
