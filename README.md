# sdlock-pico

Set and remove the password on an SD card, using a Raspberry Pi Pico and an
SD card module.

SD cards have a built-in password feature (CMD42 in the SD specification).
A card with a password locks itself every time it powers on, and can't be
read until it's unlocked. Some devices, like car head units, use this to tie
a card to the device.

Most computers can't do anything with a locked card, because they fail to
start it up. The Pico talks to the card in SPI mode, which works on locked
cards.

This has been tested on real hardware for one use case.

## AI generated

This code was written by AI (Claude Opus 5) and is provided as is. Review
it before use. You use it at your own risk, and the author takes no
responsibility or liability.

## Before you start

- **If you lose the password, the card is unusable.** This tool can't reset
  or erase a card without its password. Write the password down somewhere
  safe.
- **Try it on a spare card first.**
- **Unplug the Pico before you put in or take out a card.**
- **Saved passwords are stored as plain text on the Pico.** Anyone with the
  Pico can read them. Reflashing the Pico deletes them, so keep a copy
  elsewhere too.

## Parts

| Part | Approx. USD | Notes |
|---|---|---|
| Raspberry Pi Pico (any model) | $4–12 | Pico, Pico W or Pico 2. Get one with headers already soldered if you can. |
| Full-size SD card module | $3–10 | Must have a 3V3 power pin, not 5V only. |
| 6 female-to-female jumper wires | $2–4 | Short ones. Long wires are the most common cause of problems. |
| USB cable for the Pico | — | |
| A spare SD card | — | For testing. |

A micro SD card in an adapter works, but cheap adapters often cause
problems. If things are unreliable, try another adapter first.

## Wiring

| Module pin | Pico GPIO | Pico pin number |
|---|---|---|
| CS | GP17 | 22 |
| SCK | GP18 | 24 |
| MOSI | GP19 | 25 |
| MISO | GP16 | 21 |
| 3V3 | — | 36 (3V3 OUT) |
| GND | — | 38 |
| 5V | — | not connected |

Pin 36 is 3V3 OUT. Pin 37, right next to it, is 3V3_EN. If the power wire is
on 37 by mistake, the module gets no power, but the Pico itself still seems
to work normally.

## Install

1. Install MicroPython on the Pico from
   [micropython.org/download](https://micropython.org/download/). Pick the
   file for your board.
2. Copy `sdlock.py` and `main.py` to the Pico. `sdiag.py` is optional and
   only needed for wiring problems.
   - In [Thonny](https://thonny.org/): open each file, then
     File > Save as > Raspberry Pi Pico.
   - Or from a terminal, in this folder:
     `mpremote cp sdlock.py main.py sdiag.py :`
     (`pip install mpremote` if you don't have it.)

To update later, `git pull` and run the same `mpremote cp` command. It skips
files that haven't changed.

`main.py` runs every time the Pico starts.

## Using it

1. Unplug the Pico, put the card in, plug the Pico back in.
2. Connect to the Pico with `mpremote repl` or any serial terminal. Press
   Enter to show the menu. If you get a `>>>` prompt instead, press
   **Ctrl+D** to start it.

**Ctrl+D** restarts the menu at any point. **Ctrl+C** or **q** leaves it for
the Python prompt.

**In Thonny**, Ctrl+D restarts the Pico but doesn't start the menu, because
of the way Thonny connects. Open `main.py` from the Pico and click **Run**
instead.

It's safe to unplug the Pico whenever the menu is showing. Every change is
finished before the menu comes back. If you're using mpremote, exit it with
**Ctrl+]** first, or it prints a harmless error.

```
sdlock 1.1.0

Card:    SL08G 12345678  7580 MB SDHC/SDXC
Status:  unlocked
Saved:   not a remembered card

  1) Set password
  2) Clear password
  t) Test a password
  p) Saved passwords and cards
  w) Wiring check
  r) Re-read the card
  q) Quit to Python prompt

Safe to unplug whenever this menu is showing.
```

To use a different card, unplug the Pico, swap the card, and plug it back
in.

### Set a password

Choose **1**. Then:

1. Pick a saved password, or type a new one. A new password is typed twice,
   and you can save it under a name for next time.
2. The tool checks the wiring by reading the card several times. If any read
   differs, it stops without writing anything.
3. Confirm, and the password is set.
4. The tool checks the card stored it correctly by locking and unlocking it
   with the password.
5. You can choose to remember the card. Next time, the menu recognises it and
   uses its password automatically.

The card stays unlocked until it's powered off, then locks itself every time
it powers on.

If the card already has a password, choose it when asked and the tool
changes it to the new one.

### Clear a password

Choose **2**. If the card is remembered, press Enter to use its saved
password. Otherwise pick a saved one or type it. The tool checks the wiring,
asks you to confirm, removes the password, then checks the card no longer
locks.

### Test a password

Choose **t**:

- **Unlock** checks a password on a locked card. The card stays unlocked
  until it's powered off. The stored password doesn't change. If it works,
  you can remember the card, which is how you add a card that already has
  a password.
- **Lock** checks a password on an unlocked card that has one.
- **Find which form** is for when you know the password, but not exactly how
  the original device sent it. See below.

### Saved passwords and cards

Choose **p** to see, add or delete saved passwords, and to forget remembered
cards. They're stored in `sdlock.json` on the Pico.

## Typing passwords

A password is up to 16 bytes. Type it either as:

- text, like `hunter2`, or
- hex starting with `0x`, like `0x0123456789abcdef`.

The tool always shows exactly what it will send, for example
`8 bytes  0123456789abcdef`.

The length is part of the password. `0x01020304` (4 bytes) and the same bytes
padded with zeros to 16 bytes are two different passwords to the card. This
matters if you're matching a password another device set.

### Finding the right form

If a device shows you its password but the card won't unlock with it, the
device may pad it, reverse it, or send hex digits as text. Choose **t**, then
**3**, and type the password the way you know it. The tool tries the common
variations:

- as typed
- padded with zeros to 8 and to 16 bytes
- padded with `0xFF` to 16 bytes
- bytes reversed
- hex digits sent as text, or text read as hex

It only uses unlock, which can't change the stored password. The SD
specification has no limit on wrong attempts.

## Troubleshooting

Work down the list.

**The menu says "Could not start the card".** Choose **d** (Diagnose). It
sends a few commands several times and shows the card's replies:

- Every round the same, starting `CMD0=01`: the wiring is fine.
- `--` everywhere: nothing is getting through. Check the power wire is on pin
  36, not 37. Measure 3.2–3.4 V between the module's 3V3 and GND pins. Check
  each wire. Then use `sdiag.py` (below).
- `--` in different places each round: a loose connection or weak power
  supply.

**The wiring check fails, or things work only sometimes.** Use shorter
wires, keep SCK away from MISO, and push the card fully into the socket. Try
a different micro SD adapter. Adding a 100 nF and a 10 µF capacitor across
the module's 3V3 and GND can help.

**"Warning: the card refused data CRC checking".** The card won't check
data for errors, so a fault in the wiring could set a different password
than the one you typed. The menu won't set or clear passwords on this card.

**"That is not the password on this card".** Check the length first, since
that's the most common mistake. Then try **t** > **3** to find the right
form.

**The card locks, but the original device still rejects it.** The device
sends a different password than the one you set. You need its exact bytes
and length. A password shown on a screen may be shortened or reformatted.

### sdiag.py

Wiring checks with a multimeter. It sends no commands to the card, so it
can't affect the password. Leave the menu first (**q** or Ctrl+C), then:

```python
>>> import sdiag
>>> sdiag.miso_line()   # is anything connected to MISO?
>>> sdiag.hold(1)       # CS, SCK, MOSI held high: expect ~3.3 V
>>> sdiag.hold(0)       # held low: expect ~0 V
>>> sdiag.clock()       # SCK and MOSI clocking: expect about 1.6 V
>>> sdiag.drive_low()   # MOSI driven low: expect MOSI ~0 V, MISO ~3.3 V
```

Measure at the module's pins, not the Pico's, so you're testing the wires
too. If `drive_low()` shows MOSI high and MISO low, those two wires are
swapped.

## Using it from Python

Leave the menu (**q** or Ctrl+C) to get the Python prompt.

```python
>>> import sdlock
>>> sd = sdlock.SDLock()
```

| Call | What it does |
|---|---|
| `SDLock(spi_id=0, sck=18, mosi=19, miso=16, cs=17, baudrate=400000, auto=True, verbose=True)` | Start the card. `auto=False` skips starting it, for `probe()`. |
| `sd.status()` | Show whether the card is locked. |
| `sd.soak()` | Wiring check. Needs an unlocked card. |
| `sd.set(pwd)` | Set a password. The card locks at next power-on. |
| `sd.set(old, new)` | Change a password. |
| `sd.verify(pwd)` | Lock then unlock with `pwd`. Returns `False` if the card holds a different password. |
| `sd.clear(pwd)` | Remove the password. |
| `sd.unlock(pwd)` | Unlock until power-off. |
| `sd.lock(pwd)` | Lock now. Only works with the stored password. |
| `sd.setlock(pwd)` | Set a password and lock in one step. |
| `sd.cid()`, `sd.capacity()` | Card ID and size. Work on a locked card. |
| `sd.readblock(lba)` | Read one 512-byte block. Fails on a locked card. |
| `sd.probe()` | Diagnose: show raw replies to a few commands. |
| `sdlock.try_all(sd, sdlock.forms(pwd))` | Find which form of a password unlocks the card. |

A wrong password raises `sdlock.PasswordError`. Other card problems raise
`sdlock.SDError`.

## How it works

CMD42 sends a 512-byte block to the card:

| Byte | Contents |
|---|---|
| 0 | mode |
| 1 | password length |
| 2… | password |
| rest | zeros |

| Mode bit | Meaning |
|---|---|
| `0x01` | set password |
| `0x02` | clear password |
| `0x04` | lock (1) or unlock (0) |
| `0x08` | force erase. Not implemented, because it wipes the card. |

- Mode `0x00` with the right password unlocks the card until power-off,
  without changing the password.
- To change a password, the old and new passwords are sent together, with the
  length covering both.
- A locked card still answers status (CMD13), card ID (CMD10) and size
  (CMD9), so the menu can show these before unlocking.
- In SPI mode, cards don't check data for errors unless asked. The tool turns
  checking on (CMD59) and sends a CRC with each block, so a block damaged by
  bad wiring is rejected instead of setting the wrong password.

## Tests

The tests run on a computer, no Pico needed. They use an emulated SD card:

```
python3 tests/test_all.py
```

## Acknowledgements

The CMD42 details come from the SD Association's *Physical Layer Simplified
Specification*, which is free to download.
