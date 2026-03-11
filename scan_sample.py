import serial
import time
import requests
import cv2
import numpy as np
from os.path import expanduser
import os
import shutil
import argparse
import json
import sys
import termios
import tty
from pathlib import Path

os.environ['BLINKA_FT232H'] = '1'
import board
import digitalio


def positive_int(value):
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"Expected integer ≥ 1, got {value}")
    return ivalue

def kv_dict(s):
    try:
        key, value = s.split("=")
        return key, int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Expected key=value (e.g. top=2)"
        )

def get_or_create_calibration_scan(
    path: str,
    capture_fn,
    average_stack: int = 5,
    force_new: bool = False,
    label: str = "calibration",
):
    """
    Generic handler for dark or flat frame capture.

    Saves:
        - raw data as .npy
        - a viewable image as .png

    Returns
    -------
    image : np.ndarray
    """

    png_path = os.path.splitext(path)[0] + ".png"

    if os.path.exists(path) and not force_new:
        print(f"Using existing {label} scan: {path}")
        return np.load(path)

    print(f"Capturing new {label} scan...")
    image = capture_fn(average_stack)

    # Save numpy
    np.save(path, image)

    # Save PNG for visual inspection
    png_img = image

    # Ensure dtype compatible with PNG
    if png_img.dtype not in (np.uint8, np.uint16):
        img = png_img.astype(np.float32)
        lo, hi = np.percentile(img, (0.5, 99.5))
        if hi > lo:
            img = (img - lo) / (hi - lo)
        img = np.clip(img, 0, 1)
        png_img = (img * 65535).astype(np.uint16)

    cv2.imwrite(png_path, png_img)

    print(f"Saved new {label} scan to:")
    print(f"  NPY: {path}")
    print(f"  PNG: {png_path}")

    return image

def send(ser, cmd: str):
    ser.write((cmd + "\r").encode("ascii"))
    ser.flush()

def query(ser, cmd: str) -> str:
    """Send a command and read response."""
    send(ser, cmd)
    # Some units echo; some end with \r\n; be tolerant:
    line = ser.readline().decode(errors="ignore").strip()
    return line

def is_moving(ser) -> bool:
    # Ask the drive to print a status var; you may need to adapt parsing
    resp = query(ser, f"PR {MOTION_STATUS_VAR}")
    # Try to extract a number from the response (e.g. "MV = 1" or "1")
    digits = "".join(ch for ch in resp if ch.isdigit() or ch == "-")
    try:
        val = int(digits)
        return val != 0
    except ValueError:
        # If parsing fails, fall back to short wait + assume still moving
        print('is_moving parse failed')
        return True

def wait_until_stopped(ser, dn_char='A', timeout_s=10.0):
    t0 = time.time()
    buffer = ""

    while True:
        if time.time() - t0 > timeout_s:
            raise TimeoutError("Timed out waiting for DN (move complete)")

        if ser.in_waiting:
            data = ser.read(ser.in_waiting).decode(errors="ignore")
            buffer += data

            # print(repr(data))

            if dn_char in buffer:
                return

def take_photo(images_averaged=3):
    try:
        camera_url = f"{CAMERA_URL}/capture?stack={images_averaged}"
        response = requests.get(camera_url, timeout=10)
        response.raise_for_status()

        img_array = np.frombuffer(response.content, dtype=np.uint8)
        image = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)

        if image is None:
            raise ValueError("Failed to decode image")

        return image

    except (requests.RequestException, ValueError) as e:
        print(f"[Error] Image fetch failed: {e}")
        return None


def wait_for_space_or_abort(msg=''):
    """
    SPACE → continue
    q     → abort (raises KeyboardInterrupt)
    """

    print(f"{msg} - press SPACE to continue, or q to abort...")

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)

            if ch == " ":
                print()
                return True

            if ch.lower() == "q":
                print("\nAbort requested.")
                raise KeyboardInterrupt

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def set_lamp_on():
    p = digitalio.DigitalInOut(board.C0)
    p.direction = digitalio.Direction.OUTPUT
    p.value = True

def set_lamp_off():
    p = digitalio.DigitalInOut(board.C0)
    p.direction = digitalio.Direction.OUTPUT
    p.value = False




##########################################################

parser = argparse.ArgumentParser()
parser.add_argument('--run_name', type=str, help='Name of this run.', required=True)
parser.add_argument('--degree_increments', type=positive_int, default=10, help='Degree increments.', required=False)
parser.add_argument('--oct_stack', type=positive_int, default=3, help='Number of averaged images per step.', required=False)
parser.add_argument('--display_overlay', action='store_true', help='Show an overlay on the image for debugging.', required=False)
parser.add_argument("--new_dark", action="store_true", help="Force capture of new dark frame")
parser.add_argument("--dark_stack", type=int, default=5, help="Number of frames to average for dark")
parser.add_argument("--new_flat", action="store_true", help="Force capture of new flat frame")
parser.add_argument("--flat_stack", type=int, default=5, help="Number of frames to average for flat")
args = parser.parse_args()


SERIAL_PORT = "/dev/ttyUSB0"
BAUDRATE = 9600
TIMEOUT = 1

FULL_STEPS_PER_REV = 200
MICROSTEPS = 256                                    # MS
STEPS_PER_REV = FULL_STEPS_PER_REV * MICROSTEPS
INITIAL_VELOCITY = 50                               # VI (steps per second)
MAX_VELOCITY = 768000                               # VM
ACCELERATION = 10000                                # A
DECELERATION = 10000                                # D
RUN_CURRENT = 100                                   # RC (percent)
DEVICE_NAME = 'A'

DEGREE_INCREMENT = args.degree_increments
NUM_POSITIONS = int(360 / DEGREE_INCREMENT)

MOTION_STATUS_VAR = "MV"

# Address of the Pi camera server
CAMERA_URL = "http://192.168.7.2:8000"

# dimensions used for calibration
CALIBRATED_WIDTH = 2028
CALIBRATED_HEIGHT = 1520

# directories
BASE_DIR = f"{expanduser('~')}/OCT"
SCAN_DIR = f"{BASE_DIR}/scans/{args.scan_name}"
IMAGE_DIR = f"{SCAN_DIR}/images"
if os.path.exists(IMAGE_DIR):
    shutil.rmtree(IMAGE_DIR)
os.makedirs(IMAGE_DIR)

CONFIG_DIR = f"{BASE_DIR}/config"
if not os.path.exists(CONFIG_DIR):
    os.makedirs(CONFIG_DIR)

CALIBRATION_JSON_PATH = f"{Path(__file__).resolve().parent}/camera_calibration_charuco.json"


def run_scan():
    try:
        # -----------------------------
        # Load calibration
        # -----------------------------
        with open(CALIBRATION_JSON_PATH, "r") as f:
            calib = json.load(f)

        K = np.array(calib["K"], dtype=np.float64)
        dist = np.array(calib["dist"], dtype=np.float64)

        width = calib["image_size"]["width"]
        height = calib["image_size"]["height"]
        size = (width, height)

        print("Loaded calibration:")
        print("  Image size:", size)
        print("  K:\n", K)
        print("  dist:", dist)

        # -----------------------------
        # Prepare undistortion maps
        # -----------------------------
        # IMPORTANT for CT: keep geometry unchanged
        newK = K.copy()

        map1, map2 = cv2.initUndistortRectifyMap(
            cameraMatrix=K,
            distCoeffs=dist,
            R=None,
            newCameraMatrix=newK,
            size=size,
            m1type=cv2.CV_32FC1
        )

        
        # dark - LED off
        wait_for_space_or_abort(msg='Capturing the dark calibration scan (LED off)')
        set_lamp_off()
        dark = get_or_create_calibration_scan(
            path=f"{CONFIG_DIR}/dark.npy",
            capture_fn=take_photo,
            average_stack=5,
            force_new=args.new_dark,
            label="dark",
        )

        # flat - LED on, no sample
        wait_for_space_or_abort(msg='Capturing the flat calibration scan (LED on, no sample)')
        set_lamp_on()
        flat = get_or_create_calibration_scan(
            path=f"{CONFIG_DIR}/flat.npy",
            capture_fn=take_photo,
            average_stack=5,
            force_new=args.new_flat,
            label="flat",
        )

        wait_for_space_or_abort(msg='Scanning the sample (LED on, with sample)')

        with serial.Serial(SERIAL_PORT, BAUDRATE, timeout=TIMEOUT) as ser:
            time.sleep(0.5)

            # Set up initial stepper parameters
            send(ser, f"EE=0")
            send(ser, f"DN={DEVICE_NAME}")
            send(ser, f"MS={MICROSTEPS}")
            send(ser, f"VI={INITIAL_VELOCITY}")
            send(ser, f"VM={MAX_VELOCITY}")
            send(ser, f"A={ACCELERATION}")
            send(ser, f"D={DECELERATION}")
            send(ser, f"RC={RUN_CURRENT}")
            send(ser, f"P=0")

            # turn lamp on
            set_lamp_on()

            # --- Scan ---
            for i in range(NUM_POSITIONS):
                angle = i * DEGREE_INCREMENT
                target_steps = round((angle / 360.0) * STEPS_PER_REV)
                print(f'position {i}, angle {angle}, target steps {target_steps}')

                # Move Absolute (MA)
                send(ser, f"MA {target_steps},1")

                # Wait for motion to finish
                wait_until_stopped(ser, dn_char=DEVICE_NAME)

                # Settle time for vibration/rig flex
                time.sleep(1.0)

                img = take_photo(images_averaged=args.oct_stack)

                h, w = img.shape[:2]
                assert h == height and w == width

                # apply the calibration parameters
                undistorted = cv2.remap(
                    img,
                    map1,
                    map2,
                    interpolation=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT
                )

                # write the image
                filename = f"img_{i:02d}_{angle:03d}deg.png"
                cv2.imwrite(f"{IMAGE_DIR}/{filename}", undistorted)

            # turn lamp off
            set_lamp_off()

            # Return to 0°
            send(ser, "MA 0")
            wait_until_stopped(ser, dn_char=DEVICE_NAME)

    except KeyboardInterrupt:
        print("\nKeyboard interrupt detected. Stopping scan safely...")

    finally:
        # turn lamp off
        set_lamp_off()


if __name__ == "__main__":
    run_scan()
