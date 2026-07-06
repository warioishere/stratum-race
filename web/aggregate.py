#!/usr/bin/env python3
"""Aggregate str_race.py JSON session exports into a single leaderboard.json.

Reads every ``*.json`` file in a sessions directory (each produced by
``str_race.py --json-out``), merges their confirmed races, and writes a
compact leaderboard document for the web UI.

Stdlib only, matching str_race.py.

Usage:
    python3 aggregate.py --sessions /var/lib/stratum-race/sessions \
                         --out /var/www/stratumrace/data/leaderboard.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

MIN_RACES_FOR_RANK = 3

# Map our stratum pool names to mempool.space pool slugs so pools can be
# classified by whether they actually found a block recently. ckpool solo
# regions all mine under the "Solo CK" tag.
MEMPOOL_SLUGS = {
    "antpool": "antpool",
    "f2pool": "f2pool",
    "viabtc": "viabtc",
    "spiderpool": "spiderpool",
    "binance_pool": "binancepool",
    "secpool": "secpool",
    "luxor": "luxor",
    "ocean": "ocean",
    "braiins_pool": "braiinspool",
    "nicehash": "nicehash",
    "emcd": "emcdpool",
    "kanopool": "kanopool",
    "ckpool": "solock",
    "ckpool_eu": "solock",
    "ckpool_au": "solock",
    "ckpool_sg": "solock",
    "public_pool": "publicpool",
    "public_pool_21496": "publicpool",
    "braiins_solo": "braiinssolo",
    "atlaspool": "atlaspool",
    "parasite": "parasite",
}

# Used when the mempool.space lookup fails: pools with a meaningful share of
# network hashrate that reliably find blocks every day.
FALLBACK_BIG = {
    "antpool", "f2pool", "viabtc", "spiderpool", "binance_pool",
    "secpool", "luxor", "ocean", "braiins_pool",
}


BLOCK_INTERVAL_MS = 600_000.0
BLOCK_SUBSIDY_SATS = 312_500_000  # 3.125 BTC until the 2028 halving
DEFAULT_FEE_FRACTION = 0.02  # used when the live lookup fails


def current_fee_fraction() -> float:
    """Fees as a fraction of total block reward, averaged over recent blocks.

    Weights the cost of mining an empty (coinbase-only) template: the subsidy
    is still earned, only the fee portion is forfeited.
    """
    try:
        req = urllib.request.Request(
            "https://mempool.space/api/v1/blocks",
            headers={"User-Agent": "stratum-race-web/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            blocks = json.loads(resp.read().decode())
        fees = [b["extras"]["totalFees"] for b in blocks if "extras" in b]
        if not fees:
            return DEFAULT_FEE_FRACTION
        avg = sum(fees) / len(fees)
        return avg / (avg + BLOCK_SUBSIDY_SATS)
    except Exception as exc:  # noqa: BLE001 - best effort
        print(f"fee fraction lookup failed: {exc}", file=sys.stderr)
        return DEFAULT_FEE_FRACTION


def blocks_last_24h() -> Optional[Dict[str, int]]:
    """slug -> blocks found in the last 24h, or None if the lookup failed."""
    try:
        req = urllib.request.Request(
            "https://mempool.space/api/v1/mining/pools/24h",
            headers={"User-Agent": "stratum-race-web/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return {p["slug"]: p.get("blockCount", 0) for p in data.get("pools", [])}
    except Exception as exc:  # noqa: BLE001 - best effort, fall back to static list
        print(f"mempool 24h lookup failed: {exc}", file=sys.stderr)
        return None


def load_sessions(sessions_dir: Path) -> List[Dict[str, Any]]:
    sessions = []
    for path in sorted(sessions_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"skipping {path.name}: {exc}", file=sys.stderr)
            continue
        if isinstance(data, dict) and "races" in data and "pools" in data:
            data["_file"] = path.name
            sessions.append(data)
    return sessions


def merge_races(sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Confirmed races across all sessions, deduped by prevhash."""
    by_prevhash: Dict[str, Dict[str, Any]] = {}
    for sess in sessions:
        for race in sess.get("races", []):
            if not race.get("confirmed"):
                continue
            key = race.get("prevhash") or f"{sess['_file']}:{race.get('index')}"
            prev = by_prevhash.get(key)
            # If two sessions saw the same block, keep the one with more arrivals.
            if prev is None or len(race.get("arrivals_offset_ms", {})) > len(
                prev.get("arrivals_offset_ms", {})
            ):
                by_prevhash[key] = race
    races = list(by_prevhash.values())
    races.sort(key=lambda r: r.get("first_epoch") or 0)
    return races


def stats_for(offsets: List[float]) -> Dict[str, Optional[float]]:
    if not offsets:
        return {"median": None, "avg": None, "p95": None, "best": None, "worst": None}
    ordered = sorted(offsets)
    p95_idx = min(len(ordered) - 1, max(0, math.ceil(0.95 * len(ordered)) - 1))
    return {
        "median": round(statistics.median(ordered), 3),
        "avg": round(statistics.fmean(ordered), 3),
        "p95": round(ordered[p95_idx], 3),
        "best": round(ordered[0], 3),
        "worst": round(ordered[-1], 3),
    }


def build_pool_rows(
    sessions: List[Dict[str, Any]], races: List[Dict[str, Any]]
) -> "tuple[List[Dict[str, Any]], float]":
    pools: Dict[str, Dict[str, Any]] = {}

    def entry(name: str) -> Dict[str, Any]:
        return pools.setdefault(
            name,
            {
                "name": name,
                "host": None,
                "port": None,
                "wins": 0,
                "seen": 0,
                "eligible": 0,
                "missed": 0,
                "empty_first": 0,
                "offsets": [],
                "stale_samples": [],
                "gap_samples": [],
                "reconnects": 0,
                "read_timeouts": 0,
                "remote_closes": 0,
                "connect_errors": 0,
                "connect_timeouts": 0,
                "connections": 0,
                "notify_total": 0,
                "sessions": 0,
                "last_error": None,
                "last_excluded": False,
            },
        )

    # Connection health and identity come from per-session pool summaries.
    for sess in sessions:
        for p in sess.get("pools", []):
            e = entry(p["name"])
            e["host"] = p.get("host") or e["host"]
            e["port"] = p.get("port") or e["port"]
            e["sessions"] += 1
            for key in (
                "reconnects",
                "read_timeouts",
                "remote_closes",
                "connect_errors",
                "connect_timeouts",
                "connections",
                "notify_total",
            ):
                e[key] += p.get(key) or 0
            # Track the most recent failure reason we saw for this pool.
            err = (
                p.get("subscribe_error")
                or p.get("auth_error")
                or p.get("exclude_reason")
            )
            if err:
                e["last_error"] = str(err)[:160]
            e["last_excluded"] = bool(p.get("excluded_at_baseline"))

    # Timing comes from the merged, deduped race list. Rankings use each
    # pool's first NON-EMPTY (full) template: pools that lead with an empty
    # coinbase-only template get credit only once the real template lands.
    # Legacy sessions predate the distinction and treat all arrivals as full.
    for race in races:
        arrivals = race.get("arrivals_offset_ms", {}) or {}
        nonempty = race.get("nonempty_arrivals_offset_ms")
        if nonempty is None:
            nonempty = arrivals
        empty_first = set(race.get("empty_first_pools") or [])
        winner = race.get("winner_nonempty") or race.get("winner")
        for name in race.get("eligible_at_start", []) or arrivals.keys():
            entry(name)["eligible"] += 1
        gaps = race.get("empty_to_full_ms") or {}
        # Older sessions lack empty_to_full_ms; the two offset sets have
        # different zero points, so recover the shift from the non-empty
        # winner when its first notify was already a full template.
        winner_ne = race.get("winner_nonempty")
        delta = None
        if winner_ne and winner_ne not in empty_first:
            delta = arrivals.get(winner_ne)
        for name, off in arrivals.items():
            e = entry(name)
            e["seen"] += 1
            if name in empty_first:
                e["empty_first"] += 1
            try:
                off_f = float(off)
            except (TypeError, ValueError):
                continue
            # Stale window: time this pool's miners kept hashing the OLD
            # block, measured against the first notify seen anywhere.
            e["stale_samples"].append(off_f)
            gap = 0.0
            if name in gaps:
                gap = float(gaps[name])
            elif name in empty_first and name in nonempty and delta is not None:
                gap = max(0.0, float(nonempty[name]) + float(delta) - off_f)
            e["gap_samples"].append(gap)
        for name, offset in nonempty.items():
            try:
                entry(name)["offsets"].append(float(offset))
            except (TypeError, ValueError):
                pass
        for name in race.get("missed_pools", []) or []:
            entry(name)["missed"] += 1
        if winner:
            entry(winner)["wins"] += 1

    # Tier: "big" = the pool (or its mempool.space counterpart) found at least
    # one block in the last 24 hours; everything else is "small".
    found_24h = blocks_last_24h()
    fee_frac = current_fee_fraction()
    rows = []
    for e in pools.values():
        offsets = e.pop("offsets")
        stale_samples = e.pop("stale_samples")
        gap_samples = e.pop("gap_samples")
        # Downtime-equivalent waste: stale time is a total loss (the miner is
        # hashing a block that would be orphaned); empty-template time costs
        # only the fee share of the reward. Normalized against the 10-minute
        # average block interval and expressed as minutes per day.
        if stale_samples:
            stale_avg = statistics.fmean(stale_samples)
            gap_avg = statistics.fmean(gap_samples) if gap_samples else 0.0
            effective_ms = stale_avg + fee_frac * gap_avg
            waste_min_day = round(effective_ms / BLOCK_INTERVAL_MS * 1440.0, 2)
            stale_avg = round(stale_avg, 1)
            gap_avg = round(gap_avg, 1)
        else:
            stale_avg = gap_avg = waste_min_day = None
        slug = MEMPOOL_SLUGS.get(e["name"])
        if found_24h is not None:
            blocks_24h = found_24h.get(slug, 0) if slug else 0
            tier = "big" if blocks_24h > 0 else "small"
        else:
            blocks_24h = None
            tier = "big" if e["name"] in FALLBACK_BIG else "small"
        st = stats_for(offsets)
        seen, eligible = e["seen"], e["eligible"]
        if len(offsets) >= MIN_RACES_FOR_RANK:
            status = "ranked"
        elif seen > 0:
            status = "collecting"
        elif e["notify_total"] > 0 or e["connections"] > 0:
            status = "no_races_yet"
        else:
            status = "unreachable"
        rows.append(
            {
                **e,
                "median_ms": st["median"],
                "avg_ms": st["avg"],
                "p95_ms": st["p95"],
                "best_ms": st["best"],
                "worst_ms": st["worst"],
                "win_pct": round(100.0 * e["wins"] / seen, 1) if seen else None,
                "seen_pct": round(100.0 * seen / eligible, 1) if eligible else None,
                "empty_first_pct": round(100.0 * e["empty_first"] / seen, 1) if seen else None,
                "waste_min_day": waste_min_day,
                "stale_ms_avg": stale_avg,
                "empty_gap_ms_avg": gap_avg,
                "tier": tier,
                "blocks_24h": blocks_24h,
                "status": status,
            }
        )

    def sort_key(r: Dict[str, Any]):
        rank_group = 0 if r["status"] == "ranked" else 1 if r["status"] == "collecting" else 2
        median = r["median_ms"] if r["median_ms"] is not None else float("inf")
        return (rank_group, median, -r["seen"], r["name"])

    rows.sort(key=sort_key)
    rank = 0
    for r in rows:
        if r["status"] == "ranked":
            rank += 1
            r["rank"] = rank
        else:
            r["rank"] = None
    return rows, fee_frac


def recent_races(races: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    out = []
    for race in races[-limit:][::-1]:
        offsets = race.get("nonempty_arrivals_offset_ms")
        if offsets is None:
            offsets = race.get("arrivals_offset_ms") or {}
        arrivals = sorted(
            ((n, float(v)) for n, v in offsets.items()),
            key=lambda kv: kv[1],
        )
        second = arrivals[1] if len(arrivals) > 1 else None
        winner = race.get("winner_nonempty") or race.get("winner")
        first_any = race.get("winner")
        out.append(
            {
                "height": race.get("block_height"),
                "utc": race.get("first_utc"),
                "epoch": race.get("first_epoch"),
                "miner": race.get("block_miner"),
                "prevhash_short": race.get("prevhash_short"),
                "winner": winner,
                # Pool that delivered the very first notify (empty or not),
                # only when it differs from the full-template winner.
                "empty_jumpstart": first_any if first_any and first_any != winner else None,
                "second": second[0] if second else None,
                "second_delay_ms": round(second[1], 3) if second else None,
                "spread_ms": round(arrivals[-1][1], 3) if arrivals else None,
                "pools_seen": len(race.get("arrivals_offset_ms") or {}),
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge str_race sessions into leaderboard.json")
    ap.add_argument("--sessions", required=True, help="Directory of str_race --json-out files")
    ap.add_argument("--out", required=True, help="Path to write leaderboard.json")
    ap.add_argument("--recent", type=int, default=40, help="How many recent races to include")
    ap.add_argument("--vantage", default="", help="Optional label describing the measurement vantage point")
    args = ap.parse_args()

    sessions_dir = Path(args.sessions)
    sessions = load_sessions(sessions_dir) if sessions_dir.is_dir() else []
    races = merge_races(sessions)
    rows, fee_frac = build_pool_rows(sessions, races)

    total_secs = sum(s.get("meta", {}).get("duration_seconds") or 0 for s in sessions)
    payload = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "vantage": args.vantage,
        "sessions": len(sessions),
        "observation_seconds": total_secs,
        "races": len(races),
        "first_race_utc": races[0].get("first_utc") if races else None,
        "last_race_utc": races[-1].get("first_utc") if races else None,
        "min_races_for_rank": MIN_RACES_FOR_RANK,
        "ranking_basis": "first non-empty template",
        "fee_fraction_pct": round(100.0 * fee_frac, 2),
        "waste_note": (
            "waste_min_day = downtime-equivalent minutes of mining lost per day: "
            "(avg stale ms + fee_fraction x avg empty-template ms) / 600s block interval x 1440. "
            "Measured relative to the fastest pool at this vantage, so it is a lower bound."
        ),
        "pools": rows,
        "recent_races": recent_races(races, args.recent),
        "methodology_note": (
            "Wins are first observed by this client/vantage point, "
            "not global proof of pool propagation victory."
        ),
        "credit": "str_race.py by @proofofmike",
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1) + "\n")
    tmp.replace(out)
    print(f"wrote {out}: {len(rows)} pools, {len(races)} races, {len(sessions)} sessions")


if __name__ == "__main__":
    main()
