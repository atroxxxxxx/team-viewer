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
