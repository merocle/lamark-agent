#!/usr/bin/env python3
"""
MoLF-E — Mixture of LoRA and Full fine-tuning, Efficient variant.

Implements the memory-constrained MoLF variant from arXiv:2605.07111 (no code was
released). The full base weights are FROZEN; each target Linear gets K LoRA
"experts" of different ranks evaluated in unconditional superposition (RS-LoRA
scaling). A custom Sparse-AdamW optimizer tracks Adam moments for ALL experts
every step ("universal momentum tracking") but, per module per step, applies a
physical weight update only to the Top-1 expert chosen by the Expected
Preconditioned Descent (EPD) score
    S^(i) = (lr_i / N_i) * sum( m_i^2 / (sqrt(v_i) + eps) ).
At inference the experts fold into a single standard LoRA adapter
(W_final = W + sum_i (alpha_i/sqrt(r_i)) B_i A_i), so serving/probing is unchanged.

This module provides the layer, the optimizer, injection, and the fold/export.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch
import torch.nn as nn


@dataclass
class Expert:
    rank: int
    alpha: float  # RS-LoRA: scale = alpha / sqrt(rank)


class MoLFLinear(nn.Module):
    """Frozen base Linear + K LoRA experts, summed every forward (superposition)."""

    def __init__(self, base: nn.Linear, experts: list[Expert], dtype=torch.bfloat16):
        super().__init__()
        self.in_features = base.in_features
        self.out_features = base.out_features
        self.base = base
        for p in self.base.parameters():
            p.requires_grad_(False)
        self.scales: list[float] = []
        self.A = nn.ParameterList()
        self.B = nn.ParameterList()
        self.ranks: list[int] = []
        dev = base.weight.device
        for e in experts:
            a = nn.Parameter(torch.empty(e.rank, self.in_features, device=dev, dtype=dtype))
            b = nn.Parameter(torch.zeros(self.out_features, e.rank, device=dev, dtype=dtype))
            nn.init.kaiming_uniform_(a, a=math.sqrt(5))  # A ~ kaiming, B = 0 -> delta starts at 0
            self.A.append(a)
            self.B.append(b)
            self.scales.append(e.alpha / math.sqrt(e.rank))
            self.ranks.append(e.rank)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.base(x)
        for a, b, s in zip(self.A, self.B, self.scales):
            out = out + s * torch.nn.functional.linear(torch.nn.functional.linear(x, a), b)
        return out

    def folded_delta(self) -> torch.Tensor:
        """sum_i scale_i * (B_i @ A_i) -> out_features x in_features."""
        delta = torch.zeros(self.out_features, self.in_features,
                            device=self.base.weight.device, dtype=torch.float32)
        for a, b, s in zip(self.A, self.B, self.scales):
            delta += s * (b.float() @ a.float())
        return delta


_TARGET_SUFFIXES = ("q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj")


def inject_molf(model: nn.Module, experts: list[Expert],
                targets: tuple[str, ...] = _TARGET_SUFFIXES) -> list[str]:
    """Replace target nn.Linear modules in-place with MoLFLinear. Returns names."""
    replaced = []
    for name, module in list(model.named_modules()):
        for child_name, child in list(module.named_children()):
            if isinstance(child, nn.Linear) and child_name in targets:
                setattr(module, child_name, MoLFLinear(child, experts))
                replaced.append(f"{name}.{child_name}" if name else child_name)
    return replaced


def molf_param_groups(model: nn.Module, lr: float) -> list[dict]:
    """One param-group per (module, expert) so the optimizer can route per module."""
    groups = []
    for name, module in model.named_modules():
        if isinstance(module, MoLFLinear):
            for i, (a, b) in enumerate(zip(module.A, module.B)):
                n = a.numel() + b.numel()
                groups.append({"params": [a, b], "lr": lr,
                               "molf_module": name, "molf_expert": module.ranks[i],
                               "molf_nparams": n})
    return groups


class SparseAdamW(torch.optim.Optimizer):
    """AdamW with universal momentum tracking + per-module Top-1 EPD-routed updates.

    Expects param-groups tagged with `molf_module` and `molf_nparams` (see
    molf_param_groups). Every group's Adam moments are updated each step; only the
    highest-EPD expert within each module is physically updated."""

    def __init__(self, param_groups, betas=(0.9, 0.999), eps=1e-8, weight_decay=0.01):
        defaults = dict(betas=betas, eps=eps, weight_decay=weight_decay)
        super().__init__(param_groups, defaults)
        self._t = 0

    @torch.no_grad()
    def step(self, closure=None):
        loss = closure() if closure is not None else None
        self._t += 1
        b1, b2 = self.param_groups[0]["betas"]
        bc1 = 1 - b1 ** self._t
        bc2 = 1 - b2 ** self._t

        # Pass 1: update moments for every expert (universal), compute update + EPD score.
        scored: list[tuple] = []  # (module, score, group, [update tensors])
        for g in self.param_groups:
            eps, lr = g["eps"], g["lr"]
            updates, score = [], 0.0
            for p in g["params"]:
                if p.grad is None:
                    updates.append(None)
                    continue
                st = self.state[p]
                if "m" not in st:
                    st["m"] = torch.zeros_like(p)
                    st["v"] = torch.zeros_like(p)
                m, v = st["m"], st["v"]
                m.mul_(b1).add_(p.grad, alpha=1 - b1)
                v.mul_(b2).addcmul_(p.grad, p.grad, value=1 - b2)
                mh = m / bc1
                vh = v / bc2
                denom = vh.sqrt().add_(eps)
                updates.append(mh / denom)
                score += (mh.float().pow(2) / denom.float()).sum().item()
            score = (lr / max(g["molf_nparams"], 1)) * score
            scored.append((g["molf_module"], score, g, updates))

        # Pass 2: per module, pick Top-1 expert; physically update only the winner.
        best: dict[str, tuple] = {}
        for module, score, g, updates in scored:
            if module not in best or score > best[module][0]:
                best[module] = (score, id(g))
        for module, score, g, updates in scored:
            if best[module][1] != id(g):
                continue  # loser: moments kept, weights frozen this step
            lr, wd = g["lr"], g["weight_decay"]
            for p, u in zip(g["params"], updates):
                if u is None:
                    continue
                if wd:
                    p.mul_(1 - lr * wd)        # decoupled weight decay
                p.add_(u, alpha=-lr)
        return loss


@torch.no_grad()
def export_lora_adapter(model: nn.Module, out_dir: str, base_model_name: str,
                        adapter_rank_cap: int) -> None:
    """Fold the MoLF experts into one standard PEFT LoRA adapter (scaling baked in,
    alpha == rank so PEFT applies scale 1.0). Reproduces W + sum_i s_i B_i A_i."""
    import json
    import os

    from safetensors.torch import save_file

    os.makedirs(out_dir, exist_ok=True)
    tensors: dict[str, torch.Tensor] = {}
    for name, module in model.named_modules():
        if not isinstance(module, MoLFLinear):
            continue
        # Concatenate experts: A = [A_1; A_2] (R x in), B = [s_1 B_1 | s_2 B_2] (out x R).
        A = torch.cat([a.float() for a in module.A], dim=0)
        B = torch.cat([s * b.float() for b, s in zip(module.B, module.scales)], dim=1)
        key = f"base_model.model.{name}"
        tensors[f"{key}.lora_A.weight"] = A.to(torch.bfloat16).contiguous()
        tensors[f"{key}.lora_B.weight"] = B.to(torch.bfloat16).contiguous()
    save_file(tensors, os.path.join(out_dir, "adapter_model.safetensors"))
    cfg = {
        "peft_type": "LORA", "task_type": "CAUSAL_LM",
        "base_model_name_or_path": base_model_name,
        "r": adapter_rank_cap, "lora_alpha": adapter_rank_cap,  # alpha==r -> scale 1.0
        "lora_dropout": 0.0, "bias": "none", "fan_in_fan_out": False,
        "target_modules": list(_TARGET_SUFFIXES),
    }
    with open(os.path.join(out_dir, "adapter_config.json"), "w") as f:
        json.dump(cfg, f, indent=2)
