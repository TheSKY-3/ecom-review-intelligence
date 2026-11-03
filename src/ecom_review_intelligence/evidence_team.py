"""D2 constrained card-reader team: one reader per evidence card, in parallel.

A diagnosis run samples up to five evidence cards; today the operator clicks
them one by one, and each click pays one model round-trip. The reading itself
is already fully constrained (payload builder, verbatim quote check, number
re-derivation), so a "team" here is not about giving the model more freedom --
it is about running the SAME constrained reader for every card concurrently
and merging the results deterministically.

The split follows the C2 lesson (model ordering added nothing; rules stay
rules): the fan-out/fan-in and the merge summary are deterministic code in
this module; the model only ever sees one card payload per reader, exactly
what ``run_insight`` would have sent. All shared-state mutation (validation,
caching, view building) stays on the calling thread; only the stateless
``gateway.analyse`` call runs on worker threads.

Pure logic only: no network, no filesystem. The reader function is injected.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

MAX_READING_WORKERS = 5
"""At most five evidence cards exist per run, so the pool never exceeds this."""


class ReadingFailure:
    """Sentinel for one reader that raised; never carries review text."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason


@dataclass(frozen=True)
class ReadingTask:
    """One per-card unit of work; ``payload`` may contain sample review text
    and therefore never leaves process memory except to the model provider."""

    card_alias: str
    payload: Mapping[str, object]


def execute_reading_team(
    tasks: Sequence[ReadingTask],
    reader_fn: Callable[[ReadingTask], object],
    *,
    max_workers: int = MAX_READING_WORKERS,
) -> dict[str, object]:
    """Fan ``reader_fn`` out over a bounded thread pool, fan back in order.

    Results come back keyed by card alias in submission order, so the merge is
    deterministic regardless of completion order. Any reader exception is
    captured as a :class:`ReadingFailure` for that card only -- one failing
    reader never takes down the team.
    """

    outcomes: dict[str, object] = {}
    if not tasks:
        return outcomes
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as pool:
        futures = [(task.card_alias, pool.submit(reader_fn, task)) for task in tasks]
    for alias, future in futures:
        try:
            outcomes[alias] = future.result()
        except Exception as error:  # noqa: BLE001 - one reader failure is isolated by design
            outcomes[alias] = ReadingFailure(str(error)[:80] or "MODEL_ERROR")
    return outcomes


def summarize_reading(
    statuses: Mapping[str, str], model_calls: Mapping[str, int]
) -> dict[str, int]:
    """Deterministic team summary: per-status card counts and total model calls."""

    summary = {"total": len(statuses), "ready": 0, "validation_failed": 0, "unavailable": 0}
    for status in statuses.values():
        if status == "READY":
            summary["ready"] += 1
        elif status == "VALIDATION_FAILED":
            summary["validation_failed"] += 1
        else:
            summary["unavailable"] += 1
    summary["model_calls"] = sum(int(count) for count in model_calls.values())
    return summary
