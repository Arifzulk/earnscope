"""Membangun web/data.json untuk dashboard statis EarnScope."""
import json
import logging
import os
import time
import traceback

import db
import metrics

log = logging.getLogger("earnscope.build_dashboard")

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
DATA_JSON = os.path.join(WEB_DIR, "data.json")


def fmt_wib(ms):
    from datetime import datetime, timezone, timedelta
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone(timedelta(hours=7)))
    return dt.strftime("%d %b %Y %H:%M:%S WIB")


def build(conn=None):
    close = False
    if conn is None:
        conn = db.connect()
        close = True
    board = metrics.build_leaderboard(conn)
    now_ms = int(time.time() * 1000)
    payload = {
        "generated_at_ms": now_ms,
        "generated_at_wib": fmt_wib(now_ms),
        "notes": "Snapshot dari API publik Strike (api.strikefinance.org), tanpa auth. "
                 "Kinerja 'sejak tracker' dihitung dari snapshot EarnScope sendiri; "
                 "'vs 1.0' mengukur sejak inception (share price awal = 1.0).",
        **board,
    }
    # Modul ekspansi day-2: masing-masing punya extend(conn, payload),
    # terisolasi — kegagalan satu modul tidak menggagalkan build.
    for mod_name in ("build_detail", "build_earn", "build_projector"):
        try:
            mod = __import__(mod_name)
            mod.extend(conn, payload)
        except Exception:
            log.warning("modul %s gagal (build lanjut): %s", mod_name,
                        traceback.format_exc(limit=1))
    os.makedirs(WEB_DIR, exist_ok=True)
    tmp = DATA_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, DATA_JSON)
    log.info("data.json ditulis: %d vault, %s", len(board["vaults"]),
             payload["generated_at_wib"])
    if close:
        conn.close()
    return payload


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    db.init_db()
    build()
