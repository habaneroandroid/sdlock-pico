# sdlock-pico

Set, test, change and remove the **CMD42 password** on an SD card, using a
Raspberry Pi Pico as the host.

This code has been tested in real life successfully in a single use case.

SD cards have a password lock feature defined in the SD specification: a
16-byte password in the card's `PWD` register, with its length in `PWD_LEN`.
Once a password is set, the card **locks itself automatically at every
power-up** and refuses all data access until the host sends the correct
password. Some embedded devices — car head units, industrial controllers,
medical equipment — rely on this to bind a card to a device.

Almost no desktop tooling can touch these cards. Linux's SD stack reads the
card's `SCR` register during initialisation, which a locked card will not
allow, so the card typically fails to enumerate at all. This project works
around that by driving the card in **SPI mode**, where initialisation never
touches `SCR`. A locked card comes up fine and will answer CMD42.

## AI Generated

This codebase is entirely AI-generated (opus 5) and provided as is.
Review it thoroughly before use, as you use it completely at your own risk and the author assumes no responsibility or liability.

## Read this before you start

**A password you do not have is a card you cannot use.** There is no
recovery path in this tool. The only specified way out is CMD42 force-erase
(mode bit `0x08`), which wipes the card, may fail on a card with permanent
write protect set, and is **deliberately not implemented here**. Write your
password down somewhere outside your terminal history before you set it.

**Test with a card you can afford to lose.** The password travels
to the card inside a 512-byte data block. On a marginal SPI link a flipped
bit sets a password that nobody knows. Prove the link with `sdread.soak()`
first.

**Never insert or remove a card while the board is powered.** Power down,
swap, power up. Every time.

**Length is part of the password.** Four bytes, and those same four bytes
zero-padded to sixteen, are two different passwords. If you are reproducing
a password some other host set, you need its exact bytes *and* its exact
length.

## Bill of materials

| Item | Approx. USD | Notes |
|---|---|---|
| Raspberry Pi Pico (any variant) | $4–12 | Pico, Pico W, Pico 2. Pre-soldered headers save effort. |
| Full-size SD card breakout module | $3–10 | Must have a **3V3** supply input, not 5V-only. |
| Female-to-female jumper leads ×6 | $2–4 | Short ones. Long bundles are the most common cause of flaky links. |
| Micro-USB or USB-C cable | — | Whatever your Pico takes. |
| A scrap SD card | — | For testing. Do not initially use the card you care about. |

A micro-SD card in a full-size adapter works, but a cheap adapter with
missing or badly-made contacts is a real source of trouble. If you get
intermittent behaviour, try a different adapter before anything else.

## Wiring

Defaults are SPI0. Pass different pins to `SDLock()` if you need to.

| Module pad | Pico GPIO | Pico physical pin |
|---|---|---|
| CS | GP17 | 22 |
| SCK | GP18 | 24 |
| MOSI | GP19 | 25 |
| MISO | GP16 | 21 |
| 3V3 | — | **36** (3V3 OUT) |
| GND | — | 38 |
| 5V | — | leave unconnected |

Notes that save time:

- **Physical pin 36 is `3V3 OUT`; pin 37 next to it is `3V3_EN`.** A supply
  lead one position out gives you a completely dead module while the Pico's
  onboard LED still works perfectly.

## Install

1. Flash MicroPython to the Pico from
   [micropython.org/download](https://micropython.org/download/) — pick the
   build matching your board.
2. Install Thonny or equivalent
3. Copy `sdlock.py`, `sdread.py`, `sdiag.py` and `sdtry.py` to the Pico's
   filesystem.

```python
>>> import sdlock
>>> sdlock.VERSION
'1.0.0'
```

If you edit a module, restart the interpreter (**Ctrl+D** in Thonny, or
Run → Stop/Restart backend) before re-importing. `import` returns the cached
copy otherwise, and you will debug code that is no longer on disk.

## Full process

### 1. Power down, insert the card, power up

In that order, always.

### 2. Initialise

```python
>>> import sdlock, sdread
>>> sd = sdlock.SDLock()
card initialised (SDHC/SDXC, data CRC on)
R1=0x00 R2=0x00  locked=no
```

`locked=YES` on a card you expected to be free means it already has a
password. `data CRC OFF` with a CMD59 warning means the card refused CRC
checking; you can continue, but you lose the protection against a corrupted
password write.

If this occurs, go to [Troubleshooting](#troubleshooting) and do not proceed.

### 3. Prove the link

```python
>>> sdread.soak(sd)
block 0        12 identical reads   first 16: 00000000...   [MBR signature]
...
soak passed
```

Every block must read identically every time. If any block is inconsistent,
**stop** — fix the hardware before sending CMD42.

### 4. Set the password

```python
>>> sd.set('0x0123456789abcdef')
R1=0x00 R2=0x00  locked=no
```

`locked=no` is correct. `set` stores the password without locking; the card
locks itself at the next power-up.

To lock immediately instead, use `sd.setlock(...)`. The two-step route is
preferred because the power-cycle in the next step verifies the write.

### 5. Power-cycle and confirm auto-lock

Unplug USB, leave the card in the socket, wait a few seconds, plug back in.

```python
>>> import sdlock, sdread
>>> sd = sdlock.SDLock()
R1=0x00 R2=0x01  locked=YES
```

Then confirm the lock actually blocks data access — an exception here is the
pass condition:

```python
>>> sdread.readblock(sd, 0)
RuntimeError: CMD17 rejected, R1=0x04 (0x04 = card is locked)
```

### 6. Confirm the password and that the data survived

```python
>>> sd.unlock('0x0123456789abcdef')
R1=0x00 R2=0x00  locked=no
>>> sdread.check(sd, 0, 8)
```

No `LOCK_UNLOCK_FAILED` proves the card holds exactly the bytes you think it
does. The successful read proves a lock cycle left the data untouched.

The card is now unlocked **until the next power cycle only**. The stored
password is unchanged.

### 7. Remove the password

```python
>>> sd.clear('0x0123456789abcdef')
```

Power-cycle and confirm `locked=no`. The card is an ordinary SD card again.

## Tools

### `sdlock.py`

The CMD42 host. Everything else is optional.

| Call | Effect |
|---|---|
| `SDLock(spi_id=0, sck=18, mosi=19, miso=16, cs=17, baudrate=400000, auto=True)` | Construct and initialise. `auto=False` skips init so you can call `probe()` on a card that will not come up. |
| `sd.init(crc=True)` | Re-initialise. `crc=False` skips CMD59. |
| `sd.status()` | CMD13. Prints and returns `{'r1','r2','locked','failed'}`. |
| `sd.set(pwd)` | Assign a password. Card locks at next power-up. |
| `sd.set(old, new)` | Change an existing password. |
| `sd.setlock(pwd)` | Assign and lock in one command. |
| `sd.lock(pwd)` | Lock now, using the stored password. |
| `sd.unlock(pwd)` | Unlock until next power cycle. Doesn't alter the stored password. |
| `sd.clear(pwd)` | Remove the password permanently. |
| `sd.probe(rounds=6)` | Diagnostic: raw R1 from a command sequence, repeated. |

Passwords accept `bytes`, a `'0x...'` hex string, or plain text. Max 16 bytes.

### `sdread.py`

Read-only. Never writes.

| Call | Effect |
|---|---|
| `readblock(sd, lba=0)` | CMD17 single block. Raises on a locked card. |
| `check(sd, lba=0, repeat=8)` | Read one block repeatedly; raise if reads differ. |
| `soak(sd, lbas=..., repeat=12)` | `check()` across several widely spaced blocks. |

### `sdiag.py`

Pin-level hardware checks. Sends no SD commands at all, so it cannot affect a
card's lock state. Needs a multimeter for all but `miso_line()`. **Measure at
the module pads, not the Pico pins**, so each reading tests the lead too.

| Call | Effect |
|---|---|
| `miso_line()` | Whether anything holds MISO high, via internal pull-up/pull-down. |
| `hold(level, secs=20)` | Park CS/SCK/MOSI at a DC level. Expect ~3.3 V or ~0 V. |
| `clock(secs=20)` | Clock 0xAA. SCK and MOSI should meter about half of 3.3 V. |
| `drive_low(secs=20)` | Drive MOSI actively low. Expect MOSI ~0 V, MISO ~3.3 V. |

`drive_low()` is the decisive one: a floating pulled-up line cannot read 0 V,
so it separates a driven line from a disconnected one — which `hold()` alone
cannot do. MOSI high and MISO low means those two leads are transposed.

### `sdtry.py`

For when you know a card's password as text or as a displayed value, but not
the exact bytes and length the original host used. Uses temporary `unlock`
only, so it cannot alter a stored password.

```python
>>> import sdlock, sdtry
>>> sd = sdlock.SDLock()                                 # locked=YES
>>> sdtry.try_all(sd, sdtry.forms('0x0123456789abcdef'))
```

`forms(pwd)` generates the plausible encodings — as given, zero-padded to 8
and 16, `0xFF`-padded, byte-reversed, and hex-digits-as-ASCII — deduplicated
and likeliest first. Pass your own `[(name, bytes), ...]` list instead if you
have better ideas.

The SD specification defines no failed-attempt counter, so wrong guesses
should not harm a card.

## Troubleshooting

Work top to bottom. Each step assumes the ones above passed.

**`CMD0 failed` or `no response to CMDn`, consistently.** The card is not
answering at all. Build without initialising and look at the pattern:

```python
>>> sd = sdlock.SDLock(auto=False)
>>> sd.probe()
```

- All rounds identical and sensible (`CMD0=01`) → link is fine, problem is
  elsewhere.
- All `--` on every round → nothing is reaching or returning from the card.
- `--` appearing in different places on different rounds → marginal link or
  supply, not miswiring.

**All `--`.** In order: check the supply lead is on physical pin 36 and not
37; meter the module's 3V3 pad against its GND pad, expecting 3.2–3.4 V;
check continuity of all six leads end to end, wiggling each; then run
`sdiag.drive_low()` and `sdiag.miso_line()`. Remove the card and re-run
`probe()` — an identical result with and without a card tells you the card is
not participating, which narrows it to power, seating or leads.

**Intermittent.** Suspect the leads and the card socket before the code.
Shorten leads, keep SCK away from MISO, reseat the card firmly (these sockets
are friction-fit and easy to leave a millimetre short), try a different
micro-SD adapter, add 100 nF + 10 µF across the module's 3V3 and GND. Try
`SDLock(baudrate=100000)` — if a lower clock fixes it, it is signal
integrity.

**Reads inconsistent in `soak()`.** Same causes as intermittent. Do not send
CMD42 until this is clean.

**`LOCK_UNLOCK_FAILED`.** The card compared your password against the stored
one and they differ. On `set`, it means the card already has a password. On
`unlock` or `clear`, it means wrong bytes, wrong length, or wrong encoding.
Check the length first — it is the most common mistake. `sdtry.py` exists for
exactly this.

**Card locks but the original device still rejects it.** The stored password
does not match what that device sends. Devices differ in the length they use
and in whether they pad. You need the device's exact bytes; a value displayed
on a screen may be truncated or reformatted.

**A module edit seems to have no effect.** Stale cached import. Ctrl+D and
re-import.

## How it works

CMD42 (`LOCK_UNLOCK`) is an ordinary single-block write. The 512-byte data
block is:

| Offset | Contents |
|---|---|
| 0 | mode byte |
| 1 | `PWDS_LEN`, password data length |
| 2… | password data |
| … | zero padding to 512 bytes |

Mode bits:

| Bit | Value | Meaning |
|---|---|---|
| 0 | `0x01` | `SET_PWD` — store the password data as the new password |
| 1 | `0x02` | `CLR_PWD` — clear the password, given the current one |
| 2 | `0x04` | `LOCK_UNLOCK` — 1 locks the card, 0 unlocks it |
| 3 | `0x08` | `ERASE` — force erase. **Not implemented.** |

Behaviour worth knowing:

- Mode `0x00` with the correct password unlocks the card until the next power
  cycle. The stored password is untouched, which makes it the safe way to
  test a candidate.
- To change a password, `SET_PWD` carries old and new concatenated, with
  `PWDS_LEN` covering both.
- A card with `PWD_LEN != 0` locks itself at every power-up. This is why
  `set` followed by a power cycle is equivalent to `setlock`, and why moving
  a card between devices re-locks it.
- While locked, a card answers only the basic command class plus `ACMD41`,
  `CMD16` and the lock-card class. `CMD13` still works, which is how
  `status()` reports lock state on a locked card.

SPI mode disables CRC checking by default, which means a card will accept a
corrupted lock/unlock block without complaint and set a password nobody
knows. `init()` therefore enables checking with CMD59 and `_cmd42` sends a
real CRC16, so a bad block is rejected instead.

## Acknowledgements

The CMD42 data structure and mode bits are from the SD Association's
*Physical Layer Simplified Specification*, which is freely available.
