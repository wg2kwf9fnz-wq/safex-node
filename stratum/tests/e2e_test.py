#!/usr/bin/env python3
"""
End-to-end test for the Safex-patched xmrig-proxy, without real mining.

1. Loads a REAL Safex block template captured from a mainnet daemon (tpl.json).
2. Verifies our independent Python block-hashing-blob code reproduces the daemon's
   own blockhashing_blob (cross-checks the Python reference against safexd).
3. Serves that template from a mock safexd (getheight/getinfo/json_rpc).
4. Connects to xmrig-proxy as a stratum miner, receives a job, and checks the job blob
   equals the hashing blob Safex would compute for the template with the proxy's
   extra-nonce inserted (i.e. the merkle root is correct for a v1 miner tx).
5. Submits a share with a fake "winning" hash so the proxy calls submit_block, and checks
   the submitted block == template + our nonce + the same extra nonce.
"""
import json, sys, socket, threading, time, struct
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from sfxaddr import keccak256

TPL = json.load(open(sys.argv[1]))['result']
WALLET = sys.argv[2]
PROXY_PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 3333
DAEMON_PORT = int(sys.argv[4]) if len(sys.argv) > 4 else 17402

def rd_varint(b, i):
    n = s = 0
    while True:
        c = b[i]; i += 1
        n |= (c & 0x7f) << s; s += 7
        if c < 0x80: return n, i
def varint(n):
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7f) | 0x80); n >>= 7
    out.append(n); return bytes(out)

def parse_block(blob):
    i = 0
    major, i = rd_varint(blob, i); minor, i = rd_varint(blob, i); ts, i = rd_varint(blob, i)
    i += 32; nonce_off = i; i += 4
    hdr_end = i
    tx_start = i
    ver, i = rd_varint(blob, i); unlock, i = rd_varint(blob, i)
    nin, i = rd_varint(blob, i); assert nin == 1 and blob[i] == 0xff; i += 1
    h, i = rd_varint(blob, i)
    nout, i = rd_varint(blob, i)
    for _ in range(nout):
        a, i = rd_varint(blob, i); t, i = rd_varint(blob, i)
        assert blob[i] == 2; i += 1 + 32
    xl, i = rd_varint(blob, i); i += xl
    tx_end = i  # v1 tx: no signatures for txin_gen, no RCT
    ntx, i = rd_varint(blob, i)
    hashes = [blob[i + 32 * k:i + 32 * (k + 1)] for k in range(ntx)]
    assert i + 32 * ntx == len(blob), 'trailing bytes in block blob'
    return dict(nonce_off=nonce_off, hdr_end=hdr_end, tx=(tx_start, tx_end), hashes=hashes, height=h, nout=nout, ver=ver)

def tree_hash(hs):
    # CryptoNote tree hash
    n = len(hs)
    if n == 1: return hs[0]
    if n == 2: return keccak256(hs[0] + hs[1])
    cnt = 1
    while cnt * 2 <= n: cnt *= 2
    ints = hs[:2 * cnt - n] + [keccak256(hs[j] + hs[j + 1]) for j in range(2 * cnt - n, n, 2)]
    while cnt > 2:
        cnt //= 2
        ints = [keccak256(ints[2 * j] + ints[2 * j + 1]) for j in range(cnt)]
    return keccak256(ints[0] + ints[1])

def hashing_blob(blob):
    p = parse_block(blob)
    miner_tx_hash = keccak256(blob[p['tx'][0]:p['tx'][1]])
    root = tree_hash([miner_tx_hash] + p['hashes'])
    return blob[:p['hdr_end']] + root + varint(len(p['hashes']) + 1)

tpl_blob = bytes.fromhex(TPL['blocktemplate_blob'])
p = parse_block(tpl_blob)
print(f"template: height={TPL['height']} miner_tx v{p['ver']} outputs={p['nout']} other_txs={len(p['hashes'])} reserved_offset={TPL['reserved_offset']}")
assert hashing_blob(tpl_blob).hex() == TPL['blockhashing_blob'], 'python reference != safexd blockhashing_blob'
print('OK  python hashing-blob reference matches safexd output')

submitted = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, obj):
        b = json.dumps(obj).encode(); self.send_response(200)
        self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)) or 0)
        if self.path in ('/getheight',):
            return self._send({'height': TPL['height'], 'hash': TPL['prev_hash'], 'status': 'OK'})
        if self.path in ('/getinfo', '/get_info'):
            return self._send({'height': TPL['height'], 'top_block_hash': TPL['prev_hash'], 'status': 'OK'})
        req = json.loads(body or b'{}')
        m = req.get('method'); rid = req.get('id')
        if m in ('getblocktemplate', 'get_block_template'):
            params = req.get('params', {})
            assert params.get('reserve_size') == 8, f'proxy did not send reserve_size: {params}'
            return self._send({'jsonrpc': '2.0', 'id': rid, 'result': TPL})
        if m in ('submitblock', 'submit_block'):
            submitted.append(req['params'][0])
            return self._send({'jsonrpc': '2.0', 'id': rid, 'result': {'status': 'OK'}})
        if m in ('get_info', 'getinfo'):
            return self._send({'jsonrpc': '2.0', 'id': rid, 'result': {'height': TPL['height'], 'top_block_hash': TPL['prev_hash'], 'status': 'OK'}})
        return self._send({'jsonrpc': '2.0', 'id': rid, 'error': {'code': -1, 'message': 'unknown ' + str(m)}})
    do_GET = do_POST

srv = HTTPServer(('127.0.0.1', DAEMON_PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
print(f'mock safexd listening on {DAEMON_PORT}; waiting for proxy on {PROXY_PORT} ...')

def connect():
    for _ in range(60):
        try:
            s = socket.create_connection(('127.0.0.1', PROXY_PORT), timeout=10); return s
        except OSError: time.sleep(1)
    raise SystemExit('proxy not reachable')

# Miner #1 gets extra_nonce 0 (identical to the daemon template, proves nothing), so we keep it
# connected and test with miner #2+, whose job requires the proxy to recompute the merkle root.
first = connect(); ff = first.makefile('rwb')
ff.write((json.dumps({'id': 1, 'jsonrpc': '2.0', 'method': 'login', 'params': {'login': 'x', 'pass': 'x', 'agent': 'e2e/1.0', 'algo': ['rx/sfx']}}) + '\n').encode()); ff.flush()
first_job = json.loads(ff.readline())['result']['job']
s = None; job = None
for attempt in range(30):
    s = connect(); f = s.makefile('rwb')
    f.write((json.dumps({'id': 1, 'jsonrpc': '2.0', 'method': 'login', 'params': {'login': 'x', 'pass': 'x', 'agent': 'e2e/1.0', 'algo': ['rx/sfx']}}) + '\n').encode()); f.flush()
    resp = json.loads(f.readline())
    if resp.get('result') and resp['result'].get('job'):
        job = resp['result']['job']; break
    s.close(); time.sleep(2)
assert job, f'no job from proxy: {resp}'
print(f"job: algo={job.get('algo')} height={job.get('height')} seed={job.get('seed_hash','')[:16]}.. target={job['target']}")
assert job.get('algo') == 'rx/sfx', job.get('algo')
assert job.get('seed_hash') == TPL['seed_hash']

job_blob = bytes.fromhex(job['blob'])
# find the proxy's extra nonce: try candidates and compare with the job blob
ro = TPL['reserved_offset']
match = None
for en in range(1, 4096):
    b = bytearray(tpl_blob); b[ro:ro + 4] = struct.pack('<I', en)
    if hashing_blob(bytes(b)) == job_blob:
        match = en; break
assert job_blob.hex() != TPL['blockhashing_blob'], 'second miner got an unmodified template blob'
assert match is not None, 'job blob merkle root does not match any expected extra nonce -> miner tx hashing is WRONG'
print(f'OK  job blob == Safex hashing blob for template with extra_nonce={match}')

nonce = 'deadbeef'
# xmrig reads the last 8 bytes as little-endian uint64; value 1 => maximum difficulty -> proxy must submit_block
fake_hash = '00' * 24 + '01' + '00' * 7
f.write((json.dumps({'id': 2, 'jsonrpc': '2.0', 'method': 'submit', 'params': {'id': resp['result']['id'], 'job_id': job['job_id'], 'nonce': nonce, 'result': fake_hash}}) + '\n').encode()); f.flush()
print('submit response:', f.readline().decode().strip())
for _ in range(20):
    if submitted: break
    time.sleep(0.5)
assert submitted, 'proxy did not call submit_block'
sb = bytes.fromhex(submitted[0])
exp = bytearray(tpl_blob); exp[ro:ro + 4] = struct.pack('<I', match); exp[p['nonce_off']:p['nonce_off'] + 4] = bytes.fromhex(nonce)
assert sb == bytes(exp), 'submitted block differs from expected template+nonce+extra_nonce'
hb = bytearray(job_blob); hb[p['nonce_off']:p['nonce_off'] + 4] = bytes.fromhex(nonce)
assert hashing_blob(sb) == bytes(hb), 'submitted block hashing blob != what the miner hashed'
print('OK  submit_block payload == template + miner nonce + same extra nonce; its hashing blob == what the miner hashed')
print('ALL TESTS PASSED')
