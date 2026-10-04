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
            return self._send({'height': TPL['height'], 'top_block_hash': TPL['prev_hash'], 'status': 'OK'})
        req = json.loads(body or b'{}'); m = req.get('method'); rid = req.get('id'); p = req.get('params', {})
        if m in ('getblocktemplate', 'get_block_template'):
            template_calls.append((p.get('wallet_address'), 'extra_nonce' in p))
            if p.get('wallet_address') == 'NOPE':
                return self._send({'jsonrpc': '2.0', 'id': rid, 'error': {'code': -2, 'message': 'Failed to parse wallet address'}})
            return self._send({'jsonrpc': '2.0', 'id': rid, 'result': TPL})
        if m in ('submitblock', 'submit_block'):
            submitted.append(p[0])
            return self._send({'jsonrpc': '2.0', 'id': rid, 'result': {'status': 'OK'}})
        return self._send({'jsonrpc': '2.0', 'id': rid, 'error': {'code': -1, 'message': 'unknown'}})
    do_GET = do_POST
srv = HTTPServer(('127.0.0.1', DAEMON), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# ------------------------------------------------------------------ gateway process
gw = None
def start_gateway():
    global gw
    env = dict(os.environ, STRATUM_PORT=str(STRATUM), API_PORT=str(API), API_TOKEN='tok', DAEMON_HOST='127.0.0.1',
               DAEMON_PORT=str(DAEMON), DEFAULT_DIFF='10000', STATE_FILE=STATE, PROXY_BIN=PROXY, WORKER_TTL=str(TTL),
               MAX_ADDRS_PER_IP='2', INSTANCE_PORT_BASE='31500', BIND_HOST='127.0.0.1')
    gw = subprocess.Popen([sys.executable, GATEWAY], env=env, stdout=open(os.path.join(tmp, 'gw.log'), 'a'), stderr=subprocess.STDOUT)
    for _ in range(50):
        try:
            socket.create_connection(('127.0.0.1', STRATUM), timeout=1).close(); return
        except OSError: time.sleep(0.2)
    raise SystemExit('gateway did not start')
def stop_gateway():
    gw.send_signal(signal.SIGTERM)
    try: gw.wait(15)
    except subprocess.TimeoutExpired: gw.kill()
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
        line = self.f.readline()
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
cleanup()
print('ALL GATEWAY TESTS PASSED')
