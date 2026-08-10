"""Provision-level diff engine.

Aligns provisions by node_key and produces structured diffs.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from taxwatch.normalize.base import ProvisionData


@dataclass
class ProvisionDiff:
    node_key: str
    change_type: str  # added, removed, modified, renumbered
    old_heading: str = ""
    new_heading: str = ""
    old_text: str = ""
    new_text: str = ""
    diff_text: str = ""
    similarity: float = 1.0


def diff_provisions(
    old_provisions: list[ProvisionData],
    new_provisions: list[ProvisionData],
    rename_threshold: float = 0.9,
) -> list[ProvisionDiff]:
    old_map = {p.node_key: p for p in old_provisions}
    new_map = {p.node_key: p for p in new_provisions}

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    diffs: list[ProvisionDiff] = []

    for key in sorted(old_keys & new_keys):
        old_p, new_p = old_map[key], new_map[key]
        if old_p.text == new_p.text:
            continue
        unified = _unified_diff(old_p.text, new_p.text, key)
        diffs.append(ProvisionDiff(
            node_key=key,
            change_type="modified",
            old_heading=old_p.heading,
            new_heading=new_p.heading,
            old_text=old_p.text,
            new_text=new_p.text,
            diff_text=unified,
            similarity=_similarity(old_p.text, new_p.text),
        ))

    removed_keys = old_keys - new_keys
    added_keys = new_keys - old_keys

    renamed = _detect_renames(
        {k: old_map[k] for k in removed_keys},
        {k: new_map[k] for k in added_keys},
        rename_threshold,
    )

    for old_key, new_key, sim in renamed:
        removed_keys.discard(old_key)
        added_keys.discard(new_key)
        old_p, new_p = old_map[old_key], new_map[new_key]
        unified = _unified_diff(old_p.text, new_p.text, f"{old_key} → {new_key}")
        diffs.append(ProvisionDiff(
            node_key=new_key,
            change_type="renumbered",
            old_heading=f"{old_p.heading} (was {old_key})",
            new_heading=new_p.heading,
            old_text=old_p.text,
            new_text=new_p.text,
            diff_text=unified,
            similarity=sim,
        ))

    for key in sorted(removed_keys):
        p = old_map[key]
        diffs.append(ProvisionDiff(
            node_key=key,
            change_type="removed",
            old_heading=p.heading,
            old_text=p.text,
            diff_text=f"--- {key}\n(entire provision removed)",
            similarity=0.0,
        ))

    for key in sorted(added_keys):
        p = new_map[key]
        diffs.append(ProvisionDiff(
            node_key=key,
            change_type="added",
            new_heading=p.heading,
            new_text=p.text,
            diff_text=f"+++ {key}\n(new provision)",
            similarity=0.0,
        ))

    return diffs


def _unified_diff(old_text: str, new_text: str, label: str) -> str:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines, fromfile=f"old/{label}", tofile=f"new/{label}",
    )
    return "".join(diff)


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _detect_renames(
    removed: dict[str, ProvisionData],
    added: dict[str, ProvisionData],
    threshold: float,
) -> list[tuple[str, str, float]]:
    if not removed or not added:
        return []

    renames: list[tuple[str, str, float]] = []
    used_new: set[str] = set()

    for old_key, old_p in removed.items():
        best_key: str | None = None
        best_sim = 0.0
        for new_key, new_p in added.items():
            if new_key in used_new:
                continue
            sim = _similarity(old_p.text, new_p.text)
            if sim >= threshold and sim > best_sim:
                best_sim = sim
                best_key = new_key
        if best_key is not None:
            renames.append((old_key, best_key, best_sim))
            used_new.add(best_key)

    return renames
