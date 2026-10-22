"""Data halaman detail per vault untuk EarnScope.

Fungsi utama: extend(conn, payload) -> payload (dict data.json) ditambah
payload["vault_detail"] = {vault_id: {...}}.

Sengaja dipisah dari build_dashboard.py supaya build dashboard utama tetap
ringan; coordinator memanggil extend() setelah payload leaderboard jadi.
Semua kegagalan per-vault hanya jadi WARNING di log, tidak pernah raise.
"""
import json
import logging
import time

import metrics
import strike_api

log = logging.getLogger("earnscope.build_detail")

# Jumlah titik maksimum per seri di data.json (downsample bila lebih).
MAX_POINTS = 1500

# TTL cache history resmi di tabel meta (6 jam) supaya build 5-menitan
# tidak menghantam API portfolio tiap siklus.
OFFICIAL_CACHE_TTL_MS = 6 * 3600 * 1000

# Kolom resmi di array history /portfolio (terverifikasi 22 Okt 2026 via GET
# langsung: tiap baris = [ts_ms, tvl_usd, pnl_usd, fees_earned_usd,
# depositors, share_price]; param period=30d|7d|all berfungsi, default 30d).
HISTORY_FIELDS = ["ts_ms", "tvl_usd", "pnl_usd", "fees_earned_usd",
                  "depositors", "share_price"]

# Kunci portfolio yang diringkas untuk "isi portfolio" (agregat resmi).
PORTFOLIO_KEYS = [
    "total_value_locked", "apr", "period_start_share_price",
    "period_start_timestamp", "all_time_deposits", "all_time_withdrawals",
    "all_time_pnl", "return_24h", "period_return", "volume", "all_time_volume",
    "depositors", "fee", "commission_bps", "fee_earned",
    "pending_fees_owed", "sharpe_ratio", "max_drawdown",
    "total_liquidation_fees_earned", "created_at",
]


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _downsample(points, limit=MAX_POINTS):
    """Ambil sampel merata bila titik melebihi limit; selalu simpan ujung."""
    n = len(points)
    if n <= limit:
        return points
    step = n / limit
    out = [points[int(i * step)] for i in range(limit)]
    if out[-1] != points[-1]:
        out[-1] = points[-1]
    return out


def _official_history(conn, vault_id):
    """History resmi dari /v2/vault/{id}/portfolio?period=all, dengan cache.

    Returns dict {share_price, tvl, depositors, source, fetched_at_ms, ...}.
    Gagal total -> dict kosong (graceful, tidak raise).
    """
    cache_key = "official_history_cache_" + vault_id
    now = int(time.time() * 1000)
    raw = None
    fetched_at = None

    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?",
                           (cache_key,)).fetchone()
        if row:
            cached = json.loads(row["value"])
            fetched_at = cached.get("fetched_at_ms")
            if fetched_at and now - fetched_at < OFFICIAL_CACHE_TTL_MS:
                raw = cached.get("history") or []
    except Exception as e:
        log.warning("baca cache official history %s gagal: %s: %s",
                    vault_id[:8], type(e).__name__, e)

    if raw is None:
        # Cache kosong/kedaluwarsa: fetch resmi, toleran penuh terhadap error.
        try:
            d = strike_api.get_json(
                f"{strike_api.STRIKE_BASE}/v2/vault/{vault_id}/portfolio",
                params={"period": "all"}, timeout=30, retries=1)
            if isinstance(d, dict) and isinstance(d.get("history"), list):
                raw = d["history"]
                fetched_at = now
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        (cache_key, json.dumps(
                            {"fetched_at_ms": fetched_at, "history": raw})))
                    conn.commit()
                except Exception as e:
                    log.warning("simpan cache official history %s gagal: %s: %s",
                                vault_id[:8], type(e).__name__, e)
            else:
                raw = []
                log.warning("official history %s: respons tak terduga (%s)",
                            vault_id[:8], type(d).__name__ if d is not None else "None")
        except Exception as e:
            log.warning("official history %s gagal: %s: %s",
                        vault_id[:8], type(e).__name__, e)
            raw = []

    sp, tvl, deps = [], [], []
    try:
        for pt in raw or []:
            if not isinstance(pt, (list, tuple)) or len(pt) < 6:
                continue
            ts = int(pt[0])
            v_sp = _f(pt[5])
            v_tvl = _f(pt[1])
            v_dep = _f(pt[4])  # depositors = kolom ke-5
            if v_sp is not None:
                sp.append([ts, v_sp])
            if v_tvl is not None:
                tvl.append([ts, v_tvl])
            if v_dep is not None:
                deps.append([ts, int(v_dep)])
    except Exception as e:
        log.warning("parse official history %s gagal: %s: %s",
                    vault_id[:8], type(e).__name__, e)
        return {"share_price": [], "tvl": [], "depositors": []}

    return {
        "share_price": _downsample(sp),
        "tvl": _downsample(tvl),
        "depositors": _downsample(deps),
        "fields": HISTORY_FIELDS,
        "source": (f"{strike_api.STRIKE_BASE}/v2/vault/{vault_id}/portfolio"
                   "?period=all"),
        "point_count": len(sp),
        "first_ts": sp[0][0] if sp else None,
        "last_ts": sp[-1][0] if sp else None,
        "fetched_at_ms": fetched_at,
    }


def _series(conn, vault_id):
    """Seri share price dari snapshot EarnScope sendiri: [[ts_ms, sp], ...]."""
    rows = conn.execute(
        "SELECT ts, share_price FROM vault_snapshots WHERE vault_id=? "
        "AND share_price IS NOT NULL ORDER BY ts",
        (vault_id,)).fetchall()
    pts = []
    for r in rows:
        sp = _f(r["share_price"])
        if sp is not None:
            pts.append([int(r["ts"]), sp])
    return _downsample(pts)


def _depositor_trend(conn, vault_id):
    """Tren jumlah depositor: [[ts_ms, count], ...]."""
    rows = conn.execute(
        "SELECT ts, count FROM vault_depositor_snapshots WHERE vault_id=? "
        "AND count IS NOT NULL ORDER BY ts",
        (vault_id,)).fetchall()
    return [[int(r["ts"]), int(r["count"])] for r in rows]


def _portfolio_summary(conn, vault_id):
    """Ringkasan isi portfolio terbaru (kunci agregat resmi saja)."""
    try:
        row = conn.execute(
            "SELECT ts, payload_json FROM vault_portfolio_latest WHERE vault_id=?",
            (vault_id,)).fetchone()
        if not row or not row["payload_json"]:
            return None
        d = json.loads(row["payload_json"])
        out = {k: d.get(k) for k in PORTFOLIO_KEYS if k in d}
        out["fetched_at_ms"] = int(row["ts"])
        return out
    except Exception as e:
        log.warning("portfolio summary %s gagal: %s: %s",
                    vault_id[:8], type(e).__name__, e)
        return None


def _meta(conn, vault_id):
    """Kartu info vault: nama, komisi leader, lockup, min deposit, dsb."""
    try:
        v = conn.execute("SELECT * FROM vaults WHERE vault_id=?",
                         (vault_id,)).fetchone()
        if v is None:
            return None
        last = metrics.latest_snapshot(conn, vault_id)
        sp = _f(last["share_price"]) if last is not None else None
        bps = v["leader_commission_bps"]
        return {
            "name": v["name"],
            "type": v["type"],
            "nickname": v["nickname"],
            "leader_account_id": v["leader_account_id"],
            "leader_commission_bps": bps,
            "leader_commission_pct": (bps / 100.0) if bps is not None else None,
            "lockup_days": v["lockup_days"],
            "min_deposit": v["min_deposit"],
            "equity": _f(last["equity"]) if last is not None else None,
            "total_shares": _f(last["total_shares"]) if last is not None else None,
            "depositors": last["depositors"] if last is not None else None,
            "share_price": sp,
            "risk_label": metrics.risk_label(sp),
            "last_snapshot_ms": int(last["ts"]) if last is not None else None,
            "status": v["status"],
        }
    except Exception as e:
        log.warning("meta vault %s gagal: %s: %s",
                    vault_id[:8], type(e).__name__, e)
        return None


def extend(conn, payload):
    """Tambahkan payload["vault_detail"]; tidak pernah raise.

    Coordinator memanggil ini setelah payload leaderboard jadi, sebelum
    data.json ditulis.
    """
    detail = {}
    try:
        rows = conn.execute("SELECT vault_id FROM vaults").fetchall()
    except Exception as e:
        log.warning("daftar vault gagal dibaca: %s: %s",
                    type(e).__name__, e)
        payload["vault_detail"] = {}
        return payload

    for r in rows:
        vid = r["vault_id"]
        try:
            detail[vid] = {
                "series": _series(conn, vid),
                "official_history": _official_history(conn, vid),
                "portfolio": _portfolio_summary(conn, vid),
                "depositor_trend": _depositor_trend(conn, vid),
                "meta": _meta(conn, vid),
            }
        except Exception as e:
            # Pengaman terakhir: satu vault rusak tidak boleh mematikan build.
            log.warning("detail vault %s dilewati: %s: %s",
                        vid[:8], type(e).__name__, e)
            detail[vid] = {"series": [], "official_history": {},
                           "portfolio": None, "depositor_trend": [],
                           "meta": None}
    payload["vault_detail"] = detail
    payload["vault_detail_note"] = (
        "'series' = EarnScope's own snapshots (live, every ±5 minutes); "
        "'official_history' = reconstruction from Strike's official history "
        "(/v2/vault/{id}/portfolio?period=all, columns [ts, tvl, pnl, "
        "feesEarned, depositors, share_price]); 'portfolio' = latest "
        "official aggregates (the API does not expose per-market position "
        "details)."
    )
    return payload


if __name__ == "__main__":
    import db
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    db.init_db()
    conn = db.connect()
    p = {"test": True}
    extend(conn, p)
    conn.close()
    print("vaults:", len(p["vault_detail"]))
    for vid, d in p["vault_detail"].items():
        m = d["meta"] or {}
        print(vid[:8], m.get("name"),
              "| series:", len(d["series"]),
              "| official sp:", len(d["official_history"].get("share_price", [])),
              "| depositor_trend:", len(d["depositor_trend"]),
              "| portfolio:", "ok" if d["portfolio"] else "empty")
