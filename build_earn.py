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
                last_err = "bentuk respons tak dikenal"
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
            "Skema resmi Borrow to Earn: pengguna mengunci jaminan (mis. ADA), "
            "menerima kredit dalam USD dengan bunga 0 persen, dan kredit itu "
            "wajib disalurkan ke vault Strike Liquidity Provider (SLP). "
            "Simulator di borrow.html menghitung: kredit = nilai jaminan x "
            "persen draw; nilai akhir kredit = kredit x (1 + kinerja SLP pada "
            "skenario terpilih); kesehatan line = ekuitas jaminan akhir / "
            "pokok kredit. Harga ADA diambil live dari API pasar Strike setiap "
            "build; bila gagal, simulator meminta input manual dari pengguna. "
            "Seri kinerja SLP berasal dari snapshot tracker EarnScope sendiri."
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
        "name": "Vault Strike",
        "tagline": ("Vault copy trading: modal mengikuti strategi leader. "
                    "Kinerja diukur dari share price riil, bukan klaim APR."),
        "numbers": [
            {"label": "Vault terlacak", "value": len(vrows), "unit": ""},
            {"label": "Total equity", "value": sum(equities) if equities else None,
             "unit": "USD"},
            {"label": "Kinerja terbaik vs 1.0",
             "value": max(perfs) if perfs else None, "unit": "%"},
            {"label": "Kinerja terburuk vs 1.0",
             "value": min(perfs) if perfs else None, "unit": "%"},
            {"label": "Vault underwater",
             "value": eco.get("underwater_count"), "unit": ""},
        ],
        "vaults": vrows,
        "methodology": (
            "Share price, equity, dan jumlah depositor diambil dari API publik "
            "Strike tiap ±5 menit. 'Kinerja vs 1.0' = (share price / 1.0 - 1), "
            "mengukur sejak inception vault. 'Sejak tracker' mengukur sejak "
            "snapshot pertama EarnScope. Komisi leader memotong hasil dan "
            "sudah tercermin di share price."
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
        "tagline": ("Deposit di vault SLP sekaligus mengumpulkan poin Season 1 "
                    "yang dikonversi menjadi STRIKE. Angka boost di bawah ini "
                    "berlabel ESTIMASI."),
        "is_estimate": True,
        "numbers": [
            {"label": "Kinerja SLP vs 1.0",
             "value": slp.get("perf_vs_1_pct") if slp else None, "unit": "%"},
            {"label": "Kinerja SLP sejak tracker",
             "value": slp.get("perf_sejak_tracker_pct") if slp else None,
             "unit": "%"},
            {"label": "Equity SLP", "value": slp_equity, "unit": "USD"},
            {"label": "Depositor SLP",
             "value": slp.get("depositors") if slp else None, "unit": ""},
            {"label": "ESTIMASI poin per hari per $1.000",
             "value": (boost["per_1000_usd"]["points_per_day"]
                       if boost else None), "unit": "poin"},
            {"label": "ESTIMASI STRIKE per hari per $1.000",
             "value": (boost["per_1000_usd"]["strike_per_day"]
                       if boost else None), "unit": "STRIKE"},
            {"label": "ESTIMASI STRIKE s/d akhir season per $1.000",
             "value": (boost["per_1000_usd"]["strike_until_season_end"]
                       if boost else None), "unit": "STRIKE"},
            {"label": "Sisa waktu season",
             "value": round(remaining, 1), "unit": "hari"},
        ],
        "season": {
            "ends": "28 Okt 2026",
            "points_per_day": POINTS_PER_DAY,
            "pool_strike": SEASON_POOL_STRIKE,
            "strike_per_point": STRIKE_PER_POINT,
            "remaining_days": round(remaining, 2),
        },
        "methodology": (
            "Parameter resmi Season 1: 888 poin per hari dibagi pro-rata "
            "berdasarkan rata-rata deposit tertimbang waktu; pool 20.000 "
            "STRIKE; ±0,7508 STRIKE per poin; season berakhir 28 Okt 2026. "
            "Strike menghitung poin dari snapshot per jam di server internal "
            "mereka; angka di sini merekonstruksi pembagian pro-rata dari "
            "equity SLP publik sehingga ini ESTIMASI, bukan angka resmi."
        ),
        "data_gaps": [
            "Rata-rata deposit tertimbang waktu per depositor tidak tersedia "
            "publik; pro-rata dihitung dari equity agregat snapshot terakhir.",
        ],
        "detail_page": None,
    })

    # 3. Borrow to Earn
    borrow = payload.get("borrow") or {}
    products.append({
        "id": "borrow",
        "name": "Borrow to Earn",
        "tagline": ("Jaminan (mis. ADA) menjadi kredit USD bunga 0 persen yang "
                    "wajib masuk SLP: eksposur SLP ter-leverage tanpa biaya bunga."),
        "numbers": [
            {"label": "Harga ADA-USD (live)",
             "value": borrow.get("ada_usd"), "unit": "USD",
             "note": borrow.get("ada_usd_ts_wib")},
            {"label": "Kinerja SLP sejak tracker (basis leverage)",
             "value": slp.get("perf_sejak_tracker_pct") if slp else None,
             "unit": "%"},
            {"label": "Bunga kredit", "value": 0, "unit": "%"},
            {"label": "Tujuan wajib kredit", "value": "Vault SLP", "unit": ""},
        ],
        "mechanism": [
            "Kunci jaminan, mis. ADA senilai C USD.",
            "Tarik kredit USD sebesar persen draw dari C, bunga 0 persen.",
            "Kredit wajib disalurkan ke vault SLP.",
            "Hasil bersih = nilai kredit di SLP pada akhir horizon - pokok kredit.",
            "Kesehatan line = ekuitas jaminan akhir / pokok kredit.",
        ],
        "health_thresholds": [
            {"min_ratio": 2.0, "status": "Aman"},
            {"min_ratio": 1.5, "status": "Waspada"},
            {"min_ratio": 0.0, "status": "Bahaya"},
        ],
        "methodology": (
            "Mekanisme mengikuti halaman resmi Borrow and Earn di "
            "docs.strikefinance.org. Simulator memakai harga ADA live dari "
            "API pasar Strike dan kinerja SLP historis dari snapshot "
            "EarnScope. Threshold kesehatan line adalah asumsi EarnScope: "
            "Strike tidak mempublikasikan rasio likuidasi resmi."
        ),
        "data_gaps": [
            "Rasio likuidasi / margin call resmi tidak dipublikasikan.",
            "Persentase draw maksimum per jenis jaminan tidak dipublikasikan "
            "via API publik.",
        ],
        "detail_page": "borrow.html",
    })

    # 4. Staking STRIKE
    products.append({
        "id": "staking",
        "name": "Staking STRIKE",
        "tagline": "Bagi hasil revenue Strike untuk staker STRIKE.",
        "numbers": [
            {"label": "APR resmi",
             "value": None, "unit": "",
             "status": "Tidak dipublikasikan"},
            {"label": "Mekanisme bagi hasil",
             "value": "Revenue sharing", "unit": ""},
        ],
        "methodology": (
            "Tokenomics Strike menyebut staker menerima bagian revenue. "
            "Namun APR resmi TIDAK dipublikasikan melalui API publik mana pun "
            "yang kami temukan, dan total staking resmi tidak bisa diturunkan "
            "dari data on-chain (registry staking bersifat opt-in di server "
            "Strike). EarnScope menulis apa adanya dan tidak mengarang angka."
        ),
        "data_gaps": [
            "APR / APY staking tidak dipublikasikan.",
            "Total STRIKE yang di-stake tidak bisa diverifikasi on-chain.",
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
        "source": "Koios (api.koios.rest): asset_addresses + asset_info, tanpa key",
        "methodology": (
            "Jumlah holder dan konsentrasi top holder dihitung dari seluruh "
            "alamat pemegang aset STRIKE via Koios asset_addresses (paginasi), "
            "supply dari asset_info; snapshot diambil 1x per hari. "
            "Total staking resmi TIDAK ditampilkan di panel ini dalam bentuk "
            "apa pun: liquid staking Strike memakai registry opt-in yang "
            "disimpan di server Strike (off-chain), sehingga tidak bisa "
            "diturunkan dari data on-chain. Panel ini tidak mengarang angka "
            "staking."
        ),
    }
    return out


def _skeleton_borrow(err):
    return {"ada_usd": None, "ada_usd_ts": None, "ada_usd_ts_wib": None,
            "ada_symbol": ADA_SYMBOL, "last_ok": False, "fetch_error": err,
            "slp_series": [], "slp_series_raw_count": 0,
            "methodology_note": "Gagal membangun data borrow pada build ini."}


def _skeleton_onchain(err):
    return {"holder_count": None, "top1_pct": None, "top5_pct": None,
            "top10_pct": None, "supply_total_raw": None,
            "supply_strike": 25000000,
            "ts": None, "ts_wib": None, "history": [], "snapshot_count": 0,
            "error": err,
            "methodology": ("Data holder belum tersedia pada build ini. "
                            "Total staking resmi tidak bisa diturunkan "
                            "on-chain dan tidak ditampilkan.")}


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
