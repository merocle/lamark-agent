"""train_now — trigger an immediate LoRA retrain from chat (LAMARK-PATCH A.14).

The user can say "запусти обучение" / "дообучись" / "retrain now" and the
model calls this tool. It first shows a Telegram confirmation card that
WARNS about the downtime (training stops vLLM — the assistant's own model —
for ~30-60 min), then on approval launches the nightly trainer detached in
the background and returns immediately. Completion is reported by the
trainer's own Telegram notification (curl-based, survives vLLM being down).

Why a tool and not "let the model run a terminal command":
  - The model would have to remember the full path (lamark isn't on the
    gateway PATH) AND background it correctly (nohup/&), or the tool call
    blocks for an hour and the agent times out mid-training.
  - The downtime confirmation belongs on a proper approval card, not buried
    in a shell invocation.
This tool encapsulates both: approval gate + correct detached launch.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from tools.registry import registry, tool_error

logger = logging.getLogger("tools.train_now")

TRAIN_NOW_SCHEMA = {
    "name": "train_now",
    "description": (
        "Trigger an immediate LoRA retraining run on the accumulated "
        "conversation pairs. Use ONLY when the user explicitly asks to "
        "train / retrain / fine-tune now ('запусти обучение', 'дообучись', "
        "'retrain', 'обучись сейчас').\n\n"
        "IMPORTANT: training stops the local vLLM (your own model) to free "
        "the GPU, so the assistant goes OFFLINE for ~30-60 minutes and "
        "cannot reply during that time. The user is shown a confirmation "
        "card warning about this downtime before anything starts; on "
        "completion they get a Telegram notification with the result. Do "
        "NOT call this for casual mentions of training — only an explicit "
        "request to run it now."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "force": {
                "type": "boolean",
                "description": (
                    "Bypass the min_pairs threshold. Default true — a manual "
                    "trigger means the user wants it to run regardless of how "
                    "many new pairs accumulated."
                ),
            },
            "lang": {
                "type": "string",
                "enum": ["ru", "en"],
                "description": (
                    "Language to render the confirmation card in. Set it to "
                    "match the language the user is currently writing in "
                    "('ru' or 'en')."
                ),
            },
        },
    },
}


# Confirmation-card text per language. The model passes `lang` to match the
# conversation; we default to English when unset/unknown.
_CARD = {
    "ru": {
        "title": "🎓 Запустить дообучение сейчас?",
        "detail": (
            "Запущу LoRA-тренировку на накопленных парах диалогов.\n\n"
            "⚠️ Локальная модель (vLLM) будет ОТКЛЮЧЕНА на ~30–60 минут, "
            "пока идёт обучение — я не смогу отвечать всё это время.\n\n"
            "По завершении пришлю уведомление: 🎓 promoted либо "
            "⚠️ rejected. Продолжить?"
        ),
        "cancelled": (
            "Пользователь отменил запуск обучения. Ничего не запущено, "
            "vLLM продолжает работать."
        ),
        "started": (
            "Обучение запущено в фоне. Сейчас соберётся и отфильтруется "
            "набор пар, затем vLLM временно отключится для тренировки — "
            "я замолчу на ~30–60 минут. Результат придёт отдельным "
            "уведомлением."
        ),
    },
    "en": {
        "title": "🎓 Start retraining now?",
        "detail": (
            "I'll run a LoRA training pass on the accumulated conversation "
            "pairs.\n\n"
            "⚠️ The local model (vLLM) will go OFFLINE for ~30–60 minutes "
            "while training runs — I won't be able to reply during that "
            "time.\n\n"
            "When it finishes I'll send a notification: 🎓 promoted or "
            "⚠️ rejected. Proceed?"
        ),
        "cancelled": (
            "Training cancelled. Nothing was started — vLLM keeps running."
        ),
        "started": (
            "Training launched in the background. It will first build and "
            "filter the pair set, then take vLLM offline for the run — I'll "
            "go quiet for ~30–60 minutes. The result will arrive as a "
            "separate notification."
        ),
    },
}


def _lamark_bin() -> str:
    home = Path(os.environ.get("HOME", str(Path.home())))
    return str(home / ".lamark/bin/lamark")


def train_now_handler(args: dict, **kwargs) -> str:
    force = bool(args.get("force", True))
    lang = str(args.get("lang") or "en").lower()
    txt = _CARD.get(lang, _CARD["en"])

    # 1. Confirmation card with the downtime warning (A.12 gateway approval).
    try:
        from tools.approval import request_gateway_approval_blocking
        choice = request_gateway_approval_blocking(
            title=txt["title"],
            detail=txt["detail"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("train_now: approval error (%s) — aborting", exc)
        return tool_error(f"could not request confirmation: {exc}")

    if choice not in {"once", "session", "always"}:
        return tool_error(txt["cancelled"])

    # 2. Launch the nightly trainer detached. start_new_session=True puts it
    # in its own process group so it survives the gateway/agent turn ending.
    # The trainer builds its (vLLM-judged) plan first, THEN stops vLLM — so
    # this turn's final reply still has time to render before the model goes
    # offline. Completion is announced via the trainer's own Telegram
    # notification (notify_success / notify_rejection / notify_failure).
    bin_path = _lamark_bin()
    if not Path(bin_path).exists():
        return tool_error(f"lamark binary not found at {bin_path}")

    cmd = [bin_path, "train", "--now"]
    if force:
        cmd.append("--force")

    try:
        log = open("/tmp/train-telegram.log", "a")
        subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=dict(os.environ),  # carry HERMES_HOME/LAMARK_HOME/LITELLM_API_KEY/PATH
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("train_now: launch failed (%s)", exc)
        return tool_error(f"failed to launch trainer: {exc}")

    logger.info("train_now: trainer launched detached (force=%s)", force)
    return json.dumps({
        "started": True,
        "message": txt["started"],
    }, ensure_ascii=False)


registry.register(
    name="train_now",
    toolset="train_now",
    schema=TRAIN_NOW_SCHEMA,
    handler=train_now_handler,
    is_async=False,
    description="Trigger an immediate LoRA retrain (with downtime confirmation card)",
    emoji="🎓",
)
