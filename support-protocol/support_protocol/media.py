import shutil

FRAME_WIDTH = 640
FRAME_HEIGHT = 360


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg не найден в PATH")
    return path


def publish_args(url: str, mode: str, display: str) -> list[str]:
    ffmpeg = ffmpeg_path()
    output = [
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p", "-f", "rtsp", "-rtsp_transport", "tcp", url,
    ]
    if mode == "screen":
        return [
            ffmpeg, "-loglevel", "error", "-f", "x11grab", "-framerate", "5",
            "-video_size", "1280x720", "-i", display,
            "-vf", f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}", *output,
        ]
    if mode == "camera":
        return [
            ffmpeg, "-loglevel", "error", "-f", "v4l2", "-framerate", "5", "-i", "/dev/video0",
            "-vf", f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}", *output,
        ]
    return [
        ffmpeg, "-loglevel", "error", "-f", "lavfi",
        "-i", f"testsrc=size={FRAME_WIDTH}x{FRAME_HEIGHT}:rate=5", *output,
    ]


def play_args(url: str) -> list[str]:
    return [
        ffmpeg_path(), "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url,
        "-vf", f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]


def media_action(active_tunnel: str, wanted_tunnel: str, status: str) -> str:
    """Что сделать с публикацией клиента: stop, start, keep или idle."""
    if status == "resolved":
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
    if status == "resolved" or not wanted_tunnel:
        return False
    if playing_tunnel != wanted_tunnel:
        return True
    return not running
