"""Выбор размера локальных моделей по памяти машины."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Role:
    repo: str
    files: tuple[str, ...]
    vision: bool = False


@dataclass(frozen=True)
class Profile:
    name: str
    reason: str
    gpu_layers: int
    text: Role
    vision: Role
    decision: Role


def select_profile(ram_gib: float, vram_gib: float) -> str:
    if vram_gib >= 16 or ram_gib >= 32:
        return "high"
    if vram_gib >= 8 or ram_gib >= 16:
        return "mid"
    if vram_gib >= 4 or ram_gib >= 8:
        return "small"
    return "low"


def profile_for(name: str, ram_gib: float, vram_gib: float) -> Profile:
    gpu_layers = -1 if vram_gib > 0 else 0
    text, vision, decision = _ROLES[name]
    return Profile(
        name=name,
        reason=_reason(name, ram_gib, vram_gib),
        gpu_layers=gpu_layers,
        text=text,
        vision=vision,
        decision=decision,
    )


def detect_profile() -> Profile:
    ram = _ram_gib()
    vram = _vram_gib()
    return profile_for(select_profile(ram, vram), ram, vram)


def _reason(name: str, ram_gib: float, vram_gib: float) -> str:
    memory = f"{ram_gib:.0f} ГиБ RAM"
    if vram_gib > 0:
        memory += f", {vram_gib:.0f} ГиБ VRAM"
    labels = {
        "low": "малые модели",
        "small": "модели около 1,5B и SmolVLM-500M",
        "mid": "модели 3B",
        "high": "модели 7B",
    }
    return f"{labels[name]}, {memory}"


def _ram_gib() -> float:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) / (1024 * 1024)
    return 0


def _vram_gib() -> float:
    if shutil.which("nvidia-smi"):
        values = _nvidia_mib()
        if values:
            return max(values) / 1024
    if shutil.which("rocm-smi"):
        values = _rocm_mib()
        if values:
            return max(values) / 1024
    return 0


def _nvidia_mib() -> list[float]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    values = []
    for line in out.splitlines():
        token = line.strip().split()[0] if line.strip() else ""
        if token.isdigit():
            values.append(float(token))
    return values


def _rocm_mib() -> list[float]:
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showmeminfo", "vram", "--csv"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    values = []
    for line in out.splitlines():
        for token in line.replace(",", " ").split():
            if token.isdigit() and int(token) > 256:
                values.append(float(token))
    return values


_TEXT = {
    "low": Role("Qwen/Qwen2.5-0.5B-Instruct-GGUF", ("qwen2.5-0.5b-instruct-q4_k_m.gguf",)),
    "small": Role("Qwen/Qwen2.5-1.5B-Instruct-GGUF", ("qwen2.5-1.5b-instruct-q5_k_m.gguf",)),
    "mid": Role("Qwen/Qwen2.5-3B-Instruct-GGUF", ("qwen2.5-3b-instruct-q5_k_m.gguf",)),
    "high": Role(
        "Qwen/Qwen2.5-7B-Instruct-GGUF",
        (
            "qwen2.5-7b-instruct-q5_k_m-00001-of-00002.gguf",
            "qwen2.5-7b-instruct-q5_k_m-00002-of-00002.gguf",
        ),
    ),
}

_VISION = {
    "low": Role(
        "ggml-org/SmolVLM-256M-Instruct-GGUF",
        ("SmolVLM-256M-Instruct-Q8_0.gguf", "mmproj-SmolVLM-256M-Instruct-Q8_0.gguf"),
        vision=True,
    ),
    "small": Role(
        "ggml-org/SmolVLM-500M-Instruct-GGUF",
        ("SmolVLM-500M-Instruct-Q8_0.gguf", "mmproj-SmolVLM-500M-Instruct-Q8_0.gguf"),
        vision=True,
    ),
    "mid": Role(
        "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF",
        ("Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf", "mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf"),
        vision=True,
    ),
    "high": Role(
        "ggml-org/Qwen2.5-VL-7B-Instruct-GGUF",
        ("Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf", "mmproj-Qwen2.5-VL-7B-Instruct-Q8_0.gguf"),
        vision=True,
    ),
}

_ROLES = {
    name: (_TEXT[name], _VISION[name], _TEXT[name])
    for name in ("low", "small", "mid", "high")
}
