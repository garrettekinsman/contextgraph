"""
quality.py — Quality agent for tagger fitness evaluation.

Measures tagging strategy quality through two proxy signals:

1. Context density: fraction of assembled context that is tag-retrieved
   (vs. recency-only). Higher = tagger is surfacing relevant material.

2. Reframing frequency: fraction of recent user messages containing
   reframing signals. Lower = user isn't fighting to re-establish context.

These proxies drive the fitness function for GP-evolved tagger evolution.
The quality agent maintains per-strategy score histories for comparison.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from reframing import reframing_rate
from assembler import AssemblyResult


# ── Score dataclasses ─────────────────────────────────────────────────────────

@dataclass
class InteractionQuality:
    """Quality measurement for a single interaction."""
    timestamp: float
    tagger_id: str
    context_density: float      # 0–1; topic_count / total messages assembled
    reframing_signal: float     # 0–1; from ReframingSignal.confidence
    composite: float            # weighted combination


@dataclass
class TaggerStats:
    """Accumulated quality stats for one tagger strategy."""
    tagger_id: str
    scores: List[InteractionQuality] = field(default_factory=list)

    def mean_composite(self, last_n: int = 20) -> float:
        """Mean composite score over the last N interactions."""
        window = self.scores[-last_n:] if self.scores else []
        if not window:
            return 0.5  # neutral prior
        return sum(s.composite for s in window) / len(window)

    def mean_density(self, last_n: int = 20) -> float:
        window = self.scores[-last_n:]
        if not window:
            return 0.0
        return sum(s.context_density for s in window) / len(window)

    def mean_reframing(self, last_n: int = 20) -> float:
        window = self.scores[-last_n:]
        if not window:
            return 0.5
        return sum(s.reframing_signal for s in window) / len(window)


# ── Quality agent ─────────────────────────────────────────────────────────────

class QualityAgent:
    """
    Tracks and scores tagger strategies over time.

    Usage:
        agent = QualityAgent()
        score = agent.record(
            tagger_id="v0-baseline",
            assembly_result=result,
            user_text="the query that produced this assembly",
            recent_user_texts=["last 10 user messages..."],
        )
        fitness = agent.fitness(tagger_id="v0-baseline")
    """

    # Weighting: density matters more than reframing (reframing is noisier)
    DENSITY_WEIGHT    = 0.6
    REFRAMING_WEIGHT  = 0.4

    def __init__(self, state_path: Optional[str] = None) -> None:
        self._stats: Dict[str, TaggerStats] = {}
        self._state_path = Path(state_path) if state_path else \
            Path(__file__).parent / "data" / "quality-state.json"
        self._load()

    # ── recording ─────────────────────────────────────────────────────────────

    def record(
        self,
        tagger_id: str,
        assembly_result: AssemblyResult,
        user_text: str,
        recent_user_texts: Optional[List[str]] = None,
    ) -> InteractionQuality:
        """
        Record a quality observation for a tagger after one interaction.

        Parameters
        ----------
        tagger_id           Identifier for the tagger strategy being scored.
        assembly_result     What the assembler returned for this interaction.
        user_text           The current user message (checked for reframing).
        recent_user_texts   Recent user messages for reframing rate context.
        """
        density = self._context_density(assembly_result)

        # Reframing: check current message + recent window
        texts_to_check = [user_text]
        if recent_user_texts:
            texts_to_check = recent_user_texts[-9:] + [user_text]  # max 10
        rf_rate = reframing_rate(texts_to_check)

        # Composite: density up = good, reframing up = bad
        # Normalise reframing so 0 → 1.0 contribution, 1 → 0.0
        composite = (
            self.DENSITY_WEIGHT   * density +
            self.REFRAMING_WEIGHT * (1.0 - rf_rate)
        )

        iq = InteractionQuality(
            timestamp=time.time(),
            tagger_id=tagger_id,
            context_density=density,
            reframing_signal=rf_rate,
            composite=composite,
        )

        if tagger_id not in self._stats:
            self._stats[tagger_id] = TaggerStats(tagger_id=tagger_id)
        self._stats[tagger_id].scores.append(iq)
        self._save()
        return iq

    # ── fitness ───────────────────────────────────────────────────────────────

    def fitness(self, tagger_id: str, last_n: int = 20) -> float:
        """
        Return a fitness score [0–1] for a tagger strategy.
        Higher is better. Returns 0.5 (neutral prior) if no data yet.
        """
        if tagger_id not in self._stats:
            return 0.5
        return self._stats[tagger_id].mean_composite(last_n)

    def rank_taggers(self, last_n: int = 20) -> List[tuple]:
        """Return [(tagger_id, fitness)] sorted best-first."""
        return sorted(
            [(tid, s.mean_composite(last_n)) for tid, s in self._stats.items()],
            key=lambda x: -x[1],
        )

    def stats(self, tagger_id: str) -> Optional[TaggerStats]:
        return self._stats.get(tagger_id)

    def all_tagger_ids(self) -> List[str]:
        return list(self._stats.keys())

    # ── internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _context_density(result: AssemblyResult) -> float:
        """
        Fraction of assembled messages that came from the topic layer.
        Proxy for 'tagger surfaced relevant material'.

        Returns 0.0 if no messages assembled (neutral, not penalised).
        """
        total = result.recency_count + result.topic_count
        if total == 0:
            return 0.0
        return result.topic_count / total

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            with self._state_path.open() as f:
                raw = json.load(f)
            for tid, data in raw.items():
                scores = [InteractionQuality(**s) for s in data.get("scores", [])]
                self._stats[tid] = TaggerStats(tagger_id=tid, scores=scores)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass  # corrupt state — start fresh

    def _save(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        raw = {}
        for tid, stats in self._stats.items():
            raw[tid] = {
                "tagger_id": tid,
                "scores": [
                    {
                        "timestamp": s.timestamp,
                        "tagger_id": s.tagger_id,
                        "context_density": s.context_density,
                        "reframing_signal": s.reframing_signal,
                        "composite": s.composite,
                    }
                    for s in stats.scores[-200:]  # keep last 200 per tagger
                ],
            }
        with self._state_path.open("w") as f:
            json.dump(raw, f, indent=2)
