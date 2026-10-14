from pathlib import Path

import pytest
from smart_traffic_backend.operations import ObservationStore

from scripts.pilot_readiness import dataset_evidence, software_tests
from scripts.restore_observations import restore


def test_junit_cannot_pass_empty_or_failed_evidence(tmp_path: Path) -> None:
    path = tmp_path / "tests.xml"
    assert software_tests(path)["status"] == "NOT_VERIFIED"
    path.write_text("<testsuites/>", encoding="utf-8")
    assert software_tests(path)["status"] == "FAIL"
    path.write_text("<testsuite><testcase><failure/></testcase></testsuite>", encoding="utf-8")
    assert software_tests(path)["status"] == "FAIL"
    path.write_text(
        "<testsuite><testcase/><testcase><skipped/></testcase></testsuite>", encoding="utf-8"
    )
    result = software_tests(path)
    assert result["status"] == "PASS" and result["skipped"] == 1
    assert dataset_evidence(None)["status"] == "NOT_VERIFIED"
    assert dataset_evidence(tmp_path / "missing")["status"] == "FAIL"


def test_restore_keeps_site_and_refuses_overwrite(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.sqlite3", site_id="site-A")
    store.initialize()
    backup = tmp_path / "backups" / store.backup()
    restored = tmp_path / "restored.sqlite3"
    restore(backup, restored)
    with ObservationStore(restored).connect() as connection:
        assert connection.execute("SELECT id FROM site").fetchone()[0] == "site-A"
    with pytest.raises(FileExistsError):
        restore(backup, restored)
    with pytest.raises(ValueError, match="different site"):
        ObservationStore(restored, site_id="site-B").initialize()
