import os
os.environ['BLINKA_FT232H'] = '1'
import board
import digitalio
import time

p = digitalio.DigitalInOut(board.C0)
p.direction = digitalio.Direction.OUTPUT

p.value = False
duration = 60  # total time in seconds
interval = 10  # toggle interval in seconds

try:
    for i in range(0, duration, interval):
        p.value = not p.value
        print(f"Time: {i:>2}s | Value: {p.value}")
        time.sleep(interval)
finally:
    p.value = False
