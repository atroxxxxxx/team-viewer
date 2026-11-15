import re
import shutil
from dataclasses import dataclass

_DISPLAY = re.compile(r"^:[0-9]{1,2}(?:\.[0-9]{1,2})?$")

FRAME_WIDTH = 640
FRAME_HEIGHT = 360
DEFAULT_FPS = 60


@dataclass(frozen=True)
class CaptureGeometry:
    width: int
    height: int

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0:
            raise ValueError("некорректный размер экрана")

    def content_area(self) -> tuple[int, int, int, int]:
        ratio = min(FRAME_WIDTH / self.width, FRAME_HEIGHT / self.height)
        width = max(2, int(self.width * ratio) // 2 * 2)
        height = max(2, int(self.height * ratio) // 2 * 2)
        left = (FRAME_WIDTH - width) // 4 * 2
        top = (FRAME_HEIGHT - height) // 4 * 2
        return left, top, width, height

    def project(self, x: int, y: int) -> tuple[int, int] | None:
        left, top, width, height = self.content_area()
        if not (left <= x < left + width and top <= y < top + height):
            return None
        return (x - left) * self.width // width, (y - top) * self.height // height

    def filter(self) -> str:
        left, top, width, height = self.content_area()
        return f"scale={width}:{height},pad={FRAME_WIDTH}:{FRAME_HEIGHT}:{left}:{top}:black"


def x11_display(value: str) -> str:
    return value if _DISPLAY.fullmatch(value) else ":0"


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg не найден в PATH")
    return path


def valid_fps(value) -> bool:
    return type(value) is int and value > 0


def publish_args(url: str, mode: str, display: str, *, fps: int = DEFAULT_FPS, crf: int = 23,
                 capture: CaptureGeometry | None = None) -> list[str]:
    if not valid_fps(fps) or not 18 <= crf <= 35:
        raise ValueError("unsupported video settings")
    ffmpeg = ffmpeg_path()
    output = [
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-threads", "2", "-crf", str(crf), "-maxrate", "2M", "-bufsize", "1M",
        "-g", str(fps), "-keyint_min", str(fps), "-sc_threshold", "0", "-bf", "0",
        "-x264-params", "repeat-headers=1:open-gop=0", "-pix_fmt", "yuv420p",
        "-flush_packets", "1", "-f", "rtsp", "-rtsp_transport", "tcp", url,
    ]
    if mode == "screen":
        geometry = capture or CaptureGeometry(1280, 720)
        return [
            ffmpeg, "-loglevel", "error", "-f", "x11grab", "-framerate", str(fps),
            "-video_size", f"{geometry.width}x{geometry.height}", "-i", x11_display(display or ""),
            "-vf", geometry.filter(), *output,
        ]
    if mode == "camera":
        return [
            ffmpeg, "-loglevel", "error", "-f", "v4l2", "-framerate", str(fps), "-i", "/dev/video0",
            "-vf", f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}", *output,
        ]
    if mode != "testsrc":
        raise ValueError("unknown capture mode")
    return [
        ffmpeg, "-loglevel", "error", "-re", "-f", "lavfi",
        "-i", f"testsrc=size={FRAME_WIDTH}x{FRAME_HEIGHT}:rate={fps}", *output,
    ]


def play_args(url: str) -> list[str]:
    return [
        ffmpeg_path(), "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", "5000000",
        "-probesize", "32768", "-analyzeduration", "100000",
        "-fflags", "nobuffer", "-flags", "low_delay", "-threads", "1", "-i", url,
        "-an", "-sn", "-dn", "-threads:v", "1", "-fps_mode", "passthrough", "-flush_packets", "1",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]


def latest_frame(buffer: bytearray, chunk: bytes, frame_size: int) -> bytes | None:
    """Сохраняет частичный кадр и отдаёт только последний целый RGB-кадр."""
    buffer.extend(chunk)
    count = len(buffer) // frame_size
    if not count:
        return None
    end = count * frame_size
    raw = bytes(buffer[end - frame_size:end])
    del buffer[:end]
    return raw


def session_ended(status: str) -> bool:
    return status in {"resolved", "closed"}


def media_action(active_tunnel: str, wanted_tunnel: str, status: str) -> str:
    """Что сделать с публикацией клиента: stop, start, keep или idle."""
    if session_ended(status):
        return "stop" if active_tunnel else "idle"
    if wanted_tunnel and wanted_tunnel != active_tunnel:
        return "start"
    if active_tunnel and not wanted_tunnel:
        return "stop"
    if active_tunnel:
        return "keep"
    return "idle"


def should_open_player(playing_tunnel: str, wanted_tunnel: str, running: bool, status: str) -> bool:
    """Оператор открывает плеер, пока заявка жива, а поток ещё не идёт."""
    if session_ended(status) or not wanted_tunnel:
        return False
    if playing_tunnel != wanted_tunnel:
        return True
    return not running
