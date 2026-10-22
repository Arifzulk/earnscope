"""Ekstensi payload dashboard EarnScope: simulator deposit, rewards projector,
position tracker.

Dipakai sebagai: build_projector.extend(conn, payload) setelah
metrics.build_leaderboard() membangun payload dasar. Fungsi extend TIDAK
pernah raise: setiap bagian dibungkus try/except dan kegagalan hanya
menghasilkan {"error": ...} di key terkait.

Jalankan langsung (python3 build_projector.py) untuk menambal web/data.json
yang sudah ada dengan ketiga bagian ini tanpa membangun ulang leaderboard.
"""
import json
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db  # noqa: E402

log = logging.getLogger("earnscope.build_projector")

WEB_DIR = os.path.join(HERE, "web")
DATA_JSON = os.path.join(WEB_DIR, "data.json")

# --- Konstanta Strike Rewards Season 1 (asumsi terdokumentasi di bawah) ---
# Season berakhir 28 Okt 2026; jam penutupan PASTI tidak terverifikasi dari
# docs resmi (docs.strikefinance.org tidak bisa diakses dari mesin ini saat
# build), jadi dipakai 23:59 WIB sebagai asumsi eksplisit.
SEASON_END_MS = int(__import__("datetime").datetime(
    2026, 10, 28, 23, 59, 59,
    tzinfo=__import__("datetime").timezone(
        __import__("datetime").timedelta(hours=7))).timestamp() * 1000)
SEASON_END_LABEL = "28 Oct 2026 23:59 WIB (assumed closing time; the exact closing time is not yet verified)"
POINTS_PER_DAY = 888
POOL_STRIKE = 20000
STRIKE_PER_POINT = 0.7508

SLP_VAULT_ID = "3fd3ab0b-318b-4f99-9ad6-66e9e917bea8"

METHODOLOGY_NOTE = (
    "ESTIMATE. Strike Rewards Season 1 distributes 888 points per day "
    "pro-rata based on time-weighted average deposits, computed from "
    "Strike's internal hourly snapshots. EarnScope does not have access to "
    "those internal snapshots, so this projection is reconstructed from "
    "public data: each SLP depositor's current equity position is used as a "
    "proxy for their time-weighted average deposit. The estimate covers only "
    "the remaining days until the season ends and may differ from Strike's "
    "official calculation. Conversion to STRIKE uses approximately 0.7508 "
    "STRIKE per point from the 20,000 STRIKE pool. These figures are NOT a "
    "promise of returns."
)


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _downsample(rows, max_points=1500):
    n = len(rows)
    if n <= max_points or n == 0:
        return rows
    step = n / float(max_points)
    return [rows[int(i * step)] for i in range(max_points)]


def _sp_series(conn, vault_id, max_points=1500):
    rows = conn.execute(
        "SELECT ts, share_price FROM vault_snapshots WHERE vault_id=? "
        "AND share_price IS NOT NULL ORDER BY ts", (vault_id,)).fetchall()
    pts = [[r["ts"], round(_f(r["share_price"]), 6)] for r in rows
           if _f(r["share_price"]) is not None]
    return _downsample(pts, max_points)


def _equity_series(conn, vault_id, max_points=1500):
    rows = conn.execute(
        "SELECT ts, equity FROM vault_snapshots WHERE vault_id=? "
        "AND equity IS NOT NULL ORDER BY ts", (vault_id,)).fetchall()
    pts = [[r["ts"], round(_f(r["equity"]), 2)] for r in rows
           if _f(r["equity"]) is not None]
    return _downsample(pts, max_points)


def _latest_depositor_rows(conn, vault_id):
    row = conn.execute(
        "SELECT ts, count, depositors_json FROM vault_depositor_snapshots "
        "WHERE vault_id=? ORDER BY ts DESC LIMIT 1", (vault_id,)).fetchone()
    if row is None:
        return None, 0, []
    try:
        deps = json.loads(row["depositors_json"] or "[]")
    except (TypeError, ValueError):
        deps = []
    return row["ts"], row["count"] or len(deps), deps


def _norm_depositor(d):
    """Normalisasi satu baris depositor dari endpoint /v2/vault/{id}/depositors.

    Field terverifikasi langsung dari API (Okt 2026): address, deposited,
    total_deposited, total_withdrawn, current_equity, share_percentage, pnl,
    deposited_since (ms). Endpoint TIDAK mengembalikan nickname.
    """
    addr = (d.get("address") or "")
    if isinstance(addr, str):
        addr = addr.strip().lower()
    return {
        "address": addr,
        "nickname": None,
        "total_deposited": _f(d.get("total_deposited", d.get("deposited")), 0.0),
        "total_withdrawn": _f(d.get("total_withdrawn"), 0.0),
        "current_equity": _f(d.get("current_equity"), 0.0),
        "share_pct": _f(d.get("share_percentage"), 0.0),
        "pnl": _f(d.get("pnl"), 0.0),
        "deposited_since_ms": d.get("deposited_since"),
    }


def _build_simulator(conn):
    out = {}
    vaults = conn.execute(
        "SELECT vault_id, name, leader_commission_bps FROM vaults").fetchall()
    for v in vaults:
        vid = v["vault_id"]
        series = _sp_series(conn, vid)
        if not series:
            continue
        out[vid] = {
            "name": v["name"],
            "commission_bps": v["leader_commission_bps"],
            "series": series,
            "current_sp": series[-1][1],
            "first_sp": series[0][1],
            "min_ts": series[0][0],
            "max_ts": series[-1][0],
            "point_count": len(series),
        }
    return {"vaults": out, "note": "Share price series from EarnScope's own snapshots."}


def _build_rewards(conn):
    slp_name = conn.execute(
        "SELECT name FROM vaults WHERE vault_id=?", (SLP_VAULT_ID,)).fetchone()
    ts, count, deps = _latest_depositor_rows(conn, SLP_VAULT_ID)
    norm = [_norm_depositor(d) for d in deps]
    norm.sort(key=lambda d: d["current_equity"], reverse=True)
    total_equity = sum(d["current_equity"] for d in norm)
    return {
        "season_end_ms": SEASON_END_MS,
        "season_end_label": SEASON_END_LABEL,
        "points_per_day": POINTS_PER_DAY,
        "pool_strike": POOL_STRIKE,
        "strike_per_point": STRIKE_PER_POINT,
        "slp_vault_id": SLP_VAULT_ID,
        "slp_vault_name": slp_name["name"] if slp_name else "Strike Liquidity Provider (SLP)",
        "slp_depositor_count": count,
        "slp_total_equity": round(total_equity, 2),
        "slp_snapshot_ts": ts,
        "slp_depositors": norm,
        "slp_equity_series": _equity_series(conn, SLP_VAULT_ID),
        "methodology_note": METHODOLOGY_NOTE,
    }


def _build_tracker(conn):
    out = {}
    vaults = conn.execute("SELECT vault_id, name FROM vaults").fetchall()
    for v in vaults:
        vid = v["vault_id"]
        ts, count, deps = _latest_depositor_rows(conn, vid)
        out[vid] = {
            "name": v["name"],
            "snapshot_ts": ts,
            "count": count,
            "depositors": [_norm_depositor(d) for d in deps],
        }
    return {"vaults": out}


def extend(conn, payload):
    """Tambahkan simulator/rewards/tracker ke payload dashboard. Tidak raise."""
    try:
        payload["simulator"] = _build_simulator(conn)
    except Exception as e:
        log.warning("simulator gagal: %s", e)
        payload["simulator"] = {"error": "simulator data unavailable"}
    try:
        payload["rewards"] = _build_rewards(conn)
    except Exception as e:
        log.warning("rewards gagal: %s", e)
        payload["rewards"] = {"error": "rewards data unavailable"}
    try:
        payload["tracker"] = _build_tracker(conn)
    except Exception as e:
        log.warning("tracker gagal: %s", e)
        payload["tracker"] = {"error": "tracker data unavailable"}
    return payload


def patch_data_json():
    """Satu kali: baca web/data.json yang ada, tambahkan ketiga bagian, tulis balik."""
    conn = db.connect()
    try:
        with open(DATA_JSON) as f:
            payload = json.load(f)
        extend(conn, payload)
        payload["generated_at_ms"] = int(time.time() * 1000)
        tmp = DATA_JSON + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, DATA_JSON)
        log.info("data.json ditambal: simulator=%d vault, rewards depositors=%d, tracker=%d vault",
                 len(payload.get("simulator", {}).get("vaults", {})),
                 len(payload.get("rewards", {}).get("slp_depositors", [])),
                 len(payload.get("tracker", {}).get("vaults", {})))
        return payload
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    patch_data_json()
