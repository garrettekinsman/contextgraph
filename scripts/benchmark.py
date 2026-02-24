"""
benchmark.py — Compare baseline vs GP tagger on real interaction data.

Replays logged interactions through both taggers, assembles context for each,
and measures quality scores side-by-side.

Usage:
  python3 scripts/benchmark.py [--gp-model data/gp-tagger.pkl] [--verbose]
"""

import argparse
import pickle
import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import iter_records, InteractionRecord
from store import Message, MessageStore
from features import extract_features
from tagger import assign_tags as baseline_assign, StructuredProgramTagger
from assembler import ContextAssembler, AssemblyResult
from quality import QualityAgent
from gp_tagger import evolve_genetic_tagger, GeneticTagger


def run_tagger_on_records(
    tagger_id: str,
    assign_fn,   # (features, u, a) -> List[str]
    records: List[InteractionRecord],
    qa: QualityAgent,
    verbose: bool = False,
) -> dict:
    """Run a tagger through all records, building a store and measuring quality."""
    import tempfile
    db = tempfile.mktemp(suffix=".db")
    store = MessageStore(db_path=db)
    assembler = ContextAssembler(store, token_budget=4000)

    recent_user_texts: List[str] = []
    total_density = 0.0
    total_reframing = 0.0
    n = 0

    for record in records:
        features = extract_features(record.user_text, record.assistant_text)
        tags = assign_fn(features, record.user_text, record.assistant_text)

        # Store the message with these tags
        msg = Message.new(
            session_id=record.session_id,
            user_id=record.user_id,
            timestamp=record.interaction_at,
            user_text=record.user_text,
            assistant_text=record.assistant_text,
            tags=tags,
            token_count=record.token_count,
        )
        store.add_message(msg)

        # Assemble context for this message (simulates what the responder would get)
        result = assembler.assemble(record.user_text, tags)

        # Score
        recent_user_texts.append(record.user_text)
        if len(recent_user_texts) > 10:
            recent_user_texts = recent_user_texts[-10:]

        iq = qa.record(
            tagger_id=tagger_id,
            assembly_result=result,
            user_text=record.user_text,
            recent_user_texts=recent_user_texts,
        )
        total_density += iq.context_density
        total_reframing += iq.reframing_signal
        n += 1

        if verbose:
            ts = time.strftime("%H:%M", time.localtime(record.interaction_at))
            print(f"  [{ts}] tags={tags[:3]}... density={iq.context_density:.2f} "
                  f"refr={iq.reframing_signal:.2f} comp={iq.composite:.2f}")

    return {
        "tagger_id": tagger_id,
        "n": n,
        "mean_density": total_density / n if n else 0,
        "mean_reframing": total_reframing / n if n else 0,
        "fitness": qa.fitness(tagger_id),
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark baseline vs GP tagger")
    parser.add_argument("--gp-model", help="Path to evolved GP tagger pickle")
    parser.add_argument("--since", metavar="YYYY-MM-DD")
    parser.add_argument("--pop", type=int, default=30, help="GP pop size if evolving fresh")
    parser.add_argument("--gen", type=int, default=10, help="GP generations if evolving fresh")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    records = list(iter_records(start_date=args.since))
    if not records:
        print("No records. Run harvester.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"Benchmarking on {len(records)} interactions\n")

    # Split: first 70% training, last 30% test
    split = int(len(records) * 0.7)
    train_records = records[:split]
    test_records = records[split:]
    print(f"  Train: {len(train_records)}  Test: {len(test_records)}\n")

    # Evolve or load GP tagger
    if args.gp_model and Path(args.gp_model).exists():
        with open(args.gp_model, "rb") as f:
            gp_tagger = pickle.load(f)
        print(f"Loaded GP tagger: {gp_tagger.tagger_id}")
    else:
        print(f"Evolving GP tagger (pop={args.pop}, gen={args.gen})...")
        gp_tagger = evolve_genetic_tagger(
            records=train_records,
            pop_size=args.pop, n_gen=args.gen,
        )
        print(f"  → {gp_tagger.tagger_id}\n")

    # Quality agents (separate state for clean comparison)
    import tempfile
    qa_baseline = QualityAgent(state_path=tempfile.mktemp(suffix=".json"))
    qa_gp = QualityAgent(state_path=tempfile.mktemp(suffix=".json"))

    # Run baseline
    print("── Baseline tagger ──")
    baseline_result = run_tagger_on_records(
        "baseline-v0",
        baseline_assign,
        test_records,
        qa_baseline,
        verbose=args.verbose,
    )

    # Run GP tagger
    print("\n── GP tagger ──")
    gp_result = run_tagger_on_records(
        gp_tagger.tagger_id,
        lambda f, u, a: gp_tagger.assign(f, u, a).tags,
        test_records,
        qa_gp,
        verbose=args.verbose,
    )

    # Run ensemble (baseline + GP)
    print("\n── Ensemble (baseline + GP) ──")
    from ensemble import EnsembleTagger
    from tagger import StructuredProgramTagger
    baseline_tagger = StructuredProgramTagger()

    ensemble = EnsembleTagger(vote_threshold=0.3)
    ensemble.register("baseline-v0",
                       lambda f, u, a: baseline_tagger.assign(f, u, a),
                       initial_weight=1.0)
    ensemble.register(gp_tagger.tagger_id,
                       lambda f, u, a: gp_tagger.assign(f, u, a),
                       initial_weight=0.8)

    import tempfile
    qa_ens = QualityAgent(state_path=tempfile.mktemp(suffix=".json"))
    ensemble_result = run_tagger_on_records(
        "ensemble",
        lambda f, u, a: ensemble.assign(f, u, a).tags,
        test_records,
        qa_ens,
        verbose=args.verbose,
    )

    # Summary
    all_results = [
        ("Baseline", baseline_result),
        ("GP", gp_result),
        ("Ensemble", ensemble_result),
    ]
    print(f"\n{'='*72}")
    print(f"{'Metric':<25} {'Baseline':>12} {'GP':>12} {'Ensemble':>12} {'Best':>8}")
    print(f"{'='*72}")
    for metric in ["mean_density", "mean_reframing", "fitness"]:
        vals = {name: r[metric] for name, r in all_results}
        if metric == "mean_reframing":
            best = min(vals, key=vals.get)
        else:
            best = max(vals, key=vals.get)
        line = f"  {metric:<23}"
        for name, _ in all_results:
            line += f" {vals[name]:>11.3f}"
        line += f" {'← ' + best:>8}"
        print(line)
    print(f"{'='*72}")


if __name__ == "__main__":
    main()
