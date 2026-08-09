"""Layout state shared by atomic ops."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LayoutParams:
    """Tunable knobs for recipe / ops (agent edits these, not ad-hoc scripts)."""

    pitch: float = 200.0
    side: float = 170.0
    an_gap: float = 420.0
    an_y: float = 0.0
    island_pad_x: float = 220.0
    island_pad_y: float = 200.0
    width_mul: float = 3.5
    height_mul: float = 2.0
    x_gain: float = 1.8
    lane: float = 260.0
    target_nn: float = 150.0
    scale_cap: float = 2.2
    target_util: float = 0.08
    min_util: float = 0.03
    pack_min_scale: float = 0.55
    pack_iters: int = 4
    # Cap uniform pack so nn_p50 does not fall below this (keeps icons readable).
    pack_nn_floor: float = 140.0
    overlap_iters: int = 160
    overlap_step: float = 3.0
    margin: float = 160.0
    # explode_clusters / enforce_min_gap
    cluster_thr: float = 8.0
    cluster_gap: float = 35.0
    min_center_gap: float = 160.0


@dataclass
class LayoutState:
    positions: dict[str, tuple[float, float]] = field(default_factory=dict)
    pinned: set[str] = field(default_factory=set)
    names: dict[str, str] = field(default_factory=dict)
    layers: dict[str, str] = field(default_factory=dict)
    links: list[tuple[str, str]] = field(default_factory=list)
    adj: dict[str, set[str]] = field(default_factory=dict)
    spine: set[str] = field(default_factory=set)
    # None = whole graph active; else only these ids may be moved by scoped ops.
    scope: set[str] | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    last_moved: set[str] = field(default_factory=set)

    def copy(self) -> LayoutState:
        return LayoutState(
            positions=dict(self.positions),
            pinned=set(self.pinned),
            names=dict(self.names),
            layers=dict(self.layers),
            links=list(self.links),
            adj={k: set(v) for k, v in self.adj.items()},
            spine=set(self.spine),
            scope=None if self.scope is None else set(self.scope),
            meta=dict(self.meta),
            last_moved=set(self.last_moved),
        )


@dataclass
class OpResult:
    state: LayoutState
    moved: set[str]
    op: str
    params: dict[str, Any] = field(default_factory=dict)
    note: str = ""
