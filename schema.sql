CREATE TABLE IF NOT EXISTS vaults (
  vault_id TEXT PRIMARY KEY,
  name TEXT,
  type TEXT,
  leader_account_id TEXT,
  nickname TEXT,
  leader_commission_bps INTEGER,
  lockup_days INTEGER,
  min_deposit TEXT,
  created_at_ms INTEGER,
  status TEXT,
  first_seen_ts INTEGER,
  last_seen_ts INTEGER
);

CREATE TABLE IF NOT EXISTS vault_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  vault_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  share_price REAL,
  equity REAL,
  wallet_balance REAL,
  total_shares REAL,
  depositors INTEGER,
  apr TEXT,
  period_return TEXT,
  all_time_pnl TEXT,
  fee_earned TEXT,
  sharpe_ratio TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_vault_ts ON vault_snapshots (vault_id, ts);

CREATE TABLE IF NOT EXISTS vault_history_events (
  event_id TEXT PRIMARY KEY,
  vault_id TEXT NOT NULL,
  ts INTEGER,
  type TEXT,
  status TEXT,
  address TEXT,
  account_id TEXT,
  nickname TEXT,
  funding_source TEXT,
  share_price REAL,
  shares_minted TEXT,
  shares_burned TEXT,
  usd_amount TEXT,
  estimated_usd TEXT,
  avg_entry_price TEXT,
  raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_hist_vault_ts ON vault_history_events (vault_id, ts);

CREATE TABLE IF NOT EXISTS vault_depositor_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  vault_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  count INTEGER,
  depositors_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_dep_vault_ts ON vault_depositor_snapshots (vault_id, ts);

CREATE TABLE IF NOT EXISTS vault_portfolio_latest (
  vault_id TEXT PRIMARY KEY,
  ts INTEGER NOT NULL,
  payload_json TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  vault_id TEXT,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS strike_holder_daily (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  holder_count INTEGER,
  top1_pct REAL,
  top5_pct REAL,
  top10_pct REAL,
  supply_total TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
