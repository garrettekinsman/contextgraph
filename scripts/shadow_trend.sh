#!/usr/bin/env bash
# shadow_trend.sh — hourly shadow evaluation + trend logging
# Appends one JSON record to data/shadow-trend.jsonl each run.
# Designed to be called from cron; safe to run manually too.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO/venv"
TREND="$REPO/data/shadow-trend.jsonl"

# Activate venv
source "$VENV/bin/activate"

# Run shadow evaluation (unbounded budget — we're measuring, not injecting)
python3 "$REPO/scripts/shadow.py" --budget 999999 --report > /tmp/shadow_run.log 2>&1

# Extract key metrics from the updated report and append a trend record
REPO="$REPO" python3 - <<'EOF'
import json, datetime, pathlib, os

repo = pathlib.Path(os.environ["REPO"])
report_path = repo / "data" / "shadow-report.json"
trend_path  = repo / "data" / "shadow-trend.jsonl"

with open(report_path) as f:
    r = json.load(f)

record = {
    "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    "interactions": r.get("total_interactions"),
    "topic_retrieval_rate": r.get("graph", {}).get("topic_retrieval_rate"),
    "mean_density": r.get("graph", {}).get("mean_density"),
    "mean_topic_msgs": r.get("graph", {}).get("mean_topic_msgs"),
    "mean_total_msgs": r.get("graph", {}).get("mean_total_msgs"),
    "novel_msgs_total": r.get("graph", {}).get("novel_msgs_total"),
    "reframing_rate": r.get("graph", {}).get("reframing_rate", None),
    "unique_tags": len(r.get("graph", {}).get("unique_tags_surfaced", [])),
    "token_budget": r.get("token_budget"),
}

with open(trend_path, "a") as f:
    f.write(json.dumps(record) + "\n")

print(f"[shadow_trend] {record['ts']} — {record['interactions']} interactions, "
      f"topic_retrieval={record['topic_retrieval_rate']:.1%}, "
      f"novel_msgs={record['novel_msgs_total']}")
EOF
