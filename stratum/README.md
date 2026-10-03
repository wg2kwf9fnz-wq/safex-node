# Safex stratum server (solo mining)

`xmrig-proxy` v6.26.0 running in **daemon (solo) mode** against `safexd`, patched for Safex.

## Why a patch is needed
Stock xmrig-proxy can already hash-select `rx/sfx` (RandomSFX), but its block-template parser
assumes a modern Monero miner transaction. Safex differs:

| | Monero | Safex 7.0.3 |
|---|---|---|
| miner tx version | 2 (RingCT) | **1** |
| miner tx outputs | 1 | **up to 11** (reward decomposed into digits) |
| output encoding | amount, type, key | amount, **token_amount**, type, key |
| miner tx hash | keccak(prefix ‖ rct_base ‖ rct_prunable) | **keccak(whole tx)** |
| get_block_template | `extra_nonce` param | only `reserve_size` |
| address prefixes | XMR | `Safex` / `Safexi` / `Safexs` |

`xmrig-proxy-safex.patch` adds a `SFX` coin and handles all of the above (~80 lines).

## Tests
`tests/e2e_test.py` replays a **real Safex mainnet block template** (height 2,099,175) through the proxy
via a mock `safexd` and checks, byte for byte, that:
1. a Python reference reproduces safexd's own `blockhashing_blob`;
2. the job sent to a 2nd miner (non-zero extra nonce) has the merkle root Safex would compute;
3. a winning share makes the proxy call `submit_block` with template + nonce + the same extra nonce.

The Docker build runs this test and fails if it does not pass.

## Notes / limits
- Solo mining: every block found pays 100% to the wallet set in the dashboard. No pool payouts.
- xmrig-proxy does **not** verify share hashes itself; worker hashrate is what the miners claim.
  Fine for your own rigs, but keep it in mind for rented hashrate. Blocks found are the real proof.
- Per-worker difficulty: login as `name+DIFF` (e.g. `rig1+200000`). Default difficulty: 20000.
