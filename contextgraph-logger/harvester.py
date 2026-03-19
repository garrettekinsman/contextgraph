#!/usr/bin/env python3
"""
harvester.py — Bridge OpenClaw sessions and memory files into ContextGraph.

Two harvesters in one:
  1. Session harvester: crawls ~/.openclaw/data/messages.db → POST to /ingest
  2. Memory file harvester: crawls ~/.openclaw/workspace/memory/ → POST to /ingest

State files track progress:
  - data/ingest-state.json  — last ingested timestamp per session_id
  - data/memory-state.json  — content hash per memory file path

Usage:
  python3 harvester.py [--dry-run] [--verbose] [--sessions-only] [--memory-only]

Author: Agent: Mei (梅) — Tsinghua KEG Lab
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from config import (
    SERVER_URL,
    MESSAGES_DB,
    MEMORY_DIRS,
    WORKSPACE,
    INGEST_STATE_FILE,
    MEMORY_STATE_FILE,
    MAX_CONTENT_PER_FILE,
    REQUEST_TIMEOUT,
)

# ── Injection sanitization ────────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    (r"(?i)ignore\s+(previous|all|prior|above|earlier)\s+instructions?", "[REDACTED:instruction-override]"),
    (r"(?i)disregard\s+(previous|all|prior|above|earlier)\s+instructions?", "[REDACTED:instruction-override]"),
    (r"(?i)you\s+are\s+now\s+(?:a\s+)?(?:an?\s+)?[a-z]+", "[REDACTED:role-override]"),
    (r"(?i)new\s+instruction\s*:", "[REDACTED:instruction-inject]"),
    (r"(?i)system\s+prompt\s*:", "[REDACTED:system-inject]"),
    (r"(?i)(?:^|\n)\s*\[SYSTEM\]\s*:", "[REDACTED:system-tag]"),
    (r"(?i)(?:^|\n)\s*<\|system\|>", "[REDACTED:system-token]"),
]


def _sanitize(text: str) -> str:
    """Strip prompt injection patterns from content."""
    for pattern, replacement in _INJECTION_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


# ── State helpers ─────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _post_ingest(payload: dict, dry_run: bool, verbose: bool) -> bool:
    """POST to /ingest. Returns True on success."""
    if dry_run:
        if verbose:
            print(f"    [DRY-RUN] POST /ingest session_id={payload.get('session_id')!r} "
                  f"external_id={payload.get('external_id')!r}")
        return True
    try:
        r = requests.post(
            f"{SERVER_URL}/ingest",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        if verbose:
            print(f"    ERROR posting to /ingest: {e}")
        return False


# ── Session harvester ─────────────────────────────────────────────────────────

def _pair_messages(rows: List[Tuple]) -> List[dict]:
    """
    Pair user/assistant rows into (user_text, assistant_text) records.

    rows: [(id, session_id, role, content, timestamp), ...]
    Returns list of dicts ready for /ingest.
    """
    # Group by session_id, sorted by timestamp
    sessions: Dict[str, List[Tuple]] = {}
    for row in rows:
        sid = row[1]
        sessions.setdefault(sid, []).append(row)

    records = []
    for sid, msgs in sessions.items():
        msgs.sort(key=lambda r: r[4])  # sort by timestamp
        # Pair consecutive user/assistant turns
        i = 0
        while i < len(msgs):
            row = msgs[i]
            _, session_id, role, content, timestamp = row
            if role == "user":
                # Look for next assistant message
                if i + 1 < len(msgs) and msgs[i + 1][2] == "assistant":
                    _, _, _, asst_content, _ = msgs[i + 1]
                    records.append({
                        "session_id": session_id,
                        "user_text": _sanitize(content[:2000]),
                        "assistant_text": _sanitize(asst_content[:2000]),
                        "timestamp": timestamp,
                        "external_id": f"msg:{row[0]}",  # stable id from DB row id
                    })
                    i += 2
                else:
                    # Unpaired user message — ingest with empty assistant
                    records.append({
                        "session_id": session_id,
                        "user_text": _sanitize(content[:2000]),
                        "assistant_text": "",
                        "timestamp": timestamp,
                        "external_id": f"msg:{row[0]}",
                    })
                    i += 1
            else:
                i += 1  # skip orphan assistant messages

    return records


def harvest_sessions(dry_run: bool = False, verbose: bool = False) -> dict:
    """Crawl messages.db and POST new sessions to /ingest."""
    stats = {"records_found": 0, "records_posted": 0, "records_skipped": 0, "errors": 0}

    if not MESSAGES_DB.exists() or MESSAGES_DB.stat().st_size == 0:
        if verbose:
            print("  messages.db is empty or missing — skipping session harvest")
        return stats

    state = _load_json(INGEST_STATE_FILE)

    try:
        conn = sqlite3.connect(str(MESSAGES_DB))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Check table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        if not cur.fetchone():
            if verbose:
                print("  messages table not found in DB — skipping session harvest")
            conn.close()
            return stats

        cur.execute("SELECT id, session_id, role, content, timestamp FROM messages ORDER BY timestamp ASC")
        rows = [tuple(r) for r in cur.fetchall()]
        conn.close()
    except sqlite3.Error as e:
        if verbose:
            print(f"  DB error: {e}")
        stats["errors"] += 1
        return stats

    records = _pair_messages(rows)
    stats["records_found"] = len(records)

    if verbose:
        print(f"  Session harvest: {len(records)} message pairs found")

    for rec in records:
        ext_id = rec["external_id"]
        session_id = rec["session_id"]
        ts = rec["timestamp"]

        # Skip if already ingested (track by external_id in state)
        if ext_id in state.get("ingested_ids", {}):
            stats["records_skipped"] += 1
            continue

        if verbose:
            print(f"  → {session_id} ts={ts} ext={ext_id}")

        ok = _post_ingest(rec, dry_run, verbose)
        if ok:
            if not dry_run:
                state.setdefault("ingested_ids", {})[ext_id] = ts
            stats["records_posted"] += 1
        else:
            stats["errors"] += 1

    if not dry_run and stats["records_posted"] > 0:
        _save_json(INGEST_STATE_FILE, state)

    return stats


# ── Memory file harvester ─────────────────────────────────────────────────────

def _hash_file(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _parse_frontmatter_tags(text: str) -> List[str]:
    """Extract YAML frontmatter tags list."""
    if not text.startswith("---"):
        return []
    lines = text.split("\n")
    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return []
    for line in lines[1:end]:
        k, _, v = line.partition(":")
        if k.strip() == "tags":
            v = v.strip()
            if v.startswith("["):
                return [t.strip() for t in v[1:-1].split(",") if t.strip()]
    return []


def _extract_title(content: str) -> str:
    for line in content.split("\n")[:10]:
        if line.startswith("# "):
            return line[2:].strip()
    return "(untitled)"


def _infer_category(relpath: str) -> str:
    if "/daily/" in relpath:
        return "daily-log"
    if "/projects/" in relpath:
        return "project"
    if "/decisions/" in relpath:
        return "decision"
    if "/contacts/" in relpath:
        return "contact"
    return "memory"


def harvest_memory(dry_run: bool = False, verbose: bool = False, force: bool = False) -> dict:
    """Crawl memory/ markdown files and POST changed files to /ingest."""
    stats = {"files_found": 0, "files_posted": 0, "files_skipped": 0, "errors": 0}
    state = _load_json(MEMORY_STATE_FILE)

    files = []
    for d in MEMORY_DIRS:
        if d.exists():
            files.extend(sorted(d.glob("**/*.md")))

    stats["files_found"] = len(files)

    if verbose:
        print(f"  Memory harvest: {len(files)} .md files found across {len(MEMORY_DIRS)} dirs")

    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            if verbose:
                print(f"  ERROR reading {path}: {e}")
            stats["errors"] += 1
            continue

        if not content.strip():
            stats["files_skipped"] += 1
            continue

        relpath = str(path.relative_to(WORKSPACE))
        content_hash = _hash_file(content)

        # Skip if unchanged
        if not force and state.get(relpath) == content_hash:
            stats["files_skipped"] += 1
            continue

        category = _infer_category(relpath)
        title = _extract_title(content)
        tags = _parse_frontmatter_tags(content)
        external_id = f"memory-file:{relpath}"

        # user_text = query-friendly label; assistant_text = content blob
        user_text = f"[{category}] {title}"
        assistant_text = _sanitize(content[:MAX_CONTENT_PER_FILE])

        payload = {
            "session_id": f"memory-harvest:{category}",
            "user_text": user_text,
            "assistant_text": assistant_text,
            "timestamp": path.stat().st_mtime,
            "external_id": external_id,
        }

        if verbose:
            print(f"  → {relpath} ({len(content)} chars, tags={tags})")

        ok = _post_ingest(payload, dry_run, verbose)
        if ok:
            if not dry_run:
                state[relpath] = content_hash
            stats["files_posted"] += 1
        else:
            stats["errors"] += 1

    if not dry_run and stats["files_posted"] > 0:
        _save_json(MEMORY_STATE_FILE, state)

    return stats


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Harvest OpenClaw data into ContextGraph")
    parser.add_argument("--dry-run", action="store_true", help="Print actions, don't write")
    parser.add_argument("--verbose", "-v", action="store_true", help="Detailed output")
    parser.add_argument("--force", action="store_true", help="Re-ingest all files regardless of hash")
    parser.add_argument("--sessions-only", action="store_true", help="Only harvest session DB")
    parser.add_argument("--memory-only", action="store_true", help="Only harvest memory files")
    args = parser.parse_args()

    print("ContextGraph Harvester")
    print("=" * 40)
    print(f"Server: {SERVER_URL}")
    if args.dry_run:
        print("[DRY-RUN MODE]")
    print()

    do_sessions = not args.memory_only
    do_memory = not args.sessions_only

    if do_sessions:
        print("── Session DB harvest ──")
        s = harvest_sessions(dry_run=args.dry_run, verbose=args.verbose)
        print(f"  Found: {s['records_found']}  Posted: {s['records_posted']}  "
              f"Skipped: {s['records_skipped']}  Errors: {s['errors']}")
        print()

    if do_memory:
        print("── Memory file harvest ──")
        m = harvest_memory(dry_run=args.dry_run, verbose=args.verbose, force=args.force)
        print(f"  Found: {m['files_found']}  Posted: {m['files_posted']}  "
              f"Skipped: {m['files_skipped']}  Errors: {m['errors']}")
        print()

    if args.dry_run:
        print("[DRY-RUN — no changes written to state files]")


if __name__ == "__main__":
    main()
