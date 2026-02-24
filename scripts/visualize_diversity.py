"""
visualize_diversity.py — Visualise genomic diversity of a GP tagger population.

Produces three panels:
  1. Behavioral fingerprint map (PCA of tag-assignment vectors) — where each dot
     is one individual, position encodes *what it does*, size encodes genome size,
     colour encodes fitness.
  2. Structural scatter: tree height vs node count, coloured by fitness.
  3. Population overview: distributions of tree size, height, and fitness.

Usage:
  python3 scripts/visualize_diversity.py [--pop 50] [--gen 15] [--out diversity.png]
                                          [--tag TAG] [--verbose]
"""

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import iter_records
from features import extract_features
from gp_tagger import (
    PSET, TOOLBOX, features_to_vector,
    build_training_examples, evolve_predicates_for_tag,
    TagPredictor,
)
from tagger import assign_tags as baseline_assign, CORE_TAGS
from deap import algorithms, base, creator, gp, tools


# ── Custom evolution that captures the full final population ──────────────────

def evolve_with_population(
    tag: str,
    training_examples,
    pop_size: int = 50,
    n_gen: int = 15,
    verbose: bool = False,
):
    """Like evolve_predicates_for_tag but returns (best, full_population)."""
    from gp_tagger import _evaluate_individual

    toolbox = base.Toolbox()
    toolbox.register("expr",       gp.genHalfAndHalf, pset=PSET, min_=1, max_=4)
    toolbox.register("individual", tools.initIterate, creator.Individual, toolbox.expr)
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)
    toolbox.register("compile",    gp.compile, pset=PSET)
    toolbox.register("evaluate",   _evaluate_individual,
                     tag=tag, training_examples=training_examples)
    toolbox.register("select",     tools.selTournament, tournsize=3)
    toolbox.register("mate",       gp.cxOnePoint)
    toolbox.register("expr_mut",   gp.genFull, min_=0, max_=2)
    toolbox.register("mutate",     gp.mutUniform, expr=toolbox.expr_mut, pset=PSET)
    toolbox.decorate("mate",   gp.staticLimit(key=lambda x: x.height, max_value=8))
    toolbox.decorate("mutate", gp.staticLimit(key=lambda x: x.height, max_value=8))

    pop = toolbox.population(n=pop_size)
    hof = tools.HallOfFame(1)
    algorithms.eaSimple(pop, toolbox, cxpb=0.7, mutpb=0.3,
                        ngen=n_gen, halloffame=hof, verbose=verbose)
    return hof[0], pop


def behavioral_fingerprint(individual, training_vecs):
    """
    Run the compiled individual on every training example.
    Returns a binary vector: [did_fire_on_example_0, did_fire_on_example_1, ...]
    """
    try:
        func = TOOLBOX.compile(expr=individual)
    except Exception:
        return np.zeros(len(training_vecs))

    bits = []
    for vec in training_vecs:
        try:
            bits.append(1.0 if float(func(*vec)) > 0.5 else 0.0)
        except Exception:
            bits.append(0.0)
    return np.array(bits)


# ── Plotting ──────────────────────────────────────────────────────────────────

def plot_diversity(population, training_vecs, tag: str, out_path: str):
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler

    # Collect per-individual stats
    fitnesses = []
    sizes = []
    heights = []
    fingerprints = []

    for ind in population:
        f = ind.fitness.values[0] if ind.fitness.valid else 0.0
        fitnesses.append(f)
        sizes.append(len(ind))
        heights.append(ind.height)
        fingerprints.append(behavioral_fingerprint(ind, training_vecs))

    fitnesses = np.array(fitnesses)
    sizes = np.array(sizes)
    heights = np.array(heights)
    fp_matrix = np.vstack(fingerprints)

    # PCA on behavioral fingerprints
    # Handle degenerate case: all identical fingerprints
    fp_std = np.std(fp_matrix, axis=0)
    valid_cols = fp_std > 0
    if valid_cols.sum() < 2:
        # Not enough variance — use random jitter for visualization
        pca_coords = np.random.randn(len(population), 2) * 0.1
        pca_var = [0.0, 0.0]
    else:
        fp_reduced = fp_matrix[:, valid_cols]
        scaler = StandardScaler()
        fp_scaled = scaler.fit_transform(fp_reduced)
        n_components = min(2, fp_scaled.shape[1])
        pca = PCA(n_components=n_components)
        pca_coords_raw = pca.fit_transform(fp_scaled)
        pca_var = list(pca.explained_variance_ratio_)
        if n_components == 1:
            pca_coords = np.column_stack([pca_coords_raw[:, 0],
                                          np.zeros(len(population))])
        else:
            pca_coords = pca_coords_raw
        while len(pca_var) < 2:
            pca_var.append(0.0)

    # ── Figure layout: 3 panels ────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 5.5), facecolor="#0e0e14")
    fig.suptitle(
        f"GP Population Diversity — tag: \"{tag}\"  "
        f"(n={len(population)},  unique fitness={len(set(round(f,3) for f in fitnesses))} distinct)",
        color="#e8e8f0", fontsize=13, fontweight="bold", y=1.01,
    )

    cmap = plt.cm.plasma
    norm = mcolors.Normalize(vmin=0.0, vmax=1.0)

    ax_style = dict(facecolor="#16161f", labelcolor="#a0a0b8",
                    titlecolor="#c8c8e0")

    # ── Panel 1: Behavioral fingerprint PCA ───────────────────────────────
    ax1 = fig.add_subplot(1, 3, 1)
    ax1.set_facecolor(ax_style["facecolor"])
    scatter1 = ax1.scatter(
        pca_coords[:, 0], pca_coords[:, 1],
        c=fitnesses, cmap=cmap, norm=norm,
        s=np.clip(sizes * 6, 20, 220),
        alpha=0.82, edgecolors="#ffffff22", linewidths=0.3,
    )
    ax1.set_title("Behavioral fingerprint (PCA)", color=ax_style["titlecolor"], fontsize=10)
    ax1.set_xlabel(f"PC1 ({pca_var[0]*100:.0f}% var)", color=ax_style["labelcolor"], fontsize=8)
    ax1.set_ylabel(f"PC2 ({pca_var[1]*100:.0f}% var)", color=ax_style["labelcolor"], fontsize=8)
    ax1.tick_params(colors="#606080", labelsize=7)
    for spine in ax1.spines.values():
        spine.set_edgecolor("#303050")

    # Annotate the best individual
    best_idx = int(np.argmax(fitnesses))
    ax1.scatter(*pca_coords[best_idx], s=280, c="none",
                edgecolors="#ffdd44", linewidths=1.5, zorder=5)
    ax1.annotate(f"best\nfit={fitnesses[best_idx]:.2f}",
                 xy=pca_coords[best_idx],
                 xytext=(10, 10), textcoords="offset points",
                 color="#ffdd44", fontsize=7, arrowprops=dict(arrowstyle="-", color="#ffdd6688"))

    # Size legend
    for s_val, label in [(1, "1 node"), (5, "5"), (15, "15+")]:
        ax1.scatter([], [], s=s_val * 6, c="#888", label=label, alpha=0.7)
    ax1.legend(title="tree size", title_fontsize=7, fontsize=6,
               labelcolor="#a0a0c0", facecolor="#202030", edgecolor="#404060",
               loc="lower right")

    # ── Panel 2: Structural scatter (height × size) ───────────────────────
    ax2 = fig.add_subplot(1, 3, 2)
    ax2.set_facecolor(ax_style["facecolor"])
    ax2.scatter(
        heights, sizes,
        c=fitnesses, cmap=cmap, norm=norm,
        s=55, alpha=0.75, edgecolors="#ffffff15", linewidths=0.3,
    )
    ax2.set_title("Tree structure", color=ax_style["titlecolor"], fontsize=10)
    ax2.set_xlabel("Tree height", color=ax_style["labelcolor"], fontsize=8)
    ax2.set_ylabel("Node count", color=ax_style["labelcolor"], fontsize=8)
    ax2.tick_params(colors="#606080", labelsize=7)
    for spine in ax2.spines.values():
        spine.set_edgecolor("#303050")

    # Annotate clusters
    for h in sorted(set(heights)):
        mask = heights == h
        mean_size = sizes[mask].mean()
        mean_fit = fitnesses[mask].mean()
        ax2.annotate(f"n={mask.sum()}", xy=(h, mean_size),
                     xytext=(2, 2), textcoords="offset points",
                     color="#70709a", fontsize=6)

    # ── Panel 3: Distribution histograms ─────────────────────────────────
    ax3 = fig.add_subplot(1, 3, 3)
    ax3.set_facecolor(ax_style["facecolor"])

    # Fitness distribution
    bins_f = np.linspace(0, 1, 16)
    n_f, _, patches_f = ax3.hist(fitnesses, bins=bins_f, alpha=0.85,
                                  color="#7b5ea7", label="fitness", zorder=3)
    # Colour bars by fitness value
    for patch, left in zip(patches_f, bins_f[:-1]):
        patch.set_facecolor(cmap(norm(left + 0.03)))
        patch.set_alpha(0.85)

    ax3_r = ax3.twinx()
    ax3_r.set_facecolor(ax_style["facecolor"])
    ax3_r.hist(sizes, bins=12, alpha=0.4, color="#5b8dd9", label="size")
    ax3_r.set_ylabel("Node count freq", color="#5b8dd9", fontsize=7)
    ax3_r.tick_params(colors="#5b8dd9", labelsize=7)

    ax3.set_title("Population distributions", color=ax_style["titlecolor"], fontsize=10)
    ax3.set_xlabel("Fitness score", color=ax_style["labelcolor"], fontsize=8)
    ax3.set_ylabel("Count (fitness)", color="#c87bd9", fontsize=7)
    ax3.tick_params(colors="#606080", labelsize=7)
    for spine in ax3.spines.values():
        spine.set_edgecolor("#303050")

    # Stats annotation
    stats_text = (
        f"fitness  μ={fitnesses.mean():.3f}  σ={fitnesses.std():.3f}  max={fitnesses.max():.3f}\n"
        f"size     μ={sizes.mean():.1f}  σ={sizes.std():.1f}  max={sizes.max()}\n"
        f"height   μ={heights.mean():.1f}  σ={heights.std():.1f}  max={heights.max()}\n"
        f"behavioral entropy: {_behavioral_entropy(fp_matrix):.3f}"
    )
    ax3.text(0.02, 0.97, stats_text, transform=ax3.transAxes,
             fontsize=6.5, verticalalignment='top', fontfamily='monospace',
             color="#a0c0a0",
             bbox=dict(boxstyle='round,pad=0.4', facecolor='#101020', alpha=0.8))

    # ── Shared colorbar ────────────────────────────────────────────────────
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=[ax1, ax2, ax3], fraction=0.015, pad=0.01,
                        label="Fitness score")
    cbar.ax.yaxis.label.set_color("#a0a0c0")
    cbar.ax.tick_params(colors="#a0a0c0", labelsize=7)
    cbar.outline.set_edgecolor("#404060")

    plt.tight_layout(pad=1.2)
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"Saved → {out_path}")


def _behavioral_entropy(fp_matrix: np.ndarray) -> float:
    """
    Mean per-position entropy across the fingerprint matrix.
    0 = everyone does the same thing; 1 = maximum diversity.
    """
    n = fp_matrix.shape[0]
    if n < 2:
        return 0.0
    p = fp_matrix.mean(axis=0)
    eps = 1e-9
    h = -(p * np.log2(p + eps) + (1 - p) * np.log2(1 - p + eps))
    return float(h.mean())


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Visualise GP population diversity")
    parser.add_argument("--tag",     default="networking",
                        help=f"Tag to evolve and visualise (default: networking). "
                             f"Available: {', '.join(sorted(CORE_TAGS))}")
    parser.add_argument("--pop",     type=int, default=50)
    parser.add_argument("--gen",     type=int, default=15)
    parser.add_argument("--out",     default="diversity.png")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    records = list(iter_records())
    if not records:
        print("No records. Run harvester.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"Evolving tag={args.tag!r}  pop={args.pop}  gen={args.gen}  "
          f"on {len(records)} interactions...")

    examples = build_training_examples(records, args.tag, baseline_assign)
    training_vecs = [vec for vec, _ in examples]

    best, population = evolve_with_population(
        args.tag, examples,
        pop_size=args.pop, n_gen=args.gen, verbose=args.verbose,
    )

    print(f"Best individual: fitness={best.fitness.values[0]:.3f}  "
          f"size={len(best)}  height={best.height}")
    print(f"Best tree:\n  {str(best)}")
    print(f"\nPlotting population of {len(population)}...")

    plot_diversity(population, training_vecs, args.tag, args.out)


if __name__ == "__main__":
    main()
