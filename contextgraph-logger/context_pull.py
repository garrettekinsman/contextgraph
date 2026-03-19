#!/usr/bin/env python3
"""
context_pull.py — Query ContextGraph and return a system-prompt-ready context block.

Calls /assemble with a query string, formats the response as markdown
suitable for injection into an OpenClaw system prompt or heartbeat.

Usage:
  python3 context_pull.py "memory harvester not working"
  python3 context_pull.py --budget 1500 --json "maxrisk status"
  python3 context_pull.py --tags "maxrisk,trading" "portfolio review"

Python API:
  from context_pull import pull_context
  block = pull_context("memory harvester not working")

Author: Agent: Mei (梅) — Tsinghua KEG Lab
"""

import argparse
import json
import sys
import time
from typing import List, Optional

import requests

from config import SERVER_URL, DEFAULT_TOKEN_BUDGET, REQUEST_TIMEOUT


# ── Core ──────────────────────────────────────────────────────────────────────

def pull_context(
    query: str,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    tags: Optional[List[str]] = None,
    session_id: Optional[str] = None,
) -> dict:
    """
    Assemble context from ContextGraph for a query.

    Parameters:
        query: The user's incoming message or topic
        token_budget: Max tokens for assembled context
        tags: Optional explicit tags to include
        session_id: Optional session context

    Returns:
        {
            "context_block": str,       # Formatted markdown (empty if nothing found)
            "message_count": int,
            "total_tokens": int,
            "tags_used": List[str],
            "ok": bool,
            "error": Optional[str],
        }
    """
    payload: dict = {
        "user_text": query,
        "token_budget": token_budget,
    }
    if tags:
        payload["tags"] = tags
    if session_id:
        payload["session_id"] = session_id

    try:
        r = requests.post(
            f"{SERVER_URL}/assemble",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        return {
            "context_block": "",
            "message_count": 0,
            "total_tokens": 0,
            "tags_used": [],
            "ok": False,
            "error": str(e),
        }

    # Format the response into an injectable markdown block
    context_block = _format_context_block(data)

    return {
        "context_block": context_block,
        "message_count": len(data.get("messages", [])),
        "total_tokens": data.get("total_tokens", 0),
        "tags_used": data.get("tags_used", []),
        "ok": True,
        "error": None,
    }


def _format_timestamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3].rsplit(" ", 1)[0] + "..."


def _format_context_block(data: dict) -> str:
    """Format /assemble response as injectable markdown."""
    messages = data.get("messages", [])
    if not messages:
        return ""

    tags_used = data.get("tags_used", [])
    total_tokens = data.get("total_tokens", 0)

    lines = [
        "## Retrieved Context",
        "",
        f"*Assembled by ContextGraph — {len(messages)} messages, ~{total_tokens} tokens*",
    ]
    if tags_used:
        lines.append(f"*Query tags: [{', '.join(tags_used[:10])}]*")
    lines.append("")

    for msg in messages:
        ts = msg.get("timestamp", 0)
        date_str = _format_timestamp(ts) if ts else "unknown"

        user_text = msg.get("user_text", "").strip()
        assistant_text = msg.get("assistant_text", "").strip()
        tags = msg.get("tags", [])

        # Extract title from user_text
        if user_text.startswith("["):
            idx = user_text.find("]")
            title = user_text[idx + 1:].strip() if idx > 0 else user_text
        else:
            title = _truncate(user_text.split("\n")[0], 60)

        lines.append(f"### [{date_str}] {title}")
        if tags:
            lines.append(f"*Tags: {', '.join(tags[:5])}*")
        if assistant_text:
            lines.append("")
            lines.append(_truncate(assistant_text, 400))
        lines.append("")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pull context from ContextGraph for a query"
    )
    parser.add_argument("query", nargs="?", default="",
                        help="Query text")
    parser.add_argument("--budget", type=int, default=DEFAULT_TOKEN_BUDGET,
                        help=f"Token budget (default: {DEFAULT_TOKEN_BUDGET})")
    parser.add_argument("--tags", default="",
                        help="Comma-separated explicit tags")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Output raw JSON")
    parser.add_argument("--stats-only", action="store_true",
                        help="Print stats only, no context block")
    args = parser.parse_args()

    if not args.query:
        print("Usage: context_pull.py 'your query here'", file=sys.stderr)
        sys.exit(1)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None

    result = pull_context(
        query=args.query,
        token_budget=args.budget,
        tags=tags,
        session_id=args.session_id,
    )

    if not result["ok"]:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    if args.as_json:
        print(json.dumps(result, indent=2))
    elif args.stats_only:
        print(f"Query: {args.query!r}")
        print(f"Messages: {result['message_count']}")
        print(f"Tokens: {result['total_tokens']}")
        print(f"Tags: {result['tags_used']}")
    else:
        print(f"Query: {args.query!r}")
        print(f"Budget: {args.budget} tokens")
        print("=" * 60)
        print()
        if result["context_block"]:
            print(result["context_block"])
        else:
            print("(no relevant context found)")
        print()
        print(f"Stats: {result['message_count']} messages, "
              f"~{result['total_tokens']} tokens, tags={result['tags_used']}")


if __name__ == "__main__":
    main()
