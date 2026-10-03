#!/usr/bin/env python3
"""Mac operator workflow: fetch, analyse into review, then import explicitly approved PDFs."""

import argparse
import fcntl
import hashlib
import html
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import riordino as r  # noqa: E402


def digest(path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def process(workspace, host, remote_inbox, key_file):
    inbox = workspace / "Inbox"
    subprocess.run(
        [
            "rsync",
            "-rt",
            "--no-links",
            "--include=*/",
            "--include=*.pdf",
            "--include=*.PDF",
            "--exclude=*",
            f"{host}:{remote_inbox.rstrip('/')}/",
            f"{inbox}/",
        ],
        check=True,
    )
    # Key lives only in this process; no credential file is copied into the workspace.
    os.environ["RIORDINO_LOCAL_KEY"] = key_file.read_text().strip()
    for source in sorted(inbox.rglob("*")):
        if not source.is_file() or source.suffix.lower() != ".pdf":
            continue
        if time.time() - source.stat().st_mtime < 30:
            print(f"Waiting for scan to finish: {source.name}. Run again in 30 seconds.")
            continue
        batch_id = digest(source)
        review = workspace / "Review" / batch_id
        if (review / "complete.json").exists():
            print(f"Already prepared: {source.name}")
            continue
        original = workspace / "Originals" / f"{batch_id}.pdf"
        if not original.exists():
            shutil.copy2(source, original)
        if digest(original) != batch_id:
            raise RuntimeError("Original checksum mismatch; refusing to process.")
        stage = Path(tempfile.mkdtemp(prefix=f"{batch_id[:12]}-", dir=workspace / "Work"))
        options = r.PipelineOptions(
            input_paths=[original],
            output_dir=stage,
            blank_threshold=0.001,
            dpi=150,
            provider="openai",
            base_url="http://127.0.0.1:8003/v1",
            model="gemma4-e4b-local",
            api_key_env="RIORDINO_LOCAL_KEY",
            request_timeout=300,
            batch_size=1,
            llm_workers=1,
            analysis_detail="concise",
            max_retries=0,
            dry_run=False,
            languages=["en", "de", "fr", "it"],
            skip_blanks=True,
            skip_rotation=True,
            skip_ordering=True,
            save_steps=True,
        )
        r.check_dependencies(options)
        r.run_pipeline(options)
        files = sorted(stage.glob("*.pdf"))
        if not files:
            raise RuntimeError("No output PDFs produced; original retained.")
        links = "".join(
            f'<li><a href="{html.escape(p.name, quote=True)}">{html.escape(p.name)}</a></li>' for p in files
        )
        (stage / "Review.html").write_text(
            '<!doctype html><meta charset="utf-8"><title>Review bulk scan</title>'
            "<h1>Review split documents</h1><p>Source: " + html.escape(source.name) + "</p>"
            "<p>Check every page and boundary. Copy only approved PDFs into the Approved folder, "
            "then run Import Approved Scans. Originals are preserved.</p><ul>" + links + "</ul>"
        )
        (stage / "complete.json").write_text(
            json.dumps({"source": source.name, "sha256": batch_id, "files": [p.name for p in files]}, indent=2)
        )
        stage.rename(review)
        print(f"Ready for review: {review}")
    print(f"Review folder: {workspace / 'Review'}")


IMPORT_CODE = """import sys,os,json,hashlib
from pathlib import Path
raw=sys.stdin.buffer.read()
sha=hashlib.sha256(raw).hexdigest()
base=Path('/mnt/user/data/paperless')
ledger=base/'bulk-imported'
ledger.mkdir(exist_ok=True)
marker=ledger/(sha+'.json')
if marker.exists():
 state=json.loads(marker.read_text())['state']
 if state=='done':
  print('ALREADY_IMPORTED '+sha);sys.exit(0)
 print('PENDING_REVIEW '+sha);sys.exit(2)
# Exclusive intent prevents duplicate upload after interrupted imports.
with marker.open('x') as f:
 json.dump({'state':'pending'},f);f.flush();os.fsync(f.fileno())
stage=ledger/(sha+'.pdf')
with stage.open('xb') as f:
 f.write(raw);f.flush();os.fsync(f.fileno())
os.chmod(stage,0o644)
os.chown(stage,1000,100)
dest=base/'consume'/('bulk-'+sha+'.pdf')
if dest.exists():
 print('PENDING_REVIEW '+sha);sys.exit(2)
os.rename(stage,dest)
tmp=marker.with_suffix('.tmp')
with tmp.open('w') as f:
 json.dump({'state':'done','filename':dest.name},f);f.flush();os.fsync(f.fileno())
os.replace(tmp,marker)
print('IMPORTED '+sha)
"""


def import_approved(workspace, host):
    files = sorted(p for p in (workspace / "Approved").iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    if not files:
        print("No approved PDFs. Review the results first, then copy approved PDFs into Approved.")
        return
    print("\n".join(p.name for p in files))
    if input(f"Import these {len(files)} reviewed PDFs into Paperless? Type IMPORT: ") != "IMPORT":
        print("Nothing imported.")
        return
    for path in files:
        with path.open("rb") as f:
            subprocess.run(
                ["ssh", "-o", "BatchMode=yes", host, "python3 -c " + shlex.quote(IMPORT_CODE)], stdin=f, check=True
            )
        target = workspace / "Imported" / f"{digest(path)}.pdf"
        if not target.exists():
            shutil.copy2(path, target)
        # Retain Approved copies; remote ledger makes later runs idempotent.
    print("Submitted to Paperless. Confirm processing in Paperless; all local copies retained.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["process", "import"])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--host", default="root@tower.local")
    parser.add_argument("--remote-inbox", default="/mnt/user/data/paperless/bulk-inbox")
    parser.add_argument("--key-file", type=Path, default=Path.home() / "Library/Application Support/gemma4-e4b/api.key")
    args = parser.parse_args()
    for name in ["Inbox", "Originals", "Review", "Work", "Approved", "Imported"]:
        (args.workspace / name).mkdir(parents=True, exist_ok=True)
    with (args.workspace / ".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another bulk scan operation is running.") from None
        if args.action == "process":
            process(args.workspace, args.host, args.remote_inbox, args.key_file)
        else:
            import_approved(args.workspace, args.host)


if __name__ == "__main__":
    main()
