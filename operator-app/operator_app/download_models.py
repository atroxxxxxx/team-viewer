"""Скачивание локальных GGUF с Hugging Face, если их ещё нет."""

from __future__ import annotations

import json
import os
from pathlib import Path

from operator_app.hardware import Profile, Role, detect_profile

ROOT = Path(__file__).resolve().parents[1]


def models_dir() -> Path:
    override = os.environ.get("SUPPORT_MODELS_DIR", "").strip()
    if override:
        return Path(override)
    return ROOT / "models"


def ensure_models(dest: Path | None = None) -> dict:
    dest = dest or models_dir()
    dest.mkdir(parents=True, exist_ok=True)
    profile = detect_profile()
    marker = dest / "manifest.json"
    if marker.is_file():
        current = json.loads(marker.read_text(encoding="utf-8"))
        if _ready(dest, current, profile):
            print(f"Модели уже на месте, профиль {profile.name}: {profile.reason}", flush=True)
            current["root"] = str(dest)
            return current
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError(
            "Для скачивания нужен пакет huggingface_hub. Установите зависимости из requirements-models.txt"
        ) from exc
    print(f"Профиль {profile.name}: {profile.reason}", flush=True)
    saved: dict[str, list[str]] = {}
    for role_name, role in (("text", profile.text), ("vision", profile.vision), ("decision", profile.decision)):
        saved[role_name] = []
        for filename in role.files:
            print(f"Скачиваю {role_name}: {role.repo} {filename}", flush=True)
            hf_hub_download(repo_id=role.repo, filename=filename, local_dir=dest / role_name)
            saved[role_name].append(f"{role_name}/{filename}")
    manifest = {
        "profile": profile.name,
        "reason": profile.reason,
        "gpu_layers": profile.gpu_layers,
        "text_repo": profile.text.repo,
        "vision_repo": profile.vision.repo,
        "decision_repo": profile.decision.repo,
        "text": saved["text"][0],
        "text_parts": saved["text"],
        "vision": saved["vision"][0],
        "vision_mmproj": saved["vision"][1],
        "vision_parts": saved["vision"],
        "decision": saved["decision"][0],
        "decision_parts": saved["decision"],
    }
    marker.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["root"] = str(dest)
    return manifest


def _ready(dest: Path, manifest: dict, profile: Profile) -> bool:
    if manifest.get("profile") != profile.name or manifest.get("gpu_layers") != profile.gpu_layers:
        return False
    roles = {"text": profile.text, "vision": profile.vision, "decision": profile.decision}
    for name, role in roles.items():
        parts = manifest.get(f"{name}_parts")
        if parts != [f"{name}/{filename}" for filename in role.files]:
            return False
        if not _files_ok(dest, parts, role):
            return False
    return True


def _files_ok(dest: Path, parts: list[str], role: Role) -> bool:
    if len(parts) != len(role.files):
        return False
    for relative in parts:
        path = dest / relative
        if not path.is_file() or path.stat().st_size < 1024:
            return False
    return True


def main() -> None:
    manifest = ensure_models()
    print(manifest["profile"], manifest["reason"], flush=True)


if __name__ == "__main__":
    main()
