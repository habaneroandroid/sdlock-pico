"""
Desktop tests for sdlock.py and main.py, no Pico needed.

machine.py in this folder stands in for MicroPython's machine module. Its
SPI talks to an emulated SD card that follows the SPI protocol byte by byte,
including CRCs, lock state and password rules.

    python3 tests/test_all.py
"""

import builtins, contextlib, io, json, os, runpy, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [HERE, REPO]          # fake machine first, then the real code

import machine, sdlock

def fresh(sdhc=True):
    machine.CARD = C2 = machine.Card(sdhc); return C2

# ---- library ----
c = fresh()
sd = sdlock.SDLock(verbose=False)
assert sd.crc and sd.sdhc
assert sd.capacity() == 15160 * 1024
assert sd.cid()['name'] == 'SL08G' and sd.cid()['serial'] == '12345678', sd.cid()
assert sd.soak(quiet=True)
sd.set('abc'); assert c.pwd == b'abc' and not c.locked
assert sd.verify('abc') and not c.locked
assert not sd.verify('abd')
c.power_cycle(); sd.init(); assert sd.status(quiet=True)['locked']
try: sd.readblock(0); raise SystemExit('read on locked card worked')
except sdlock.SDError: pass
sd.unlock('abc'); sd.set('abc', 'xyz'); assert c.pwd == b'xyz'
c.glitch_next_block = True
try: sd.clear('xyz'); raise SystemExit('corrupted block accepted')
except sdlock.SDError as e: assert 'rejected' in str(e), e
assert c.pwd == b'xyz'
sd.clear('xyz'); assert c.pwd == b''
sd.crc = False
try: sd.set('q'); raise SystemExit('wrote with CRC off')
except sdlock.SDError: pass
sd.crc = True
c.flaky_reads = 1                    # a bad read must fail the wiring check
with contextlib.redirect_stdout(io.StringIO()):
    assert not sd.soak(quiet=True)
sm = fresh(sdhc=False); sd = sdlock.SDLock(verbose=False)
assert sd.capacity() == 524288 and sd.soak(quiet=True)
print('library tests ok')

# ---- menu, driven by scripted input ----
os.chdir(tempfile.mkdtemp())        # main.py writes sdlock.json here

def menu(keys, card):
    machine.CARD = card
    feed = list(keys)
    def fake_input(prompt=''):
        print(prompt, end='')
        if not feed: raise EOFError
        k = feed.pop(0); print(k); return k
    builtins.input = fake_input
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try: runpy.run_path(REPO + '/main.py', run_name='__main__')
        except EOFError: pass
    assert not feed, 'unused keys %r' % feed
    return buf.getvalue()

def store(): return json.load(open('sdlock.json'))

c = machine.Card()
# set a new typed password, save as "car", remember card
out = menu(['1', '0x0123456789abcdef', '0x0123456789abcdef', 'y', 'car',
            'y', 'y', 'q'], c)
assert c.pwd == bytes.fromhex('0123456789abcdef') and not c.locked, out
assert 'Done. The card will lock' in out and 'Wiring OK' in out
s = store(); assert s['passwords'] == {'car': '0123456789abcdef'}
rec = list(s['cards'].values())[0]; assert rec['name'] == 'car', s
SET_OUT = out

# power cycle: menu shows LOCKED and the saved password; clear uses it
c.power_cycle()
out = menu(['2', '', 'y', 'q'], c)
assert 'LOCKED' in out and 'password car' in out, out
assert c.pwd == b'' and 'Done. The card has no password now.' in out, out
rec = list(store()['cards'].values())[0]; assert rec['pwd'] is None

# set with a saved password: no typing twice; card already remembered
out = menu(['1', 'car', 'y', 'q'], c)   # pick saved by name
assert c.pwd == bytes.fromhex('0123456789abcdef'), out
assert 'Remember this card' not in out

# locked card, change password: current from card record, new typed
c.power_cycle()
out = menu(['1', '', 'NewPw', 'NewPw', '', 'y', 'q'], c)
assert c.pwd == b'NewPw', out   # case kept

# typo in second entry: nothing changes
out = menu(['1', 'aaa', 'aab', 'q'], c)
assert c.pwd == b'NewPw' and "don't match" in out

# wrong password on clear
c.power_cycle()
out = menu(['2', 'n', 'nope', '', 'q'], c)
assert c.pwd == b'NewPw' and 'not the password' in out, out

# test menu: find password form; card holds zero-padded-to-16 form
c.power_cycle(); c.pwd = b'hunter2' + bytes(9); c.locked = True
out = menu(['t', '3', 'hunter2', '', 'n', 'q'], c)
assert 'MATCH' in out and not c.locked, out

# unlock a card that was locked elsewhere, then remember it
PWD = '04285431436c6172696f6e3935333231'
other = machine.Card(); other.pwd = bytes.fromhex(PWD)
other.cid = other.cid[:9] + b'\x9b\xa7\x17\x25' + other.cid[13:]   # new serial
other.power_cycle()
out = menu(['t', '1', '0x' + PWD, 'y', 'clarion', 'y', 'q'], other)
assert 'Unlocked.' in out and 'Remember this card' in out, out
assert not other.locked
rec = [r for r in store()['cards'].values() if r['label'].endswith('9ba71725')][0]
assert rec['name'] == 'clarion' and rec['pwd'] == PWD and rec['date'] is None
other.power_cycle()
out = menu(['q'], other)
assert 'Saved:   password clarion\n' in out, out

# wiring check on locked card, and flaky wiring blocks writes
c.power_cycle()
out = menu(['w', 'q'], c); assert 'Card answered 20 times' in out, out
d = machine.Card(); d.flaky_reads = 1
out = menu(['1', 'abc', 'abc', '', 'q'], d)
assert d.pwd == b'' and 'Not writing anything' in out, out

# card that fails to start
dead = machine.Card(); dead.byte = lambda b, cs: None
out = menu(['q'], dead); assert 'Could not start the card' in out, out
print('menu tests ok')
if '-v' in sys.argv:
    print(SET_OUT)
