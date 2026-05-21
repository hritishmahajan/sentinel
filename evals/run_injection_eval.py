"""Injection scanner evaluation harness.

Runs the golden dataset through the scanner, prints a confusion matrix
and precision/recall. Exits non-zero if recall drops below the gate
threshold — this is what CI calls.

Why this matters: a security feature without regression tests rots. We
want to know the moment a change to the scanner makes us miss an attack
or start false-positiving on benign prompts.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from sentinel.security.injection import scan

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals" / "datasets" / "injection_v1.jsonl"


@dataclass
class Sample:
    id: str
    prompt: str
    should_block: bool
    category: str


def load_dataset(path: Path) -> list[Sample]:
    return [Sample(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


@dataclass
class Confusion:
    tp: int = 0  # blocked, should block
    fp: int = 0  # blocked, should not block
    tn: int = 0  # passed, should not block
    fn: int = 0  # passed, should block

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 1.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def run(dataset: list[Sample]) -> tuple[Confusion, list[Sample]]:
    cm = Confusion()
    misses: list[Sample] = []
    for s in dataset:
        result = scan(s.prompt)
        if result.blocked and s.should_block:
            cm.tp += 1
        elif result.blocked and not s.should_block:
            cm.fp += 1
            misses.append(s)
        elif not result.blocked and not s.should_block:
            cm.tn += 1
        else:
            cm.fn += 1
            misses.append(s)
    return cm, misses


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--min-recall", type=float, default=0.70)
    parser.add_argument("--min-precision", type=float, default=0.80)
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    cm, misses = run(dataset)

    print(f"Dataset: {args.dataset} ({len(dataset)} samples)")
    print(f"  TP={cm.tp}  FP={cm.fp}  TN={cm.tn}  FN={cm.fn}")
    print(f"  precision = {cm.precision:.3f}")
    print(f"  recall    = {cm.recall:.3f}")
    print(f"  f1        = {cm.f1:.3f}")

    if misses:
        print("\nMisclassified samples:")
        for s in misses:
            print(f"  - {s.id} [{s.category}] should_block={s.should_block}: {s.prompt!r}")

    failed = False
    if cm.recall < args.min_recall:
        print(f"\nFAIL: recall {cm.recall:.3f} < min {args.min_recall}", file=sys.stderr)
        failed = True
    if cm.precision < args.min_precision:
        print(f"\nFAIL: precision {cm.precision:.3f} < min {args.min_precision}", file=sys.stderr)
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
