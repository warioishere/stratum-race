# Stratum Race — web leaderboard

A small, self-hostable website that runs `str_race.py` continuously and
publishes a live ranking of Bitcoin mining pools by how quickly they deliver
new-block stratum notifications (`mining.notify` with `clean_jobs=true`).

Live instance: **https://stratumrace.com**

Everything is dependency-free: the racer and aggregator are stdlib Python,
the site is static HTML/CSS/JS served by nginx.

## Layout

| Path | Purpose |
|------|---------|
| `pools/web-pools.json` | Pool set raced by the site: the 30 known solo pools plus major pools from [mempool.space](https://mempool.space/graphs/mining/pools) with public stratum endpoints (OCEAN, ViaBTC, F2Pool, AntPool, Binance Pool, Luxor, SpiderPool, SECPOOL, Braiins, Kano, NiceHash, EMCD, Public Pool) |
| `run_races.sh` | Loop: run a 30-minute race session, aggregate, repeat |
| `aggregate.py` | Merges all session exports into `leaderboard.json` (dedupes races by prevhash, recomputes stats across the full window) |
| `site/` | Static frontend — dark, minimal leaderboard that polls `data/leaderboard.json` |
| `deploy/` | nginx vhost, systemd unit, and an idempotent `setup.sh` |

## How rankings work

Every confirmed race (a new prevhash confirmed by multiple pools within the
confirm window) contributes one arrival offset per pool: the number of
milliseconds that pool was behind the first pool to deliver the block.
The leaderboard sorts pools by **median offset** across all races in the
observation window (default: last 14 days of sessions). Pools need at least
3 observed races to be ranked; the rest are shown as *collecting* or
*unreachable*.

**Empty templates:** some pools broadcast a coinbase-only (empty) template
the moment a block is found and only later send the full template. Ranking on
"first notify" would reward skipping transaction selection entirely, so the
leaderboard times each pool to its **first non-empty template** instead. No
pool is excluded — the *Empty 1st* column shows how often a pool led with an
empty template, and each block's earlier empty notify appears as
*Empty jump-start* in the recent-blocks table.

Wins are what **this vantage point** observed first — not global proof of
propagation victory. See the main README for methodology caveats.

**Vantage point:** all timings are measured from wherever the racer runs (the
live instance measures from San Francisco), and pools with nearby
infrastructure have a structural advantage. The site states its vantage in the
header and methodology. For a fuller picture, run racers from several regions
and compare.

## Deploying

Everything is driven by one script: **`web/deploy/deploy.sh`**.

**Requirements:** a fresh Debian/Ubuntu server (1 small VPS is plenty), root
SSH access, and your domain's DNS **already pointed at the server** (needed
for the Let's Encrypt certificate).

**From your own machine**, out of a checkout of this repo:

```bash
./web/deploy/deploy.sh --host root@YOUR_SERVER_IP --domain stratumrace.com
```

That copies the checkout to the server (plain ssh + tar, no other tools
needed), installs nginx + certbot, creates an unprivileged `stratumrace`
system user, installs the `stratum-racer` systemd service (15-minute race
sessions, re-aggregating the leaderboard after each), requests the TLS
certificate, and verifies the site answers over HTTPS. SSH authentication is
whatever you normally use — keys or an interactive password prompt; nothing
is stored.

**Directly on the server** (equivalent, if you prefer to clone there):

```bash
sudo ./web/deploy/deploy.sh --domain stratumrace.com
```

### deploy.sh options

| Flag | Meaning |
|------|---------|
| `--host user@server` | Deploy remotely over SSH; omit to install on the current machine (as root) |
| `--domain DOMAIN` | Public domain for the site (required) |
| `--vantage "LABEL"` | Measurement-location label shown on the site, e.g. `"Denver, US (Hetzner)"`. Auto-detected on DigitalOcean if omitted |
| `--reset` | Wipe all collected race data and restart the stats from zero |
| `--help` | Usage |

### Day-2 operations

- **Update the site/code:** pull or edit the repo, re-run the same
  `deploy.sh` command. Re-deploys are idempotent and graceful — the racer is
  interrupted with SIGINT so the in-flight session still writes its results,
  and collected data is kept.
- **Reset the stats:** add `--reset`.
- **Watch the racer:** `journalctl -u stratum-racer -f` · check health with
  `systemctl status stratum-racer`.
- **Where things live on the server:** code in `/opt/stratum-race`, collected
  sessions in `/var/lib/stratum-race/sessions`, web root in
  `/var/www/stratumrace`, config in `/etc/stratum-race/racer.env`.

Tuning knobs in `/etc/stratum-race/racer.env` (`SESSION_SECS`,
`FIRST_SESSION_SECS`, and `KEEP_DAYS` are rewritten by each deploy; a
customized `VANTAGE` is preserved):

```
SESSION_SECS=900         # length of each race session
FIRST_SESSION_SECS=600   # shorter first session so data shows up sooner
KEEP_DAYS=14             # aggregation window
VANTAGE=my basement, AS12345   # shown on the site's methodology section
POOLS=/opt/stratum-race/web/pools/web-pools.json
```

### Migrating to a new server

1. Point the domain's DNS at the new server.
2. Run `deploy.sh --host root@NEW_SERVER_IP --domain your-domain` (re-run it
   once more if certbot raced DNS propagation).
3. Optional — carry the collected history over:

```bash
scp root@OLD_SERVER:/var/lib/stratum-race/sessions/session-*.json /tmp/sessions/
scp /tmp/sessions/session-*.json root@NEW_SERVER:/var/lib/stratum-race/sessions/
ssh root@NEW_SERVER 'chown stratumrace:stratumrace /var/lib/stratum-race/sessions/*.json'
```

The leaderboard folds the old sessions in at the next aggregation
(within ~15 minutes).

There is also an optional GitHub Actions workflow
(`.github/workflows/deploy-web.yml`) that runs the same deployment from a
runner using one-time masked inputs — handy if you want push-button deploys
from the GitHub UI instead of a terminal.

## Local preview

```bash
python3 web/aggregate.py --sessions /path/to/session-jsons --out web/site/data/leaderboard.json
cd web/site && python3 -m http.server 8080
```
