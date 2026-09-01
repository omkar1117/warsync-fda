"""The query set used across experiments.

Two partitions, following the temporal-slice protocol: `BASE` is answerable
from the initial corpus and is used to measure whether updates perturb answers
that should not have changed; `SLICE` targets content that only arrives with a
later update and is used to measure freshness correctness.
"""

BASE = [
    "out-of-specification investigation root cause not identified",
    "audit trail disabled shared administrator account",
    "quality control unit authority to reject batches",
    "corrective and preventive action effectiveness verification",
    "aseptic process simulation intervention not represented",
    "complaint evaluation reportability device malfunction",
    "cleaning validation maximum allowable carryover",
    "environmental monitoring recovery corrective action",
    "dietary ingredient identity testing certificate of analysis",
    "laboratory controls method suitability failure",
    "deviation reporting distributed product potency",
    "validated range operations without deviation records",
]

SLICE = [
    "supplemental finding recorded in revision following further review",
]
