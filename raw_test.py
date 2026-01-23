import time
import numpy as np
from picamera2 import Picamera2

picam2 = Picamera2()

# Select a sensor mode for full resolution capture
# The HQ camera's full resolution is 4056x3040
modes = picam2.sensor_modes
# Select the appropriate mode (often index 0 or 1 for full resolution, check your output)
# Printing modes can help: print(modes)
selected_mode = modes[0] 

camera_config = picam2.create_still_configuration(
    raw={'format': selected_mode['unpacked']},
    sensor={'output_size': selected_mode['size'], 'bit_depth': selected_mode['bit_depth']}
)
picam2.configure(camera_config)

picam2.start()
time.sleep(2)

# Capture the raw frame as a 16-bit numpy array
# .view(np.uint16) is used to unpack the 12-bit packed sensor data into 16-bit elements
raw_array = picam2.capture_array("raw").view(np.uint16)

picam2.stop()
print(f"Captured raw array with shape: {raw_array.shape}")
