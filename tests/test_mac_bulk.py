import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/mac_bulk.py"
spec = importlib.util.spec_from_file_location("mac_bulk", SCRIPT)
bulk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bulk)


def test_import_ledger_prevents_reimport_after_consumer_removes_pdf(tmp_path):
    (tmp_path / "consume").mkdir()
    code = bulk.IMPORT_CODE.replace("Path('/mnt/user/data/paperless')", f"Path({str(tmp_path)!r})")
    code = code.replace("os.chown(stage,1000,100)", "# Ownership is assigned by root on Tower.")
    payload = b"%PDF-test-placeholder"
    first = subprocess.run([sys.executable, "-c", code], input=payload, capture_output=True, check=True)
    assert first.stdout.startswith(b"IMPORTED ")
    files = list((tmp_path / "consume").glob("*.pdf"))
    assert len(files) == 1 and files[0].read_bytes() == payload
    files[0].unlink()  # Simulate Paperless consuming the submitted file.
    second = subprocess.run([sys.executable, "-c", code], input=payload, capture_output=True, check=True)
    assert second.stdout.startswith(b"ALREADY_IMPORTED ")
    assert not list((tmp_path / "consume").glob("*.pdf"))
    marker = next((tmp_path / "bulk-imported").glob("*.json"))
    marker.write_text(json.dumps({"state": "pending"}))
    third = subprocess.run([sys.executable, "-c", code], input=payload, capture_output=True)
    assert third.returncode == 2
    assert third.stdout.startswith(b"PENDING_REVIEW ")
    assert not list((tmp_path / "consume").glob("*.pdf"))
