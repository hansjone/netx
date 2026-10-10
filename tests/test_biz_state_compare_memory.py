"""Bounded sampling regressions and an opt-in comparison memory benchmark."""

from __future__ import annotations

import gc
import hashlib
import json
import os
import random
import subprocess
import time
import tracemalloc
import types
from collections import defaultdict, deque
from copy import deepcopy

import pytest

from netx_api.biz_state.compare_engine import _StratifiedSample, compare_rows, stratify_take


def reference_sample(items, limit):
    if len(items) <= limit:
        return list(items)
    buckets = defaultdict(deque)
    for item in items:
        buckets[item[0]].append(item)
    out = []
    while buckets and len(out) < limit:
        for key in list(buckets):
            out.append(buckets[key].popleft())
            if not buckets[key]:
                del buckets[key]
            if len(out) >= limit:
                break
    return out


def test_streaming_sample_preserves_round_robin_and_small_input_order():
    rng = random.Random(42)
    for _ in range(100):
        items = [(str(rng.randrange(20)), i) for i in range(rng.randrange(100))]
        for limit in (0, 1, 5, 20, 100):
            expected = reference_sample(items, limit)
            assert stratify_take(iter(items), limit, key_fn=lambda item: item[0]) == expected


def test_sample_storage_is_bounded_for_both_many_rows_and_many_strata():
    sample = _StratifiedSample(50)
    for i in range(20_000):
        sample.add(i, str(i) if i >= 10_000 else "one-large-peer")
    assert len(sample.heap) == 50
    assert len(sample.strata) == 50
    assert sample.picked() == [0, *range(10_000, 10_049)]


@pytest.mark.parametrize("iface_fields,rules,pmap", [
    ([], [], {}),
    (["interface"], [], {}),
    (["interface"], [{"from": "GE", "to": "gei"}], {"gei-1": "gei-2"}),
])
def test_compare_does_not_mutate_inputs_when_reusing_rows(iface_fields, rules, pmap):
    before = [{"id": "1", "interface": "GE-1", "state": "up"}]
    after = [{"id": "1", "interface": "GE-2", "state": "up"}]
    original = deepcopy((before, after))
    compare_rows(before_rows=before, after_rows=after, key_fields=["id"],
                 iface_fields=iface_fields, compare_fields=["state"], port_map=pmap,
                 iface_normalize_rules=rules, include_unchanged=True, unchanged_limit=1)
    assert (before, after) == original


def test_memory_benchmark_against_git_baseline():
    """Run explicitly with NETX_BENCH_ROWS=1000000; not part of routine CI."""
    n = int(os.environ.get("NETX_BENCH_ROWS", "0"))
    if not n:
        pytest.skip("opt-in large synthetic benchmark")
    baseline_ref = os.environ.get("NETX_BENCH_REF", "HEAD")
    source = subprocess.run(["git", "show", f"{baseline_ref}:netx_api/biz_state/compare_engine.py"],
                            check=True, capture_output=True, text=True, encoding="utf-8").stdout
    baseline = types.ModuleType("netx_api.biz_state._benchmark_baseline")
    baseline.__package__ = "netx_api.biz_state"
    exec(compile(source, "baseline_compare_engine.py", "exec"), baseline.__dict__)
    before = [{"id": str(i), "neighbor": str(i % 50), "state": "up", "afi": "vpnv4"} for i in range(n)]
    after = [dict(row) for row in before]
    records = []
    digests = []
    for name, compare in (("baseline", baseline.compare_rows), ("optimized", compare_rows)):
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        result = compare(before_rows=before, after_rows=after, key_fields=["id"], iface_fields=[],
                         compare_fields=["state"], include_unchanged=True, unchanged_limit=5000,
                         compact_unchanged=True)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        digests.append(hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest())
        records.append({"engine": name, "rows_per_side": n, "peak_engine_mib": round(peak / 2**20, 2),
                        "seconds_with_tracemalloc": round(elapsed, 3)})
        del result
    assert digests[0] == digests[1], "large-sheet results and sample order must match"
    print("\n" + json.dumps({"baseline_ref": baseline_ref, "input_allocations_included": False,
                              "results": records}, indent=2))
