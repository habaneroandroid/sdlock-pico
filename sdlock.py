"""
sdlock.py - SD card password lock/unlock (CMD42) over SPI, for MicroPython.

Sets, tests, changes and removes the CMD42 password on an SD card using a
Raspberry Pi Pico as the host.

Why SPI: a password-locked card can be brought up over SPI because SPI-mode
initialisation never reads the SCR register. Linux's SD stack does read it,
which is why a locked card usually fails to enumerate on a PC and why most
desktop tooling cannot touch one.

Wiring (SPI0 defaults; pass different pins to SDLock() to change them):

    module CS   --- GP17, physical pin 22
    module SCK  --- GP18, physical pin 24
    module MOSI --- GP19, physical pin 25
    module MISO --- GP16, physical pin 21
    module 3V3  --- physical pin 36  (3V3 OUT, NOT pin 37 = 3V3_EN)
    module GND  --- physical pin 38
    module 5V   --- leave unconnected

Never insert or remove a card while the board is powered.

Usage from the REPL:

    >>> import sdlock
    >>> sd = sdlock.SDLock()
    >>> sd.status()
    >>> sd.set('0x0123456789abcdef')    # assign; card locks at next power-up
    >>> sd.unlock('0x0123456789abcdef') # test a password, until power cycle
    >>> sd.clear('0x0123456789abcdef')  # remove permanently
    >>> sd.lock('0x0123456789abcdef')   # lock now, password already stored
    >>> sd.setlock('0x0123456789abcdef')# assign and lock in one command
    >>> sd.set('0xold', '0xnew')        # change an existing password

Passwords may be given as bytes, a '0x...' hex string, or plain text. The
length is part of the password: four bytes and those same four bytes padded
to sixteen are different passwords to the card.

If init fails, build the object without initialising and run the diagnostic:

    >>> sd = sdlock.SDLock(auto=False)
    >>> sd.probe()

Force-erase (CMD42 mode bit 0x08) is deliberately not implemented. It wipes
the card, and on a card with permanent write protect set it can leave the
card unusable. See the README for the consequences of losing a password.
"""

from machine import Pin, SPI
import time

try:
    from ubinascii import hexlify
except ImportError:
    from binascii import hexlify

VERSION = '1.0.0'

# CMD42 data block, byte 0 (mode). Bit 3, force erase, is intentionally absent.
MODE_UNLOCK = 0x00
MODE_SET_PWD = 0x01
MODE_CLR_PWD = 0x02
MODE_LOCK = 0x04

# R1, first response byte in SPI mode
R1_IDLE = 0x01
R1_ILLEGAL_CMD = 0x04

# R2, second response byte of CMD13 in SPI mode
R2_CARD_LOCKED = 0x01
R2_LOCK_UNLOCK_FAILED = 0x02

MAX_PWD = 16

NCR_BYTES = 100          # how long to wait for R1
NCS_BYTES = 8            # idle clocks after CS low, before the command frame


def crc7(data):
    """CRC7, poly x^7+x^3+1, returned in bits 6..0. Used on command frames."""
    crc = 0
    for b in data:
        for i in range(8):
            inv = ((b >> (7 - i)) & 1) ^ ((crc >> 6) & 1)
            crc = (crc << 1) & 0x7F
            if inv:
                crc ^= 0x09
    return crc


def crc16(data):
    """CRC16-CCITT, poly x^16+x^12+x^5+1, init 0. Used on data blocks."""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc <<= 1
            if crc & 0x10000:
                crc ^= 0x11021
    return crc & 0xFFFF


def parse_pwd(pwd):
    """Accept bytes, a '0x...' hex string, or plain text."""
    if pwd is None:
        return b''
    if isinstance(pwd, (bytes, bytearray)):
        out = bytes(pwd)
    elif pwd[:2] in ('0x', '0X'):
        h = pwd[2:]
        if len(h) == 0 or len(h) % 2:
            raise ValueError('hex password needs an even digit count')
        out = bytes(int(h[i:i + 2], 16) for i in range(0, len(h), 2))
    else:
        out = pwd.encode()
    if len(out) > MAX_PWD:
        raise ValueError('password longer than %d bytes' % MAX_PWD)
    return out


class SDError(Exception):
    pass


class SDLock:
    """A CMD42 host. Construct with auto=False to skip initialisation."""

    def __init__(self, spi_id=0, sck=18, mosi=19, miso=16, cs=17,
                 baudrate=400000, auto=True):
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.spi = SPI(spi_id, baudrate=baudrate, polarity=0, phase=0,
                       sck=Pin(sck), mosi=Pin(mosi), miso=Pin(miso))
        self.sdhc = False
        self.crc = False
        if auto:
            self.init()

    # ---- low level ----

    def _cmd(self, cmd, arg=0, extra=0):
        """Send a command with CS already low. Returns (r1, extra_bytes)."""
        # Ncs: idle clocks before the frame, and let a busy MISO come back up.
        for _ in range(NCS_BYTES):
            if self.spi.read(1, 0xFF)[0] == 0xFF:
                break

        buf = bytearray(6)
        buf[0] = 0x40 | cmd
        buf[1] = (arg >> 24) & 0xFF
        buf[2] = (arg >> 16) & 0xFF
        buf[3] = (arg >> 8) & 0xFF
        buf[4] = arg & 0xFF
        buf[5] = (crc7(buf[:5]) << 1) | 1
        self.spi.write(buf)

        r1 = 0xFF
        for _ in range(NCR_BYTES):
            r1 = self.spi.read(1, 0xFF)[0]
            if not (r1 & 0x80):
                break
        else:
            raise SDError('no response to CMD%d' % cmd)

        tail = self.spi.read(extra, 0xFF) if extra else b''
        return r1, tail

    def _txn(self, cmd, arg=0, extra=0):
        """One complete command transaction, CS low to CS high."""
        self.cs(0)
        try:
            return self._cmd(cmd, arg, extra)
        finally:
            self.cs(1)
            self.spi.read(1, 0xFF)

    def _acmd(self, cmd, arg=0):
        """CMD55 then the application command, each in its own CS cycle."""
        r1, _ = self._txn(55)
        if r1 & 0xFE:
            raise SDError('CMD55 refused, R1=0x%02x' % r1)
        return self._txn(cmd, arg)[0]

    def _wait_ready(self, timeout_ms=10000):
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if self.spi.read(1, 0xFF)[0] == 0xFF:
                return
            time.sleep_ms(1)
        raise SDError('card stayed busy')

    # ---- init ----

    def init(self, crc=True):
        """Bring the card up in SPI mode. Works on a locked card."""
        self.cs(1)
        self.spi.write(b'\xff' * 10)          # >= 74 clocks with CS high

        for _ in range(16):
            try:
                r1, _ = self._txn(0)           # CMD0 GO_IDLE_STATE
            except SDError:
                r1 = 0xFF
            if r1 == R1_IDLE:
                break
            time.sleep_ms(50)
        else:
            raise SDError('CMD0 failed - check wiring, power and pull-ups')

        last = None
        for _ in range(4):                     # CMD8 SEND_IF_COND
            try:
                r1, echo = self._txn(8, 0x000001AA, extra=4)
                break
            except SDError as e:
                last = e
                time.sleep_ms(20)
        else:
            raise SDError('CMD8 got no response: %s' % last)

        v2 = not (r1 & R1_ILLEGAL_CMD)
        if v2 and (echo[2] != 0x01 or echo[3] != 0xAA):
            raise SDError('CMD8 echo mismatch: %s' % hexlify(echo))

        last = None
        deadline = time.ticks_add(time.ticks_ms(), 3000)
        while True:
            try:
                if self._acmd(41, 0x40000000 if v2 else 0) == 0:
                    break
            except SDError as e:
                last = e                       # transient, keep trying
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise SDError('ACMD41 timed out - card never left idle%s'
                              % ('' if last is None else ' (last: %s)' % last))
            time.sleep_ms(20)

        if v2:
            r1, ocr = self._txn(58, extra=4)   # CMD58 READ_OCR
            self.sdhc = bool(ocr[0] & 0x40)
        else:
            self.sdhc = False

        self._txn(16, 512)                     # CMD16 SET_BLOCKLEN

        self.crc = False
        if crc:
            r1, _ = self._txn(59, 1)           # CMD59 CRC_ON_OFF, 1 = on
            self.crc = (r1 == 0)
            if not self.crc:
                print('warning: CMD59 refused (R1=0x%02x), data CRC stays off'
                      % r1)

        print('card initialised (%s, data CRC %s)'
              % ('SDHC/SDXC' if self.sdhc else 'SDSC',
                 'on' if self.crc else 'OFF'))
        return self.status()

    # ---- diagnostics ----

    def probe(self, rounds=6):
        """Raw R1 for a short command sequence, repeated. Reads only.

        Every round should look the same. Entries showing '--' (no response),
        or answers that vary between rounds, point at the wiring or the supply
        rather than at the card.
        """
        self.cs(1)
        self.spi.write(b'\xff' * 10)
        for r in range(rounds):
            out = []
            for c, a, e in ((0, 0, 0), (8, 0x000001AA, 4), (55, 0, 0),
                            (58, 0, 4), (13, 0, 1)):
                try:
                    r1, tail = self._txn(c, a, extra=e)
                    out.append('CMD%d=%02x%s'
                               % (c, r1,
                                  (':' + hexlify(tail).decode()) if tail else ''))
                except SDError:
                    out.append('CMD%d=--' % c)
                time.sleep_ms(2)
            print('%d: %s' % (r + 1, ' '.join(out)))

    # ---- status ----

    def status(self, quiet=False):
        """CMD13 SEND_STATUS. Returns a dict; also prints unless quiet."""
        r1, tail = self._txn(13, extra=1)
        r2 = tail[0]
        locked = bool(r2 & R2_CARD_LOCKED)
        failed = bool(r2 & R2_LOCK_UNLOCK_FAILED)
        if not quiet:
            print('R1=0x%02x R2=0x%02x  locked=%s%s'
                  % (r1, r2, 'YES' if locked else 'no',
                     '  LOCK_UNLOCK_FAILED' if failed else ''))
        return {'r1': r1, 'r2': r2, 'locked': locked, 'failed': failed}

    # ---- CMD42 ----

    def _cmd42(self, mode, pwd=b''):
        """Send one CMD42 lock/unlock data block."""
        block = bytearray(512)
        block[0] = mode
        block[1] = len(pwd)
        block[2:2 + len(pwd)] = pwd

        self.cs(0)
        try:
            r1, _ = self._cmd(42, 0)
            if r1 != 0:
                raise SDError('CMD42 rejected, R1=0x%02x' % r1)

            self.spi.read(1, 0xFF)
            self.spi.write(b'\xfe')            # start block token
            self.spi.write(block)
            c = crc16(block)                   # real CRC16; the card checks it
            self.spi.write(bytes((c >> 8, c & 0xFF)))   # once CMD59 is on

            for _ in range(64):
                token = self.spi.read(1, 0xFF)[0]
                if token != 0xFF:
                    break
            else:
                raise SDError('no data response token')
            if (token & 0x1F) != 0x05:
                raise SDError('data rejected, token=0x%02x' % token)

            self._wait_ready()
        finally:
            self.cs(1)
            self.spi.read(1, 0xFF)

        st = self.status(quiet=True)
        if st['failed']:
            raise SDError('card reports LOCK_UNLOCK_FAILED - wrong password, '
                          'or the wrong length or encoding (R2=0x%02x)'
                          % st['r2'])
        self.status()
        return st

    # ---- operations ----

    def unlock(self, pwd):
        """Unlock until the next power cycle. Does not alter the stored
        password, so it is the safe way to test a candidate."""
        return self._cmd42(MODE_UNLOCK, parse_pwd(pwd))

    def lock(self, pwd=None):
        """Lock now. Most cards want the stored password passed in."""
        return self._cmd42(MODE_LOCK, parse_pwd(pwd))

    def set(self, pwd, new_pwd=None):
        """Assign a password, or change one by passing old then new.

        The card is not locked immediately; it locks itself at the next
        power-up, which is the behaviour most host devices rely on.
        """
        data = parse_pwd(pwd) + parse_pwd(new_pwd)
        if len(data) > MAX_PWD * 2:
            raise ValueError('combined password too long')
        return self._cmd42(MODE_SET_PWD, data)

    def setlock(self, pwd):
        """Assign a password and lock in one command."""
        return self._cmd42(MODE_SET_PWD | MODE_LOCK, parse_pwd(pwd))

    def clear(self, pwd):
        """Remove the password permanently. The card then behaves normally."""
        return self._cmd42(MODE_CLR_PWD, parse_pwd(pwd))
