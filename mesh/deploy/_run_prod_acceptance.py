#!/usr/bin/env python3
"""Run prod acceptance via SSH (paramiko)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("MESH_DEPLOY_HOST", "104.250.53.182")
PORT = int(os.environ.get("MESH_DEPLOY_PORT", "22341"))
USER = os.environ.get("MESH_DEPLOY_USER", "root")
PASSWORD = os.environ.get("MESH_DEPLOY_PASSWORD", "")
REMOTE_BASE = os.environ.get("MESH_DEPLOY_REMOTE_BASE", "/opt/geekpark-mesh")
CONTAINER = os.environ.get("MESH_CONTAINER", "geekpark-mesh")

MESH_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = MESH_ROOT / "eval"
REPORT_LOCAL = EVAL_DIR / "reports" / "FINAL_ACCEPTANCE_REPORT.prod.md"


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 7200) -> int:
    print(f"\n>>> {cmd}\n", flush=True)
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    for line in iter(stdout.readline, ""):
        print(line, end="", flush=True)
    err = stderr.read().decode("utf-8", errors="replace")
    if err.strip():
        print(err, file=sys.stderr, flush=True)
    return stdout.channel.recv_exit_status()


def upload_eval(sftp: paramiko.SFTPClient) -> None:
    remote_eval = f"{REMOTE_BASE}/eval"
    print(f"Uploading {EVAL_DIR} -> {remote_eval}", flush=True)

    def ensure_remote_dir(path: str) -> None:
        parts = path.strip("/").split("/")
        cur = ""
        for p in parts:
            cur += f"/{p}"
            try:
                sftp.stat(cur)
            except OSError:
                sftp.mkdir(cur)

    ensure_remote_dir(remote_eval)
    for local in EVAL_DIR.rglob("*"):
        rel = local.relative_to(EVAL_DIR).as_posix()
        remote = f"{remote_eval}/{rel}".replace("\\", "/")
        if local.is_dir():
            try:
                sftp.stat(remote)
            except OSError:
                sftp.mkdir(remote)
        else:
            ensure_remote_dir(str(Path(remote).parent).replace("\\", "/"))
            sftp.put(str(local), remote)


def main() -> int:
    if not PASSWORD:
        print("Set MESH_DEPLOY_PASSWORD", file=sys.stderr)
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting {USER}@{HOST}:{PORT}...", flush=True)
    client.connect(
        HOST,
        port=PORT,
        username=USER,
        password=PASSWORD,
        timeout=30,
        banner_timeout=30,
        auth_timeout=30,
    )
    print("Connected.", flush=True)

    sftp = client.open_sftp()
    upload_eval(sftp)
    sftp.close()

    code = run(
        client,
        f"set -e; docker cp {REMOTE_BASE}/eval {CONTAINER}:/srv/mesh/eval; "
        f"docker exec {CONTAINER} python -c \"from app import db; from eval.eval_lib import db_stats; c=db.connect(); print(db_stats(c))\"",
        timeout=120,
    )
    if code != 0:
        client.close()
        return code

    code = run(
        client,
        f"docker exec {CONTAINER} python eval/run_final_eval.py --corpus prod --e2e --followup --sse",
        timeout=7200,
    )
    if code != 0:
        client.close()
        return code

    REPORT_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    sftp = client.open_sftp()
    remote_report = f"{REMOTE_BASE}/eval/reports/FINAL_ACCEPTANCE_REPORT.md"
    try:
        sftp.get(remote_report, str(REPORT_LOCAL))
        print(f"\nReport saved: {REPORT_LOCAL}", flush=True)
    except OSError:
        # report may only exist inside container
        run(
            client,
            f"docker cp {CONTAINER}:/srv/mesh/eval/reports/FINAL_ACCEPTANCE_REPORT.md {remote_report}",
            timeout=60,
        )
        sftp.get(remote_report, str(REPORT_LOCAL))
        print(f"\nReport saved (from container): {REPORT_LOCAL}", flush=True)
    sftp.close()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
