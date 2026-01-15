import serial
import time
import math
import subprocess

SERIAL_PORT = "/dev/ttyUSB0"
BAUDRATE = 9600
TIMEOUT = 1

FULL_STEPS_PER_REV = 200
MICROSTEPS = 16
STEPS_PER_REV = FULL_STEPS_PER_REV * MICROSTEPS

DEGREE_INCREMENT = 10
NUM_POSITIONS = int(360 / DEGREE_INCREMENT)

MOTION_STATUS_VAR = "MV"  # <-- if your unit uses a different one, change this

def send(ser, cmd: str):
    ser.write((cmd + "\r").encode("ascii"))
    ser.flush()

def query(ser, cmd: str) -> str:
    """Send a command and read one line back (best-effort)."""
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
        return True

def wait_until_stopped(ser, poll_s=0.05, timeout_s=10.0):
    t0 = time.time()
    while True:
        if not is_moving(ser):
            return
        if time.time() - t0 > timeout_s:
            raise TimeoutError("Motor did not stop within timeout")
        time.sleep(poll_s)

def take_photo(index: int, angle_deg: int):
    filename = f"img_{index:02d}_{angle_deg:03d}deg.jpg"
    # subprocess.run(["libcamera-still", "-n", "-o", filename], check=False)
    # print(f"Captured {filename}")

with serial.Serial(SERIAL_PORT, BAUDRATE, timeout=TIMEOUT) as ser:
    time.sleep(0.5)

    # Microstep resolution
    send(ser, f"MS {MICROSTEPS}")

    # Print current position
    print("Current position:", query(ser, "PR P"))

    # --- Scan ---
    for i in range(NUM_POSITIONS):
        angle = i * DEGREE_INCREMENT
        target_steps = round((angle / 360.0) * STEPS_PER_REV)

        # Move Absolute (MA)
        send(ser, f"MA {target_steps}")

        # Wait for motion to finish (poll)
        wait_until_stopped(ser, poll_s=0.05, timeout_s=10.0)

        # Settle time for vibration/rig flex
        time.sleep(0.2)

        take_photo(i, angle)

    # Return to 0°
    send(ser, "MA 0")
    wait_until_stopped(ser)
