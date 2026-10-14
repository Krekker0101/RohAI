"""Build an evidence inventory; never authorizes physical control."""

import argparse
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from traffic_vision.dataset.files import digest_file
from traffic_vision.dataset.inventory import inventory
from traffic_vision.dataset.release import open_release
from traffic_vision.dataset.validation import validate_dataset


def software_tests(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "NOT_VERIFIED", "reason": "JUnit evidence missing"}
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failures = sum(bool(list(case.iter("failure")) + list(case.iter("error"))) for case in cases)
    skipped = sum(bool(list(case.iter("skipped"))) for case in cases)
    return {
        "status": "PASS" if cases and failures == 0 else "FAIL",
        "cases": len(cases),
        "failed": failures,
        "skipped": skipped,
        "sha256": digest_file(path),
        "scope": "Software suite only; skipped physical tests remain unverified",
    }


def dataset_evidence(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"status": "NOT_VERIFIED", "reason": "No field release supplied"}
    try:
        release = open_release(path)
        root = path if path.is_dir() else path.parent
        validation = validate_dataset(release.dataset, root)
        field = release.dataset.purpose == "field"
        return {
            "status": "PASS" if validation.valid and field else "FAIL",
            "manifest_sha256": release.manifest_sha256,
            "field": field,
            "validation": validation.model_dump(mode="json"),
        }
    except (OSError, ValueError, PermissionError) as exc:
        return {"status": "FAIL", "reason": str(exc)}


def capture_evidence(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"status": "NOT_VERIFIED"}
    try:
        if path.stat().st_size > 65536:
            raise ValueError("Capture report exceeds size limit")
        evidence = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict) or evidence.get("scope") != "LOCAL_CAPTURE_ONLY":
            raise ValueError("Capture-only report required")
        return {
            "status": "MEASURED"
            if evidence.get("status") == "MEASURED"
            and evidence.get("frames", 0) >= 2
            and evidence.get("source_closed") is True
            else "FAIL",
            "sha256": digest_file(path),
            "evidence": evidence,
            "scope": "Capture only; full Vision and road accuracy remain NOT_VERIFIED",
        }
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "FAIL", "reason": str(exc)}


def build(
    root: Path, tests: Path, release: Path | None, capture: Path | None = None
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "product": "Observation-only intersection analytics pilot",
        "software_tests": software_tests(tests),
        "field_dataset": dataset_evidence(release),
        "media": inventory(root),
        "local_capture": capture_evidence(capture),
        "live_camera": {"status": "NOT_VERIFIED", "reason": "Recorded video is not a live test"},
        "physical_hil": {
            "status": "NOT_VERIFIED",
            "reason": "Independent real-device evidence required",
        },
        "customer_pilot": {
            "status": "NOT_VERIFIED",
            "reason": "Partner and measured acceptance required",
        },
        "road_effectiveness": "NOT_VERIFIED",
        "physical_activation": False,
        "note": "Inventory is not certification. "
        "Existing reports are never promoted to field proof.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--tests", type=Path, default=Path("artifacts/pilot/python-tests.xml"))
    parser.add_argument("--release", type=Path)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.root, args.tests, args.release, args.capture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    print(json.dumps({"software": result["software_tests"], "physical_activation": False}))


if __name__ == "__main__":
    main()
