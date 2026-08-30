import copy
import json
import math

import pytest

import repair_eligibility


def _beats(measures):
    return [
        {"time": index * 0.5, "measure": measure, "future": {"id": index}}
        for index, measure in enumerate(measures)
    ]


def test_detector_returns_exact_immutable_changes_for_converter_signature():
    beats = _beats([-1, -1, 1, 1, 1, 2, 2, 3, 4, 4])
    original = copy.deepcopy(beats)

    assessment = repair_eligibility.assess_repeated_measure_markers(beats)

    assert assessment == {
        "status": "eligible",
        "eligible": True,
        "affected_count": 4,
        "repeated_run_count": 3,
        "first_index": 3,
        "first_time": 1.5,
        "changes": (
            (3, 1, -1),
            (4, 1, -1),
            (6, 2, -1),
            (9, 4, -1),
        ),
        "blocker_code": None,
        "message": "",
    }
    assert isinstance(assessment["changes"], tuple)
    json.dumps(assessment, allow_nan=False)
    assert beats == original


@pytest.mark.parametrize(
    "measures",
    [
        [],
        [-1, -1],
        [1, 2, 3, 4],
        [1, -1, -1, 2, -1, -1, 3],
        [-1, 4, -1, 5, 6],
    ],
)
def test_detector_accepts_no_defect_measure_patterns(measures):
    assessment = repair_eligibility.assess_repeated_measure_markers(
        _beats(measures)
    )

    assert assessment["status"] == "no_defect"
    assert assessment["eligible"] is False
    assert assessment["changes"] == ()
    assert assessment["blocker_code"] is None


@pytest.mark.parametrize(
    ("measures", "blocker_code"),
    [
        ([0, 0, 1, 1], "unsupported_measure_marker"),
        ([-2, -2, 1, 1], "unsupported_measure_marker"),
        ([1, 1, 2, 3], "insufficient_repeated_measure_runs"),
        ([1, 1, 3, 3], "non_consecutive_measure_runs"),
        ([2, 2, 1, 1], "non_consecutive_measure_runs"),
        ([1, 1, -1, 2, 2], "mixed_measure_marker_pattern"),
        ([1, -1, 1, 2], "mixed_measure_marker_pattern"),
    ],
)
def test_detector_blocks_ambiguous_measure_patterns(measures, blocker_code):
    assessment = repair_eligibility.assess_repeated_measure_markers(
        _beats(measures)
    )

    assert assessment["status"] == "ambiguous"
    assert assessment["eligible"] is False
    assert assessment["changes"] == ()
    assert assessment["blocker_code"] == blocker_code


@pytest.mark.parametrize(
    "beats",
    [
        None,
        [{"time": 0.0, "measure": 1}, "not an object"],
        [{"time": True, "measure": 1}],
        [{"time": 0.0, "measure": True}],
        [{"time": math.nan, "measure": 1}],
        [{"time": 0.0, "measure": 1, "future": math.inf}],
        [{"time": 0.0, "measure": 1}, {"time": 0.0, "measure": 1}],
        [{"time": 1.0, "measure": 1}, {"time": 0.0, "measure": 1}],
    ],
)
def test_detector_fails_closed_for_malformed_beat_streams(beats):
    assessment = repair_eligibility.assess_repeated_measure_markers(beats)

    assert assessment["status"] == "malformed"
    assert assessment["eligible"] is False
    assert assessment["changes"] == ()
    assert assessment["blocker_code"]
