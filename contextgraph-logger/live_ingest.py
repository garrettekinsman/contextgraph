#!/usr/bin/env python3
"""
live_ingest.py — Live turn logging shim for OpenClaw → ContextGraph.

Called after each OpenClaw turn to POST a single message pair to /ingest.
Thin, fast, no state beyond what the harvester manages.

Usage — JSON on stdin:
  echo '{"session_id":"abc","user_text":"hi","assistant_text":"hello","timestamp":1234567890}' | python3 live_ingest.py

Usage — CLI args:
  python3 live_ingest.py --session-id abc --user-text "hi" --assistant-text "hello"

Usage — Python import:
  from live_ingest import ingest_turn
  ingest_turn(session_id="abc", user_text="hi", assistant_text="hello")

Author: Agent: Mei (梅) — Tsinghua KEG Lab
"""

import argparse
import json
import sys
import time
from typing import Optional

import requests

from config import SERVER_URL, REQUEST_TIMEOUT


def ingest_turn(
    session_id: str,
    user_text: str,
    assistant_text: str,
    timestamp: Optional[float] = None,
    external_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict:
    """
    POST a single turn to ContextGraph /ingest.

    Parameters:
        session_id: OpenClaw session key
        user_text: The user's message text
        assistant_text: The assistant's response text
        timestamp: Unix timestamp (defaults to now)
        external_id: Optional stable ID for deduplication
        user_id: Optional user identifier

    Returns:
        dict with 'ok' (bool), 'status_code' (int or None), 'error' (str or None)
    """
    if timestamp is None:
        timestamp = time.time()

    payload: dict = {
        "session_id": session_id,
        "user_text": user_text[:4000],          # cap to avoid API limits
        "assistant_text": assistant_text[:4000],
        "timestamp": timestamp,
    }
    if external_id:
        payload["external_id"] = external_id
    if user_id:
        payload["user_id"] = user_id

    try:
        r = requests.post(
            f"{SERVER_URL}/ingest",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return {"ok": True, "status_code": r.status_code, "error": None}
    except requests.HTTPError as e:
        return {"ok": False, "status_code": e.response.status_code if e.response else None, "error": str(e)}
    except requests.RequestException as e:
        return {"ok": False, "status_code": None, "error": str(e)}


def _from_stdin() -> Optional[dict]:
    """Try to read JSON payload from stdin (non-blocking check)."""
    if not sys.stdin.isatty():
        try:
            data = json.load(sys.stdin)
            return data
        except (json.JSONDecodeError, OSError):
            pass
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Post a single OpenClaw turn to ContextGraph"
    )
    parser.add_argument("--session-id", default="cli")
    parser.add_argument("--user-text", default="")
    parser.add_argument("--assistant-text", default="")
    parser.add_argument("--timestamp", type=float, default=None)
    parser.add_argument("--external-id", default=None)
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress output on success")
    args = parser.parse_args()

    # Prefer stdin JSON over CLI args
    stdin_data = _from_stdin()
    if stdin_data:
        session_id = stdin_data.get("session_id", args.session_id)
        user_text = stdin_data.get("user_text", args.user_text)
        assistant_text = stdin_data.get("assistant_text", args.assistant_text)
        timestamp = stdin_data.get("timestamp", args.timestamp)
        external_id = stdin_data.get("external_id", args.external_id)
        user_id = stdin_data.get("user_id", args.user_id)
    else:
        session_id = args.session_id
        user_text = args.user_text
        assistant_text = args.assistant_text
        timestamp = args.timestamp
        external_id = args.external_id
        user_id = args.user_id

    if not user_text and not assistant_text:
        print("ERROR: no content to ingest (provide --user-text/--assistant-text or JSON on stdin)",
              file=sys.stderr)
        sys.exit(1)

    result = ingest_turn(
        session_id=session_id,
        user_text=user_text,
        assistant_text=assistant_text,
        timestamp=timestamp,
        external_id=external_id,
        user_id=user_id,
    )

    if result["ok"]:
        if not args.quiet:
            print(f"ok — ingested turn for session={session_id!r} (HTTP {result['status_code']})")
        sys.exit(0)
    else:
        print(f"ERROR: {result['error']} (HTTP {result['status_code']})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
