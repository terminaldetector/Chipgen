"""parallel.py — independent candidates measured side by side.

A candidate's measurement is a pure function of what it is (a module's
bytes, the notes, the seconds): nothing it computes depends on another
candidate, and nothing is written. So candidates can be rendered in
separate processes and their results put back in order — the same numbers
as one after another (the tests compare them), in a fraction of the time
on a machine with cores to spare.

    workers()   how many processes: CHIPGEN_WORKERS if set, else the
                machine's cores, at most MAX_WORKERS; 1 means in order
    run(fn, items)
                fn(item) for each item, in the items' order. On a platform
                without fork, with one worker, or if the pool fails, it
                runs them in order and says so (`how`)

Only module-level functions and picklable items go in; the loop that
decides, commits and writes stays in the caller's process.
"""

import multiprocessing as mp
import os
from typing import Callable, List, Tuple

MAX_WORKERS = 4


def workers() -> int:
    env = os.environ.get("CHIPGEN_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, min(MAX_WORKERS, os.cpu_count() or 1))


def run(fn: Callable, items: List, n: int = None) -> Tuple[List, str]:
    """[fn(x) for x in items], in order. -> (results, how)."""
    n = workers() if n is None else max(1, n)
    n = min(n, len(items))
    if n <= 1 or "fork" not in mp.get_all_start_methods():
        return [fn(x) for x in items], "in order (one worker)"
    try:
        with mp.get_context("fork").Pool(n) as pool:
            return pool.map(fn, items), f"{n} processes"
    except Exception as error:          # a pool that fails is not a result
        return [fn(x) for x in items], (f"in order (the pool failed: "
                                         f"{type(error).__name__})")


#: the function and items of a run_closure, inherited by the forked workers
_CLOSURE = None


def _call(i):
    fn, items = _CLOSURE
    return fn(items[i])


def run_closure(fn: Callable, items: List, n: int = None) -> Tuple[List, str]:
    """run() for a function that cannot be pickled (a closure over the
    caller's state): the workers are forked after it is set, so they have
    it already; only the indices and the results cross."""
    global _CLOSURE
    n = workers() if n is None else max(1, n)
    n = min(n, len(items))
    if n <= 1 or "fork" not in mp.get_all_start_methods():
        return [fn(x) for x in items], "in order (one worker)"
    _CLOSURE = (fn, items)
    try:
        with mp.get_context("fork").Pool(n) as pool:
            return pool.map(_call, range(len(items))), f"{n} processes"
    except Exception as error:
        return [fn(x) for x in items], (f"in order (the pool failed: "
                                         f"{type(error).__name__})")
    finally:
        _CLOSURE = None
