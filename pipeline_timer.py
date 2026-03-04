from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import time
from typing import Dict, List, Tuple


@dataclass
class PipelineTimer:
    """
    Prints each step duration immediately and stores timings for a final report.
    """
    timings: List[Tuple[str, float]] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter)

    @contextmanager
    def step(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.timings.append((name, elapsed))
            print(f"{name:<30} {elapsed:>8.3f} s")

    def report(self, *, sort: bool = False, show_total: bool = True) -> None:
        rows = list(self.timings)
        if sort:
            rows.sort(key=lambda x: x[1], reverse=True)

        total = sum(dt for _, dt in rows)
        name_w = max([len("Step"), *(len(n) for n, _ in rows)] or [4])

        header = f"{'Step':<{name_w}}  {'Seconds':>10}  {'%':>7}"
        print("\n" + header)
        print("-" * len(header))

        for name, dt in rows:
            pct = (dt / total * 100.0) if total > 0 else 0.0
            print(f"{name:<{name_w}}  {dt:>10.3f}  {pct:>6.1f}%")

        if show_total:
            print("-" * len(header))
            print(f"{'TOTAL':<{name_w}}  {total:>10.3f}  {100.0:>6.1f}%")

    def as_dict(self) -> Dict[str, float]:
        return {name: dt for name, dt in self.timings}