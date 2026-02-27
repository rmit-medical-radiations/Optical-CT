from flask import Flask, send_file, request
import io
import numpy as np
import cv2
from picamera2 import Picamera2
from libcamera import controls
import threading

app = Flask(__name__)
cam_lock = threading.Lock()

# ---- Camera Setup ----
picam2 = Picamera2()

config = picam2.create_still_configuration(
    main={"format": "YUV420", "size": (2028, 1520), "preserve_ar": True},
    buffer_count=4,
)
picam2.configure(config)

picam2.set_controls({
    "AeEnable": False,
    "AwbEnable": False,
    "NoiseReductionMode": controls.draft.NoiseReductionModeEnum.Off,
    "ExposureTime": 500000,
    "AnalogueGain": 1.0,
    "Sharpness": 0.0,
    "Saturation": 0.0,
    "Contrast": 1.0,
})

picam2.start()


def capture_projection_timestamped(picam2, num_avg=8, stream="main"):
    # Take a reference request to define "after settle"
    req0 = picam2.capture_request()
    t0 = req0.get_metadata()["SensorTimestamp"]
    req0.release()

    frames = []
    while len(frames) < num_avg:
        req = picam2.capture_request()
        meta = req.get_metadata()
        img = req.make_array(stream)
        req.release()

        if meta["SensorTimestamp"] > t0:
            frames.append(img)

    return np.mean(np.stack(frames).astype(np.float32), axis=0)


@app.route("/capture", methods=["GET"])
def capture():
    """
    Query parameters:
        stack: number of frames to average (default=1)
        mode: mean or median (default=mean)
    """

    stack = int(request.args.get("stack", 1))
    mode = request.args.get("mode", "mean")

    with cam_lock:
        cfg = picam2.camera_configuration()
        W, H = cfg["main"]["size"]
        img = capture_projection(num_avg=stack)
        img_u8 = np.clip(np.round(img), 0, 255).astype(np.uint8)

    success, png = cv2.imencode(
        ".png",
        img_u8,
        [cv2.IMWRITE_PNG_COMPRESSION, 1]
    )

    if not success:
        return "Encode failed", 500

    resp = send_file(
        io.BytesIO(png.tobytes()),
        mimetype="image/png"
    )

    # ---- Strongly discourage caching ----
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"

    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
