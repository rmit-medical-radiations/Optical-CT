import requests
import cv2
from os.path import expanduser
import os
import shutil
import time
import sys
import termios
import tty
import select

# Address of the Pi camera server
CAMERA_URL = "http://192.168.7.2:8000/capture"

IMAGE_DIR = f"{expanduser('~')}/Downloads/calibration_images"
if os.path.exists(IMAGE_DIR):
    shutil.rmtree(IMAGE_DIR)
os.makedirs(IMAGE_DIR)


def crop_borders(
    image,
    top=0,
    bottom=0,
    left=0,
    right=0
):
    """
    Crop pixels from each border with safety checks.
    """
    h, w = image.shape[:2]

    if top + bottom >= h or left + right >= w:
        raise ValueError("Crop dimensions exceed image size")

    return image[
        top : h - bottom,
        left : w - right
    ]

def take_photo(index: int, crop: dict[str, int] | None = None):
    filename = f"img_{index:02d}.png"
    try:
        response = requests.get(CAMERA_URL, timeout=5)
        response.raise_for_status()

        img_array = np.frombuffer(response.content, dtype=np.uint8)
        image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        if image is None:
            raise ValueError("Failed to decode image")

        # Resize to 512x512
        image_512 = cv2.resize(image, (512, 512), interpolation=cv2.INTER_AREA)

        # Apply cropping if required
        if crop is not None:
            image_512 = crop_borders(image_512, top=crop['top'], bottom=crop['bottom'], left=crop['left'], right=crop['right'])

        # Save as PNG
        cv2.imwrite(f'{IMAGE_DIR}/{filename}', image_512)

        return image_512

    except (requests.RequestException, ValueError) as e:
        print(f"[Error] Image fetch failed: {e}")
        return None


crop = {'top':60, 'bottom':20, 'left':20, 'right':20}


def get_key():
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.read(1)
    return None

fd = sys.stdin.fileno()
old_settings = termios.tcgetattr(fd)

try:
    tty.setcbreak(fd)   # immediate key reads, no Enter
    print("Press SPACE to continue, Q to quit")

    i = 0
    while True:
        key = get_key()
        if key:
            if key == 'q':
                print("Exiting")
                break
            elif key == ' ':
                take_photo(index=i, crop=crop)
                i += 1

finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)