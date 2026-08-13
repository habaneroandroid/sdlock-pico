"""
sdtry.py - work out which byte form of a known password a locked card holds.

Useful when you know a card's password as printed text or as a displayed hex
value, but not the exact bytes and length the original host sent. The length
is part of the password: four bytes and those same four bytes padded to
sixteen are different passwords to the card.

Uses CMD42 mode 0x00 (unlock) only. That is a temporary unlock, valid until
the next power cycle. It never sets, changes or clears a stored password, so
running this against a working card cannot alter it.

    >>> import sdlock, sdtry
    >>> sd = sdlock.SDLock()                  # expect locked=YES
    >>> sdtry.try_all(sd, sdtry.forms('0x0123456789abcdef'))
    >>> sdtry.try_all(sd, sdtry.forms('hunter2'))

On a match, power-cycle and the card comes back up locked, exactly as it was.

The SD specification defines no failed-attempt counter, so wrong guesses
should not harm the card. That is the specification, not a promise about
every controller, so prefer a card you can afford to lose.
"""

import sdlock

try:
    from ubinascii import hexlify
except ImportError:
    from binascii import hexlify


def forms(pwd, pad_to=(8, 16)):
    """Build the plausible encodings of one password, likeliest first.

    Accepts the same inputs as sdlock.parse_pwd: bytes, a '0x...' hex string,
    or plain text. A hex string also yields its literal-digit reading, and
    text also yields its bytes interpreted as hex where that is possible.
    """
    base = []
    raw = sdlock.parse_pwd(pwd)
    base.append(('as given', raw))

    if isinstance(pwd, str):
        if pwd[:2] in ('0x', '0X'):
            base.append(('hex digits as ASCII text', pwd[2:].encode()))
        else:
            try:
                base.append(('text read as hex bytes',
                             sdlock.parse_pwd('0x' + pwd)))
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
        if b not in seen:
            seen.add(b)
            uniq.append((name, b))
    return uniq


def try_all(sd, cands, reinit=True):
    """Try each (name, bytes) candidate. Returns the matching bytes, or None."""
    if not sd.status(quiet=True)['locked']:
        print('card is not locked - nothing to test')
        return None

    for name, pwd in cands:
        label = '%-38s %2d bytes  %s' % (name, len(pwd),
                                         hexlify(pwd).decode())
        try:
            sd.unlock(pwd)
        except sdlock.SDError:
            print('no    %s' % label)
            if reinit:
                try:
                    sd.init()
                except sdlock.SDError as e:
                    print('  re-init failed (%s) - power-cycle and restart' % e)
                    return None
            continue

        if not sd.status(quiet=True)['locked']:
            print('MATCH %s' % label)
            print('unlocked until the next power cycle')
            return pwd
        print('no    %s  (accepted but still locked)' % label)

    print('none of those matched')
    return None
