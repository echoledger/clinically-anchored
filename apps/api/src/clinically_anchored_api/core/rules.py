"""Red-flag rule engine.

Fixed rules a clinician writes, not model judgement -- nothing here decides
that a patient is fine. A check-in that matches a rule gets pulled to the
top of the clinician's queue (is_red_flag = true on the check_ins row).

*** PLACEHOLDER RULES ***
The rules below are illustrative examples only, written before the actual
phone call with Sarah to gather her real red-flag list (see "Next step" in
decisions-and-open-questions.md). They are NOT clinically validated and must
not be used with a real patient. Replace them with her actual list -- ideally
sourced from her post-op instructions/protocol library (data-catalogue D13) --
before this touches anything but synthetic data.
"""

from collections.abc import Callable

# Each rule: (description, function of answers dict -> bool). `answers` is
# whatever shape the check-in form submits -- see docs/api's check-in schema
# once it's settled. Keyed by checkbox id for now.
Rule = tuple[str, Callable[[dict], bool]]

PLACEHOLDER_RULES: list[Rule] = [
    ("fever", lambda answers: bool(answers.get("fever"))),
    ("heavy_bleeding", lambda answers: bool(answers.get("heavy_bleeding"))),
    ("severe_pain", lambda answers: bool(answers.get("severe_pain"))),
    ("wound_opening", lambda answers: bool(answers.get("wound_opening"))),
]


def evaluate_red_flags(answers: dict) -> tuple[bool, list[str]]:
    """Returns (is_red_flag, [matched rule descriptions])."""
    matched = [description for description, check in PLACEHOLDER_RULES if check(answers)]
    return (len(matched) > 0, matched)
