"""
Hardware detection for Lamark setup.

Reports the user's GPU+RAM situation back to the install script so the
right tier of model gets picked. Designed to NOT require torch — runs from
plain host Python in a shell installer context.

Detection priorities:
1. `nvidia-smi` → GPU name, VRAM, compute capability
2. `/proc/meminfo` → system RAM
3. `uname -m` → architecture
4. Heuristic: Spark detection (GB10 + sm_121 + aarch64 + unified mem)

Output is a dict that hardware.detect_tier() consumes.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class HardwareInfo:
    gpu_name: Optional[str] = None
    gpu_count: int = 0
    vram_gb: float = 0.0       # per-GPU VRAM (0 if N/A — Spark reports N/A)
    compute_cap: Optional[str] = None   # e.g. "12.1" for Blackwell
    ram_gb: float = 0.0
    arch: str = ""             # uname -m
    is_spark: bool = False
    # Effective memory available for model weights. For Spark this is
    # system RAM (unified). For discrete GPUs it's VRAM.
    effective_memory_gb: float = 0.0

    def to_dict(self) -> dict:
        return {
            "gpu_name": self.gpu_name,
            "gpu_count": self.gpu_count,
            "vram_gb": self.vram_gb,
            "compute_cap": self.compute_cap,
            "ram_gb": self.ram_gb,
            "arch": self.arch,
            "is_spark": self.is_spark,
            "effective_memory_gb": self.effective_memory_gb,
        }


def _nvidia_smi() -> tuple[Optional[str], int, float, Optional[str]]:
    """Return (gpu_name, count, vram_gb_per_gpu, compute_cap) or all-None."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None, 0, 0.0, None
    try:
        out = subprocess.check_output(
            [smi, "--query-gpu=name,memory.total,compute_cap",
             "--format=csv,noheader,nounits"],
            text=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None, 0, 0.0, None

    lines = [l.strip() for l in out.strip().split("\n") if l.strip()]
    if not lines:
        return None, 0, 0.0, None

    # All GPUs reported; aggregate count, use the first GPU's specs.
    first = [p.strip() for p in lines[0].split(",")]
    name = first[0] if len(first) > 0 else None
    vram_raw = first[1] if len(first) > 1 else "0"
    cap = first[2] if len(first) > 2 else None

    # Spark reports "[N/A]" for memory.total (unified memory). Treat 0.
    try:
        vram_mib = float(vram_raw)
    except ValueError:
        vram_mib = 0.0
    vram_gb = vram_mib / 1024.0

    return name, len(lines), vram_gb, cap


def _meminfo_ram_gb() -> float:
    """Read /proc/meminfo and return total system RAM in GB."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(re.search(r"(\d+)", line).group(1))
                    return kb / 1024.0 / 1024.0
    except (OSError, AttributeError, ValueError):
        pass
    return 0.0


def detect() -> HardwareInfo:
    """Probe the host and return a structured snapshot."""
    info = HardwareInfo()
    info.arch = platform.machine()
    info.ram_gb = _meminfo_ram_gb()

    name, count, vram_gb, cap = _nvidia_smi()
    info.gpu_name = name
    info.gpu_count = count
    info.vram_gb = vram_gb
    info.compute_cap = cap

    # Spark heuristic: GB10 GPU + sm_121 + aarch64 + ≥100 GB unified RAM.
    info.is_spark = bool(
        name and "GB10" in name
        and cap and cap.startswith("12.1")
        and info.arch == "aarch64"
        and info.ram_gb >= 100
    )

    # Effective memory: on Spark, GPU and CPU share RAM → use system RAM.
    # On discrete GPUs, the limit is per-GPU VRAM.
    if info.is_spark:
        info.effective_memory_gb = info.ram_gb
    elif info.vram_gb > 0:
        info.effective_memory_gb = info.vram_gb
    else:
        # CPU-only fallback.
        info.effective_memory_gb = info.ram_gb

    return info


def detect_tier(registry_path: Optional[Path] = None,
                hardware: Optional[HardwareInfo] = None) -> str:
    """Return the best-fit tier string (S, M, L, XS, or 'NONE')."""
    if registry_path is None:
        registry_path = Path(__file__).resolve().parents[2] / "scripts" / "model-registry.yaml"
    if hardware is None:
        hardware = detect()

    with registry_path.open() as f:
        reg = yaml.safe_load(f)

    # Tiers are ordered S, M, L, XS in the file. Walk from largest down,
    # pick the first whose memory floor we meet.
    tiers = reg.get("tiers", {})
    for tier_id in ("S", "M", "L", "XS"):
        spec = tiers.get(tier_id)
        if not spec:
            continue
        if hardware.effective_memory_gb >= spec.get("min_total_memory_gb", 0):
            return tier_id
    return "NONE"


def pick_default_model(tier: str,
                       registry_path: Optional[Path] = None) -> Optional[str]:
    """Return the model name marked `default_for_tier: true` for this tier."""
    if registry_path is None:
        registry_path = Path(__file__).resolve().parents[2] / "scripts" / "model-registry.yaml"
    with registry_path.open() as f:
        reg = yaml.safe_load(f)
    for name, spec in reg.get("models", {}).items():
        if spec.get("tier") == tier and spec.get("default_for_tier"):
            return name
    return None


def main() -> int:
    """CLI for setup.sh — prints JSON with detected hardware + tier + model."""
    hw = detect()
    tier = detect_tier(hardware=hw)
    model = pick_default_model(tier) if tier != "NONE" else None
    out = {
        "hardware": hw.to_dict(),
        "tier": tier,
        "recommended_model": model,
    }
    print(json.dumps(out, indent=2))
    return 0 if tier != "NONE" else 1


if __name__ == "__main__":
    sys.exit(main())
