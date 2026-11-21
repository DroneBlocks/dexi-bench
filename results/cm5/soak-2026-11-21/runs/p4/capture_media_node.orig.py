#!/usr/bin/env python3
"""capture_media: photos and video from the DEXI camera, saved on the Pi.

ROS (namespace /dexi, so everything is under /dexi/capture_media/):
  ~/capture_photo    std_srvs/Trigger  save the next camera frame
  ~/start_recording  std_srvs/Trigger  start an MP4 recording
  ~/stop_recording   std_srvs/Trigger  stop it
  ~/status           std_msgs/String   JSON, 1 Hz and on every change

HTTP on http_port (default 8090), for the GCS and anything else on the LAN:
  GET    /media          JSON list of files, newest first
  GET    /media/<name>   the file (Range supported; ?download=1 to save)
  DELETE /media/<name>   delete it

RC: photo_aux_index / video_aux_index (1..6 = aux1..aux6, 0 = off) read
/fmu/out/manual_control_setpoint, the same way tag_nav's engage switch does.
A tap on the photo button takes a photo; holding it for record_hold_s (3 s)
starts or stops a recording. The video channel records while its switch is high.

/dexi/capture_media/recording (std_msgs/Bool, latched) says whether a recording
is running; dexi_led blinks the LED ring red while it is true.

The camera topic is only subscribed while it is needed: a short-lived
CameraTap (its own node and thread) exists for a photo or a recording, so an
idle node costs next to nothing.
"""
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import rclpy
import rclpy.executors
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSDurabilityPolicy, QoSHistoryPolicy
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger

from dexi_capture_media import media_files
from dexi_capture_media.rc_switch import AuxSwitch, HoldButton

PX4_QOS = QoSProfile(
    reliability=QoSReliabilityPolicy.BEST_EFFORT,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=1,
)
FRAME_FRESH_S = 2.0
FRAME_WAIT_S = 2.0      # how long a photo or recording waits for the first frame


class CameraTap:
    """Subscribes to the camera only while it exists.

    Its own node and executor thread, so the main node's services can simply
    block on next_frame() without stalling frame delivery. Creating one costs a
    few tens of milliseconds; close() removes the subscription.
    """

    def __init__(self, topic):
        # use_global_arguments=False: the launch remaps __node for the whole
        # process, which would otherwise rename this node to capture_media too.
        self.node = rclpy.create_node('capture_media_camera', namespace='dexi',
                                      use_global_arguments=False)
        self.cond = threading.Condition()
        self.frame = None            # (CompressedImage, receive time)
        self.count = 0
        self.node.create_subscription(CompressedImage, topic, self._on_image, 1)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def _spin(self):
        try:
            self.executor.spin()
        except (rclpy.executors.ExternalShutdownException, rclpy.executors.ShutdownException):
            pass

    def _on_image(self, msg):
        with self.cond:
            self.frame = (msg, time.time())
            self.count += 1
            self.cond.notify_all()

    def next_frame(self, timeout):
        """Wait for a frame newer than any seen so far; None on timeout."""
        with self.cond:
            seen = self.count
            if not self.cond.wait_for(lambda: self.count > seen, timeout):
                return None
            return self.frame[0]

    def latest_jpeg(self):
        """Bytes of the newest frame if it is fresh, else None (for the recorder)."""
        with self.cond:
            fresh = self.frame and time.time() - self.frame[1] < FRAME_FRESH_S
            msg = self.frame[0] if fresh else None
        return bytes(msg.data) if msg is not None else None

    def close(self):
        self.executor.shutdown(timeout_sec=1.0)
        self.thread.join(timeout=2.0)
        self.node.destroy_node()


class Recorder:
    """Feeds the latest camera frame to ffmpeg at a fixed rate; ffmpeg writes H.264 MP4.

    The camera's own rate varies, and timestamping frames on arrival is not
    reliable through ffmpeg's MJPEG input. So a thread writes whatever frame
    is newest, `fps` times a second of wall-clock time, repeating a frame if
    none arrived and catching up if ffmpeg was slow. The video's length always
    matches how long it recorded. The file is written as <name>.part and
    renamed when ffmpeg finishes, so a half-written video is never served.
    """

    def __init__(self, path, tap, fps):
        self.path = path
        self.part = path + '.part'
        self.tap = tap                      # CameraTap; closed by finish()
        self.fps = float(fps)
        self.started = time.time()
        self.frames = 0
        self.stopping = threading.Event()
        self.proc = subprocess.Popen(
            ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
             '-f', 'mjpeg', '-framerate', f'{self.fps:g}', '-i', 'pipe:0',
             '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
             '-movflags', '+faststart', '-f', 'mp4', self.part],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self):
        while not self.stopping.is_set():
            due = int((time.time() - self.started) * self.fps) + 1
            frame = self.tap.latest_jpeg()
            try:
                while self.frames < due and frame is not None:
                    self.proc.stdin.write(frame)
                    self.frames += 1
            except (BrokenPipeError, ValueError, OSError):
                return
            self.stopping.wait(0.5 / self.fps)

    @property
    def elapsed(self):
        return time.time() - self.started

    def finish(self, timeout=30.0):
        """Close the stream and wait for ffmpeg. Returns (ok, error text)."""
        self.stopping.set()
        self.thread.join(timeout=5.0)
        self.tap.close()
        try:
            self.proc.stdin.close()   # EOF: ffmpeg finishes the file and exits
        except (BrokenPipeError, OSError):
            pass
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        err = self.proc.stderr.read()   # -loglevel error keeps this small
        if self.proc.returncode == 0 and os.path.exists(self.part):
            os.replace(self.part, self.path)
            return True, ''
        if os.path.exists(self.part):
            os.remove(self.part)
        return False, (err or b'').decode(errors='replace').strip()[-300:]


class CaptureMediaNode(Node):
    def __init__(self):
        super().__init__('capture_media')
        p = self.declare_parameter
        p('image_topic', '/cam0/image_raw/compressed')
        p('media_dir', '/home/dexi/dexi_media')
        p('http_port', 8090)
        p('min_free_mb', 200)        # refuse to capture (and stop recording) below this
        p('max_video_s', 600.0)      # stop a recording after this long; 0 = no limit
        p('video_fps', 15.0)         # frame rate of recorded video; DEXI's camera runs ~15 fps
        p('photo_aux_index', 0)      # 1..6 = aux1..aux6; 0 = off
        p('video_aux_index', 0)
        p('aux_threshold', 0.5)
        p('record_hold_s', 3.0)      # hold the photo button this long to start/stop recording; 0 = off
        g = lambda n: self.get_parameter(n).value

        self.media_dir = os.path.expanduser(g('media_dir'))
        os.makedirs(self.media_dir, exist_ok=True)
        self.min_free_mb = int(g('min_free_mb'))
        self.max_video_s = float(g('max_video_s'))
        self.video_fps = float(g('video_fps'))
        self.http_port = int(g('http_port'))

        self.lock = threading.Lock()
        self.recorder = None
        self.last_event = ''
        self.image_topic = g('image_topic')

        self.create_service(Trigger, '~/capture_photo', self._srv_photo)
        self.create_service(Trigger, '~/start_recording', self._srv_start)
        self.create_service(Trigger, '~/stop_recording', self._srv_stop)
        self.status_pub = self.create_publisher(String, '~/status', 10)
        # Latched, so dexi_led (and anything started later) gets the current state.
        latched = QoSProfile(reliability=QoSReliabilityPolicy.RELIABLE,
                             durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                             history=QoSHistoryPolicy.KEEP_LAST, depth=1)
        self.recording_pub = self.create_publisher(Bool, '~/recording', latched)
        self.recording_published = None
        self.create_timer(1.0, self._tick)

        self.photo_sw = HoldButton(g('photo_aux_index'), g('aux_threshold'), g('record_hold_s'))
        self.video_sw = AuxSwitch(g('video_aux_index'), g('aux_threshold'))
        if self.photo_sw.enabled or self.video_sw.enabled:
            # Imported here so a stack without px4_msgs still runs with RC off.
            from px4_msgs.msg import ManualControlSetpoint
            self.create_subscription(ManualControlSetpoint, '/fmu/out/manual_control_setpoint',
                                     self._on_rc, PX4_QOS)

        self.http = ThreadingHTTPServer(('', self.http_port), _make_handler(self))
        self.http.daemon_threads = True
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

        self.get_logger().info(
            f'capture_media ready: {self.media_dir}, http :{self.http_port}, '
            f'photo aux{self.photo_sw.index or "-off"}, video aux{self.video_sw.index or "-off"}')

    # ---------------------------------------------------------------- capture

    def _free_mb(self):
        return shutil.disk_usage(self.media_dir).free // (1024 * 1024)

    def capture_photo(self):
        if self._free_mb() < self.min_free_mb:
            return False, f'disk almost full ({self._free_mb()} MB free)'
        rec = self.recorder
        if rec:
            frame = rec.tap.next_frame(FRAME_WAIT_S)
        else:
            tap = CameraTap(self.image_topic)
            try:
                frame = tap.next_frame(FRAME_WAIT_S)
            finally:
                tap.close()
        if frame is None:
            return False, 'no camera frame (is the camera running?)'
        name = media_files.photo_name(frame.format)
        with open(os.path.join(self.media_dir, name), 'wb') as f:
            f.write(bytes(frame.data))
        return True, name

    def start_recording(self):
        with self.lock:
            if self.recorder:
                return False, f'already recording {os.path.basename(self.recorder.path)}'
        if self._free_mb() < self.min_free_mb:
            return False, f'disk almost full ({self._free_mb()} MB free)'
        tap = CameraTap(self.image_topic)
        if tap.next_frame(FRAME_WAIT_S) is None:
            tap.close()
            return False, 'no camera frame (is the camera running?)'
        name = media_files.video_name()
        try:
            rec = Recorder(os.path.join(self.media_dir, name), tap, self.video_fps)
        except FileNotFoundError:
            tap.close()
            return False, 'ffmpeg is not installed'
        with self.lock:
            self.recorder = rec
        return True, name

    def stop_recording(self):
        with self.lock:
            rec, self.recorder = self.recorder, None
        if rec is None:
            return False, 'not recording'
        ok, err = rec.finish()
        name = os.path.basename(rec.path)
        if not ok:
            return False, f'recording failed: {err or "ffmpeg error"}'
        return True, f'{name} ({rec.elapsed:.0f} s)'

    def _done(self, ok, message):
        self.last_event = message
        # rclpy pins one severity per call site, so these must be separate calls.
        if ok:
            self.get_logger().info(message)
        else:
            self.get_logger().warning(message)
        self._publish_status()
        return ok, message

    def _srv(self, fn, response):
        response.success, response.message = self._done(*fn())
        return response

    def _srv_photo(self, req, res):
        return self._srv(self.capture_photo, res)

    def _srv_start(self, req, res):
        return self._srv(self.start_recording, res)

    def _srv_stop(self, req, res):
        return self._srv(self.stop_recording, res)

    def _on_rc(self, msg):
        press = self.photo_sw.update(msg, time.monotonic())
        if press == 'tap':
            self._done(*self.capture_photo())
        elif press == 'hold':
            self._done(*(self.stop_recording() if self.recorder else self.start_recording()))
        edge = self.video_sw.update(msg)
        if edge == 'rising':
            self._done(*self.start_recording())
        elif edge == 'falling' and self.recorder:
            self._done(*self.stop_recording())

    # ---------------------------------------------------------------- status

    def _tick(self):
        rec = self.recorder
        if rec:
            if self.max_video_s > 0 and rec.elapsed >= self.max_video_s:
                self._done(*self.stop_recording())
            elif self._free_mb() < self.min_free_mb:
                ok, msg = self.stop_recording()
                self._done(ok, f'disk almost full, stopped: {msg}')
        self._publish_status()

    def status(self):
        rec = self.recorder
        files = media_files.list_media(self.media_dir)
        return {
            'recording': rec is not None,
            'file': os.path.basename(rec.path) if rec else None,
            'elapsed_s': round(rec.elapsed, 1) if rec else 0.0,
            'photos': sum(f['type'] == 'photo' for f in files),
            'videos': sum(f['type'] == 'video' for f in files),
            'free_mb': self._free_mb(),
            'http_port': self.http_port,
            'last_event': self.last_event,
        }

    def _publish_status(self):
        self.status_pub.publish(String(data=json.dumps(self.status())))
        recording = self.recorder is not None
        if recording != self.recording_published:
            self.recording_pub.publish(Bool(data=recording))
            self.recording_published = recording

    def shutdown(self):
        if self.recorder:
            self.stop_recording()
        self.http.shutdown()


def _make_handler(node):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _cors(self):
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Range')
            self.send_header('Access-Control-Expose-Headers', 'Content-Length, Content-Range')

        def _json(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _target(self):
            url = urlparse(self.path)
            parts = url.path.strip('/').split('/')
            if parts[0] != 'media':
                return url, None, False
            if len(parts) == 1:
                return url, None, True
            name = unquote(parts[1]) if len(parts) == 2 else ''
            return url, (name if media_files.is_valid_name(name) else ''), True

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            url, name, ok = self._target()
            if not ok or name == '':
                return self._json(404, {'error': 'not found'})
            if name is None:
                return self._json(200, {'files': media_files.list_media(node.media_dir),
                                        'free_mb': node._free_mb()})
            path = os.path.join(node.media_dir, name)
            if not os.path.isfile(path):
                return self._json(404, {'error': 'not found'})
            size = os.path.getsize(path)
            rng = media_files.parse_range(self.headers.get('Range'), size)
            if rng == 'unsatisfiable':
                self.send_response(416)
                self._cors()
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
            start, end = rng or (0, size - 1)
            self.send_response(206 if rng else 200)
            self._cors()
            self.send_header('Content-Type', media_files.mime_type(name))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end - start + 1))
            if rng:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            if 'download' in parse_qs(url.query):
                self.send_header('Content-Disposition', f'attachment; filename="{name}"')
            self.end_headers()
            with open(path, 'rb') as f:
                f.seek(start)
                remaining = end - start + 1
                try:
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass   # the browser cancelled (normal while seeking a video)

        def do_DELETE(self):
            _, name, ok = self._target()
            if not ok or not name:
                return self._json(404, {'error': 'not found'})
            path = os.path.join(node.media_dir, name)
            if not os.path.isfile(path):
                return self._json(404, {'error': 'not found'})
            os.remove(path)
            node.get_logger().info(f'deleted {name}')
            return self._json(200, {'deleted': name})

    return Handler


def main(args=None):
    rclpy.init(args=args)
    node = CaptureMediaNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
