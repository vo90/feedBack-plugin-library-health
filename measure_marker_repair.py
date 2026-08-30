"""Closed planning and application for repeated Feedpak measure markers.

The validator owns detection semantics in ``repair_eligibility.py``.  This
module turns that shared, read-only assessment into one source-bound action and
applies it only after recomputing the exact same action from current data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Callable


RULE_CODE = "timeline.repeated-measure-markers"
OPERATION = "normalize_repeated_measure_markers"


@dataclass(frozen=True)
class RepeatedMeasureMarkerChange:
    beat_index: int
    expected_measure: int
    replacement_measure: int
    entry_sha256: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NormalizeRepeatedMeasureMarkers:
    beat_array_path: tuple[str | int, ...]
    expected_length: int
    original_sha256: str
    result_sha256: str
    changes: tuple[RepeatedMeasureMarkerChange, ...]

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return ()

    @property
    def change_count(self) -> int:
        return len(self.changes)

    def to_dict(self) -> dict:
        return {
            "operation": OPERATION,
            "beat_array_path": list(self.beat_array_path),
            "expected_length": self.expected_length,
            "original_sha256": self.original_sha256,
            "result_sha256": self.result_sha256,
            "changes": [change.to_dict() for change in self.changes],
        }


def declared_beat_member_paths(
    manifest: dict,
    *,
    validate_member_path: Callable[[str], str],
    load_json: Callable[[str], tuple[str, object | None, str | None]],
    error_type: type[Exception],
) -> list[str]:
    """Return every declared member that could store a package beat grid.

    Unlike FeedBack's active-source resolver, this repair deliberately keeps
    sidecar and arrangement copies consistent.  Unreadable or malformed
    declared members are returned so the normal planner blocks the complete
    transaction instead of partially repairing the package.
    """
    candidates: list[str] = []
    declared = manifest.get("song_timeline")
    if declared is not None:
        if not isinstance(declared, str) or not declared:
            raise error_type(
                "invalid_declared_beat_source",
                "The declared song timeline path is invalid, so every beat copy cannot be repaired safely.",
            )
        candidates.append(declared)

    arrangements = manifest.get("arrangements")
    if not isinstance(arrangements, list):
        raise error_type(
            "invalid_declared_beat_source",
            "The arrangement list is invalid, so every beat copy cannot be repaired safely.",
        )
    for entry in arrangements:
        if not isinstance(entry, dict):
            raise error_type(
                "invalid_declared_beat_source",
                "An arrangement declaration is invalid, so every beat copy cannot be repaired safely.",
            )
        member = entry.get("file")
        if not isinstance(member, str) or not member:
            raise error_type(
                "invalid_declared_beat_source",
                "An arrangement file path is invalid, so every beat copy cannot be repaired safely.",
            )
        candidates.append(member)

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        member_path = validate_member_path(candidate)
        if member_path in seen:
            continue
        seen.add(member_path)
        if member_path.lower().endswith(".jsonc"):
            result.append(member_path)
            continue
        status, document, _source_sha256 = load_json(member_path)
        if status != "valid" or not isinstance(document, dict):
            result.append(member_path)
            continue
        if "beats" not in document:
            continue
        beats = document.get("beats")
        if not isinstance(beats, list) or beats:
            result.append(member_path)
    return result


def plan_operation(
    document: dict,
    *,
    assess: Callable[[object], dict],
    error_type: type[Exception],
) -> NormalizeRepeatedMeasureMarkers | None:
    """Build the one exact operation or fail closed on an uncertain pattern."""
    assessment = assess(document.get("beats"))
    status = assessment.get("status")
    if status == "no_defect":
        return None
    if status != "eligible":
        raise error_type(
            str(assessment.get("blocker_code") or "ambiguous_measure_markers"),
            str(
                assessment.get("message")
                or "The beat measure pattern cannot be normalized safely."
            ),
        )

    beats = document.get("beats")
    if not isinstance(beats, list):  # Defensive; the assessment already checks.
        raise error_type(
            "beats_not_array", "Beat markers are not stored as an array."
        )
    changes = tuple(
        RepeatedMeasureMarkerChange(
            beat_index=index,
            expected_measure=expected_measure,
            replacement_measure=replacement_measure,
            entry_sha256=_digest(beat),
        )
        for index, expected_measure, replacement_measure in assessment["changes"]
        for beat in (beats[index],)
    )
    repaired = [dict(beat) for beat in beats]
    for change in changes:
        repaired[change.beat_index]["measure"] = change.replacement_measure
    return NormalizeRepeatedMeasureMarkers(
        beat_array_path=("beats",),
        expected_length=len(beats),
        original_sha256=_digest(beats),
        result_sha256=_digest(repaired),
        changes=changes,
    )


def apply_operation(
    document: dict,
    operation: dict,
    normalized: set[tuple[tuple[str | int, ...], int]],
    *,
    assess: Callable[[object], dict],
    error_type: type[Exception],
) -> None:
    """Recompute, verify, then apply one repeated-marker normalization."""
    if not isinstance(operation, dict) or set(operation) != {
        "operation",
        "beat_array_path",
        "expected_length",
        "original_sha256",
        "result_sha256",
        "changes",
    }:
        raise error_type("invalid_plan", "The repair preview is invalid.")
    if operation.get("operation") != OPERATION:
        raise error_type("invalid_plan", "The repair preview is invalid.")

    expected = plan_operation(document, assess=assess, error_type=error_type)
    if expected is None or expected.to_dict() != operation:
        raise error_type("invalid_plan", "The repair preview is invalid.")
    path = expected.beat_array_path
    if operation.get("beat_array_path") != list(path):
        raise error_type("invalid_plan", "The repair preview is invalid.")
    beats = document.get("beats")
    if (
        not isinstance(beats, list)
        or operation.get("expected_length") != len(beats)
        or _digest(beats) != operation.get("original_sha256")
    ):
        raise error_type(
            "source_changed",
            "The beat timeline changed after this preview. Review the safe fix again before applying it.",
        )

    targets = []
    for change in expected.changes:
        marker = (path, change.beat_index)
        beat = beats[change.beat_index]
        if marker in normalized:
            raise error_type("invalid_plan", "The repair preview is invalid.")
        if (
            not isinstance(beat, dict)
            or beat.get("measure") != change.expected_measure
            or change.replacement_measure != -1
            or _digest(beat) != change.entry_sha256
        ):
            raise error_type(
                "source_changed",
                "The beat timeline changed after this preview. Review the safe fix again before applying it.",
            )
        targets.append((marker, beat, change.replacement_measure))

    for marker, beat, replacement in targets:
        beat["measure"] = replacement
        normalized.add(marker)
    if _digest(beats) != operation.get("result_sha256"):
        raise error_type(
            "invalid_plan", "The repaired beat timeline did not match its preview."
        )


def _digest(value) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ValueError("Unsupported JSON value in beat timeline") from exc
    return hashlib.sha256(encoded).hexdigest()
