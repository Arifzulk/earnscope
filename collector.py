"""Collector EarnScope: polling API vault Strike tiap ±5 menit, snapshot SQLite.

Siklus inti (±5 menit): /v2/vaults + per vault detail + history (dedup by id).
Tiap jam: portfolio + depositors lengkap per vault.
Tiap 24 jam: snapshot holder STRIKE via Koios.

Berjalan selamanya; setiap siklus membangun ulang web/data.json.
Log: data/collector.log
"""
import json
import logging
import os
import sys
import time
import traceback

import db
import metrics
import build_dashboard
import strike_api

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOG_PATH = os.path.join(db.DATA_DIR, "collector.log")

os.makedirs(db.DATA_DIR, exist_ok=True)
# Hanya FileHandler: run_collector.sh mengarahkan stdout+stderr ke file yang
# sama, jadi StreamHandler akan menduplikasi setiap baris log.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH)])
log = logging.getLogger("earnscope.collector")

CYCLE_SECONDS = 5 * 60
HOURLY_SECONDS = 60 * 60
DAILY_SECONDS = 24 * 60 * 60


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ms_now():
    return int(time.time() * 1000)


def poll_vaults_cycle(conn):
    """Snapshot 5-menitan: daftar vault + detail + history events."""
    vaults = strike_api.fetch_vaults()
    if not vaults:
        log.warning("fetch_vaults kosong — siklus dilewati")
        return 0
    now = ms_now()
    new_events = 0
    for v in vaults:
        vid = v.get("vault_id")
        if not vid:
            continue
        known = conn.execute("SELECT vault_id FROM vaults WHERE vault_id=?",
                             (vid,)).fetchone()
        is_new = known is None
        conn.execute(
            """INSERT INTO vaults (vault_id,name,type,leader_account_id,nickname,
               leader_commission_bps,lockup_days,min_deposit,created_at_ms,status,
               first_seen_ts,last_seen_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(vault_id) DO UPDATE SET
                 name=excluded.name, type=excluded.type,
                 leader_account_id=excluded.leader_account_id,
                 nickname=excluded.nickname,
                 leader_commission_bps=excluded.leader_commission_bps,
                 lockup_days=excluded.lockup_days, min_deposit=excluded.min_deposit,
                 status=excluded.status, last_seen_ts=excluded.last_seen_ts""",
            (vid, v.get("name"), v.get("type"), v.get("leader_account_id"),
             v.get("nickname"), v.get("leader_commission_bps"),
             v.get("lockup_days"), v.get("min_deposit"), v.get("created_at"),
             v.get("status"), now, now))

        prev = conn.execute(
            "SELECT share_price FROM vault_snapshots WHERE vault_id=? "
            "ORDER BY ts DESC LIMIT 1", (vid,)).fetchone()
        prev_sp = prev["share_price"] if prev else None

        detail = strike_api.fetch_vault_detail(vid) or {}
        sp = _f(detail.get("share_price", v.get("share_price")))
        conn.execute(
            """INSERT INTO vault_snapshots (vault_id,ts,share_price,equity,
               wallet_balance,total_shares,depositors,apr,period_return,
               all_time_pnl,fee_earned,sharpe_ratio)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid, now, sp, _f(detail.get("equity", v.get("equity"))),
             _f(detail.get("wallet_balance", v.get("wallet_balance"))),
             _f(detail.get("total_shares", v.get("total_shares"))),
             v.get("depositors"), str(v.get("apr")), str(v.get("period_return")),
             str(v.get("all_time_pnl")), str(v.get("fee_earned")),
             str(v.get("sharpe_ratio"))))

        metrics.detect_events(conn, vid, prev_sp, sp, is_new)

        # History events (dedup by event id) — bahan seri waktu & projector
        for ev in strike_api.fetch_vault_history(vid):
            eid = ev.get("id")
            if not eid:
                continue
            try:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO vault_history_events
                       (event_id,vault_id,ts,type,status,address,account_id,nickname,
                        funding_source,share_price,shares_minted,shares_burned,
                        usd_amount,estimated_usd,avg_entry_price,raw_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (eid, vid, ev.get("timestamp"), ev.get("type"),
                     ev.get("status"), ev.get("address"), ev.get("account_id"),
                     ev.get("nickname"), ev.get("funding_source"),
                     _f(ev.get("share_price")), str(ev.get("shares_minted")),
                     str(ev.get("shares_burned")), str(ev.get("usd_amount")),
                     str(ev.get("estimated_usd")), str(ev.get("avg_entry_price")),
                     json.dumps(ev, ensure_ascii=False)))
                new_events += cur.rowcount  # 1 bila benar-benar baru, 0 bila duplikat
            except Exception:
                log.warning("gagal simpan event %s: %s", eid, traceback.format_exc(limit=1))
    conn.commit()
    log.info("siklus vault selesai: %d vault, +%d event history", len(vaults), new_events)
    return len(vaults)


def poll_hourly(conn):
    """Portfolio + depositors lengkap per vault (1x/jam)."""
    now = ms_now()
    vaults = [r["vault_id"] for r in conn.execute("SELECT vault_id FROM vaults")]
    for vid in vaults:
        try:
            pf = strike_api.fetch_vault_portfolio(vid)
            if pf is not None:
                conn.execute(
                    """INSERT INTO vault_portfolio_latest (vault_id,ts,payload_json)
                       VALUES (?,?,?)
                       ON CONFLICT(vault_id) DO UPDATE SET ts=excluded.ts,
                         payload_json=excluded.payload_json""",
                    (vid, now, json.dumps(pf, ensure_ascii=False)))
        except Exception:
            log.warning("portfolio %s gagal: %s", vid, traceback.format_exc(limit=1))
        try:
            deps = strike_api.fetch_vault_depositors_all(vid)
            conn.execute(
                "INSERT INTO vault_depositor_snapshots (vault_id,ts,count,depositors_json)"
                " VALUES (?,?,?,?)",
                (vid, now, deps.get("count"), json.dumps(deps.get("depositors", []),
                                                         ensure_ascii=False)))
        except Exception:
            log.warning("depositors %s gagal: %s", vid, traceback.format_exc(limit=1))
    conn.commit()
    log.info("snapshot jam-an selesai: %d vault", len(vaults))


def poll_koios_daily(conn):
    """Snapshot holder STRIKE (1x/hari).

    Langkah sampingan murni: kegagalan APAPUN hanya jadi WARNING di log dan
    menjadwalkan ulang percobaan 1 jam kemudian — siklus vault TIDAK boleh
    berhenti karenanya (insiden 21 Okt 22:29 WIB).
    """
    now = ms_now()
    try:
        snap = strike_api.strike_holder_snapshot()
        conn.execute(
            """INSERT INTO strike_holder_daily
               (ts,holder_count,top1_pct,top5_pct,top10_pct,supply_total,note)
               VALUES (?,?,?,?,?,?,?)""",
            (now, snap["holder_count"], snap["top1_pct"], snap["top5_pct"],
             snap["top10_pct"], str(snap.get("supply_total")),
             f"total_held={snap.get('total_held')}"))
        conn.commit()
        log.info("snapshot Koios: %d holder, top1=%.2f%% top10=%.2f%%",
                 snap["holder_count"], snap["top1_pct"], snap["top10_pct"])
    except Exception:
        # WARNING saja (bukan error fatal); coba lagi 1 jam lagi, bukan 24 jam.
        log.warning("snapshot Koios gagal (siklus vault tidak terganggu): %s",
                    traceback.format_exc(limit=2))
        retry_at = int(time.time()) - DAILY_SECONDS + 3600
        conn.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)",
                     ("last_koios", str(retry_at)))
        conn.commit()


def due(conn, key, interval):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    last = int(row["value"]) if row else 0
    if time.time() - last >= interval:
        conn.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)",
                     (key, str(int(time.time()))))
        conn.commit()
        return True
    return False


def main():
    db.init_db()
    conn = db.connect()
    log.info("collector EarnScope start (siklus %ds)", CYCLE_SECONDS)
    while True:
        cycle_start = time.time()
        try:
            poll_vaults_cycle(conn)
        except Exception:
            log.error("siklus vault error (dashboard tetap dibangun): %s",
                      traceback.format_exc(limit=2))
        try:
            # Bootstrap pertama langsung jalan (meta kosong); restart hanya
            # mengulang bila interval sudah lewat — siklus 5-menit tidak tertunda.
            # Dipisah per tugas: kegagalan satu tugas sampingan tidak
            # menggagalkan tugas sampingan lain.
            if due(conn, "last_hourly", HOURLY_SECONDS):
                poll_hourly(conn)
        except Exception:
            log.warning("tugas jam-an error (siklus vault tidak terganggu): %s",
                        traceback.format_exc(limit=2))
        try:
            if due(conn, "last_koios", DAILY_SECONDS):
                poll_koios_daily(conn)
        except Exception:
            log.warning("tugas Koios error (siklus vault tidak terganggu): %s",
                        traceback.format_exc(limit=2))
        try:
            build_dashboard.build(conn)
        except Exception:
            log.error("build dashboard error: %s", traceback.format_exc(limit=2))
        elapsed = time.time() - cycle_start
        time.sleep(max(10, CYCLE_SECONDS - elapsed))


if __name__ == "__main__":
    main()
