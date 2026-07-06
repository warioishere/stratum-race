# Measurement proxy — timing pools from a connection that actually mines

Some pools (OCEAN's CTO has confirmed this for OCEAN) deliver `mining.notify`
to idle connections in a later tranche than to connections submitting real
work. An idle listener therefore measures the *back of the queue* for those
pools, not the miner experience.

`stratum_proxy.py` fixes that: point a real miner at it and it relays every
byte unmodified to the upstream pool while timestamping each `mining.notify`
and counting accepted shares. Because it runs on the same machine as the
idle listeners, every block gives a paired sample — active vs. idle, same
pool, same instant, same network path. The difference is the **idle
penalty**, published on the site's *Active-miner tests* panel.

## Running it

Enabled at deploy time:

```bash
./web/deploy/deploy.sh --host root@SERVER_IP --domain SERVER_IP --with-proxy
```

Then point your miner at `stratum+tcp://SERVER_IP:3333` with any
username/worker. That's it — same IP as the website, different port.

### Rotate mode (default)

The proxy walks the pools file: after `RACES_PER_POOL` observed blocks
(default 20) or `MAX_MINUTES` (default 300) on one pool, it drops the miner
connection and moves to the next pool; the miner auto-reconnects and is now
mining there. Progress survives restarts via a state file. One sweep
characterizes every pool's idle penalty; pools showing ~0 penalty are fine
to measure with idle listeners, pools showing a real penalty deserve a
parked miner.

Knobs in `/etc/stratum-race/proxy.env` (`PROXY_PORT`, `RACES_PER_POOL`,
`MAX_MINUTES`, `PROXY_EXTRA_ARGS`); restart with
`systemctl restart stratum-proxy`.

### Park mode

Pin one pool permanently (e.g. after certifying it tranches):

```
PROXY_EXTRA_ARGS=--pool ocean
```

Run a second instance on another port for a second parked pool.

## Notes

- **Miner sizing:** the connection must clear the pool's share difficulty
  regularly to count as active — Bitaxe-class (1 TH+) works; a few TH is
  safer since some pools dislike very low hashrate. NerdMiner-class
  (KH/s) will never submit a share and defeats the purpose.
- The relay is byte-for-byte transparent: difficulty, extranonce, and
  authorization all negotiate directly between your miner and the pool.
- Events land in `/var/lib/stratum-race/active/active-<pool>.jsonl`;
  `aggregate.py --active-dir` joins them to idle races by block hash and
  computes per-pool `active_median_ms` and `idle_penalty_ms`.
- One miner connection at a time; extra connections are refused so the
  measurement stays clean.
- Watch it: `journalctl -u stratum-proxy -f`
