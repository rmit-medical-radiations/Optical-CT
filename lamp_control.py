import os
os.environ['BLINKA_FT232H'] = '1'
import board
import digitalio
import argparse

def on_off(value: str) -> str:
    v = value.lower()
    if v not in ("on", "off"):
        raise argparse.ArgumentTypeError(
            "Value must be 'on' or 'off' (case-insensitive)"
        )
    return v

parser = argparse.ArgumentParser()
parser.add_argument(
    "--power",
    type=on_off,
    choices=("on", "off"),
    default="off",
    help="Turn power on or off"
)

args = parser.parse_args()

p = digitalio.DigitalInOut(board.C0)
p.direction = digitalio.Direction.OUTPUT

if args.power == 'on':
    p.value = True
else:
    p.value = False
