# EarnScope

**An independent transparency hub for the entire Earn menu on Strike Finance**, measured from real data (share price, public APIs, Cardano on-chain data), not APR claims.

Built for the Strike 48h Hackathon (submission deadline Friday 23 Oct 2026, ±21:00 WIB). No account, no funds: public APIs and on-chain data only.

![Leaderboard screenshot](docs/screenshot-leaderboard.png)
![Vault detail screenshot](docs/screenshot-vault.png)
![Rewards projector screenshot](docs/screenshot-rewards.png)

## The problem it solves

Strike's Earn menu (copy-trading vaults, Strike Rewards/Points, Borrow to Earn, staking) exposes numbers that visitors cannot easily verify: vault performance is only visible as claims, rewards projections cannot be reconstructed publicly, and official staking figures cannot be audited on-chain. EarnScope answers with one principle: **every number shown can be traced back to its raw data, and anything that cannot be measured is reported as-is, never invented.**

Initial findings from the data (21 Oct 2026): 3 of 4 vaults sit below a share price of 1.0 (underwater), and the vault with the highest leader commission (Glacier Arc Digital, 25%) is actually the worst performer (-31%). Without an independent tracker, facts like these stay invisible.

## Modules

### 1. Vault leaderboard (`web/index.html`)
All vaults from Strike's public API: name, an honest risk label (**Underwater** when share price < 1.0, **Healthy** when >= 1.0), share price, performance vs 1.0, total equity, depositor count, leader commission, lockup, minimum deposit, plus a trend sparkline and an event log (new vaults, share price crossing 1.0). Every vault row links to its detail page. Data refreshes on every collector cycle (±5 minutes); the page auto-refreshes every 5 minutes.

### 2. Vault detail (`web/vault.html?id=<id>`)
A two-layer share price chart: **blue** = Strike's official history (since inception, from the `/history` endpoint), **green** = EarnScope's live snapshots every ±5 minutes (cannot be backfilled, built since the 21 Oct 2026 kickoff). Includes an official portfolio summary, a depositor count trend from depositors API snapshots, and range buttons (all / 30 days / 7 days) plus metric toggles (share price / TVL).

### 3. Deposit simulator (`web/simulator.html`)
Pick a vault, an amount, and a time window (looking back from the latest data) to see the historical result from real share price data. The leader commission is deducted from profit at the vault's official rate (only when there is a profit). Includes a value growth chart and a **clear disclaimer**: this is a reconstruction of the past, not a promise of returns; past performance does not guarantee future results.

### 4. Rewards Projector (`web/rewards.html`)
Projected Strike Rewards / Points Season 1 (ending **28 Oct 2026**) for Strike Liquidity Provider (SLP) depositors. Methodology: 888 points/day split pro-rata based on current equity against total SLP equity, a 20,000 STRIKE pool, conversion of ±0.7508 STRIKE/point. Shows estimated points and STRIKE per depositor, figures per $1,000 deposit, and a season countdown. **All figures are labeled ESTIMATE** because Strike computes from their internal hourly snapshots; we reconstruct from public data (depositors snapshots + SLP history). Limitation: Strike's API pagination is broken server-side (offset/cursor parameters are ignored, it always returns page 1), so history backfill is limited to ±250 of the latest events via limit=250; the full time series is built from our own 5-minute snapshots.

### 5. Borrow to Earn simulator (`web/borrow.html`)
The official scheme: lock collateral (e.g. ADA), draw USD credit at **0% interest**, and the credit must go into the SLP vault. Inputs: collateral value, draw %, horizon, an SLP performance scenario from real historical data (since tracker / 7 days / 30 days, annualized), and an assumed ADA price movement from Strike's market API (`api.strikefinance.org/price`). Outputs: net result (credit value in SLP at the end of the horizon minus principal), line health intuition (final collateral equity divided by credit principal), and a scenario chart. Disclaimer: the health thresholds are EarnScope assumptions because Strike does not publish an official liquidation ratio; not financial advice.

### 6. Earn comparison board (`web/compare.html`)
One screen comparing every Earn product with the measured figures available: **Vaults** (real share price performance + a per-vault table), **SLP + Points** (performance + projected boost, labeled ESTIMATE), **Borrow to Earn** (leveraged SLP, 0% interest), **Staking** (revenue sharing; the official APR is not published via API, reported as-is). Every product lists its methodology and a "data not available" list when applicable. No invented numbers.

### 7. STRIKE on-chain panel (`web/onchain.html`)
STRIKE token data straight from the Cardano blockchain via Koios (free, no API key): holder count, supply (25,000,000), top 1/5/10 holder concentration, and a daily holder trend. **A "total staking" figure is deliberately not displayed**: Strike's liquid staking uses an opt-in registry stored on Strike's servers (off-chain), so the official total staked cannot be derived from on-chain data. This panel does not invent it.

### 8. Position Tracker (`web/tracker.html`)
Visitors paste a Cardano address (no login, no keys) to see their positions across all vaults from Strike's public depositors data: total deposits, total withdrawals, current equity, PnL, vault share, and depositor since. SLP positions also get a personal points/STRIKE projection (ESTIMATE) and the address's on-chain STRIKE balance via Koios (fetched directly from the browser). Limitations are stated as-is: per-account Borrow to Earn lines and official staking status cannot be tracked publicly because they sit behind Strike authentication; depositors snapshots refresh hourly.

## Architecture

```
Strike public API ─┐
                   ├─► collector.py ─► SQLite (data/earnscope.db) ─► build_*.py ─► web/data.json ─► static pages (vanilla JS, no CDN)
Koios (Cardano) ───┘                                                              (index, vault, simulator, rewards,
                                                                                   tracker, borrow, compare, onchain)
```

- **`collector.py`**: a persistent process polling the vault API every ±5 minutes (share price/equity/depositor snapshots), portfolio + depositors hourly, and a STRIKE holder snapshot via Koios once a day. The Koios step and other side steps are isolated with comprehensive try/except handling: their failures only become WARNINGs in the log, and the vault cycle never stops because of them.
- **`watchdog.sh` + cron**: checks every 15 minutes; restarts the collector if the process died (pidfile `data/collector.pid`).
- **`build_dashboard.py` / `build_detail.py` / `build_earn.py` / `build_projector.py`**: combine the SQLite snapshots into `web/data.json` (a single file, read by all pages via `fetch`).
- **`web/`**: 8 static pages, vanilla JS with no dependencies or CDN, so they can be hosted anywhere as a static site.

Database structure (`data/earnscope.db`, SQLite): `vaults` (catalog), `vault_snapshots` (5-minute snapshots), `vault_history_events` (deposit/withdraw history, deduped by id), `vault_depositor_snapshots` (hourly depositor snapshots), `vault_portfolio_latest`, `strike_holder_daily`, `events` (event log).

## How to run

```bash
# 1. Run the collector (loops forever, ±5 minutes per cycle)
python3 collector.py

# or as a persistent process:
./run_collector.sh

# 2. (Optional) install the watchdog in cron, every 15 minutes:
*/15 * * * * /path/to/earnscope/watchdog.sh

# 3. Rebuild data.json from the SQLite snapshots:
python3 build_dashboard.py   # leaderboard + ecosystem
python3 build_detail.py      # per-vault detail
python3 build_earn.py        # comparison board + borrow
python3 build_projector.py   # rewards projector

# 4. Open the dashboard:
#    - directly: open web/index.html in a browser, or
#    - serve statically:  cd web && python3 -m http.server 8000
```

### Deploy to Vercel (static site)

1. Push this repo to GitHub.
2. In Vercel: New Project, import the repo, Framework Preset: **Other**, Root Directory: `web/` (or keep the root and set the output to `web`).
3. Deploy. There is no build step and no environment variable.
4. `web/data.json` is deployed as-is; for continuously updated live data, run the collector on your own machine and sync `web/data.json` to the repo (or to static storage) after each build.

## Data limitations (honest)

- **Strike's API pagination is broken server-side**: the `offset`/`cursor`/`next_cursor` parameters are ignored and it always returns page 1. Mitigation: history is fetched with `limit=250` (±250 latest events that can be backfilled); the full time series is built from our own 5-minute snapshots since 21 Oct 2026.
- **The official total staked is not available on-chain** (opt-in off-chain registry on Strike's servers): not displayed, not invented.
- **The Rewards Projector is an estimate**: Strike uses internal hourly snapshots; we reconstruct from public data.
- **Koios holder snapshots run once a day** (paging `asset_addresses` is slow, ~400 seconds for ±3,500 holders); if Koios times out, the on-chain module shows the last successful data.
- The official staking product APR is not published via API: the comparison board reports it as-is.

## Hackathon status

- Kickoff: 21 Oct 2026 (data pipeline + leaderboard). Day-2: 22 Oct 2026 (8 modules + polish + this README).
- Submit prep: 23 Oct 2026. Deadline: 23 Oct 2026 ±21:00 WIB. Winners announced 26 Oct 2026.
- Criteria: Innovation, Integration With Strike, Utility & Impact, Technical Execution, Future Potential.
