"""Stand-in for the web GCS camera feed, run on a laptop/PC on the drone's network.

    python video_client.py rosbridge --host 192.168.68.61 --secs 600 --throttle-ms 33
    python video_client.py mjpeg --url "http://192.168.68.61:8080/stream?topic=/cam0/image_raw&type=ros_compressed" --secs 600

rosbridge mode subscribes exactly like dexi-droneblocks components/CameraFeed.vue:
sensor_msgs/CompressedImage on /cam0/image_raw/compressed, compression cbor,
queue_length 1, throttle_rate as given. mjpeg mode reads a multipart HTTP stream.
Prints and writes JSON: frames received, fps, bytes/s, inter-frame gap p50/p95/max.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request


def summarize(mode, times, nbytes, secs, extra):
    gaps = [b - a for a, b in zip(times, times[1:])]
    g = sorted(gaps)
    pick = lambda q: round(g[min(len(g) - 1, int(q * len(g)))] * 1000, 1) if g else None
    return {
        "mode": mode, **extra, "secs": secs, "frames": len(times),
        "fps": round(len(times) / secs, 2), "kbytes_per_s": round(nbytes / secs / 1024, 1),
        "avg_frame_kb": round(nbytes / max(1, len(times)) / 1024, 1),
        "gap_ms_p50": pick(0.5), "gap_ms_p95": pick(0.95), "gap_ms_max": round(max(gaps) * 1000, 1) if gaps else None,
    }


def run_rosbridge(host, port, secs, throttle_ms, topic):
    import cbor2
    import websocket
    ws = websocket.create_connection(f"ws://{host}:{port}", timeout=10)
    ws.send(json.dumps({"op": "subscribe", "id": "bench", "topic": topic,
                        "type": "sensor_msgs/CompressedImage", "throttle_rate": throttle_ms,
                        "queue_length": 1, "compression": "cbor"}))
    times, nbytes = [], 0
    t_end = time.monotonic() + secs
    while time.monotonic() < t_end:
        op, data = ws.recv_data()
        if op == websocket.ABNF.OPCODE_BINARY:
            m = cbor2.loads(data)
            jpeg = m.get("msg", {}).get("data", b"")
            nbytes += len(jpeg)
            times.append(time.monotonic())
    ws.close()
    return summarize("rosbridge", times, nbytes, secs, {"throttle_ms": throttle_ms, "topic": topic})


def run_mjpeg(url, secs):
    r = urllib.request.urlopen(url, timeout=10)
    ctype = r.headers.get("Content-Type", "")
    boundary = ctype.split("boundary=")[-1].strip().encode() if "boundary=" in ctype else b"--boundarydonotcross"
    if not boundary.startswith(b"--"):
        boundary = b"--" + boundary
    times, nbytes, buf = [], 0, b""
    t_end = time.monotonic() + secs
    while time.monotonic() < t_end:
        chunk = r.read(65536)
        if not chunk:
            break
        buf += chunk
        while True:
            i = buf.find(boundary)
            j = buf.find(boundary, i + len(boundary)) if i >= 0 else -1
            if i < 0 or j < 0:
                break
            part = buf[i:j]
            k = part.find(b"\r\n\r\n")
            if k >= 0:
                nbytes += len(part) - k - 4
                times.append(time.monotonic())
            buf = buf[j:]
    r.close()
    return summarize("mjpeg", times, nbytes, secs, {"url": url})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["rosbridge", "mjpeg"])
    ap.add_argument("--host", default="192.168.68.61")
    ap.add_argument("--port", type=int, default=9090)
    ap.add_argument("--topic", default="/cam0/image_raw/compressed")
    ap.add_argument("--throttle-ms", type=int, default=33)
    ap.add_argument("--url")
    ap.add_argument("--secs", type=float, default=600)
    ap.add_argument("--out")
    a = ap.parse_args()
    res = run_rosbridge(a.host, a.port, a.secs, a.throttle_ms, a.topic) if a.mode == "rosbridge" else run_mjpeg(a.url, a.secs)
    print(json.dumps(res, indent=1))
    if a.out:
        open(a.out, "w").write(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
