"""
sdlock.py - set and clear the password on an SD card (CMD42) over SPI, for
MicroPython on a Raspberry Pi Pico.

Most people should use the menu in main.py, which runs on boot. This module
is the library behind it, and can also be used from the REPL:

    >>> import sdlock
    >>> sd = sdlock.SDLock()
    >>> sd.soak()                        # check the wiring (unlocked card)
    >>> sd.set('0x0123456789abcdef')     # set; card locks at next power-up
    >>> sd.verify('0x0123456789abcdef')  # lock then unlock with it
    >>> sd.clear('0x0123456789abcdef')   # remove the password
    >>> sd.unlock('0x0123456789abcdef')  # unlock until power-off
    >>> sd.set('0xold', '0xnew')         # change a password
    >>> sdlock.try_all(sd, sdlock.forms('hunter2'))   # find the byte form

Passwords may be bytes, a '0x...' hex string, or plain text, up to 16 bytes.
The length counts: 4 bytes and the same 4 bytes padded to 16 are different
passwords.

Wiring is in the README. If init fails, try:

    >>> sd = sdlock.SDLock(auto=False)
    >>> sd.probe()

Force-erase (CMD42 mode 0x08) is not implemented on purpose: it wipes the
card.
"""

from machine import Pin, SPI
import time

try:
    from ubinascii import hexlify
except ImportError:
    from binascii import hexlify

VERSION = '1.1.0'

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

DEFAULT_LBAS = (0, 1, 2, 1024, 65536, 1000000)   # blocks soak() reads


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


def csd_blocks(csd):
    """Card capacity in 512-byte blocks, from the 16-byte CSD register."""
    if csd[0] >> 6 == 0:                  # CSD v1, SDSC
        read_bl_len = csd[5] & 0x0F
        c_size = ((csd[6] & 0x03) << 10) | (csd[7] << 2) | (csd[8] >> 6)
        c_size_mult = ((csd[9] & 0x03) << 1) | (csd[10] >> 7)
        return (c_size + 1) << (c_size_mult + 2 + read_bl_len - 9)
    if csd[0] >> 6 == 1:                  # CSD v2, SDHC/SDXC
        c_size = ((csd[7] & 0x3F) << 16) | (csd[8] << 8) | csd[9]
    else:                                 # CSD v3, SDUC
        c_size = ((csd[6] & 0x0F) << 24) | (csd[7] << 16) | (csd[8] << 8) \
            | csd[9]
    return (c_size + 1) * 1024


def parse_cid(cid):
    """Decode the 16-byte CID register. 'id' is unique to the card."""
    def text(b):
        return ''.join(chr(c) if 32 <= c < 127 else '?' for c in b).strip()
    return {
        'id': hexlify(cid[:15]).decode(),
        'mid': cid[0],
        'oem': text(cid[1:3]),
        'name': text(cid[3:8]),
        'rev': '%d.%d' % (cid[8] >> 4, cid[8] & 0x0F),
        'serial': '%08x' % ((cid[9] << 24) | (cid[10] << 16)
                            | (cid[11] << 8) | cid[12]),
        'date': '%d-%02d' % (2000 + (((cid[13] & 0x0F) << 4) | (cid[14] >> 4)),
                             cid[14] & 0x0F),
    }


class SDError(Exception):
    pass


class PasswordError(SDError):
    """The card ran CMD42 and reported LOCK_UNLOCK_FAILED."""
    pass


class SDLock:
    """A CMD42 host. Construct with auto=False to skip initialisation, and
    verbose=False to stop it printing status after each command."""

    def __init__(self, spi_id=0, sck=18, mosi=19, miso=16, cs=17,
                 baudrate=400000, auto=True, verbose=True):
        self.verbose = verbose
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

        if self.verbose:
            print('card initialised (%s, data CRC %s)'
                  % ('SDHC/SDXC' if self.sdhc else 'SDSC',
                     'on' if self.crc else 'OFF'))
        return self.status(quiet=not self.verbose)

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

    # ---- reads (never write) ----

    def _read_data(self, cmd, arg, n, timeout_ms=1000):
        """Send a command that answers with one data block of n bytes."""
        self.cs(0)
        try:
            r1, _ = self._cmd(cmd, arg)
            if r1 != 0:
                raise SDError('CMD%d rejected, R1=0x%02x (0x04 = card is '
                              'locked)' % (cmd, r1))
            deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
            while True:
                tok = self.spi.read(1, 0xFF)[0]
                if tok != 0xFF:
                    break
                if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                    raise SDError('no data token from CMD%d' % cmd)
            if tok != 0xFE:
                raise SDError('CMD%d error token 0x%02x' % (cmd, tok))
            data = self.spi.read(n, 0xFF)
            crc = self.spi.read(2, 0xFF)
        finally:
            self.cs(1)
            self.spi.read(1, 0xFF)
        if self.crc and crc16(data) != (crc[0] << 8) | crc[1]:
            raise SDError('CMD%d data CRC mismatch' % cmd)
        return data

    def readblock(self, lba=0):
        """Read one 512-byte block with CMD17. Fails on a locked card."""
        return self._read_data(17, lba if self.sdhc else lba * 512, 512)

    def capacity(self):
        """Card size in 512-byte blocks (CMD9). Works on a locked card."""
        return csd_blocks(self._read_data(9, 0, 16))

    def cid(self):
        """Card identity (CMD10) as a dict. Works on a locked card."""
        return parse_cid(self._read_data(10, 0, 16))

    def check(self, lba=0, repeat=8, quiet=False):
        """Read one block repeatedly and confirm every read is identical."""
        first = self.readblock(lba)
        for n in range(repeat - 1):
            if self.readblock(lba) != first:
                raise SDError('block %d differed on read %d - the wiring is '
                              'unreliable' % (lba, n + 2))
        if not quiet:
            print('block %-8d %2d identical reads   first 16: %s%s'
                  % (lba, repeat, hexlify(first[:16]).decode(),
                     '   [MBR signature]'
                     if first[510] == 0x55 and first[511] == 0xAA else ''))
        return first

    def soak(self, lbas=DEFAULT_LBAS, repeat=12, quiet=False):
        """Wiring check: check() across several blocks. Returns True if
        every read matched. Needs an unlocked card. Blocks past the end of
        the card are skipped."""
        try:
            blocks = self.capacity()
        except SDError as e:
            print('could not read card size: %s' % e)
            return False
        if not quiet:
            print('card size %d blocks (%d MB)' % (blocks, blocks // 2048))
        ok = True
        for lba in lbas:
            if lba >= blocks:
                if not quiet:
                    print('block %-8d skipped, past the end of the card' % lba)
                continue
            try:
                self.check(lba, repeat, quiet)
            except SDError as e:
                print('block %-8d FAILED: %s' % (lba, e))
                ok = False
        if not quiet or not ok:
            print('wiring check %s' % ('passed' if ok else 'FAILED'))
        return ok

    # ---- CMD42 ----

    def _cmd42(self, mode, pwd=b''):
        """Send one CMD42 lock/unlock data block."""
        if mode & (MODE_SET_PWD | MODE_CLR_PWD) and not self.crc:
            raise SDError('data CRC is off, so a corrupted password could be '
                          'stored. Not writing. Re-run init(); set '
                          'sd.crc = True to override.')
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
            raise PasswordError('card reports LOCK_UNLOCK_FAILED - wrong '
                                'password, or the wrong length or encoding '
                                '(R2=0x%02x)' % st['r2'])
        if self.verbose:
            self.status()
        return st

    # ---- operations ----

    def unlock(self, pwd):
        """Unlock until the next power cycle. Does not alter the stored
        password, so it is the safe way to test a candidate."""
        return self._cmd42(MODE_UNLOCK, parse_pwd(pwd))

    def lock(self, pwd):
        """Lock now. The card only locks if pwd matches the stored one."""
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

    def verify(self, pwd):
        """Check the stored password is pwd, without a power cycle: lock
        with it, then unlock with it. Leaves the card unlocked. Returns
        False if the card refused the lock, meaning the stored password is
        something else."""
        try:
            self.lock(pwd)
        except PasswordError:
            return False
        self.unlock(pwd)
        return True

    def setlock(self, pwd):
        """Assign a password and lock in one command."""
        return self._cmd42(MODE_SET_PWD | MODE_LOCK, parse_pwd(pwd))

    def clear(self, pwd):
        """Remove the password permanently. The card then behaves normally."""
        return self._cmd42(MODE_CLR_PWD, parse_pwd(pwd))


# ---- finding the byte form of a known password ----

def forms(pwd, pad_to=(8, 16)):
    """Build the plausible encodings of one password, likeliest first.

    Accepts the same inputs as parse_pwd: bytes, a '0x...' hex string,
    or plain text. A hex string also yields its literal-digit reading, and
    text also yields its bytes interpreted as hex where that is possible.
    """
    base = []
    raw = parse_pwd(pwd)
    base.append(('as given', raw))

    if isinstance(pwd, str):
        if pwd[:2] in ('0x', '0X'):
            base.append(('hex digits as ASCII text', pwd[2:].encode()))
        else:
            try:
                base.append(('text read as hex bytes',
                             parse_pwd('0x' + pwd)))
            except ValueError:
                pass

    out = []
    for name, b in base:
        out.append(('%s' % name, b))
        for n in pad_to:
            if len(b) < n:
                out.append(('%s, zero padded to %d' % (name, n),
                            b + bytes(n - len(b))))
        if len(b) < 16:
            out.append(('%s, 0xFF padded to 16' % name,
                        b + b'\xff' * (16 - len(b))))
        if len(b) > 1:
            out.append(('%s, byte order reversed' % name, bytes(reversed(b))))

    seen, uniq = set(), []
    for name, b in out:
        if len(b) > MAX_PWD:            # e.g. long hex read as ASCII
            continue
        if b not in seen:
            seen.add(b)
            uniq.append((name, b))
    return uniq


def _reinit(sd):
    """Re-initialise the card. Returns False, with a message, on failure."""
    try:
        sd.init()
        return True
    except SDError as e:
        print('  re-init failed (%s) - power-cycle and restart' % e)
        return False


def try_all(sd, cands, reinit=True):
    """Try each (name, bytes) candidate. Returns the matching bytes, or None.

    Only a LOCK_UNLOCK_FAILED answer counts as a wrong password. Any other
    error is a link problem, so that candidate is retried once after a
    re-init; a second link error stops the run rather than skip a candidate
    that was never really tested.
    """
    if not sd.status(quiet=True)['locked']:
        print('card is not locked - nothing to test')
        return None

    for name, pwd in cands:
        label = '%-38s %2d bytes  %s' % (name, len(pwd),
                                         hexlify(pwd).decode())
        for attempt in range(2):
            try:
                sd.unlock(pwd)
                break
            except PasswordError:
                break
            except SDError as e:
                print('  link error on %s: %s' % (name, e))
                if not _reinit(sd):
                    return None
                if not sd.status(quiet=True)['locked']:
                    break                      # the unlock got through
                if attempt:
                    print('  link error twice on the same candidate - fix '
                          'the wiring (sd.soak()) and run again')
                    return None

        if not sd.status(quiet=True)['locked']:
            print('MATCH %s' % label)
            print('unlocked until the next power cycle')
            return pwd
        print('no    %s' % label)
        if reinit and not _reinit(sd):
            return None

    print('none of those matched')
    return None
