"""
sdread.py - read-only checks for an sdlock.py setup, for MicroPython.

Two jobs: prove the SPI link is reliable before you send CMD42, and prove
afterwards that a lock cycle left the card's data intact. Never writes.

    >>> import sdlock, sdread
    >>> sd = sdlock.SDLock()
    >>> sdread.check(sd)          # unlocked card: should pass
    >>> sdread.soak(sd)           # several blocks, more repeats
    >>> sdread.readblock(sd)      # locked card: should raise
    >>> sdread.capacity(sd)       # size in 512-byte blocks
"""

import time

try:
    from ubinascii import hexlify
except ImportError:
    from binascii import hexlify

DEFAULT_LBAS = (0, 1, 2, 1024, 65536, 1000000)


def _read_data(sd, cmd, arg, n, timeout_ms):
    """Send a command that answers with one data block of n bytes."""
    sd.cs(0)
    try:
        r1, _ = sd._cmd(cmd, arg)
        if r1 != 0:
            raise RuntimeError('CMD%d rejected, R1=0x%02x '
                               '(0x04 = card is locked)' % (cmd, r1))
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while True:
            tok = sd.spi.read(1, 0xFF)[0]
            if tok != 0xFF:
                break
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise RuntimeError('no data token from CMD%d' % cmd)
        if tok != 0xFE:
            raise RuntimeError('CMD%d error token 0x%02x' % (cmd, tok))
        data = sd.spi.read(n, 0xFF)
        sd.spi.read(2, 0xFF)              # CRC16, not checked here
    finally:
        sd.cs(1)
        sd.spi.read(1, 0xFF)
    return data


def readblock(sd, lba=0, timeout_ms=1000):
    """Read one 512-byte block with CMD17. Returns bytes."""
    addr = lba if sd.sdhc else lba * 512
    return _read_data(sd, 17, addr, 512, timeout_ms)


def _csd_blocks(csd):
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


def capacity(sd, timeout_ms=1000):
    """Card capacity in 512-byte blocks, read with CMD9 SEND_CSD."""
    return _csd_blocks(_read_data(sd, 9, 0, 16, timeout_ms))


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
    """Run check() across several widely spaced blocks.

    Blocks past the end of the card are skipped, so a small card does not
    report a wiring fault for a block it simply does not have.
    """
    try:
        blocks = capacity(sd)
    except RuntimeError as e:
        print('could not read card capacity: %s' % e)
        print('soak FAILED - do not write to the card')
        return False
    print('card capacity %d blocks (%d MB)' % (blocks, blocks // 2048))

    ok = True
    for lba in lbas:
        if lba >= blocks:
            print('block %-8d skipped, beyond the end of the card' % lba)
            continue
        try:
            check(sd, lba, repeat)
        except RuntimeError as e:
            print('block %-8d FAILED: %s' % (lba, e))
            ok = False
    print('soak %s' % ('passed' if ok else 'FAILED - do not write to the card'))
    return ok
