"""Modul ekspansi EarnScope: Borrow to Earn, papan banding Earn, panel STRIKE on-chain.

Cara pakai (satu baris di build_dashboard.build, SETELAH payload leaderboard jadi):

    import build_earn
    ...
    payload = {"generated_at_ms": now_ms, **board}
    build_earn.extend(conn, payload)   # <-- tambah baris ini

Fungsi extend(conn, payload) menambah tiga kunci ke payload:
  payload["borrow"]   - harga ADA-USD live + seri share price SLP (bahan simulator)
  payload["compare"]  - array produk Earn untuk papan banding
  payload["onchain"]  - holder STRIKE dari Koios (tanpa angka staking apa pun)

Tidak pernah me-raise: setiap bagian dibungkus try/except dan degradasi
menjadi skeleton bernilai null bila sumber datanya gagal. Kegagalan satu
bagian tidak menghentikan bagian lain.
"""
import logging
import math
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("earnscope.build_earn")

UA = {"User-Agent": "EarnScope/0.1 (Strike 48h hackathon; transparent Earn hub)"}

# API pasar Strike. Bentuk respons TERVERIFIKASI 22 Okt 2026:
#   GET https://api.strikefinance.org/price/v2/markPrice?symbol=ADA-USD
#   -> {"e":"markPriceUpdate","E":1792652021748,"s":"ADA-USD",
#       "p":"0.21852849","i":"0.21821412","r":"0.000155...","T":1792652400000}
# Tanpa param symbol -> list objek yang sama untuk 32 simbol.
# Catatan: GET /price (tanpa path) dan ?symbol=ADA mengembalikan 404.
PRICE_URL = "https://api.strikefinance.org/price/v2/markPrice"
ADA_SYMBOL = "ADA-USD"

SLP_VAULT_ID = "3fd3ab0b-318b-4f99-9ad6-66e9e917bea8"
SLP_NAME = "Strike Liquidity Provider (SLP)"

# Parameter resmi Strike Rewards Season 1 (dari docs + konsep)
POINTS_PER_DAY = 888.0
STRIKE_PER_POINT = 0.7508
SEASON_POOL_STRIKE = 20000
SEASON_END_WIB = datetime(2026, 10, 28, 23, 59, 59,
                          tzinfo=timezone(timedelta(hours=7)))

MAX_SERIES_POINTS = 1500
WIB = timezone(timedelta(hours=7))


def _fmt_wib(ms):
    try:
        return datetime.fromtimestamp(ms / 1000, tz=WIB).strftime(
            "%d %b %Y %H:%M:%S WIB")
    except (TypeError, ValueError, OSError):
        return None


def _fetch_ada_usd():
    """Harga ADA-USD live dari Strike Market Data API. Cepat (maks ~2x coba)."""
    last_err = None
    for attempt in range(2):
        try:
            r = requests.get(PRICE_URL, params={"symbol": ADA_SYMBOL},
                             headers=UA, timeout=10)
            if r.status_code == 200:
                d = r.json()
                if isinstance(d, list):
                    d = next((x for x in d if x.get("s") == ADA_SYMBOL), None)
                if isinstance(d, dict):
                    raw = d.get("p") or d.get("i")
                    try:
                        price = float(raw)
                    except (TypeError, ValueError):
                        price = None
                    if price:
                        return {"ok": True, "price": price,
                                "ts": d.get("E"),
                                "symbol": d.get("s", ADA_SYMBOL),
                                "error": None}
                last_err = "unrecognized response shape"
            else:
                last_err = "HTTP %s" % r.status_code
        except Exception as e:  # proxy egress kadang memutus aneh
            last_err = "%s: %s" % (type(e).__name__, e)
        if attempt == 0:
            time.sleep(1.5)
    return {"ok": False, "price": None, "ts": None,
            "symbol": ADA_SYMBOL, "error": last_err}


def _slp_series(conn, limit=MAX_SERIES_POINTS):
    """Seri [ts_ms, share_price] SLP dari vault_snapshots, downsample ~limit."""
    rows = conn.execute(
        "SELECT ts, share_price FROM vault_snapshots WHERE vault_id=? "
        "AND share_price IS NOT NULL ORDER BY ts",
        (SLP_VAULT_ID,)).fetchall()
    pts = []
    for r in rows:
        try:
            pts.append((int(r["ts"]), float(r["share_price"])))
        except (TypeError, ValueError):
            continue
    raw_n = len(pts)
    if raw_n > limit:
        stride = math.ceil(raw_n / limit)
        cut = pts[::stride]
        if cut[-1][0] != pts[-1][0]:
            cut.append(pts[-1])
        pts = cut
    return {
        "points": [[t, round(sp, 6)] for t, sp in pts],
        "raw_count": raw_n,
        "shown_count": len(pts),
        "first_ts": pts[0][0] if pts else None,
        "last_ts": pts[-1][0] if pts else None,
    }


def _build_borrow(conn):
    fx = _fetch_ada_usd()
    now_ms = int(time.time() * 1000)
    series = _slp_series(conn)
    return {
        "ada_usd": fx["price"],
        "ada_usd_ts": fx["ts"],
        "ada_usd_ts_wib": _fmt_wib(fx["ts"]) if fx["ts"] else None,
        "ada_symbol": fx["symbol"],
        "ada_source": ("Strike Market Data API: "
                       "api.strikefinance.org/price/v2/markPrice?symbol=ADA-USD"),
        "last_ok": fx["ok"],
        "fetch_error": fx["error"],
        "built_at_ms": now_ms,
        "slp_vault_id": SLP_VAULT_ID,
        "slp_series": series["points"],
        "slp_series_raw_count": series["raw_count"],
        "slp_series_shown": series["shown_count"],
        "slp_first_ts": series["first_ts"],
        "slp_last_ts": series["last_ts"],
        "slp_first_wib": _fmt_wib(series["first_ts"]),
        "slp_last_wib": _fmt_wib(series["last_ts"]),
        "methodology_note": (
            "Official Borrow to Earn scheme: users lock collateral (e.g. ADA), "
            "receive USD credit at 0 percent interest, and that credit must be "
            "routed into the Strike Liquidity Provider (SLP) vault. The "
            "simulator in borrow.html computes: credit = collateral value x "
            "draw percentage; final credit value = credit x (1 + SLP "
            "performance in the selected scenario); line health = final "
            "collateral equity / credit principal. The ADA price is fetched "
            "live from Strike's market API on every build; if that fails, the "
            "simulator asks the user for manual input. The SLP performance "
            "series comes from EarnScope's own tracker snapshots."
        ),
    }


def _season_remaining_days(now_ms):
    end_ms = int(SEASON_END_WIB.timestamp() * 1000)
    return max(0.0, (end_ms - now_ms) / 86400000.0)


def _build_compare(conn, payload):
    vaults_in = payload.get("vaults") or []
    vrows = []
    for v in vaults_in:
        vrows.append({
            "name": v.get("name"),
            "type": v.get("type"),
            "risk_label": v.get("risk_label"),
            "share_price": v.get("share_price"),
            "perf_vs_1_pct": v.get("perf_vs_1_pct"),
            "perf_sejak_tracker_pct": v.get("perf_sejak_tracker_pct"),
            "equity": v.get("equity"),
            "depositors": v.get("depositors"),
            "leader_commission_bps": v.get("leader_commission_bps"),
            "lockup_days": v.get("lockup_days"),
            "min_deposit": v.get("min_deposit"),
        })
    perfs = [v["perf_vs_1_pct"] for v in vrows
             if v["perf_vs_1_pct"] is not None]
    equities = [v["equity"] for v in vrows if v["equity"] is not None]
    eco = payload.get("ecosystem") or {}

    products = []

    # 1. Vault (copy trading)
    products.append({
        "id": "vaults",
        "name": "Strike Vaults",
        "tagline": ("Copy-trading vaults: capital follows a leader's strategy. "
                    "Performance is measured from real share price, not APR claims."),
        "numbers": [
            {"label": "Vaults tracked", "value": len(vrows), "unit": ""},
            {"label": "Total equity", "value": sum(equities) if equities else None,
             "unit": "USD"},
            {"label": "Best performance vs 1.0",
             "value": max(perfs) if perfs else None, "unit": "%"},
            {"label": "Worst performance vs 1.0",
             "value": min(perfs) if perfs else None, "unit": "%"},
            {"label": "Underwater vaults",
             "value": eco.get("underwater_count"), "unit": ""},
        ],
        "vaults": vrows,
        "methodology": (
            "Share price, equity, and depositor counts are taken from Strike's "
            "public API every ±5 minutes. 'Performance vs 1.0' = (share price "
            "/ 1.0 - 1), measured since vault inception. 'Since tracker' "
            "measures from EarnScope's first snapshot. Leader commissions "
            "reduce returns and are already reflected in the share price."
        ),
        "data_gaps": [],
        "detail_page": "index.html",
    })

    # 2. SLP + Points (Strike Rewards Season 1)
    now_ms = int(time.time() * 1000)
    slp = next((v for v in vrows if v.get("name") == SLP_NAME), None)
    slp_equity = slp.get("equity") if slp else None
    remaining = _season_remaining_days(now_ms)
    boost = None
    if slp_equity:
        try:
            share = 1000.0 / float(slp_equity)
            pts_day = share * POINTS_PER_DAY
            strike_day = pts_day * STRIKE_PER_POINT
            boost = {
                "per_1000_usd": {
                    "points_per_day": round(pts_day, 4),
                    "strike_per_day": round(strike_day, 4),
                    "strike_until_season_end": round(strike_day * remaining, 4),
                },
                "slp_equity_used": float(slp_equity),
            }
        except (TypeError, ValueError, ZeroDivisionError):
            boost = None
    products.append({
        "id": "slp_points",
        "name": "SLP + Strike Rewards",
        "tagline": ("Depositing in the SLP vault also earns Season 1 points "
                    "that convert into STRIKE. The boost figures below are "
                    "labeled ESTIMATE."),
        "is_estimate": True,
        "numbers": [
            {"label": "SLP performance vs 1.0",
             "value": slp.get("perf_vs_1_pct") if slp else None, "unit": "%"},
            {"label": "SLP performance since tracker",
             "value": slp.get("perf_sejak_tracker_pct") if slp else None,
             "unit": "%"},
            {"label": "SLP equity", "value": slp_equity, "unit": "USD"},
            {"label": "SLP depositors",
             "value": slp.get("depositors") if slp else None, "unit": ""},
            {"label": "ESTIMATED points per day per $1,000",
             "value": (boost["per_1000_usd"]["points_per_day"]
                       if boost else None), "unit": "points"},
            {"label": "ESTIMATED STRIKE per day per $1,000",
             "value": (boost["per_1000_usd"]["strike_per_day"]
                       if boost else None), "unit": "STRIKE"},
            {"label": "ESTIMATED STRIKE until season end per $1,000",
             "value": (boost["per_1000_usd"]["strike_until_season_end"]
                       if boost else None), "unit": "STRIKE"},
            {"label": "Time left in season",
             "value": round(remaining, 1), "unit": "days"},
        ],
        "season": {
            "ends": "28 Oct 2026",
            "points_per_day": POINTS_PER_DAY,
            "pool_strike": SEASON_POOL_STRIKE,
            "strike_per_point": STRIKE_PER_POINT,
            "remaining_days": round(remaining, 2),
        },
        "methodology": (
            "Official Season 1 parameters: 888 points per day split pro-rata "
            "based on time-weighted average deposits; 20,000 STRIKE pool; "
            "±0.7508 STRIKE per point; season ends 28 Oct 2026. Strike "
            "computes points from hourly snapshots on their internal servers; "
            "the figures here reconstruct the pro-rata split from public SLP "
            "equity, so they are ESTIMATES, not official numbers."
        ),
        "data_gaps": [
            "Time-weighted average deposits per depositor are not publicly "
            "available; the pro-rata split is computed from the aggregate "
            "equity of the latest snapshot.",
        ],
        "detail_page": None,
    })

    # 3. Borrow to Earn
    borrow = payload.get("borrow") or {}
    products.append({
        "id": "borrow",
        "name": "Borrow to Earn",
        "tagline": ("Collateral (e.g. ADA) becomes 0 percent interest USD "
                    "credit that must go into SLP: leveraged SLP exposure "
                    "with no interest cost."),
        "numbers": [
            {"label": "ADA-USD price (live)",
             "value": borrow.get("ada_usd"), "unit": "USD",
             "note": borrow.get("ada_usd_ts_wib")},
            {"label": "SLP performance since tracker (leverage basis)",
             "value": slp.get("perf_sejak_tracker_pct") if slp else None,
             "unit": "%"},
            {"label": "Credit interest", "value": 0, "unit": "%"},
            {"label": "Mandatory credit destination", "value": "SLP vault", "unit": ""},
        ],
        "mechanism": [
            "Lock collateral, e.g. ADA worth C USD.",
            "Draw USD credit equal to a draw percentage of C, at 0 percent interest.",
            "The credit must be routed into the SLP vault.",
            "Net result = credit value in SLP at the end of the horizon - credit principal.",
            "Line health = final collateral equity / credit principal.",
        ],
        "health_thresholds": [
            {"min_ratio": 2.0, "status": "Safe"},
            {"min_ratio": 1.5, "status": "Caution"},
            {"min_ratio": 0.0, "status": "Danger"},
        ],
        "methodology": (
            "The mechanism follows the official Borrow and Earn page at "
            "docs.strikefinance.org. The simulator uses the live ADA price "
            "from Strike's market API and historical SLP performance from "
            "EarnScope snapshots. The line health thresholds are EarnScope "
            "assumptions: Strike does not publish an official liquidation "
            "ratio."
        ),
        "data_gaps": [
            "The official liquidation / margin call ratio is not published.",
            "The maximum draw percentage per collateral type is not "
            "published via the public API.",
        ],
        "detail_page": "borrow.html",
    })

    # 4. Staking STRIKE
    products.append({
        "id": "staking",
        "name": "Staking STRIKE",
        "tagline": "Revenue sharing from Strike for STRIKE stakers.",
        "numbers": [
            {"label": "Official APR",
             "value": None, "unit": "",
             "status": "Not published"},
            {"label": "Revenue sharing mechanism",
             "value": "Revenue sharing", "unit": ""},
        ],
        "methodology": (
            "Strike's tokenomics state that stakers receive a share of "
            "revenue. However, no official APR is published through any "
            "public API we could find, and the official total staked cannot "
            "be derived from on-chain data (the staking registry is opt-in "
            "and stored on Strike's servers). EarnScope reports this as-is "
            "and does not invent numbers."
        ),
        "data_gaps": [
            "Staking APR / APY is not published.",
            "Total STRIKE staked cannot be verified on-chain.",
        ],
        "detail_page": "onchain.html",
    })

    return products


def _build_onchain(conn):
    rows = conn.execute(
        "SELECT ts, holder_count, top1_pct, top5_pct, top10_pct, supply_total "
        "FROM strike_holder_daily ORDER BY ts").fetchall()
    history = []
    for r in rows:
        try:
            history.append([int(r["ts"]), int(r["holder_count"])])
        except (TypeError, ValueError):
            continue
    latest = rows[-1] if rows else None
    supply_raw = None
    supply_strike = None
    if latest is not None:
        try:
            supply_raw = int(latest["supply_total"])
            supply_strike = round(supply_raw / 1e6)
        except (TypeError, ValueError):
            pass
    out = {
        "holder_count": (int(latest["holder_count"])
                         if latest is not None and latest["holder_count"]
                         is not None else None),
        "top1_pct": latest["top1_pct"] if latest is not None else None,
        "top5_pct": latest["top5_pct"] if latest is not None else None,
        "top10_pct": latest["top10_pct"] if latest is not None else None,
        "supply_total_raw": supply_raw,
        "supply_strike": supply_strike,
        "ts": int(latest["ts"]) if latest is not None else None,
        "ts_wib": _fmt_wib(int(latest["ts"])) if latest is not None else None,
        "history": history,
        "snapshot_count": len(history),
        "source": "Koios (api.koios.rest): asset_addresses + asset_info, no API key required",
        "methodology": (
            "Holder count and top-holder concentration are computed from all "
            "addresses holding the STRIKE asset via Koios asset_addresses "
            "(paginated); supply comes from asset_info; snapshots are taken "
            "once per day. The official total staked is NOT shown in this "
            "panel in any form: Strike's liquid staking uses an opt-in "
            "registry stored on Strike's servers (off-chain), so it cannot "
            "be derived from on-chain data. This panel does not invent "
            "staking numbers."
        ),
    }
    return out


def _skeleton_borrow(err):
    return {"ada_usd": None, "ada_usd_ts": None, "ada_usd_ts_wib": None,
            "ada_symbol": ADA_SYMBOL, "last_ok": False, "fetch_error": err,
            "slp_series": [], "slp_series_raw_count": 0,
            "methodology_note": "Failed to build borrow data in this build."}


def _skeleton_onchain(err):
    return {"holder_count": None, "top1_pct": None, "top5_pct": None,
            "top10_pct": None, "supply_total_raw": None,
            "supply_strike": 25000000,
            "ts": None, "ts_wib": None, "history": [], "snapshot_count": 0,
            "error": err,
            "methodology": ("Holder data is not available in this build yet. "
                            "The official total staked cannot be derived "
                            "on-chain and is not displayed.")}


def extend(conn, payload):
    """Tambahkan borrow / compare / onchain ke payload. Tidak pernah raise."""
    if not isinstance(payload, dict):
        log.error("extend: payload bukan dict, dilewati")
        return payload
    try:
        payload["borrow"] = _build_borrow(conn)
    except Exception as e:
        log.warning("extend: borrow gagal: %s: %s", type(e).__name__, e)
        payload["borrow"] = _skeleton_borrow("%s: %s" % (type(e).__name__, e))
    try:
        payload["compare"] = _build_compare(conn, payload)
    except Exception as e:
        log.warning("extend: compare gagal: %s: %s", type(e).__name__, e)
        payload["compare"] = []
    try:
        payload["onchain"] = _build_onchain(conn)
    except Exception as e:
        log.warning("extend: onchain gagal: %s: %s", type(e).__name__, e)
        payload["onchain"] = _skeleton_onchain("%s: %s"
                                              % (type(e).__name__, e))
    return payload
