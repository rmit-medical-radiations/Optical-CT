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


def capture_projection_timestamped(num_avg=8, stream="main", max_tries=400):
    """
    Timestamp-gated stack capture for YUV420 main stream.
    Averages the Y (luma) plane only.

    Args:
        num_avg: number of fresh frames to include in the stack
        stream: typically "main"
        max_tries: safety cap to avoid infinite loop if frames don't advance

    Returns:
        (H, W) float32 averaged luma image
    """
    # Get configured main size (width, height)
    cfg = picam2.camera_configuration()
    W, H = cfg[stream]["size"]

    # Reference frame timestamp (defines "after settle")
    req0 = picam2.capture_request()
    t0 = req0.get_metadata().get("SensorTimestamp", 0)
    req0.release()

    frames = []
    tries = 0
    while len(frames) < num_avg and tries < max_tries:
        tries += 1

        req = picam2.capture_request()
        meta = req.get_metadata()
        arr = req.make_array(stream)  # YUV420 packed: typically (H*3/2, W)
        req.release()

        ts = meta.get("SensorTimestamp", 0)
        if ts <= t0:
            continue

        # Extract Y plane safely: top H rows, full width
        # Works for YUV420 where make_array returns a 2D array.
        if arr.ndim == 2 and arr.shape[0] >= H and arr.shape[1] >= W:
            y = arr[:H, :W]
        else:
            raise RuntimeError(f"Unexpected array shape for {stream}: {arr.shape}")

        frames.append(y.astype(np.float32))

    if len(frames) < num_avg:
        raise RuntimeError(
            f"Timed out collecting {num_avg} fresh frames (got {len(frames)}). "
            f"Increase max_tries or check camera pipeline."
        )

    return np.mean(np.stack(frames, axis=0), axis=0)


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
        img = capture_projection_timestamped(picam2, num_avg=stack)
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
