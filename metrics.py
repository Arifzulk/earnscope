"""Mesin metrik vault EarnScope: kinerja share price, label risiko, log peristiwa."""
import json
import logging
import time

log = logging.getLogger("earnscope.metrics")


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def risk_label(share_price):
    """Label risiko jujur: Underwater < 1.0 / Healthy >= 1.0."""
    sp = _f(share_price)
    if sp is None:
        return "Unknown"
    return "Healthy" if sp >= 1.0 else "Underwater"


def latest_snapshot(conn, vault_id):
    return conn.execute(
        "SELECT * FROM vault_snapshots WHERE vault_id=? ORDER BY ts DESC LIMIT 1",
        (vault_id,)).fetchone()


def first_snapshot(conn, vault_id):
    return conn.execute(
        "SELECT * FROM vault_snapshots WHERE vault_id=? ORDER BY ts ASC LIMIT 1",
        (vault_id,)).fetchone()


def share_price_series(conn, vault_id, limit=60):
    """Sparklines: dari snapshot + event history (share_price saat event)."""
    rows = conn.execute(
        "SELECT ts, share_price FROM vault_snapshots WHERE vault_id=? "
        "AND share_price IS NOT NULL ORDER BY ts", (vault_id,)).fetchall()
    pts = [(r["ts"], r["share_price"]) for r in rows]
    if len(pts) < limit:
        ev = conn.execute(
            "SELECT ts, share_price FROM vault_history_events WHERE vault_id=? "
            "AND share_price IS NOT NULL ORDER BY ts", (vault_id,)).fetchall()
        extra = [(r["ts"], r["share_price"]) for r in ev]
        pts = sorted(pts + extra)
    return [round(p[1], 6) for p in pts[-limit:]]


def performance_vs_first(conn, vault_id):
    first = first_snapshot(conn, vault_id)
    last = latest_snapshot(conn, vault_id)
    if not first or not last:
        return None
    f, l = _f(first["share_price"]), _f(last["share_price"])
    if not f or not l:
        return None
    return (l / f - 1.0) * 100.0


def performance_vs_1(share_price):
    sp = _f(share_price)
    if sp is None:
        return None
    return (sp - 1.0) * 100.0


def detect_events(conn, vault_id, prev_sp, cur_sp, is_new_vault):
    """Log peristiwa: vault baru, share price menembus 1.0."""
    now = int(time.time() * 1000)
    out = []
    if is_new_vault:
        out.append(("new_vault", vault_id, "New vault detected for the first time"))
    p, c = _f(prev_sp), _f(cur_sp)
    if p is not None and c is not None:
        if p < 1.0 <= c:
            out.append(("sp_cross_up", vault_id,
                        f"Share price crossed above 1.0 ({p:.6f} -> {c:.6f})"))
        elif p >= 1.0 > c:
            out.append(("sp_cross_down", vault_id,
                        f"Share price fell below 1.0 ({p:.6f} -> {c:.6f})"))
    for kind, vid, detail in out:
        conn.execute("INSERT INTO events (ts, kind, vault_id, detail) VALUES (?,?,?,?)",
                     (now, kind, vid, detail))
        log.info("EVENT %s %s: %s", kind, vid, detail)
    return out


def recent_events(conn, limit=20):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()]


def build_leaderboard(conn):
    """Leaderboard semua vault + strip statistik ekosistem."""
    vaults = conn.execute("SELECT * FROM vaults ORDER BY name").fetchall()
    rows = []
    total_equity = 0.0
    total_depositors = 0
    underwater = 0
    for v in vaults:
        vid = v["vault_id"]
        last = latest_snapshot(conn, vid)
        if last is None:
            continue
        sp = _f(last["share_price"])
        eq = _f(last["equity"], 0.0)
        dep = last["depositors"] or 0
        total_equity += eq
        total_depositors += dep
        label = risk_label(sp)
        if label == "Underwater":
            underwater += 1
        rows.append({
            "vault_id": vid,
            "name": v["name"],
            "type": v["type"],
            "risk_label": label,
            "share_price": sp,
            "perf_sejak_tracker_pct": performance_vs_first(conn, vid),
            "perf_vs_1_pct": performance_vs_1(sp),
            "equity": eq,
            "depositors": dep,
            "leader_commission_bps": v["leader_commission_bps"],
            "lockup_days": v["lockup_days"],
            "min_deposit": v["min_deposit"],
            "apr": last["apr"],
            "period_return": last["period_return"],
            "all_time_pnl": last["all_time_pnl"],
            "sparkline": share_price_series(conn, vid),
            "snapshot_ts": last["ts"],
        })
    rows.sort(key=lambda r: (r["perf_vs_1_pct"] if r["perf_vs_1_pct"] is not None else -999),
              reverse=True)
    return {
        "vaults": rows,
        "ecosystem": {
            "vault_count": len(rows),
            "total_equity": total_equity,
            "total_depositors": total_depositors,
            "underwater_count": underwater,
            "healthy_count": len(rows) - underwater,
        },
        "events": recent_events(conn),
    }
