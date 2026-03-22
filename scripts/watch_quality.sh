#!/bin/bash
# Quick retrieval quality summary from comparison-log.jsonl
# Usage: bash watch_quality.sh [last N turns]

LOG="$HOME/.tag-context/comparison-log.jsonl"
N=${1:-20}

if [ ! -f "$LOG" ]; then
  echo "No comparison log found at $LOG"
  exit 1
fi

python3 - "$LOG" "$N" <<'EOF'
import json, sys
from datetime import datetime

log_path = sys.argv[1]
n = int(sys.argv[2])

with open(log_path) as f:
    entries = [json.loads(l) for l in f if l.strip()]

entries = entries[-n:]
total = len(entries)
zero_graph = sum(1 for e in entries if e['graph_assembly']['messages'] == 0 and e['linear_would_have']['messages'] > 0)
both_zero = sum(1 for e in entries if e['graph_assembly']['messages'] == 0 and e['linear_would_have']['messages'] == 0)
graph_wins = sum(1 for e in entries if e['graph_assembly']['tokens'] > e['linear_would_have']['tokens'])
linear_wins = sum(1 for e in entries if e['linear_would_have']['tokens'] > e['graph_assembly']['tokens'])
tied = total - graph_wins - linear_wins

print(f"\n=== ContextGraph Quality Report — last {total} turns ===")
print(f"  Zero-return (graph silent, linear had data): {zero_graph}/{total} ({100*zero_graph//total}%)")
print(f"  Both empty (OK — sparse turns):              {both_zero}/{total}")
print(f"  Graph > Linear tokens:                       {graph_wins}")
print(f"  Linear > Graph tokens:                       {linear_wins}")
print(f"  Tied:                                        {tied}")
print()
print("Turn-by-turn:")
for i, e in enumerate(entries):
    g = e['graph_assembly']
    l = e['linear_would_have']
    ts = e.get('timestamp','')[:19].replace('T',' ')
    flag = ''
    if g['messages'] == 0 and l['messages'] > 0:
        flag = ' ← MISS'
    elif g['tokens'] > l['tokens']:
        flag = ' ✓ graph wins'
    print(f"  Turn {i+1:2d} [{ts}] graph={g['messages']}msg/{g['tokens']}tok (r:{g['recency']},t:{g['topic']}) | linear={l['messages']}msg/{l['tokens']}tok{flag}")

print()
if zero_graph > 0:
    miss_rate = 100*zero_graph//total
    if miss_rate >= 30:
        print(f"  ⚠️  MISS RATE {miss_rate}% — retrieval degraded. Check envelope pollution + tags.")
    else:
        print(f"  ⚡ Miss rate {miss_rate}% — acceptable but watch it.")
else:
    print("  ✅ No retrieval misses in this window.")
EOF
