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

        frames = []

        for _ in range(max(1, stack)):
            frame = picam2.capture_array("main")
            Y = frame[:H, :W]
            frames.append(Y.astype(np.float32))

        if len(frames) == 1:
            img = frames[0]
        else:
            stack_arr = np.stack(frames, axis=0)
            if mode == "median":
                img = np.median(stack_arr, axis=0)
            else:
                img = np.mean(stack_arr, axis=0)

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
