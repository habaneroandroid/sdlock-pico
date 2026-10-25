"""Fake MicroPython machine module for the tests: Pin, and an SPI that talks
to an emulated SD card."""
import time
_t = [0]
def _ms():
    _t[0] += 1; return _t[0]
time.ticks_ms = _ms
time.ticks_add = lambda a, b: a + b
time.ticks_diff = lambda a, b: a - b
time.sleep_ms = lambda ms: None

def crc7(data):
    crc = 0
    for b in data:
        for i in range(8):
            inv = ((b >> (7 - i)) & 1) ^ ((crc >> 6) & 1)
            crc = (crc << 1) & 0x7F
            if inv: crc ^= 0x09
    return crc

def crc16(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc <<= 1
            if crc & 0x10000: crc ^= 0x11021
    return crc & 0xFFFF

class Card:
    def __init__(self, sdhc=True):
        self.sdhc = sdhc
        self.pwd = b''
        self.data = {0: bytes(510) + b'\x55\xaa'}
        self.glitch_next_block = False   # flip a bit in the next CMD42 block
        self.flaky_reads = 0             # corrupt the next N block reads
        self.power_cycle()
        # 8 GB SDHC CSD, or a 256 MB SDSC one
        self.csd = (bytes.fromhex('400E00325B5900003B377F800A4040AF') if sdhc
                    else (9 << 80 | 1023 << 62 | 7 << 47).to_bytes(16, 'big'))
        self.cid = bytes.fromhex('035344534c3038478012345678013d') + b'\x01'
    def power_cycle(self):
        self.locked = bool(self.pwd); self.failed = False
        self.crc = False; self.idle = True; self.acmd = False; self.a41 = 0
        self.out = []; self.cmd = []; self.rx = None
    def _queue(self, *bs):
        self.out += [0xFF] + list(bs)
    def _data(self, r1, payload):
        c = crc16(payload)
        self._queue(r1); self.out += [0xFF, 0xFE] + list(payload) + [c >> 8, c & 0xFF]
    def byte(self, b, cs_low):
        if not cs_low:
            return
        if self.rx is not None:                    # receiving a CMD42 block
            if not self.rx and b == 0xFF: return
            self.rx.append(b)
            if len(self.rx) == 1 + 512 + 2:
                blk = bytes(self.rx[1:513]); crc = self.rx[513] << 8 | self.rx[514]
                self.rx = None
                if self.glitch_next_block:
                    blk = bytes([blk[0], blk[1], blk[2] ^ 0x01]) + blk[3:]
                    self.glitch_next_block = False
                if self.crc and crc16(blk) != crc:
                    self.out += [0x0B, 0xFF]; return
                self._lock_unlock(blk); self.out += [0x05, 0, 0, 0xFF]
            return
        if not self.cmd and (b & 0xC0) != 0x40: return
        self.cmd.append(b)
        if len(self.cmd) == 6:
            f = bytes(self.cmd); self.cmd = []
            self._command(f[0] & 0x3F, int.from_bytes(f[1:5], 'big'), f)
    def _command(self, cmd, arg, frame):
        idle = 0x01 if self.idle else 0
        if (self.crc or cmd in (0, 8)) and (crc7(frame[:5]) << 1 | 1) != frame[5]:
            return self._queue(idle | 0x08)
        acmd, self.acmd = self.acmd, False
        if cmd == 0: self.power_cycle_soft(); return self._queue(0x01)
        if cmd == 8: return self._queue(idle, 0, 0, 1, 0xAA)
        if cmd == 55: self.acmd = True; return self._queue(idle)
        if cmd == 41 and acmd:
            self.a41 += 1
            if self.a41 >= 2: self.idle = False
            return self._queue(0x01 if self.idle else 0)
        if cmd == 58: return self._queue(idle, 0xC0 if self.sdhc else 0x80, 0xFF, 0x80, 0)
        if cmd == 16: return self._queue(idle)
        if cmd == 59: self.crc = bool(arg & 1); return self._queue(idle)
        if cmd == 13:
            r2 = (1 if self.locked else 0) | (2 if self.failed else 0)
            self.failed = False
            return self._queue(idle, r2)
        if cmd == 9: return self._data(idle, self.csd)
        if cmd == 10: return self._data(idle, self.cid)
        if cmd == 17:
            if self.locked: return self._queue(idle | 0x04)
            lba = arg if self.sdhc else arg // 512
            d = self.data.get(lba, bytes([lba & 0xFF]) * 512)
            if self.flaky_reads:
                self.flaky_reads -= 1; d = bytes([d[0] ^ 0x80]) + d[1:]
            return self._data(idle, d)
        if cmd == 42: self.rx = []; return self._queue(idle)
        return self._queue(idle | 0x04)
    def power_cycle_soft(self):
        self.idle = True; self.crc = False; self.a41 = 0
    def _lock_unlock(self, blk):
        mode, n = blk[0], blk[1]; d = bytes(blk[2:2 + n])
        if mode & 0x02:                                    # CLR
            if self.pwd and d == self.pwd: self.pwd = b''; self.locked = False
            else: self.failed = True
        elif mode & 0x01:                                  # SET
            if not self.pwd: new = d
            elif d.startswith(self.pwd) and len(d) > len(self.pwd): new = d[len(self.pwd):]
            else: self.failed = True; return
            self.pwd = new
            if mode & 0x04: self.locked = True
        elif mode & 0x04:                                  # LOCK
            if self.pwd and d == self.pwd: self.locked = True
            else: self.failed = True
        else:                                              # UNLOCK
            if self.pwd and d == self.pwd: self.locked = False
            else: self.failed = True

CARD = Card()
RESETS = []

def soft_reset():
    RESETS.append(1)
_cs = [1]

class Pin:
    OUT = IN = PULL_UP = PULL_DOWN = 0
    def __init__(self, n, *a, **k):
        self.n = n
        if 'value' in k and n == 17: _cs[0] = k['value']
    def __call__(self, v):
        if self.n == 17:
            _cs[0] = v
            if v: CARD.out = []; CARD.cmd = []

class SPI:
    def __init__(self, *a, **k): pass
    def write(self, buf):
        for b in buf: CARD.byte(b, _cs[0] == 0)
    def read(self, n, fill=0xFF):
        out = bytearray()
        for _ in range(n):
            CARD.byte(fill, _cs[0] == 0)
            out.append(CARD.out.pop(0) if (CARD.out and _cs[0] == 0) else 0xFF)
        return bytes(out)
