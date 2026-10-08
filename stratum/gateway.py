#!/usr/bin/env python3
"""
Safex stratum gateway: solo mining where every miner is paid to the Safex address in its own username.

  miner --stratum--> gateway :17404 --(per address)--> xmrig-proxy (daemon/solo mode, user=<address>) --> safexd

* Username format:  <SafexAddress>[.workername][+difficulty]      e.g.  Safex5...abc.rig1+50000
  (worker name may alternatively come from the rig-id or the password field)
* One xmrig-proxy instance per distinct address, started on demand, stopped when idle. Each instance asks the
  node for block templates that pay THAT address, so a found block pays the full reward to its finder.
* Tracks per-worker stats (shares, hashrate, last activity) and blocks found per address; serves them on
  GET /state (Bearer token) for the dashboard. State is persisted in STATE_FILE.
"""
import asyncio, collections, hmac, ipaddress, json, os, re, signal, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def env(name, default):
    v = os.environ.get(name)
    if v is None or v == '':
        return default
    return type(default)(v)

STRATUM_PORT   = env('STRATUM_PORT', 17404)
API_PORT       = env('API_PORT', 8080)
API_TOKEN      = env('API_TOKEN', '')
DAEMON_HOST    = env('DAEMON_HOST', 'node')
DAEMON_PORT    = env('DAEMON_PORT', 17402)
DEFAULT_DIFF   = env('DEFAULT_DIFF', 50000)
STATE_FILE     = env('STATE_FILE', '/data/gateway-state.json')
PROXY_BIN      = env('PROXY_BIN', '/usr/local/bin/xmrig-proxy')
WORKER_TTL     = env('WORKER_TTL', 12 * 3600)       # seconds without a share before a worker leaves the list
MAX_INSTANCES  = env('MAX_INSTANCES', 50)
MAX_CONNS_IP   = env('MAX_CONNS_PER_IP', 64)
# --- abuse limits (all per remote IP unless noted; private/loopback/Tailscale IPs are exempt from the per-IP ones)
MAX_CONNS_TOTAL = env('MAX_CONNS_TOTAL', 500)
MAX_CONNS_ADDR = env('MAX_CONNS_PER_ADDR', 64)         # per payout address
LOGINS_PER_MIN = env('LOGINS_PER_MIN', 30)             # connection attempts per minute
MAX_BAD_LOGINS = env('MAX_BAD_LOGINS', 10)             # invalid usernames/addresses within 10 min -> ban
BAN_SECONDS    = env('BAN_SECONDS', 900)
NEW_ADDR_PER_MIN = env('NEW_ADDR_PER_MIN', 6)          # global: new addresses (proxy instances) started per minute
SUBMITS_PER_SEC = env('SUBMITS_PER_SEC', 20)           # sustained share submits per connection (burst = 3 s worth)
REJECT_BAN_MIN = env('REJECT_BAN_MIN', 50)             # rejected shares (and 5x more rejected than accepted) -> ban
MIN_DIFF       = env('MIN_DIFF', 2000)                 # lowest +difficulty a miner may ask for
IDLE_CONN      = env('IDLE_CONN', 1800)                # drop a connection that sends nothing for this long
EXEMPT_PRIVATE = env('EXEMPT_PRIVATE_IPS', 1)          # 1 = LAN/loopback/Tailscale are never limited or banned
CHAIN_CACHE    = 15
CHAIN_BLOCKS   = max(1, min(500, int(env('CHAIN_BLOCKS', 100))))   # recent blocks returned by /chain
MAX_ADDRS_IP   = env('MAX_ADDRS_PER_IP', 8)
IDLE_STOP      = env('IDLE_STOP', 600)              # seconds an instance with no miners is kept alive
PORT_BASE      = env('INSTANCE_PORT_BASE', 30000)
BIND_HOST      = env('BIND_HOST', '0.0.0.0')
HASHRATE_WINDOW = 600


def log(*a):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


# ---------------------------------------------------------------- Safex address validation (base58 + keccak)
_RC = [0x0000000000000001,0x0000000000008082,0x800000000000808A,0x8000000080008000,0x000000000000808B,0x0000000080000001,
       0x8000000080008081,0x8000000000008009,0x000000000000008A,0x0000000000000088,0x0000000080008009,0x000000008000000A,
       0x000000008000808B,0x800000000000008B,0x8000000000008089,0x8000000000008003,0x8000000000008002,0x8000000000000080,
       0x000000000000800A,0x800000008000000A,0x8000000080008081,0x8000000000008080,0x0000000080000001,0x8000000080008008]
_ROT = [[0,36,3,41,18],[1,44,10,45,2],[62,6,43,15,61],[28,55,25,21,56],[27,20,39,8,14]]
_M = (1 << 64) - 1
def _rol(x, n): return ((x << n) | (x >> (64 - n))) & _M if n else x
def _keccak_f(A):
    for rnd in range(24):
        C = [A[x][0] ^ A[x][1] ^ A[x][2] ^ A[x][3] ^ A[x][4] for x in range(5)]
        D = [C[(x - 1) % 5] ^ _rol(C[(x + 1) % 5], 1) for x in range(5)]
        A = [[A[x][y] ^ D[x] for y in range(5)] for x in range(5)]
        B = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                B[y][(2 * x + 3 * y) % 5] = _rol(A[x][y], _ROT[x][y])
        A = [[B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        A[0][0] ^= _RC[rnd]
    return A
def keccak256(data):
    rate = 136
    data = bytearray(data) + b'\x01'
    while len(data) % rate:
        data += b'\x00'
    data[-1] |= 0x80
    A = [[0] * 5 for _ in range(5)]
    for off in range(0, len(data), rate):
        blk = data[off:off + rate]
        for i in range(rate // 8):
            A[i % 5][i // 5] ^= int.from_bytes(blk[8 * i:8 * i + 8], 'little')
        A = _keccak_f(A)
    return b''.join(A[i % 5][i // 5].to_bytes(8, 'little') for i in range(4))

_ALPH = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_DEC_SIZES = {0: 0, 2: 1, 3: 2, 5: 3, 6: 4, 7: 5, 9: 6, 10: 7, 11: 8}
def b58_decode(s):
    full, last = divmod(len(s), 11)
    if last not in _DEC_SIZES:
        raise ValueError('bad length')
    out = bytearray()
    for i in range(full + (1 if last else 0)):
        blk = s[i * 11:(i + 1) * 11]
        n = 0
        for ch in blk:
            n = n * 58 + _ALPH.index(ch)
        size = 8 if i < full else _DEC_SIZES[last]
        if n >> (8 * size):
            raise ValueError('overflow')
        out += n.to_bytes(size, 'big')
    return bytes(out)
def _varint(n):
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7f) | 0x80); n >>= 7
    out.append(n)
    return bytes(out)

SAFEX_PREFIX = _varint(0x10003798)      # standard mainnet address ("Safex...")
def checksum_ok(addr):
    try:
        raw = b58_decode(addr)
    except ValueError:
        return False
    if len(raw) != len(SAFEX_PREFIX) + 64 + 4 or not raw.startswith(SAFEX_PREFIX):
        return False
    return keccak256(raw[:-4])[:4] == raw[-4:]


# ---------------------------------------------------------------- username parsing
ADDR_RE   = re.compile(r'^(Safex[1-9A-HJ-NP-Za-km-z]{90,110})(?:\.([A-Za-z0-9_\-]{1,32}))?(?:\+(\d{1,12}))?$')
CLEAN_RE  = re.compile(r'[^A-Za-z0-9_\-]')

def parse_login(login, params):
    """Return (address, worker, diff_or_None) or None."""
    m = ADDR_RE.match((login or '').strip())
    if not m:
        return None
    addr, worker, diff = m.group(1), m.group(2), m.group(3)
    if not worker:
        for cand in (params.get('rigid'), params.get('rig-id'), params.get('pass')):
            cand = CLEAN_RE.sub('', str(cand or ''))[:32]
            if cand and cand.lower() not in ('x', 'n/a', 'na'):
                worker = cand
                break
    worker = worker or 'default'
    d = None
    if diff:
        d = max(MIN_DIFF, min(int(diff), 10 ** 12))
    return addr, worker, d

def target_to_diff(t):
    try:
        b = bytes.fromhex(t)
    except (ValueError, TypeError):
        return 0
    if len(b) == 4:
        v = int.from_bytes(b, 'little')
        return (0xFFFFFFFF // v) if v else 0
    if len(b) == 8:
        v = int.from_bytes(b, 'little')
        return (0xFFFFFFFFFFFFFFFF // v) if v else 0
    return 0

def short(addr):
    return addr[:5] + '.....' + addr[-5:]


# ---------------------------------------------------------------- state (stats + blocks), persisted
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.workers = {}              # key -> dict
        self.windows = {}              # key -> deque[(ts, diff)]
        self.blocks = []               # [{ts, height, address}]
        self.dirty = False

    def key(self, addr, name):
        return addr + '/' + name

    def connect(self, addr, name, ip):
        now = time.time()
        with self.lock:
            k = self.key(addr, name)
            w = self.workers.get(k)
            if w is None:
                w = self.workers[k] = dict(address=addr, worker=name, ip=ip, accepted=0, rejected=0, hashes=0,
                                           last_share=0, connected_at=now, first_seen=now, conns=0, diff=0)
                self.windows[k] = collections.deque()
            w['ip'] = ip
            w['conns'] += 1
            if w['conns'] == 1:
                w['connected_at'] = now
            self.dirty = True
            return k

    def disconnect(self, k):
        with self.lock:
            w = self.workers.get(k)
            if w:
                w['conns'] = max(0, w['conns'] - 1)
                self.dirty = True

    def share(self, k, ok, diff):
        now = time.time()
        with self.lock:
            w = self.workers.get(k)
            if not w:
                return
            if ok:
                w['accepted'] += 1
                w['hashes'] += diff
                w['last_share'] = now
                self.windows[k].append((now, diff))
            else:
                w['rejected'] += 1
            self.dirty = True

    def set_diff(self, k, diff):
        with self.lock:
            w = self.workers.get(k)
            if w and diff:
                w['diff'] = diff

    def add_block(self, addr, height):
        with self.lock:
            self.blocks.append(dict(ts=int(time.time()), height=int(height), address=addr))
            del self.blocks[:-2000]
            self.dirty = True
        log('BLOCK FOUND by', short(addr), 'at height', height)

    @staticmethod
    def last_active(w):
        return max(w['last_share'], w['connected_at'] if w['conns'] else 0)

    def housekeeping(self, now=None):
        now = now or time.time()
        with self.lock:
            for k in list(self.workers):
                w = self.workers[k]
                if w['conns'] == 0 and now - max(w['last_share'], w['connected_at']) > WORKER_TTL:
                    del self.workers[k]
                    self.windows.pop(k, None)
                    self.dirty = True
            for dq in self.windows.values():
                while dq and now - dq[0][0] > HASHRATE_WINDOW:
                    dq.popleft()

    def snapshot(self, instances=0, now=None):
        now = now or time.time()
        self.housekeeping(now)
        with self.lock:
            workers, per_addr = [], {}
            tot_hr = 0.0
            tot_acc = tot_rej = connected = 0
            for k, w in self.workers.items():
                connected += w['conns']
                if now - self.last_active(w) > WORKER_TTL:
                    continue                      # no mining activity for WORKER_TTL: not listed
                dq = self.windows.get(k) or ()
                elapsed = max(60.0, min(float(HASHRATE_WINDOW), now - w['connected_at']))
                hr = sum(d for t, d in dq if now - t <= HASHRATE_WINDOW) / elapsed
                workers.append(dict(address=w['address'], worker=w['worker'], ip=w['ip'], accepted=w['accepted'],
                                    rejected=w['rejected'], hashrate=hr, last_share=int(w['last_share']),
                                    connected=w['conns'] > 0, diff=w['diff']))
                tot_hr += hr; tot_acc += w['accepted']; tot_rej += w['rejected']
                a = per_addr.setdefault(w['address'], dict(address=w['address'], blocks=0, hashrate=0.0, connected=0, last_block=0))
                a['hashrate'] += hr; a['connected'] += w['conns']
            for b in self.blocks:
                a = per_addr.setdefault(b['address'], dict(address=b['address'], blocks=0, hashrate=0.0, connected=0, last_block=0))
                a['blocks'] += 1; a['last_block'] = max(a['last_block'], b['ts'])
            workers.sort(key=lambda x: (-x['connected'], -x['last_share']))
            addresses = sorted(per_addr.values(), key=lambda a: (-a['blocks'], -a['hashrate']))
            return dict(now=int(now), ttl_hours=WORKER_TTL / 3600.0,
                        summary=dict(workers=len(workers), connected=connected, hashrate=tot_hr, accepted=tot_acc,
                                     rejected=tot_rej, addresses=len(addresses), instances=instances,
                                     blocks=len(self.blocks)),
                        workers=workers, addresses=addresses, blocks=self.blocks[-20:][::-1])

    def save(self):
        with self.lock:
            if not self.dirty:
                return
            data = dict(version=1, blocks=self.blocks, workers=[
                {k: v for k, v in w.items() if k != 'conns'} for w in self.workers.values()])
            self.dirty = False
        try:
            os.makedirs(os.path.dirname(STATE_FILE) or '.', exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATE_FILE) or '.', prefix='.gw-')
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, STATE_FILE)
        except OSError as e:
            log('could not save state:', e)

    def load(self):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        self.blocks = [b for b in data.get('blocks', []) if isinstance(b, dict) and 'address' in b][-2000:]
        for w in data.get('workers', []):
            try:
                w['conns'] = 0
                self.workers[self.key(w['address'], w['worker'])] = w
                self.windows[self.key(w['address'], w['worker'])] = collections.deque()
            except KeyError:
                pass
        log('state loaded:', len(self.workers), 'workers,', len(self.blocks), 'blocks')

STATE = State()


# ---------------------------------------------------------------- xmrig-proxy instances (one per address)
class Instance:
    def __init__(self, addr, port):
        self.addr, self.port = addr, port
        self.proc = None
        self.conns = 0
        self.last_active = time.time()
        self.ready = False
        self.had_share = False
        self.tail = collections.deque(maxlen=40)
        self.task = None

    def alive(self):
        return self.proc is not None and self.proc.returncode is None

    async def start(self):
        cfg = {
            'mode': 'extra_nonce', 'donate-level': 0, 'custom-diff': DEFAULT_DIFF, 'custom-diff-stats': False,
            'colors': False, 'workers': False, 'watch': False,
            'bind': [{'host': '127.0.0.1', 'port': self.port, 'tls': False}],
            'pools': [{'url': '%s:%d' % (DAEMON_HOST, DAEMON_PORT), 'user': self.addr, 'coin': 'SFX',
                       'daemon': True, 'daemon-poll-interval': 1000}],
            'http': {'enabled': False},
        }
        d = os.path.join(tempfile.gettempdir(), 'gw-proxy')
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, 'proxy-%d.json' % self.port)
        with open(path, 'w') as f:
            json.dump(cfg, f)
        self.proc = await asyncio.create_subprocess_exec(
            PROXY_BIN, '--config=' + path, '--no-color',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self.task = asyncio.create_task(self._read_output())
        deadline = time.time() + 20
        while time.time() < deadline:
            if not self.alive():
                raise RuntimeError('proxy instance exited: ' + ' | '.join(list(self.tail)[-3:]))
            if await self._probe():
                self.ready = True
                log('instance up for', short(self.addr), 'on port', self.port)
                return
            await asyncio.sleep(0.5)
        raise RuntimeError('no job from node in time: ' + ' | '.join(list(self.tail)[-3:]))

    async def _probe(self):
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', self.port), 2)
        except (OSError, asyncio.TimeoutError):
            return False
        try:
            w.write((json.dumps({'id': 1, 'jsonrpc': '2.0', 'method': 'login', 'params': {
                'login': 'probe', 'pass': 'x', 'agent': 'safex-gateway/1', 'algo': ['rx/sfx']}}) + '\n').encode())
            await w.drain()
            line = await asyncio.wait_for(r.readline(), 3)
            res = (json.loads(line) or {}).get('result') or {}
            return isinstance(res.get('job'), dict)
        except (OSError, asyncio.TimeoutError, ValueError, AttributeError):
            return False
        finally:
            w.close()

    async def _read_output(self):
        pat = re.compile(r'BLOCK_FOUND height=(\d+)')
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                s = line.decode(errors='replace').rstrip()
                self.tail.append(s)
                m = pat.search(s)
                if m:
                    STATE.add_block(self.addr, int(m.group(1)))
        except Exception as e:
            log('instance reader error', e)

    def stop(self):
        if self.alive():
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass

INSTANCES = {}
_LOCKS = {}
VALID, INVALID = {}, {}
VALIDATE_SEM = asyncio.Semaphore(4)       # at most 4 node lookups for unknown addresses at once

def pick_port():
    used = {i.port for i in INSTANCES.values()}
    for p in range(PORT_BASE, PORT_BASE + MAX_INSTANCES * 2):
        if p not in used:
            return p
    raise RuntimeError('no free port')

class Refused(Exception):
    pass

async def get_instance(addr):
    lock = _LOCKS.setdefault(addr, asyncio.Lock())
    async with lock:
        inst = INSTANCES.get(addr)
        if inst and inst.alive() and inst.ready:
            return inst
        if inst:
            inst.stop(); INSTANCES.pop(addr, None)
        if len(INSTANCES) >= MAX_INSTANCES:
            raise Refused('Server is at its limit of active addresses, try again later')
        now = time.time()
        while NEW_STARTS and now - NEW_STARTS[0] > 60:
            NEW_STARTS.popleft()
        if len(NEW_STARTS) >= NEW_ADDR_PER_MIN:
            raise Refused('Server is busy starting other addresses, try again in a minute')
        NEW_STARTS.append(now)
        inst = Instance(addr, pick_port())
        INSTANCES[addr] = inst
        try:
            await inst.start()
        except Exception as e:
            log('instance start failed for', short(addr), '-', e)
            inst.stop(); INSTANCES.pop(addr, None)
            raise Refused('Node is not ready to provide block templates, try again shortly')
        return inst

def node_template(addr):
    """Ask the node itself whether it accepts this address. Returns 'ok' | 'invalid' | 'busy'."""
    body = json.dumps({'jsonrpc': '2.0', 'id': 0, 'method': 'get_block_template',
                       'params': {'wallet_address': addr, 'reserve_size': 8}}).encode()
    req = urllib.request.Request('http://%s:%d/json_rpc' % (DAEMON_HOST, DAEMON_PORT), data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
    except Exception:
        return 'busy'
    if isinstance(resp.get('result'), dict) and resp['result'].get('blocktemplate_blob'):
        return 'ok'
    err = resp.get('error') or {}
    if err.get('code') == -2 or 'address' in str(err.get('message', '')).lower():
        return 'invalid'
    return 'busy'

async def check_address(addr):
    now = time.time()
    if VALID.get(addr, 0) > now:
        return 'ok'
    if INVALID.get(addr, 0) > now:
        return 'invalid'
    if not checksum_ok(addr):
        INVALID[addr] = now + 600
        return 'invalid'
    res = await asyncio.to_thread(node_template, addr)
    if res == 'ok':
        VALID[addr] = now + 3600
    elif res == 'invalid':
        INVALID[addr] = now + 600
    for cache in (VALID, INVALID):
        if len(cache) > 5000:
            cache.clear()
    return res


# ---------------------------------------------------------------- abuse protection helpers
BANS = {}                                  # ip -> banned-until
LOGIN_LOG = collections.defaultdict(collections.deque)
NEW_STARTS = collections.deque()
_CGNAT = ipaddress.ip_network('100.64.0.0/10')   # Tailscale

def limited(ip):
    # True if per-IP limits/bans apply to this address.
    if not EXEMPT_PRIVATE:
        return True
    try:
        a = ipaddress.ip_address(ip.split('%')[0])
    except ValueError:
        return True
    if a.version == 4 and a in _CGNAT:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local)

def ban(ip, why, seconds=None):
    if not limited(ip):
        return
    dur = min(seconds, BAN_SECONDS) if seconds else BAN_SECONDS
    BANS[ip] = time.time() + dur
    log('banned', ip, 'for', int(dur), 's:', why)
    if len(BANS) > 5000:
        now = time.time()
        for k in [k for k, v in BANS.items() if v < now]:
            del BANS[k]

def banned(ip):
    until = BANS.get(ip)
    if not until:
        return False
    if until < time.time():
        del BANS[ip]
        return False
    return True

def login_rate_ok(ip):
    now = time.time()
    dq = LOGIN_LOG[ip]
    while dq and now - dq[0] > 60:
        dq.popleft()
    if len(dq) >= LOGINS_PER_MIN:
        return False
    dq.append(now)
    if len(LOGIN_LOG) > 5000:
        for k in [k for k, v in LOGIN_LOG.items() if not v or now - v[-1] > 60]:
            del LOGIN_LOG[k]
    return True


# ---------------------------------------------------------------- stratum front end
CONNS = []                       # active Conn objects
BAD_LOGINS = collections.defaultdict(list)

class Conn:
    def __init__(self, ip):
        self.ip, self.addr, self.key = ip, None, None
        self.pending = collections.OrderedDict()
        self.diff = 0
        self.inst = None
        self.acc = self.rej = 0
        self.tokens = SUBMITS_PER_SEC * 3.0
        self.t_last = time.time()

def err_line(rid, msg):
    return (json.dumps({'id': rid, 'jsonrpc': '2.0', 'error': {'code': -1, 'message': msg}}) + '\n').encode()

async def c2s(cr, bw, conn):
    while True:
        try:
            line = await asyncio.wait_for(cr.readline(), IDLE_CONN)
        except (ValueError, ConnectionError, asyncio.TimeoutError):
            return
        if not line:
            return
        try:
            m = json.loads(line)
            if isinstance(m, dict) and m.get('method') == 'submit':
                now = time.time()
                conn.tokens = min(SUBMITS_PER_SEC * 3.0, conn.tokens + (now - conn.t_last) * SUBMITS_PER_SEC)
                conn.t_last = now
                if conn.tokens < 1:
                    log('share flood from', conn.ip, short(conn.addr or ''), '- disconnecting')
                    ban(conn.ip, 'share flood', 120)
                    return
                conn.tokens -= 1
                conn.pending[json.dumps(m.get('id'))] = time.time()
                while len(conn.pending) > 500:
                    conn.pending.popitem(last=False)
        except ValueError:
            pass
        bw.write(line)
        await bw.drain()

async def s2c(br, cw, conn):
    while True:
        try:
            line = await br.readline()
        except (ValueError, ConnectionError):
            return
        if not line:
            return
        try:
            m = json.loads(line)
            if isinstance(m, dict):
                params, res = m.get('params'), m.get('result')
                if m.get('method') == 'job' and isinstance(params, dict):
                    conn.diff = target_to_diff(params.get('target')) or conn.diff
                    STATE.set_diff(conn.key, conn.diff)
                else:
                    rid = json.dumps(m.get('id'))
                    if rid in conn.pending:
                        del conn.pending[rid]
                        ok = bool(not m.get('error') and isinstance(res, dict) and res.get('status') == 'OK')
                        STATE.share(conn.key, ok, conn.diff)
                        if ok:
                            conn.acc += 1
                            if conn.inst:
                                conn.inst.had_share = True
                        else:
                            conn.rej += 1
                            if conn.rej >= REJECT_BAN_MIN and conn.acc * 5 < conn.rej:
                                ban(conn.ip, '%d rejected vs %d accepted shares' % (conn.rej, conn.acc), 600)
                                return
                    elif isinstance(res, dict) and isinstance(res.get('job'), dict):
                        conn.diff = target_to_diff(res['job'].get('target')) or conn.diff
                        STATE.set_diff(conn.key, conn.diff)
        except (ValueError, TypeError):
            pass
        cw.write(line)
        await cw.drain()

async def handle(cr, cw):
    peer = cw.get_extra_info('peername') or ('?', 0)
    ip = peer[0]
    conn = Conn(ip)
    inst = None
    bw = None
    try:
        now = time.time()
        lim = limited(ip)
        if lim and banned(ip):
            return
        if len(CONNS) >= MAX_CONNS_TOTAL:
            return
        if lim and (not login_rate_ok(ip) or sum(1 for c in CONNS if c.ip == ip) >= MAX_CONNS_IP):
            return
        BAD_LOGINS[ip] = [t for t in BAD_LOGINS[ip] if now - t < 600]
        try:
            line = await asyncio.wait_for(cr.readline(), 15)
        except (asyncio.TimeoutError, ValueError, ConnectionError):
            return
        if not line:
            return
        try:
            msg = json.loads(line)
            params = msg['params']
            assert msg['method'] == 'login' and isinstance(params, dict)
        except (ValueError, KeyError, AssertionError, TypeError):
            return
        rid = msg.get('id')
        parsed = parse_login(str(params.get('login', '')), params)
        if not parsed:
            BAD_LOGINS[ip].append(now)
            if len(BAD_LOGINS[ip]) >= MAX_BAD_LOGINS:
                ban(ip, 'too many invalid logins')
            cw.write(err_line(rid, 'Username must be your Safex address, e.g. Safex5...  (optionally .workername and +difficulty)'))
            await cw.drain(); return
        addr, worker, diff = parsed
        if sum(1 for c in CONNS if c.addr == addr) >= MAX_CONNS_ADDR:
            cw.write(err_line(rid, 'Too many connections for this address'))
            await cw.drain(); return
        if lim and addr not in {c.addr for c in CONNS if c.ip == ip} and len({c.addr for c in CONNS if c.ip == ip}) >= MAX_ADDRS_IP:
            cw.write(err_line(rid, 'Too many different addresses from one IP address'))
            await cw.drain(); return
        async with VALIDATE_SEM:
            verdict = await check_address(addr)
        if verdict == 'invalid':
            BAD_LOGINS[ip].append(now)
            if len(BAD_LOGINS[ip]) >= MAX_BAD_LOGINS:
                ban(ip, 'too many invalid logins')
            cw.write(err_line(rid, 'Invalid Safex address'))
            await cw.drain(); return
        if verdict == 'busy':
            cw.write(err_line(rid, 'Node is not ready (still syncing?), try again shortly'))
            await cw.drain(); return
        try:
            inst = await get_instance(addr)
        except Refused as e:
            cw.write(err_line(rid, str(e)))
            await cw.drain(); return
        try:
            br, bw = await asyncio.open_connection('127.0.0.1', inst.port, limit=65536)
        except OSError:
            cw.write(err_line(rid, 'Backend unavailable, try again shortly'))
            await cw.drain(); return
        inst.conns += 1
        inst.last_active = time.time()
        conn.inst = inst
        conn.addr = addr
        conn.key = STATE.connect(addr, worker, ip)
        CONNS.append(conn)
        params2 = dict(params)
        params2['login'] = worker + ('+%d' % diff if diff else '')
        msg2 = dict(msg); msg2['params'] = params2
        bw.write((json.dumps(msg2) + '\n').encode())
        await bw.drain()
        log('miner', short(addr), worker, ip, 'connected' + (' (diff %d)' % diff if diff else ''))
        t1 = asyncio.create_task(c2s(cr, bw, conn))
        t2 = asyncio.create_task(s2c(br, cw, conn))
        done, pend = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        for t in pend:
            t.cancel()
    except Exception as e:
        log('connection error from', ip, repr(e))
    finally:
        if conn in CONNS:
            CONNS.remove(conn)
        if conn.key:
            STATE.disconnect(conn.key)
        if inst:
            inst.conns = max(0, inst.conns - 1)
            inst.last_active = time.time()
        for w in (bw, cw):
            try:
                if w:
                    w.close()
            except Exception:
                pass


# ---------------------------------------------------------------- chain info for the dashboard (public chain data only)
_CHAIN = {'at': 0, 'data': None}
_CHAIN_LOCK = threading.Lock()
ATOMIC = 10 ** 10                                   # Safex has 10 decimal places

def _rpc(path, body=None):
    url = 'http://%s:%d%s' % (DAEMON_HOST, DAEMON_PORT, path)
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())

def chain_info():
    with _CHAIN_LOCK:
        if _CHAIN['data'] and time.time() - _CHAIN['at'] < CHAIN_CACHE:
            return _CHAIN['data']
        info = _rpc('/get_info')
        top = int(info['height']) - 1
        hdrs = _rpc('/json_rpc', {'jsonrpc': '2.0', 'id': 0, 'method': 'get_block_headers_range',
                                  'params': {'start_height': max(0, top - (CHAIN_BLOCKS - 1)), 'end_height': top}})['result']['headers']
        with STATE.lock:
            ours = {b['height']: b['address'] for b in STATE.blocks}
        blocks = [dict(height=h['height'], hash=h['hash'], timestamp=h['timestamp'], size=h['block_size'],
                       txs=h['num_txes'], reward=h['reward'] / ATOMIC, orphan=bool(h.get('orphan_status')),
                       found_by=ours.get(h['height'])) for h in sorted(hdrs, key=lambda x: -x['height'])]
        data = dict(height=info['height'], mempool=info.get('tx_pool_size', 0), difficulty=info.get('difficulty'),
                    start_time=info.get('start_time'), tx_count=info.get('tx_count'),
                    free_space=info.get('free_space'), blocks=blocks, now=int(time.time()))
        _CHAIN.update(at=time.time(), data=data)
        return data


# ---------------------------------------------------------------- HTTP API for the dashboard
class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == '/healthz':
            return self._send(200, {'ok': True})
        if API_TOKEN:
            got = self.headers.get('Authorization', '')
            if not hmac.compare_digest(got, 'Bearer ' + API_TOKEN):
                return self._send(401, {'error': 'unauthorized'})
        if self.path == '/state':
            return self._send(200, STATE.snapshot(instances=len(INSTANCES)))
        if self.path == '/chain':
            try:
                return self._send(200, chain_info())
            except Exception as e:
                return self._send(502, {'error': 'node not reachable: %s' % type(e).__name__})
        self._send(404, {'error': 'not found'})


# ---------------------------------------------------------------- main
async def housekeeping():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for addr, inst in list(INSTANCES.items()):
            if not inst.alive():
                log('instance for', short(addr), 'exited; will restart on next login')
                INSTANCES.pop(addr, None)
            elif inst.conns == 0 and now - inst.last_active > (IDLE_STOP if inst.had_share else 60):
                log('stopping idle instance for', short(addr))
                inst.stop(); INSTANCES.pop(addr, None)
        STATE.housekeeping()
        STATE.save()

async def main():
    STATE.load()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(s, stop.set)
    server = await asyncio.start_server(handle, BIND_HOST, STRATUM_PORT, limit=65536)
    httpd = ThreadingHTTPServer((BIND_HOST, API_PORT), Api)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    hk = asyncio.create_task(housekeeping())
    log('gateway listening: stratum %s:%d  api :%d  node %s:%d  default diff %d' % (
        BIND_HOST, STRATUM_PORT, API_PORT, DAEMON_HOST, DAEMON_PORT, DEFAULT_DIFF))
    await stop.wait()
    log('shutting down')
    hk.cancel()
    server.close()
    for inst in list(INSTANCES.values()):
        inst.stop()
    STATE.dirty = True
    STATE.save()

if __name__ == '__main__':
    asyncio.run(main())
