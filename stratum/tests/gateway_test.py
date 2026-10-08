#!/usr/bin/env python3
"""
End-to-end test of the Safex stratum gateway with a mock safexd (serving a REAL mainnet block template).

Checks:
  1. login must be a valid Safex address (rig names, bad checksums, wrong prefix are refused)
  2. two different addresses -> two xmrig-proxy instances, each asking the node for templates for ITS OWN address
  3. worker name / +difficulty parsing; shares accepted + counted; hashrate > 0
  4. a block-level share from address B is recorded for B only (blocks per address)
  5. workers drop off the list after WORKER_TTL without a share, and reappear on the next share
  6. state survives a gateway restart (blocks per address persisted)
  7. per-IP address limit
Usage: gateway_test.py <template.json> <gateway.py> <xmrig-proxy binary>
"""
import json, os, signal, socket, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sfxaddr

TPL = json.load(open(sys.argv[1]))['result']
GATEWAY, PROXY = sys.argv[2], sys.argv[3]
STRATUM, API, DAEMON = 3340, 8140, 17414
TTL = 6
tmp = tempfile.mkdtemp(prefix='gwtest-')
STATE = os.path.join(tmp, 'state.json')

def check(cond, msg):
    if not cond:
        print('FAIL:', msg); cleanup(); sys.exit(1)
    print('OK  ', msg)

# ------------------------------------------------------------------ mock safexd
template_calls = []     # (wallet, has_extra_nonce)
submitted = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, obj):
        b = json.dumps(obj).encode(); self.send_response(200)
        self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)) or 0)
        if self.path == '/getheight':
            return self._send({'height': TPL['height'], 'hash': TPL['prev_hash'], 'status': 'OK'})
        if self.path in ('/getinfo', '/get_info'):
            return self._send({'height': TPL['height'], 'top_block_hash': TPL['prev_hash'], 'status': 'OK', 'tx_pool_size': 3,
                               'difficulty': 35000000, 'start_time': int(time.time()) - 7200, 'tx_count': 1234, 'free_space': 10**12})
        req = json.loads(body or b'{}'); m = req.get('method'); rid = req.get('id'); p = req.get('params', {})
        if m in ('getblocktemplate', 'get_block_template'):
            template_calls.append((p.get('wallet_address'), 'extra_nonce' in p))
            if p.get('wallet_address') == 'NOPE':
                return self._send({'jsonrpc': '2.0', 'id': rid, 'error': {'code': -2, 'message': 'Failed to parse wallet address'}})
            return self._send({'jsonrpc': '2.0', 'id': rid, 'result': TPL})
        if m == 'get_block_headers_range':
            hs = [{'height': h, 'hash': '%064x' % h, 'timestamp': 1791000000 + h, 'block_size': 96 + h % 7, 'num_txes': h % 3,
                   'reward': 4000000000000, 'orphan_status': False} for h in range(p['start_height'], p['end_height'] + 1)]
            return self._send({'jsonrpc': '2.0', 'id': rid, 'result': {'headers': hs, 'status': 'OK'}})
        if m in ('submitblock', 'submit_block'):
            submitted.append(p[0])
            return self._send({'jsonrpc': '2.0', 'id': rid, 'result': {'status': 'OK'}})
        return self._send({'jsonrpc': '2.0', 'id': rid, 'error': {'code': -1, 'message': 'unknown'}})
    do_GET = do_POST
srv = HTTPServer(('127.0.0.1', DAEMON), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# ------------------------------------------------------------------ gateway process
gw = None
def start_gateway(extra=None):
    global gw
    env = dict(os.environ, EXEMPT_PRIVATE_IPS='0', STRATUM_PORT=str(STRATUM), API_PORT=str(API), API_TOKEN='tok', DAEMON_HOST='127.0.0.1',
               DAEMON_PORT=str(DAEMON), DEFAULT_DIFF='10000', STATE_FILE=STATE, PROXY_BIN=PROXY, WORKER_TTL=str(TTL),
               MAX_ADDRS_PER_IP='2', INSTANCE_PORT_BASE='31500', BIND_HOST='127.0.0.1')
    env.update(extra or {})
    gw = subprocess.Popen([sys.executable, GATEWAY], env=env, stdout=open(os.path.join(tmp, 'gw.log'), 'a'), stderr=subprocess.STDOUT)
    for _ in range(60):
        if gw.poll() is not None:
            raise SystemExit('gateway process exited during start-up (see gw.log)')
        try:
            socket.create_connection(('127.0.0.1', STRATUM), timeout=1).close()
            urllib.request.urlopen('http://127.0.0.1:%d/healthz' % API, timeout=2).read()
            return
        except OSError: time.sleep(0.2)
    raise SystemExit('gateway did not start')
def stop_gateway():
    gw.send_signal(signal.SIGTERM)
    try: gw.wait(15)
    except subprocess.TimeoutExpired: gw.kill(); gw.wait()
    for _ in range(50):                      # wait until the listening ports are really free again
        try:
            t = socket.socket(); t.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); t.bind(('127.0.0.1', STRATUM)); t.close(); break
        except OSError: time.sleep(0.2)
def cleanup():
    try: stop_gateway()
    except Exception: pass
    try: subprocess.call(['pkill', '-f', 'gw-proxy'])
    except OSError: pass
    print('--- gateway log tail'); print(''.join(open(os.path.join(tmp, 'gw.log')).readlines()[-15:]))

def state():
    req = urllib.request.Request('http://127.0.0.1:%d/state' % API, headers={'Authorization': 'Bearer tok'})
    return json.load(urllib.request.urlopen(req, timeout=5))

class Miner:
    def __init__(self, login, pw='x', extra=None):
        self.s = socket.create_connection(('127.0.0.1', STRATUM), timeout=30); self.f = self.s.makefile('rwb'); self.n = 1
        p = {'login': login, 'pass': pw, 'agent': 'test/1', 'algo': ['rx/sfx']}
        p.update(extra or {})
        self.f.write((json.dumps({'id': 1, 'jsonrpc': '2.0', 'method': 'login', 'params': p}) + '\n').encode()); self.f.flush()
        try:
            line = self.f.readline()
        except (ConnectionError, OSError):
            line = b''   # server closed on us (ban / limit)
        self.resp = json.loads(line) if line else None
        self.job = (self.resp or {}).get('result', {}).get('job') if self.resp else None
        self.sid = (self.resp or {}).get('result', {}).get('id') if self.resp else None
    def submit(self, hash_hex):
        self.n += 1
        self.f.write((json.dumps({'id': self.n, 'jsonrpc': '2.0', 'method': 'submit', 'params': {
            'id': self.sid, 'job_id': self.job['job_id'], 'nonce': '%08x' % (self.n * 7919), 'result': hash_hex}}) + '\n').encode()); self.f.flush()
        while True:
            m = json.loads(self.f.readline())
            if m.get('id') == self.n: return m
    def close(self): self.s.close()

def share_hash(diff): return '00' * 24 + (2**64 // diff).to_bytes(8, 'little').hex()
BLOCK_HASH = '00' * 24 + '01' + '00' * 7

start_gateway()
A, B, C = sfxaddr.gen(), sfxaddr.gen(), sfxaddr.gen()
print('addresses: A=%s... B=%s... C=%s...' % (A[:8], B[:8], C[:8]))

# 1. login validation
bad = Miner('rig1');                       check(bad.resp and 'error' in bad.resp and 'Safex address' in bad.resp['error']['message'], 'rig name as username is refused')
bad = Miner(A[:-3] + 'abc');               check(bad.resp and 'error' in bad.resp, 'address with bad checksum is refused')
bad = Miner('Safex' + A[5:].replace(A[10], A[10] == '1' and '2' or '1', 1)); check(bad.resp and 'error' in bad.resp, 'altered address is refused')
t0 = time.time()

# 2/3. two addresses, worker names, custom diff
mA = Miner(A + '.rigA+20000'); check(mA.job and mA.job['algo'] == 'rx/sfx', 'miner A (address in username) gets an rx/sfx job')
mB = Miner(B, pw='rigB');      check(mB.job is not None, 'miner B (different address, worker from password) gets a job')
check(mB.job['seed_hash'] == TPL['seed_hash'], 'job carries the node seed hash')
inst_templates = {w for (w, en) in template_calls if en}
check({A, B} <= inst_templates, 'node was asked for block templates for BOTH addresses (one proxy instance each)')
check(C not in {w for (w, en) in template_calls}, 'no instance for an address nobody logged in with')

r = mA.submit(share_hash(20000)); check(r.get('result', {}).get('status') == 'OK', 'share at diff 20000 accepted (A)')
r = mA.submit(share_hash(20000)); r = mA.submit(share_hash(20000))
r = mB.submit(share_hash(10000)); check(r.get('result', {}).get('status') == 'OK', 'share at default diff accepted (B)')
time.sleep(1)
s = state()
wa = [w for w in s['workers'] if w['address'] == A][0]; wb = [w for w in s['workers'] if w['address'] == B][0]
check(wa['worker'] == 'rigA' and wb['worker'] == 'rigB', 'worker names parsed (.suffix and password field): %s, %s' % (wa['worker'], wb['worker']))
check(wa['accepted'] == 3 and wb['accepted'] == 1, 'accepted share counts per worker (3 and 1)')
check(wa['diff'] == 20000 and wb['diff'] == 10000, 'worker difficulties tracked (+20000 override, default 10000)')
check(wa['hashrate'] > 0, 'hashrate estimate > 0 (%.1f H/s)' % wa['hashrate'])
check(s['summary']['instances'] == 2 and s['summary']['connected'] == 2, 'summary: 2 instances, 2 connected miners')

# 4. block found by B
r = mB.submit(BLOCK_HASH); check(r.get('result', {}).get('status') == 'OK', 'block-level share accepted by node (B)')
for _ in range(20):
    if len(submitted) >= 1 and state()['summary']['blocks'] >= 1: break
    time.sleep(0.5)
s = state()
check(len(submitted) == 1, 'exactly one submitblock reached the node')
blk = {a['address']: a['blocks'] for a in s['addresses']}
check(blk.get(B) == 1 and blk.get(A) == 0, 'block credited to address B only (A has 0)')
check(s['blocks'][0]['address'] == B and s['blocks'][0]['height'] == TPL['height'], 'recent blocks list shows B at template height %d' % TPL['height'])

# 7. per-IP distinct-address limit (limit is 2 in this test; A and B already connected)
c = Miner(C); check(c.resp and 'error' in c.resp and 'Too many' in c.resp['error']['message'], 'third distinct address from the same IP is refused')

# 5. 12h rule (TTL=6s here): A goes quiet, B keeps mining
time.sleep(TTL + 1)
mB.submit(share_hash(10000))
s = state()
names = {w['address'] for w in s['workers']}
check(A not in names and B in names, 'idle worker A dropped off the list after the TTL; active worker B stays')
mA.submit(share_hash(20000)); s = state()
check(A in {w['address'] for w in s['workers']}, 'worker A reappears after its next share')

# 6. persistence
mA.close(); mB.close(); time.sleep(1.5)
stop_gateway(); time.sleep(1); start_gateway()
s = state()
blk = {a['address']: a['blocks'] for a in s['addresses']}
check(blk.get(B) == 1, 'block history survived a gateway restart')
d = Miner(B); check(d.job is not None, 'address B can mine again after restart (instance respawned)'); d.close()

# 8. chain info endpoint (public chain data for the dashboard)
req = urllib.request.Request('http://127.0.0.1:%d/chain' % API, headers={'Authorization': 'Bearer tok'})
ch = json.load(urllib.request.urlopen(req, timeout=10))
check(ch['mempool'] == 3 and ch['tx_count'] == 1234 and len(ch['blocks']) == 100, '/chain: mempool, tx count and 100 recent blocks')
hs = [b['height'] for b in ch['blocks']]
check(hs == sorted(hs, reverse=True) and hs[0] == TPL['height'] - 1, '/chain: newest first, top block is height-1')
check(abs(ch['blocks'][0]['reward'] - 400.0) < 1e-9, '/chain: reward converted from atomic units (400 SFX)')
req = urllib.request.Request('http://127.0.0.1:%d/chain' % API)
try:
    urllib.request.urlopen(req, timeout=5); check(False, '/chain without token must be refused')
except urllib.error.HTTPError as e:
    check(e.code == 401, '/chain without the API token is refused (401)')
# found_by mapping: unit test with the node RPC stubbed
import importlib.util
spec = importlib.util.spec_from_file_location('gwmod', GATEWAY); gwm = importlib.util.module_from_spec(spec); spec.loader.exec_module(gwm)
gwm.STATE.blocks = [{'ts': 1, 'height': 2100005, 'address': 'SafexOURS'}]
def fake_rpc(path, body=None):
    if path == '/get_info': return {'height': 2100010, 'tx_pool_size': 0}
    return {'result': {'headers': [{'height': h, 'hash': 'x', 'timestamp': 1, 'block_size': 1, 'num_txes': 0, 'reward': 1, 'orphan_status': False} for h in range(body['params']['start_height'], body['params']['end_height'] + 1)]}}
gwm._rpc = fake_rpc
fb = {b['height']: b['found_by'] for b in gwm.chain_info()['blocks']}
check(fb.get(2100005) == 'SafexOURS' and fb.get(2100006) is None, '/chain: blocks mined through this stratum are marked with their address')

# 9. abuse limits (restart with tight limits; ban time 4 s)
mA = None
stop_gateway(); time.sleep(1)
start_gateway(dict(MAX_BAD_LOGINS='3', BAN_SECONDS='4', NEW_ADDR_PER_MIN='2', SUBMITS_PER_SEC='3', REJECT_BAN_MIN='5', MAX_ADDRS_PER_IP='9', MIN_DIFF='2000'))
def gwlog(): return open(os.path.join(tmp, 'gw.log')).read()
def wait_unbanned():
    time.sleep(5)

m = Miner(A + '.low+10')
dd = 0xFFFFFFFF // int.from_bytes(bytes.fromhex(m.job['target']), 'little') if len(m.job['target']) == 8 else 0
check(m.job is not None and 1800 <= dd <= 2200, 'requested +10 difficulty is raised to the minimum (job difficulty %d)' % dd)
m2 = Miner(B); check(m2.job is not None, 'second address starts (2nd new address this minute)')
c = Miner(C); check(c.resp and 'error' in c.resp and 'busy' in c.resp['error']['message'], 'third NEW address within a minute is refused (global start-up rate limit); got %r' % (c.resp,))
m2.close()

# share flood: way over 3 shares/s sustained (burst 9) -> disconnected + short ban
mf = Miner(A + '.flood')
for i in range(30):
    mf.n += 1
    try:
        mf.f.write((json.dumps({'id': mf.n, 'jsonrpc': '2.0', 'method': 'submit', 'params': {'id': mf.sid, 'job_id': mf.job['job_id'], 'nonce': '%08x' % mf.n, 'result': share_hash(10000)}}) + '\n').encode()); mf.f.flush()
    except OSError: break
got = 0; closed = False
mf.s.settimeout(5)
try:
    while True:
        line = mf.f.readline()
        if not line: closed = True; break
        got += 1
except (OSError, socket.timeout): pass
check(closed and got < 30, 'share flood: connection dropped after %d of 30 replies' % got)
check('share flood' in gwlog(), 'share flood is logged as the reason')
x = Miner(A + '.next'); check(x.resp is None or x.job is None, 'banned IP is refused right away')
wait_unbanned()
x = Miner(A + '.after'); check(x.job is not None, 'ban expires and the IP can mine again'); x.close()

# rejected-share ban: only bad shares
mr = Miner(A + '.bad'); closed = False
mr.s.settimeout(5)
for i in range(7):
    try:
        mr.n += 1
        mr.f.write((json.dumps({'id': mr.n, 'jsonrpc': '2.0', 'method': 'submit', 'params': {'id': mr.sid, 'job_id': mr.job['job_id'], 'nonce': '%08x' % (mr.n * 31), 'result': 'ff' * 32}}) + '\n').encode()); mr.f.flush()
        if not mr.f.readline(): closed = True; break
    except (OSError, socket.timeout): closed = True; break
check(closed and 'rejected vs' in gwlog(), 'connection that only sends rejected shares is dropped and its IP banned')
wait_unbanned()

# bad-login ban: 3 bad usernames -> banned even for a valid login
for i in range(3): Miner('nonsense%d' % i)
v = Miner(A + '.valid'); check(v.resp is None or v.job is None, '3 invalid logins ban the IP (a valid login is refused too)')
check('too many invalid logins' in gwlog(), 'ban reason logged')
wait_unbanned()
v = Miner(A + '.valid2'); check(v.job is not None, 'ban expires after BAN_SECONDS'); v.close()
cleanup()
print('ALL GATEWAY TESTS PASSED')
