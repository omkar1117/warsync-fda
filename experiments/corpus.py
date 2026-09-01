"""Deterministic synthetic corpus generation at arbitrary scale.

The bundled six-letter corpus is too small to separate the conditions: a full
rebuild of it costs ~10ms, which no query stream can observe. These experiments
need a corpus large enough that rebuild cost and incremental cost differ by
something a clock can see, so we generate one.

Everything is seeded. The same `size` produces the same corpus on every run and
on every machine, which is what makes the experiment repeatable.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OFFICES = [
    "Center for Drug Evaluation and Research (CDER)",
    "Center for Devices and Radiological Health (CDRH)",
    "Center for Biologics Evaluation and Research (CBER)",
    "Center for Food Safety and Applied Nutrition (CFSAN)",
]

PREFIXES = ["Northgate", "Cascade", "Meridian", "Bluefin", "Halcyon", "Stonebrook",
            "Ironwood", "Larkspur", "Quarry", "Vantage", "Ashfield", "Kestrel",
            "Redpoint", "Thornbury", "Windward", "Calder", "Emberly", "Foxglove"]
SUFFIXES = ["Sterile Solutions", "Analytical Laboratories", "Device Works",
            "Nutraceuticals", "Contract Manufacturing", "Biologics",
            "Pharmaceuticals", "Life Sciences", "Therapeutics", "Bioprocess"]
FORMS = ["Inc.", "LLC", "Ltd.", "Corp.", "GmbH", "Pvt. Ltd."]

#: Each finding pairs prose with the provision it cites, so the generated corpus
#: exercises the citation extractor rather than carrying pre-labelled metadata.
FINDINGS = [
    ("Your firm failed to thoroughly investigate any unexplained discrepancy or "
     "failure of a batch or any of its components to meet any of its specifications "
     "(21 CFR 211.192). Investigations were closed as laboratory error without "
     "identifying a laboratory root cause, and the review was not extended to other "
     "batches manufactured on the same line during the affected period."),
    ("Your firm failed to exercise appropriate controls over computer or related "
     "systems to assure that only authorized personnel institute changes in master "
     "production and control records (21 CFR 211.68(b)). Analysts shared a single "
     "account with administrator privileges and audit trails were disabled on "
     "several workstations at the time of the inspection."),
    ("Your firm failed to ensure that the quality control unit had the responsibility "
     "and authority to approve or reject all components, drug product containers, "
     "closures, in-process materials, packaging material, labeling, and drug products "
     "(21 CFR 211.22(a)). Lots were released by production supervision after the "
     "quality unit had placed them on hold pending investigation."),
    ("Your firm failed to establish laboratory controls that include scientifically "
     "sound and appropriate specifications, standards, sampling plans, and test "
     "procedures (21 CFR 211.160(b)). The test method was not qualified for the "
     "product matrix, and batches continued to be released against that method after "
     "the method suitability failure."),
    ("Failure to establish and maintain procedures for implementing corrective and "
     "preventive action (21 CFR 820.100(a)). The procedure does not require analysis "
     "of quality data sources to identify existing and potential causes of "
     "nonconforming product, and corrective actions were closed without verification "
     "of effectiveness."),
    ("Failure to establish and maintain procedures for receiving, reviewing, and "
     "evaluating complaints (21 CFR 820.198(a)). Complaints describing device "
     "malfunction were classified as user error without device evaluation, and no "
     "determination was made as to whether the events were reportable."),
    ("Your firm failed to establish and follow appropriate written procedures designed "
     "to prevent microbiological contamination of drug products purporting to be "
     "sterile (21 CFR 211.113(b)). The aseptic process simulation did not represent "
     "interventions routinely performed during commercial filling operations."),
    ("Your firm failed to report a deviation from applicable current good manufacturing "
     "practice regulations that may affect the safety, purity, or potency of a "
     "distributed product (21 CFR 600.14). The excursion was documented in the quality "
     "system but had not been reported at the time of the inspection."),
    ("Your firm failed to conduct at least one appropriate test or examination to "
     "verify the identity of a dietary ingredient (21 CFR 111.75(a)(1)(i)). "
     "Certificates of analysis were accepted from a broker without qualifying the "
     "supplier and without identity testing on receipt."),
    ("Your firm failed to have adequate written procedures for the cleaning and "
     "maintenance of equipment (21 CFR 211.67(b)). Cleaning validation for the shared "
     "suite did not establish maximum allowable carryover limits for the "
     "highest-potency product manufactured in that suite."),
    ("Your firm failed to establish an adequate system for monitoring environmental "
     "conditions in aseptic processing areas (21 CFR 211.42(c)(10)(iv)). Recurring "
     "recovery at a sampling site adjacent to the filling needles was recorded as a "
     "deviation without initiating corrective action."),
    ("Your firm failed to establish written procedures for production and process "
     "control designed to assure that the drug products have the identity, strength, "
     "quality, and purity they purport to possess (21 CFR 211.100(a)). Operations ran "
     "outside the validated range without deviation records."),
]

BANNER = ("[SYNTHETIC EXAMPLE — generated for the WarnSync evaluation harness. "
          "Not a real FDA record.]")

OPENING = ("The U.S. Food and Drug Administration inspected your facility. This letter "
           "summarizes significant violations of the applicable regulations found during "
           "that inspection. You are responsible for investigating and determining the "
           "causes of any violations and for preventing their recurrence.")

CLOSING = ("This letter is not intended to be an all-inclusive list of violations. Within "
           "fifteen working days of receipt of this letter, notify this office in writing "
           "of the specific steps you have taken to correct these violations, including an "
           "explanation of each step being taken to prevent recurrence, and documentation "
           "showing that the corrections have been completed.")


def _firm(rng: random.Random) -> str:
    return f"{rng.choice(PREFIXES)} {rng.choice(SUFFIXES)}, {rng.choice(FORMS)}"


def make_record(index: int, rng: random.Random, revision: int = 0) -> dict:
    """Build one letter. `revision` perturbs the body so the fingerprint changes."""
    findings = rng.sample(FINDINGS, rng.randint(7, 10))
    body = "\n\n".join(
        f"{n}. {text}" for n, text in enumerate(findings, start=1)
    )
    extra = "" if revision == 0 else (
        f"\n\n{len(findings) + 1}. Supplemental finding recorded in revision "
        f"{revision} of this letter following further review of the inspection record."
    )
    year = 2024 + index % 3
    month = 1 + index % 12
    day = 1 + index % 28
    return {
        "letter_id": f"WL-EXP-{index:05d}",
        "recipient": _firm(rng),
        "office": OFFICES[index % len(OFFICES)],
        "issuance_date": f"{year}-{month:02d}-{day:02d}",
        "posting_date": f"{year}-{month:02d}-{min(day + 2, 28):02d}",
        "subject": f"Warning Letter — inspection findings ({index:05d})",
        "source_url": f"https://example.invalid/warning-letters/WL-EXP-{index:05d}",
        "synthetic": True,
        "content": f"{BANNER}\n\n{OPENING}\n\n{body}{extra}\n\n{CLOSING}\n",
    }


def generate(directory: Path, size: int, seed: int = 20260831) -> Path:
    """Write `size` letters into `directory`. Deterministic for a given seed."""
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(size):
        rng = random.Random(seed + index)
        record = make_record(index, rng)
        (directory / f"{record['letter_id']}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return directory


def add_letter(directory: Path, index: int, seed: int = 20260831) -> str:
    record = make_record(index, random.Random(seed + index))
    (directory / f"{record['letter_id']}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return record["letter_id"]


def revise_letter(directory: Path, index: int, revision: int, seed: int = 20260831) -> str:
    record = make_record(index, random.Random(seed + index), revision=revision)
    (directory / f"{record['letter_id']}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return record["letter_id"]
