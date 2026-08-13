"""
sdiag.py - pin-level checks for the Pico to SD module link, for MicroPython.

Sends no SD commands, so it cannot affect a card's lock state. Use it when
sdlock.SDLock() will not initialise and sd.probe() shows nothing but '--'.

You need a multimeter for hold() and clock(). Measure at the MODULE pads
rather than the Pico pins, so each reading tests the lead as well as the pin.

    >>> import sdiag
    >>> sdiag.miso_line()      # is anything on the MISO line?
    >>> sdiag.hold(1)          # park CS/SCK/MOSI high, then meter the pads
    >>> sdiag.hold(0)          # park them low
    >>> sdiag.clock()          # continuous clock, meter SCK and MOSI
    >>> sdiag.drive_low()      # MOSI driven low: distinguishes 0 V from float
"""

from machine import Pin, SPI
import time

CS, SCK, MOSI, MISO = 17, 18, 19, 16


def miso_line(miso=MISO):
    """Decide whether anything out there is holding MISO high."""
    p = Pin(miso, Pin.IN, Pin.PULL_DOWN)
    time.sleep_ms(20)
    down = p.value()
    p = Pin(miso, Pin.IN, Pin.PULL_UP)
    time.sleep_ms(20)
    up = p.value()
    print('MISO with internal pull-down: %d' % down)
    print('MISO with internal pull-up:   %d' % up)
    if down == 0 and up == 1:
        print('-> floating: no external pull-up, nothing driving it.')
        print('   Try a 10k from the module 3V3 pad to the MISO pad.')
    elif down == 1:
        print('-> held high by an external pull-up. Line looks connected.')
    else:
        print('-> held LOW: short to GND, or a card pulling it down.')
    return {'pulldown': down, 'pullup': up}


def hold(level=1, secs=20):
    """Park CS, SCK and MOSI at a DC level so you can meter the module pads."""
    outs = [Pin(n, Pin.OUT, value=level) for n in (CS, SCK, MOSI)]
    print('CS, SCK, MOSI held %s for %d s - meter the MODULE pads now.'
          % ('HIGH, expect ~3.3 V' if level else 'LOW, expect ~0 V', secs))
    time.sleep(secs)
    for p in outs:
        p.init(Pin.IN)
    print('released')


def clock(secs=20, spi_id=0, baudrate=400000):
    """Clock 0xAA continuously. SCK and MOSI should both meter about half of
    3.3 V, because they are squarewaves and a DC meter averages them."""
    spi = SPI(spi_id, baudrate=baudrate, polarity=0, phase=0,
              sck=Pin(SCK), mosi=Pin(MOSI), miso=Pin(MISO))
    Pin(CS, Pin.OUT, value=1)
    print('clocking for %d s - a pad reading 0 V or a flat 3.3 V is a lead '
          'that is not carrying the signal.' % secs)
    end = time.ticks_add(time.ticks_ms(), secs * 1000)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        spi.read(256, 0xAA)
    print('done')


def drive_low(secs=20, spi_id=0, baudrate=400000):
    """Clock all-zero bytes, so MOSI is actively driven low.

    This is the test that separates a driven line from a floating one: a
    floating pulled-up line cannot read 0 V. Meter MOSI and MISO. Expect
    MOSI near 0 V and MISO near 3.3 V. The reverse means those two leads
    are transposed.
    """
    spi = SPI(spi_id, baudrate=baudrate, polarity=0, phase=0,
              sck=Pin(SCK), mosi=Pin(MOSI), miso=Pin(MISO))
    Pin(CS, Pin.OUT, value=1)
    print('driving MOSI low for %d s - meter the MOSI and MISO pads.' % secs)
    end = time.ticks_add(time.ticks_ms(), secs * 1000)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        spi.read(256, 0x00)
    print('done')
