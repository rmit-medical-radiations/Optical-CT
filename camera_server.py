from flask import Flask, send_file, Response
import io
from picamera2 import Picamera2
import cv2
import numpy as np
from pprint import pprint
from libcamera import controls

# This server runs on the RPi Zero.

app = Flask(__name__)

picam2 = Picamera2()
config = picam2.create_still_configuration(
    main={"format": "YUV420", "size": (800, 600), "preserve_ar": True},
    buffer_count=1,
)
picam2.configure(config)
picam2.start()

picam2.set_controls({
    "AeEnable": False,
    "AwbEnable": False,
    "NoiseReductionMode": controls.draft.NoiseReductionModeEnum.Off,

    "ExposureTime": 500000,
    "AnalogueGain": 1.0,

    # Minimise ISP alterations
    "Sharpness": 0.0,
    "Saturation": 0.0,
    "Contrast": 1.0,
})

pprint(picam2.camera_configuration())


@app.route("/")
def index():
    return Response("""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>Live Capture</title>
    <style>
      body { font-family: sans-serif; margin: 16px; }
      img { max-width: 100%; height: auto; border: 1px solid #ccc; }
    </style>
  </head>
  <body>
    <h3>Live Capture</h3>
    <img id="frame" src="/capture" />
    <script>
      const img = document.getElementById('frame');
      const intervalMs = 1000; // adjust (e.g. 100–1000)
      function refresh() {
        // cache-buster to force a new fetch each time
        img.src = "/capture?t=" + Date.now();
      }
      setInterval(refresh, intervalMs);
    </script>
  </body>
</html>
""", mimetype="text/html")


@app.route('/capture', methods=["GET"])
def capture():
    # Capture YUV420 frame directly as a NumPy array
    frame = picam2.capture_array("main")

    # Get configured output size
    cfg = picam2.camera_configuration()
    W, H = cfg["main"]["size"]

    # Extract Y (luminance) plane
    Y = frame[:H, :W]

    # Encode to PNG
    success, png = cv2.imencode(".png", Y)
    if not success:
        return "Encode failed", 500

    out = io.BytesIO(png.tobytes())
    resp = send_file(out, mimetype="image/png")

    # Strongly discourage caching
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"

    return resp


if __name__ == '__main__':
    # threaded=True helps if multiple browser requests overlap
    app.run(host='0.0.0.0', port=8000, threaded=True)
