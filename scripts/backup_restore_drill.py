"""Dump a Postgres database, encrypt the dump, restore it, and compare.

The source database is left in place. The restore target is created for the
check and dropped before the script exits. The dump is encrypted with
AES-256-GCM and removed with the temporary directory. Production retention
(daily dump, 30 days, one monthly copy for 12 months) is an operator schedule;
this script is the restore drill.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HOST = os.environ.get("PGHOST", "127.0.0.1")
USER = os.environ.get("PGUSER", "echodesk")
SOURCE = os.environ.get("DRILL_SOURCE_DB", "echodesk")
RESTORE = os.environ.get("DRILL_RESTORE_DB", "echodesk_restore_drill")
MARKER = os.environ.get("DRILL_MARKER", "")


def _run(args: list[str], *, data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, input=data, check=True, capture_output=True)


def _psql(database: str, sql: str) -> str:
    completed = _run(
        ["psql", "-h", HOST, "-U", USER, "-d", database, "-v", "ON_ERROR_STOP=1", "-tAc", sql]
    )
    return completed.stdout.decode().strip()


def _admin(sql: str) -> None:
    _psql("postgres", sql)


def _fingerprint(database: str) -> str:
    tables = _psql(
        database,
        "SELECT c.relname || '|' || c.relrowsecurity || '|' || c.relforcerowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1",
    )
    policies = _psql(
        database,
        "SELECT tablename || '|' || policyname || '|' || cmd FROM pg_policies "
        "WHERE schemaname = 'public' ORDER BY tablename, policyname, cmd",
    )
    counts: list[str] = []
    for line in tables.splitlines():
        name = line.split("|", 1)[0]
        if not name.replace("_", "").isalnum():
            raise SystemExit(f"unexpected table name: {name}")
        count = _psql(database, f"SELECT count(*) FROM public.{name}")
        counts.append(f"{name}={count}")
    marker = ""
    if MARKER:
        safe = MARKER.replace("'", "''")
        marker = _psql(database, f"SELECT count(*) FROM tenants WHERE code = '{safe}'")
        if marker != "1":
            raise SystemExit(f"{database} marker count is {marker}")
    return "\n".join([tables, policies, *counts, f"marker={marker}"])


def _reset_restore() -> None:
    if RESTORE == SOURCE or not RESTORE.replace("_", "").isalnum():
        raise SystemExit("restore database name is not usable")
    _admin(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{RESTORE}' AND pid <> pg_backend_pid()"
    )
    _admin(f"DROP DATABASE IF EXISTS {RESTORE}")
    _admin(f"CREATE DATABASE {RESTORE} OWNER {USER}")


def main() -> None:
    if not SOURCE.replace("_", "").isalnum():
        raise SystemExit("source database name is not usable")
    before = _fingerprint(SOURCE)
    with tempfile.TemporaryDirectory(prefix="echodesk-backup-") as directory:
        plain = Path(directory) / "dump.custom"
        sealed = Path(directory) / "dump.custom.enc"
        _run(
            [
                "pg_dump",
                "-h",
                HOST,
                "-U",
                USER,
                "-d",
                SOURCE,
                "-Fc",
                "--no-owner",
                "-f",
                str(plain),
            ]
        )
        key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        payload = plain.read_bytes()
        ciphertext = nonce + AESGCM(key).encrypt(nonce, payload, None)
        if ciphertext[12:] == payload:
            raise SystemExit("dump was not encrypted")
        sealed.write_bytes(ciphertext)
        plain.write_bytes(AESGCM(key).decrypt(nonce, ciphertext[12:], None))
        _reset_restore()
        after = ""
        try:
            _run(
                [
                    "pg_restore",
                    "-h",
                    HOST,
                    "-U",
                    USER,
                    "-d",
                    RESTORE,
                    "--no-owner",
                    "--exit-on-error",
                    str(plain),
                ]
            )
            after = _fingerprint(RESTORE)
        finally:
            _admin(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{RESTORE}' AND pid <> pg_backend_pid()"
            )
            _admin(f"DROP DATABASE IF EXISTS {RESTORE}")
    if before != after:
        raise SystemExit("restored database does not match the source")
    print("backup restore drill passed")
    print(f"source={SOURCE} encrypted=aes-256-gcm")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr.decode(errors="replace"))
        raise SystemExit(exc.returncode) from exc
