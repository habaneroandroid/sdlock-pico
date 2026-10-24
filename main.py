"""
main.py - menu for sdlock. MicroPython runs this when the Pico starts.

Press Stop in Thonny, or Ctrl+C, to get the normal Python prompt instead.

Saved passwords and remembered cards are kept in sdlock.json on the Pico,
in plain text. Anyone with the Pico can read them.
"""

import json
import os
import time

import sdlock
from sdlock import SDError, PasswordError

try:
    from ubinascii import hexlify, unhexlify
except ImportError:
    from binascii import hexlify, unhexlify

STORE = 'sdlock.json'


# ---- saved passwords and cards ----

def load():
    try:
        with open(STORE) as f:
            data = json.load(f)
    except OSError:
        data = {}
    except ValueError:
        # Keep the damaged file rather than overwrite it on the next save.
        os.rename(STORE, STORE + '.bad')
        print('%s could not be read. Moved it to %s.bad and started a new '
              'one.' % (STORE, STORE))
        data = {}
    data.setdefault('passwords', {})
    data.setdefault('cards', {})
    return data


def save(data):
    tmp = STORE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    try:
        os.rename(tmp, STORE)
    except OSError:
        os.remove(STORE)
        os.rename(tmp, STORE)


def tohex(pwd):
    return hexlify(pwd).decode()


def today():
    t = time.localtime()
    return '%d-%02d-%02d' % t[:3] if t[0] >= 2025 else None   # clock not set


# ---- input helpers ----

def ask(prompt):
    return input(prompt).strip()


def yes(prompt, default=False):
    a = ask(prompt + (' [Y/n] ' if default else ' [y/N] ')).lower()
    return default if not a else a in ('y', 'yes')


def show(pwd):
    """'16 bytes 0123...  "text"' - exactly what the card is sent."""
    s = '%d bytes  %s' % (len(pwd), tohex(pwd))
    if pwd and all(32 <= c < 127 for c in pwd):
        s += '  "%s"' % pwd.decode()
    return s


def parse(text):
    try:
        return sdlock.parse_pwd(text)
    except ValueError as e:
        print(e)
        return None


def save_as(data, pwd):
    """Offer to save a password under a name. Returns the name or None."""
    for name, h in data['passwords'].items():
        if h == tohex(pwd):
            return name                        # already saved
    if not yes('Save this password for next time?'):
        return None
    name = ask('Name for it: ')
    if not name:
        return None
    if name in data['passwords'] and not yes('Replace the saved "%s"?'
                                             % name):
        return None
    data['passwords'][name] = tohex(pwd)
    save(data)
    return name


def pick(data, rec=None, new=False):
    """Choose a password. Returns (name, bytes), or None to cancel.

    rec is this card's record, if remembered. new=True asks for a typed
    password twice, since a typo there sets a password nobody knows.
    """
    if rec and rec.get('pwd'):
        pwd = unhexlify(rec['pwd'])
        if yes('Use the password saved for this card (%s)?'
               % (rec.get('name') or show(pwd)), default=True):
            return rec.get('name'), pwd

    names = sorted(data['passwords'])
    for i, name in enumerate(names):
        print('  %d) %-12s %s'
              % (i + 1, name, show(unhexlify(data['passwords'][name]))))
    print('  n) Type a password')
    a = ask('Choice (Enter to cancel): ').lower()
    if not a:
        return None
    if a.isdigit() and 1 <= int(a) <= len(names):
        name = names[int(a) - 1]
        return name, unhexlify(data['passwords'][name])
    if a != 'n':
        return None

    print('Type it as text, or as hex starting with 0x. Up to 16 bytes.')
    pwd = parse(ask('Password: '))
    if pwd is None or not pwd:
        return None
    print('  = ' + show(pwd))
    if new:
        again = parse(ask('Again:    '))
        if again != pwd:
            print("They don't match. Nothing changed.")
            return None
    return save_as(data, pwd), pwd


def remember(data, card, name, pwd):
    """Update this card's record, or offer to create one."""
    rec = data['cards'].get(card['id'])
    if rec is None:
        if not yes('Remember this card and its password?'):
            return
        rec = data['cards'][card['id']] = {'label': card['label']}
    rec['name'] = name
    rec['pwd'] = tohex(pwd) if pwd else None
    rec['date'] = today()
    save(data)


# ---- card ----

def connect():
    """Start the card. Returns (sd, card), or (None, None) on failure."""
    try:
        sd = sdlock.SDLock(verbose=False)
        cid = sd.cid()
        mb = sd.capacity() // 2048
    except SDError as e:
        print('\nCould not start the card: %s' % e)
        print('Check the card is pushed fully in and the wiring is right '
              '(see README), then power off and on again.')
        return None, None
    card = {'id': cid['id'], 'mb': mb,
            'label': '%s %s' % (cid['name'], cid['serial']),
            'type': 'SDHC/SDXC' if sd.sdhc else 'SDSC'}
    return sd, card


def wiring_ok(sd):
    """Quick wiring check before anything that writes a password."""
    print('Checking the wiring...')
    if sd.soak(repeat=6, quiet=True):
        print('Wiring OK.')
        return True
    print('Not writing anything. Fix the wiring first (see README).')
    return False


def header(sd, card, data):
    st = sd.status(quiet=True)
    rec = data['cards'].get(card['id'])
    print()
    print('Card:    %s  %d MB %s' % (card['label'], card['mb'], card['type']))
    print('Status:  %s' % ('LOCKED' if st['locked'] else 'unlocked'))
    if rec is None:
        print('Saved:   not a remembered card')
    elif rec.get('pwd'):
        print('Saved:   password %s%s'
              % (rec.get('name') or show(unhexlify(rec['pwd'])),
                 ', set ' + rec['date'] if rec.get('date') else ''))
    else:
        print('Saved:   remembered, no password')
    if not sd.crc:
        print('Warning: the card refused data CRC checking, so setting and '
              'clearing are disabled.')
    return st


# ---- actions ----

def do_set(sd, card, data, st):
    rec = data['cards'].get(card['id'])
    old = None
    if st['locked']:
        print('This card is locked, so it already has a password. Choose it '
              'to change it to a new one.')
        got = pick(data, rec)
        if not got:
            return
        old = got[1]
        try:
            sd.unlock(old)
        except PasswordError:
            print('That is not the password on this card.')
            return

    print('Choose the new password.')
    got = pick(data, new=True)
    if not got:
        return
    name, pwd = got
    if not wiring_ok(sd):
        return
    if not yes('Set the password to %s?' % show(pwd)):
        return

    try:
        sd.set(old, pwd) if old else sd.set(pwd)
    except PasswordError:
        if old:
            print('The card refused the change. Nothing was set.')
            return
        print('The card already has a password. Choose it to change it.')
        got = pick(data, rec)
        if not got:
            return
        old = got[1]
        try:
            sd.set(old, pwd)
        except PasswordError:
            print('That is not the password on this card. Nothing changed.')
            return

    print('Checking the card stored it (lock, then unlock)...')
    if not sd.verify(pwd):
        print('PROBLEM: the card has a password, but it would not lock with '
              'the one you entered, so it is storing something else. The '
              'card stays unlocked until it is powered off.')
        return
    print('Done. The card will lock itself next time it is powered on.')
    remember(data, card, name, pwd)


def do_clear(sd, card, data, st):
    rec = data['cards'].get(card['id'])
    print('Choose the password that is on this card.')
    got = pick(data, rec)
    if not got:
        return
    pwd = got[1]
    if st['locked']:
        try:
            sd.unlock(pwd)
        except PasswordError:
            print('That is not the password on this card.')
            return
    if not wiring_ok(sd):
        return
    if not yes('Remove the password from this card?'):
        return
    try:
        sd.clear(pwd)
    except PasswordError:
        print('The card refused. Either that is not its password, or it has '
              'no password.')
        return

    # Locking with the old password should now fail.
    try:
        sd.lock(pwd)
    except PasswordError:
        print('Done. The card has no password now.')
        if rec:
            remember(data, card, None, None)
        return
    sd.unlock(pwd)
    print('PROBLEM: the card still locks with that password, so it was not '
          'removed.')


def do_test(sd, card, data, st):
    rec = data['cards'].get(card['id'])
    print()
    print('  1) Unlock with a password (until power-off)')
    print('  2) Lock with a password')
    print('  3) Find which form of a password the card uses')
    a = ask('Choice (Enter to go back): ')
    if a == '1':
        if not st['locked']:
            print('The card is not locked.')
            return
        got = pick(data, rec)
        if got:
            try:
                sd.unlock(got[1])
                print('Unlocked. That is the password on this card.')
            except PasswordError:
                print('Wrong password.')
    elif a == '2':
        if st['locked']:
            print('The card is already locked.')
            return
        got = pick(data, rec)
        if got:
            try:
                sd.lock(got[1])
                print('Locked. That is the password on this card. Unlock it '
                      'or power off and on to carry on.')
            except PasswordError:
                print('The card would not lock: that is not its password, or '
                      'it has no password.')
    elif a == '3':
        if not st['locked']:
            print('The card needs to be locked for this. Power off and on '
                  'first.')
            return
        print('Type the password the way you know it, as text or 0x hex. '
              'Common variations will be tried.')
        text = ask('Password: ')
        if not text or parse(text) is None:
            return
        pwd = sdlock.try_all(sd, sdlock.forms(text))
        if pwd:
            remember(data, card, save_as(data, pwd), pwd)


def do_saved(data):
    print()
    print('Saved passwords:')
    names = sorted(data['passwords'])
    for name in names:
        print('  %-12s %s' % (name, show(unhexlify(data['passwords'][name]))))
    if not names:
        print('  none')
    print('Remembered cards:')
    ids = sorted(data['cards'])
    for i, cid in enumerate(ids):
        rec = data['cards'][cid]
        print('  %d) %-16s %s%s'
              % (i + 1, rec['label'],
                 'password ' + (rec.get('name') or rec['pwd'])
                 if rec.get('pwd') else 'no password',
                 ', set ' + rec['date'] if rec.get('date') else ''))
    if not ids:
        print('  none')
    print('  a) Add a password   d) Delete a password   f) Forget a card')
    a = ask('Choice (Enter to go back): ').lower()
    if a == 'a':
        pwd = parse(ask('Password (text or 0x hex): '))
        if pwd:
            print('  = ' + show(pwd))
            save_as(data, pwd)
    elif a == 'd':
        name = ask('Name to delete: ')
        if name in data['passwords'] and yes('Delete "%s"?' % name):
            del data['passwords'][name]
            save(data)
    elif a == 'f':
        n = ask('Card number to forget: ')
        if n.isdigit() and 1 <= int(n) <= len(ids):
            if yes('Forget %s?' % data['cards'][ids[int(n) - 1]]['label']):
                del data['cards'][ids[int(n) - 1]]
                save(data)


def do_wiring(sd, st):
    if not st['locked']:
        sd.soak()
        return
    # A locked card won't give up its data, but it will repeat its ID.
    print('The card is locked, so only a partial check is possible.')
    try:
        first = sd.cid()
        for _ in range(20):
            if sd.cid() != first:
                print('Wiring check FAILED: the card ID read differently.')
                return
    except SDError as e:
        print('Wiring check FAILED: %s' % e)
        return
    print('Card answered 20 times the same. For a full check, use a card '
          'without a password.')


# ---- main loop ----

def main():
    print('\nsdlock %s' % sdlock.VERSION)
    data = load()
    sd, card = connect()
    while True:
        if sd is None:
            print('\n  r) Try again   d) Diagnose   q) Quit to Python prompt')
            a = ask('> ').lower()
            if a == 'r':
                sd, card = connect()
            elif a == 'd':
                sdlock.SDLock(auto=False).probe()
                print('Every round should look the same. See '
                      'Troubleshooting in the README.')
            elif a == 'q':
                return
            continue

        try:
            st = header(sd, card, data)
            print()
            print('  1) Set password')
            print('  2) Clear password')
            print('  t) Test a password')
            print('  p) Saved passwords and cards')
            print('  w) Wiring check')
            print('  r) Re-read the card')
            print('  q) Quit to Python prompt')
            a = ask('> ').lower()
            if a == '1':
                do_set(sd, card, data, st)
            elif a == '2':
                do_clear(sd, card, data, st)
            elif a == 't':
                do_test(sd, card, data, st)
            elif a == 'p':
                do_saved(data)
            elif a == 'w':
                do_wiring(sd, st)
            elif a == 'r':
                sd, card = connect()
            elif a == 'q':
                return
        except SDError as e:
            print('Card error: %s' % e)
            print('Power off and on, then try again. If it keeps happening, '
                  'run the wiring check.')


main()
