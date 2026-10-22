# EarnScope

**Hub transparansi independen untuk seluruh menu Earn di Strike Finance**, diukur dari data asli (share price, API publik, data on-chain Cardano), bukan klaim APR.

Dibangun untuk Strike 48h Hackathon (deadline submisi Jumat 23 Okt 2026 ±21.00 WIB). Tanpa akun, tanpa dana: hanya API publik + data on-chain.

![Screenshot leaderboard](docs/screenshot-leaderboard.png)
![Screenshot detail vault](docs/screenshot-vault.png)
![Screenshot rewards projector](docs/screenshot-rewards.png)

## Masalah yang dipecahkan

Menu Earn Strike (vault copy-trading, Strike Rewards/Points, Borrow to Earn, staking) memaparkan angka yang sulit diverifikasi pengunjung: kinerja vault hanya terlihat sebagai klaim, proyeksi rewards tidak bisa direkonstruksi publik, dan angka staking resmi tidak bisa diaudit on-chain. EarnScope menjawabnya dengan satu prinsip: **setiap angka yang tampil bisa dilacak ke data mentahnya, dan setiap yang tidak bisa diukur ditulis apa adanya, tidak dikarang.**

Temuan awal dari data (21 Okt 2026): 3 dari 4 vault berada di bawah share price 1.0 (underwater), dan vault dengan komisi leader tertinggi (Glacier Arc Digital, 25%) justru berkinerja terburuk (-31%). Tanpa tracker independen, fakta seperti ini tidak terlihat.

## Modul

### 1. Leaderboard vault (`web/index.html`)
Semua vault dari API publik Strike: nama, label risiko jujur (**Underwater** bila share price < 1.0, **Healthy** bila >= 1.0), share price, kinerja vs 1.0, total equity, jumlah depositor, komisi leader, lockup, min deposit, plus sparkline tren dan log peristiwa (vault baru, share price menembus 1.0). Setiap baris vault terhubung ke halaman detailnya. Data diperbarui tiap siklus collector (±5 menit); halaman auto-refresh tiap 5 menit.

### 2. Detail vault (`web/vault.html?id=<id>`)
Grafik share price dua lapis: **biru** = history resmi Strike (sejak inception, dari endpoint `/history`), **hijau** = snapshot live EarnScope tiap ±5 menit (tidak bisa di-backfill, dibangun sejak kickoff 21 Okt 2026). Dilengkapi ringkasan portfolio resmi, tren jumlah depositor dari snapshot API depositors, dan tombol rentang waktu (semua / 30 hari / 7 hari) serta metrik (share price / TVL).

### 3. Simulator deposit (`web/simulator.html`)
Pilih vault + nominal + jangka waktu (mundur dari data terbaru) untuk melihat hasil historis dari data share price asli. Komisi leader dipotong dari profit sesuai besaran resmi vault (hanya bila ada profit). Dilengkapi grafik pertumbuhan nilai dan **disclaimer jelas**: ini rekonstruksi masa lalu, bukan janji hasil; kinerja masa lalu tidak menjamin kinerja berikutnya.

### 4. Rewards Projector (`web/rewards.html`)
Proyeksi Strike Rewards / Points Season 1 (berakhir **28 Okt 2026**) untuk depositor Strike Liquidity Provider (SLP). Metodologi: 888 poin/hari dibagi pro-rata berdasarkan ekuitas saat ini terhadap total ekuitas SLP, pool 20.000 STRIKE, konversi ±0,7508 STRIKE/poin. Menampilkan estimasi poin dan STRIKE per depositor, angka per $1.000 deposit, dan countdown season. **Semua angka berlabel ESTIMASI** karena Strike menghitung dari snapshot per jam internal mereka; kami merekonstruksi dari data publik (snapshot depositors + history SLP). Batasan: paginasi API Strike rusak di sisi server (parameter offset/cursor diabaikan, selalu kembali halaman 1), jadi backfill history dibatasi ±250 event terbaru via limit=250; seri waktu lengkap dibangun dari snapshot 5-menit kami sendiri.

### 5. Simulator Borrow to Earn (`web/borrow.html`)
Skema resmi: kunci jaminan (mis. ADA) → tarik kredit USD **bunga 0%** → kredit wajib masuk vault SLP. Input: nilai jaminan, % draw, horizon, skenario kinerja SLP dari data historis asli (sejak tracker / 7 hari / 30 hari, di-annualize), dan asumsi gerak harga ADA dari API pasar Strike (`api.strikefinance.org/price`). Output: hasil bersih (nilai kredit di SLP akhir horizon minus pokok), intuisi kesehatan line (ekuitas jaminan akhir dibagi pokok kredit), dan grafik skenario. Disclaimer: threshold kesehatan adalah asumsi EarnScope karena Strike tidak mempublikasikan rasio likuidasi resmi; bukan nasihat keuangan.

### 6. Papan banding Earn (`web/compare.html`)
Satu layar membandingkan semua produk Earn dengan angka terukur yang tersedia: **Vault** (kinerja share price riil + tabel per vault), **SLP + Points** (kinerja + boost proyeksi, label ESTIMASI), **Borrow to Earn** (SLP ter-leverage, bunga 0%), **Staking** (bagi hasil revenue; APR resmi tidak dipublikasikan via API, ditulis apa adanya). Setiap produk mencantumkan metodologi dan daftar "data tidak tersedia" bila ada. Tidak ada angka yang dikarang.

### 7. Panel STRIKE on-chain (`web/onchain.html`)
Data token STRIKE langsung dari blockchain Cardano via Koios (gratis, tanpa key): jumlah holder, supply (25.000.000), konsentrasi top 1/5/10 holder, dan tren holder harian. **Angka "total staking" sengaja tidak ditampilkan**: liquid staking Strike memakai registry opt-in yang disimpan di server Strike (off-chain), sehingga total staking resmi tidak bisa diturunkan dari data on-chain. Panel ini tidak mengarangnya.

### 8. Position Tracker (`web/tracker.html`)
Pengunjung menempel alamat Cardano (tanpa login, tanpa kunci) untuk melihat posisinya di semua vault dari data depositors publik Strike: total deposit, total withdraw, ekuitas saat ini, PnL, share vault, dan sejak kapan menjadi depositor. Untuk posisi SLP ditambah proyeksi poin/STRIKE pribadi (ESTIMASI) dan saldo STRIKE on-chain alamat tersebut via Koios (langsung dari browser). Batasan ditulis apa adanya: line Borrow to Earn per akun dan status staking resmi tidak bisa dilacak publik karena berada di balik autentikasi Strike; snapshot depositors diperbarui tiap jam.

## Arsitektur

```
API publik Strike ─┐
                   ├─► collector.py ─► SQLite (data/earnscope.db) ─► build_*.py ─► web/data.json ─► halaman statis (vanilla JS, tanpa CDN)
Koios (Cardano) ───┘                                                              (index, vault, simulator, rewards,
                                                                                   tracker, borrow, compare, onchain)
```

- **`collector.py`**: proses persisten, polling API vault tiap ±5 menit (snapshot share price/equity/depositor), portfolio + depositors tiap jam, snapshot holder STRIKE via Koios 1x/hari. Langkah Koios dan langkah sampingan lain diisolasi dengan try/except menyeluruh: kegagalannya hanya jadi WARNING di log, siklus vault tidak pernah berhenti karenanya.
- **`watchdog.sh` + cron**: memeriksa tiap 15 menit; me-restart collector bila prosesnya mati (pidfile `data/collector.pid`).
- **`build_dashboard.py` / `build_detail.py` / `build_earn.py` / `build_projector.py`**: menggabungkan snapshot SQLite menjadi `web/data.json` (satu file, dibaca semua halaman via `fetch`).
- **`web/`**: 8 halaman statis, vanilla JS tanpa dependency/CDN, sehingga bisa di-host di mana saja sebagai static site.

Struktur database (`data/earnscope.db`, SQLite): `vaults` (katalog), `vault_snapshots` (snapshot 5-menitan), `vault_history_events` (riwayat deposit/withdraw, dedup by id), `vault_depositor_snapshots` (snapshot depositor per jam), `vault_portfolio_latest`, `strike_holder_daily`, `events` (log peristiwa).

## Cara menjalankan

```bash
# 1. Jalankan collector (loop selamanya, ±5 menit per siklus)
python3 collector.py

# atau sebagai proses persisten:
./run_collector.sh

# 2. (Opsional) pasang watchdog di cron, tiap 15 menit:
*/15 * * * * /path/ke/earnscope/watchdog.sh

# 3. Bangun ulang data.json dari snapshot SQLite:
python3 build_dashboard.py   # leaderboard + ekosistem
python3 build_detail.py      # detail per vault
python3 build_earn.py        # papan banding + borrow
python3 build_projector.py   # rewards projector

# 4. Buka dashboard:
#    - langsung: buka web/index.html di browser, atau
#    - serve statis:  cd web && python3 -m http.server 8000
```

### Deploy ke Vercel (static site)

1. Push repo ini ke GitHub.
2. Di Vercel: New Project → import repo → Framework Preset: **Other**, Root Directory: `web/` (atau biarkan root dan atur output ke `web`).
3. Deploy. Tidak ada build step dan tidak ada environment variable.
4. `web/data.json` ikut ter-deploy apa adanya; untuk data live yang terus diperbarui, jalankan collector di mesin sendiri dan sinkronkan `web/data.json` ke repo (atau ke storage statis) tiap selesai build.

## Batasan data (jujur)

- **Paginasi API Strike rusak di sisi server**: parameter `offset`/`cursor`/`next_cursor` diabaikan, selalu kembali halaman 1. Mitigasi: history diambil dengan `limit=250` (±250 event terbaru yang bisa di-backfill); seri waktu lengkap dibangun dari snapshot 5-menit kami sendiri sejak 21 Okt 2026.
- **Total staking resmi tidak tersedia on-chain** (registry opt-in off-chain di server Strike): tidak ditampilkan, tidak dikarang.
- **Rewards Projector adalah estimasi**: Strike memakai snapshot per jam internal; kami merekonstruksi dari data publik.
- **Snapshot holder Koios 1x/hari** (paging `asset_addresses` lambat, ~400 detik untuk ±3.500 holder); bila Koios timeout, modul on-chain menampilkan data terakhir yang berhasil.
- APR resmi produk staking tidak dipublikasikan via API: papan banding menulis apa adanya.

## Status hackathon

- Kickoff: 21 Okt 2026 (pipeline data + leaderboard). Day-2: 22 Okt 2026 (8 modul + polish + README ini).
- Submit prep: 23 Okt 2026. Deadline: 23 Okt 2026 ±21.00 WIB. Pemenang diumumkan 26 Okt 2026.
- Kriteria: Innovation, Integration With Strike, Utility & Impact, Technical Execution, Future Potential.
