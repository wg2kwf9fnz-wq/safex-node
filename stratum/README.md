# Safex stratum gateway (solo mining, every miner paid to its own address)

```
miner --stratum--> gateway :17404 --(one per address)--> xmrig-proxy (daemon/solo mode) --> safexd
```

* **Username = payout address:** `<SafexAddress>[.workername][+difficulty]`, e.g. `Safex5...abc.rig1+200000`.
  The address is checked (base58 + checksum) and then accepted by the node itself (a real `get_block_template` call).
* The gateway starts one patched xmrig-proxy per distinct address; each asks the node for block templates that pay
  **that** address. A block found by a miner pays the full reward to that miner's address. No pool, no fees, no payouts.
* `GET /state` (Bearer `API_TOKEN`) returns live stats for the dashboard: workers, hashrate, blocks found per address.
  Workers with no share for 12 hours (`WORKER_TTL`) are dropped from the list. State is kept in `/data/gateway-state.json`.
* Abuse protection (all env-tunable; LAN, loopback and Tailscale IPs are exempt from the per-IP rules):
  invalid usernames (`MAX_BAD_LOGINS` in 10 min), share floods (`SUBMITS_PER_SEC`) and rejected-share spam ban the IP for `BAN_SECONDS` (15 min);
  `LOGINS_PER_MIN`, `MAX_CONNS_PER_IP`, `MAX_ADDRS_PER_IP`, `MAX_CONNS_PER_ADDR`, `MAX_CONNS_TOTAL`; at most `NEW_ADDR_PER_MIN` new addresses per minute and `MAX_INSTANCES` (50) active addresses;
  minimum requested difficulty `MIN_DIFF` (2000); idle connections are dropped after `IDLE_CONN` (30 min). Never-used instances are stopped after 60 s, idle ones after 10 min.
* `GET /chain` returns public chain data for the dashboard (recent blocks, mempool size, uptime, free disk).

## xmrig-proxy patch (`xmrig-proxy-safex.patch`)
Stock xmrig-proxy knows `rx/sfx` but not Safex's older block format:

| | Monero | Safex 7.0.3 |
|---|---|---|
| miner tx version | 2 (RingCT) | **1** |
| miner tx outputs | 1 | **up to 11**, each with a `token_amount` field |
| miner tx hash | keccak(prefix + rct) | **keccak(whole tx)** |
| get_block_template | `extra_nonce` | only `reserve_size` |
| address prefixes | XMR | `Safex` / `Safexi` / `Safexs` |

It also logs `BLOCK_FOUND height=N` when the daemon accepts a block, which the gateway uses to credit blocks per address.

## Tests (run during the Docker build; the image fails to build if they fail)
* `tests/e2e_test.py`: replays a **real Safex mainnet block template** through one proxy and checks job blob / submitted block byte for byte.
* `tests/gateway_test.py`: refuses bad usernames, separate instances and templates per address, share/worker accounting, block credited to
  its finder only, 12 h expiry (shortened), persistence across restart, per-IP limits.

## Notes / limits
* xmrig-proxy does **not** verify share hashes itself; reported hashrate is what miners claim. Blocks found are the real proof.
* Anyone who can reach port 17404 can mine to their own address. They can only ever earn their own blocks, but they do use your node's CPU
  for templates; the limits above bound that.
