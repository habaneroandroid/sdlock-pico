"""
sdread.py - read-only checks for an sdlock.py setup, for MicroPython.

Two jobs: prove the SPI link is reliable before you send CMD42, and prove
afterwards that a lock cycle left the card's data intact. Never writes.

    >>> import sdlock, sdread
    >>> sd = sdlock.SDLock()
    >>> sdread.check(sd)          # unlocked card: should pass
    >>> sdread.soak(sd)           # several blocks, more repeats
    >>> sdread.readblock(sd)      # locked card: should raise
"""

import time

try:
    from ubinascii import hexlify
except ImportError:
    from binascii import hexlify

DEFAULT_LBAS = (0, 1, 2, 1024, 65536, 1000000)


def readblock(sd, lba=0, timeout_ms=1000):
    """Read one 512-byte block with CMD17. Returns bytes."""
    addr = lba if sd.sdhc else lba * 512
    sd.cs(0)
    try:
        r1, _ = sd._cmd(17, addr)
        if r1 != 0:
            raise RuntimeError('CMD17 rejected, R1=0x%02x '
                               '(0x04 = card is locked)' % r1)
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while True:
            tok = sd.spi.read(1, 0xFF)[0]
            if tok != 0xFF:
                break
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise RuntimeError('no data token from CMD17')
        if tok != 0xFE:
            raise RuntimeError('CMD17 error token 0x%02x' % tok)
        data = sd.spi.read(512, 0xFF)
        sd.spi.read(2, 0xFF)              # CRC16, not checked here
    finally:
        sd.cs(1)
        sd.spi.read(1, 0xFF)
    return data


def check(sd, lba=0, repeat=8):
    """Read one block repeatedly and confirm every read is identical.

    Inconsistent reads mean the link is marginal. Fix that before sending
    any CMD42: the data block carries the password, and a flipped bit there
    sets a password you do not know.
    """
    first = readblock(sd, lba)
    for n in range(repeat - 1):
        if readblock(sd, lba) != first:
            raise RuntimeError('block %d differed on read %d - SPI is '
                               'unreliable, do not send CMD42' % (lba, n + 2))
    print('block %-8d %2d identical reads   first 16: %s%s'
          % (lba, repeat, hexlify(first[:16]).decode(),
             '   [MBR signature]'
             if first[510] == 0x55 and first[511] == 0xAA else ''))
    return first


def soak(sd, lbas=DEFAULT_LBAS, repeat=12):
    """Run check() across several widely spaced blocks."""
    ok = True
    for lba in lbas:
        try:
            check(sd, lba, repeat)
        except RuntimeError as e:
            print('block %-8d FAILED: %s' % (lba, e))
            ok = False
    print('soak %s' % ('passed' if ok else 'FAILED - do not write to the card'))
    return ok
