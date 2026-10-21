"""Medición del tiempo de cada paso del pipeline."""
from __future__ import annotations

import time
from contextlib import contextmanager


class StepTimer:
    def __init__(self) -> None:
        self.steps: list[tuple[str, float]] = []
        self._t0 = time.perf_counter()

    @contextmanager
    def step(self, name: str):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.steps.append((name, time.perf_counter() - t))

    @property
    def total(self) -> float:
        return time.perf_counter() - self._t0

    def summary(self) -> str:
        lines = [f"  {name:<34} {secs:8.2f} s" for name, secs in self.steps]
        lines.append(f"  {'TOTAL':<34} {self.total:8.2f} s")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, float]:
        d = {name: round(secs, 3) for name, secs in self.steps}
        d["total"] = round(self.total, 3)
        return d
