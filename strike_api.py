"""HTTP helpers for EarnScope: polite polling with exponential backoff."""
import json
import logging
import time
import requests

log = logging.getLogger("earnscope.strike_api")

UA = {"User-Agent": "EarnScope/0.1 (Strike 48h hackathon; transparent Earn hub)"}

STRIKE_BASE = "https://api.strikefinance.org"
KOIOS_BASE = "https://api.koios.rest/api/v1"

STRIKE_POLICY = "f13ac4d66b3ee19a6aa0f2a22298737bd907cc95121662fc971b5275"
STRIKE_ASSET = "535452494b45"  # hex of "STRIKE"

# SLP = vault protocol, bahan utama Rewards Projector
SLP_VAULT_ID = "3fd3ab0b-318b-4f99-9ad6-66e9e917bea8"


def get_json(url, params=None, timeout=25, retries=4, base_delay=2.0):
    """GET JSON with polite exponential backoff. Returns dict/list or None.

    Menangkap Exception luas (bukan cuma requests.RequestException) supaya
    error aneh dari proxy egress tidak pernah lolos sebagai crash — berubah
    jadi retry lalu None. KeyboardInterrupt/SystemExit tetap lolos
    (BaseException tidak ditangkap).
    """
    delay = base_delay
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            log.warning("GET %s -> %s (attempt %d)", url, r.status_code, attempt + 1)
            if r.status_code in (429, 500, 502, 503):
                pass  # retryable
            else:
                return None
        except Exception as e:
            log.warning("GET %s failed (attempt %d): %s: %s", url, attempt + 1,
                        type(e).__name__, e)
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    log.error("GET %s gave up after %d attempts", url, retries + 1)
    return None


def post_json(url, payload, timeout=25, retries=3, base_delay=2.0):
    delay = base_delay
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json=payload, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            log.warning("POST %s -> %s (attempt %d)", url, r.status_code, attempt + 1)
            if r.status_code not in (429, 500, 502, 503):
                return None
        except Exception as e:
            log.warning("POST %s failed (attempt %d): %s: %s", url, attempt + 1,
                        type(e).__name__, e)
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    log.error("POST %s gave up after %d attempts", url, retries + 1)
    return None


def fetch_vaults():
    d = get_json(f"{STRIKE_BASE}/v2/vaults")
    if isinstance(d, dict) and "vaults" in d:
        return d["vaults"]
    return []


def fetch_vault_detail(vault_id):
    return get_json(f"{STRIKE_BASE}/v2/vault/{vault_id}")


def fetch_vault_history(vault_id):
    # CATATAN: paginasi API rusak di sisi server (offset/next_cursor diabaikan,
    # selalu mengembalikan halaman pertama). Ambil limit terbesar yang stabil.
    d = get_json(f"{STRIKE_BASE}/v2/vault/{vault_id}/history",
                 params={"limit": 250}, timeout=40)
    if isinstance(d, dict) and isinstance(d.get("history"), list):
        return d["history"]
    return []


def fetch_vault_portfolio(vault_id):
    return get_json(f"{STRIKE_BASE}/v2/vault/{vault_id}/portfolio", timeout=40)


def fetch_vault_depositors(vault_id):
    return get_json(f"{STRIKE_BASE}/v2/vault/{vault_id}/depositors", timeout=40)


def fetch_vault_depositors(vault_id):
    return get_json(f"{STRIKE_BASE}/v2/vault/{vault_id}/depositors", timeout=40)


def fetch_vault_depositors_all(vault_id):
    """Semua depositor: satu request limit=250 (cukup untuk semua vault saat ini;
    paginasi cursor API diabaikan server, jadi tidak dipakai)."""
    d = get_json(f"{STRIKE_BASE}/v2/vault/{vault_id}/depositors",
                 params={"limit": 250}, timeout=40)
    if not isinstance(d, dict):
        return {"count": 0, "depositors": []}
    deps = d.get("depositors", []) or []
    return {"count": d.get("count", len(deps)), "depositors": deps}


def koios_asset_info():
    return post_json(f"{KOIOS_BASE}/asset_info",
                     {"_asset_list": [[STRIKE_POLICY, STRIKE_ASSET]]})


def fetch_strike_holders_all(page_limit=100, soft_deadline_s=1200):
    """Semua holder STRIKE via Koios asset_addresses (paginasi offset).
    CATATAN: page_limit=100 — limit 1000 sering read-timeout di server Koios.
    soft_deadline_s: batas lunak total — bila terlampaui, kembalikan data
    parsial yang sudah terkumpul (lebih baik daripada menahan siklus vault
    berjam-jam saat Koios ngadat)."""
    holders = []
    offset = 0
    t0 = time.time()
    while True:
        if time.time() - t0 > soft_deadline_s:
            log.warning("asset_addresses: deadline %ds tercapai di offset %d — "
                        "pakai %d holder parsial", soft_deadline_s, offset, len(holders))
            break
        rows = get_json(
            f"{KOIOS_BASE}/asset_addresses",
            params={"_asset_policy": STRIKE_POLICY,
                    "_asset_name": STRIKE_ASSET,
                    "limit": page_limit, "offset": offset},
            timeout=75, retries=3)
        if not rows:
            break
        holders.extend(rows)
        if len(rows) < page_limit:
            break
        offset += page_limit
    return holders


def strike_holder_snapshot():
    """Jumlah holder + konsentrasi top holder STRIKE. Returns dict."""
    holders = fetch_strike_holders_all()
    info = koios_asset_info()
    supply = None
    if info and isinstance(info, list) and info:
        supply = info[0].get("total_supply")
    qtys = []
    for h in holders:
        try:
            qtys.append(int(h.get("quantity", "0")))
        except (TypeError, ValueError):
            pass
    qtys.sort(reverse=True)
    total_held = sum(qtys)
    sup = int(supply) if supply else (total_held or 1)
    def share(n):
        return sum(qtys[:n]) / sup * 100.0 if sup else 0.0
    return {
        "holder_count": len(qtys),
        "top1_pct": round(share(1), 4),
        "top5_pct": round(share(5), 4),
        "top10_pct": round(share(10), 4),
        "supply_total": supply,
        "total_held": str(total_held),
    }
