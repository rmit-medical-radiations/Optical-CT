import cv2
import requests
import numpy as np
import time

# Address of your Pi camera server
CAMERA_URL = "http://raspberrypi.local:8000/capture"

def fetch_image():
    try:
        response = requests.get(CAMERA_URL, timeout=5)
        response.raise_for_status()
        img_array = np.frombuffer(response.content, dtype=np.uint8)
        image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        return image
    except requests.RequestException as e:
        print(f"[Error] Request failed: {e}")
        return None

def main():
    print("Starting live preview from Pi camera. Press 'q' to quit.")

    while True:
        frame = fetch_image()
        if frame is not None:
            cv2.imshow("Camera Focus Preview", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break

        time.sleep(0.2)  # Adjust delay to balance responsiveness and load

    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()