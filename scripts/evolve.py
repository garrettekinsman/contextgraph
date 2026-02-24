"""
evolve.py — Evolve a GeneticTagger from logged interaction data.

Usage:
  python3 scripts/evolve.py [--tags TAG [TAG ...]] [--pop 50] [--gen 20]
                             [--since YYYY-MM-DD] [--out model.pkl] [--verbose]

The evolved tagger is saved as a pickle and can be loaded by replay.py
or used alongside the baseline StructuredProgramTagger in an ensemble.
"""

import argparse
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import iter_records
from gp_tagger import evolve_genetic_tagger, GeneticTagger
from tagger import CORE_TAGS


def main() -> None:
    parser = argparse.ArgumentParser(description="Evolve a GP-based tagger")
    parser.add_argument("--tags",  nargs="+", help="Tags to evolve (default: all core)")
    parser.add_argument("--pop",   type=int, default=50, help="Population size per tag")
    parser.add_argument("--gen",   type=int, default=20, help="Generations per tag")
    parser.add_argument("--since", metavar="YYYY-MM-DD", help="Training data start date")
    parser.add_argument("--until", metavar="YYYY-MM-DD")
    parser.add_argument("--out",   default="data/gp-tagger.pkl",
                        help="Output path for evolved tagger")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    tags = args.tags or sorted(CORE_TAGS)
    records = list(iter_records(start_date=args.since, end_date=args.until))

    if not records:
        print("No interaction records found. Run harvester.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"Training on {len(records)} interactions → {len(tags)} tags")
    print(f"  pop={args.pop}  gen={args.gen}")
    print(f"  Tags: {tags}")
    t0 = time.time()

    tagger = evolve_genetic_tagger(
        records=records,
        tags=tags,
        pop_size=args.pop,
        n_gen=args.gen,
        verbose=args.verbose,
    )

    elapsed = time.time() - t0
    print(f"\nEvolution complete in {elapsed:.1f}s")
    print(f"Tagger ID: {tagger.tagger_id}")
    print("\nPer-tag fitness:")
    for pred in sorted(tagger.predictors, key=lambda p: -p.fitness):
        bar = "█" * int(pred.fitness * 20)
        print(f"  {pred.tag:<25} {pred.fitness:.3f} {bar}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        pickle.dump(tagger, f)
    print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    main()
