"""Pré-verificação de armazenamento para downloads locais.

O Qobuz informa duração e qualidade, mas nem sempre informa o tamanho final
antes de entregar a URL assinada. Por isso a estimativa é deliberadamente
conservadora e a checagem é opcional: ela evita iniciar álbuns grandes quando
o dispositivo móvel já está sem espaço, sem bloquear instalações antigas.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

MIN_FREE_RESERVE = 64 * 1024 * 1024
SAFETY_MARGIN = 1.20


class InsufficientStorageError(RuntimeError):
    """O espaço livre não cobre a estimativa do download."""


@dataclass(frozen=True)
class StorageCheck:
    path: str
    free_bytes: int
    required_bytes: int

    @property
    def enough(self) -> bool:
        return self.free_bytes >= self.required_bytes


def estimate_audio_bytes(
    duration_seconds: int | float | None,
    quality: int,
    bit_depth: int | str | None = None,
    sampling_rate: int | float | str | None = None,
) -> int:
    """Estimate encoded audio bytes using duration and a safety margin.

    FLAC is estimated from PCM bitrate with a conservative 75% factor. MP3
    uses its target bitrate. Unknown/invalid durations return zero so callers
    can safely skip the pre-flight check instead of inventing a size.
    """
    try:
        duration = float(duration_seconds or 0)
    except (TypeError, ValueError):
        return 0
    if duration <= 0:
        return 0
    bitrate: float
    if int(quality) == 5:
        bitrate = 320_000
    else:
        try:
            depth = float(bit_depth or (16 if int(quality) == 6 else 24))
            rate = float(str(sampling_rate or 44.1).replace(",", "."))
            if rate < 1000:
                rate *= 1000
            bitrate = depth * rate * 2 * 0.75
        except (TypeError, ValueError):
            bitrate = 1_411_200
    return int(duration * bitrate / 8 * SAFETY_MARGIN) + MIN_FREE_RESERVE


def check_free_space(path: str, required_bytes: int) -> StorageCheck:
    """Return a check for the filesystem containing ``path``."""
    free = shutil.disk_usage(path).free
    return StorageCheck(
        path=path, free_bytes=free, required_bytes=max(0, required_bytes)
    )


def ensure_free_space(path: str, required_bytes: int) -> StorageCheck:
    """Raise a user-facing error when the estimated download does not fit."""
    check = check_free_space(path, required_bytes)
    if not check.enough:
        raise InsufficientStorageError(
            f"Espaço insuficiente em {path}: são necessários aproximadamente "
            f"{format_bytes(check.required_bytes)}, mas há apenas "
            f"{format_bytes(check.free_bytes)} livres."
        )
    return check


def format_bytes(value: int) -> str:
    """Format bytes compactly without adding a dependency."""
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(max(0, value))
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TB"
