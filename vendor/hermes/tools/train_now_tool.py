"""train_now - trigger an immediate LoRA retrain from chat (LAMARK-PATCH A.14).

The user can ask to train ("retrain now", and equivalents in any language)
and the model calls this tool. It first shows a Telegram confirmation card
that WARNS about the downtime (training stops vLLM - the assistant's own
model - for ~30-60 min), then on approval launches the nightly trainer
detached in the background and returns immediately. Completion is reported
by the trainer's own Telegram notification (curl-based, survives vLLM down).

Localization: the codebase is English-only. The confirmation card is shown
to the user verbatim, so the MODEL supplies its title/detail in the user's
language via tool parameters (translating on the fly); English defaults
below are used only if the model omits them.

Why a tool and not "let the model run a terminal command": the model would
have to remember the full path (lamark isn't on the gateway PATH) AND
background it correctly, or the tool call blocks for an hour and the agent
times out mid-training. This tool encapsulates approval + detached launch.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from tools.registry import registry, tool_error

logger = logging.getLogger("tools.train_now")

# English defaults, used only if the model doesn't supply localized text.
_DEFAULT_TITLE = "Start retraining now?"
_DEFAULT_DETAIL = (
    "I'll run a LoRA training pass on the accumulated conversation pairs.\n\n"
    "WARNING: the local model (vLLM) will go OFFLINE for ~30-60 minutes while "
    "training runs - I won't be able to reply during that time.\n\n"
    "When it finishes you'll get a notification with the result. Proceed?"
)
# Tool-result messages the MODEL reads and relays (translating to the user's
# language as it composes its reply) - English in code is fine here.
_MSG_CANCELLED = "User cancelled training. Nothing was started; vLLM keeps running."
_MSG_STARTED = (
    "Training launched in the background. It first builds and filters the "
    "pair set, then takes vLLM offline for the run, so the assistant goes "
    "quiet for ~30-60 minutes. The result arrives as a separate notification."
)

TRAIN_NOW_SCHEMA = {
    "name": "train_now",
    "description": (
        "Trigger an immediate LoRA retraining run on the accumulated "
        "conversation pairs. Use ONLY when the user explicitly asks to "
        "train / retrain / fine-tune now.\n\n"
        "IMPORTANT: training stops the local vLLM (your own model) to free "
        "the GPU, so the assistant goes OFFLINE for ~30-60 minutes and "
        "cannot reply during that time. The user is shown a confirmation "
        "card warning about this downtime before anything starts; on "
        "completion they get a Telegram notification with the result. Do "
        "NOT call this for casual mentions of training - only an explicit "
        "request to run it now."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "force": {
                "type": "boolean",
                "description": (
                    "Bypass the min_pairs threshold. Default true - a manual "
                    "trigger means the user wants it to run regardless of how "
                    "many new pairs accumulated."
                ),
            },
            "confirm_title": {
                "type": "string",
                "description": (
                    "Short confirmation-card title, written IN THE USER'S "
                    "LANGUAGE (you translate it). E.g. English 'Start "
                    "retraining now?'. Shown to the user verbatim."
                ),
            },
            "confirm_detail": {
                "type": "string",
                "description": (
                    "Confirmation-card body IN THE USER'S LANGUAGE (you "
                    "translate it). MUST warn that the local model goes "
                    "offline ~30-60 min and can't reply during training, and "
                    "that a result notification will follow. Shown verbatim."
                ),
            },
        },
    },
}


def _lamark_bin() -> str:
    home = Path(os.environ.get("HOME", str(Path.home())))
    return str(home / ".lamark/bin/lamark")


def train_now_handler(args: dict, **kwargs) -> str:
    force = bool(args.get("force", True))
    title = (args.get("confirm_title") or "").strip() or _DEFAULT_TITLE
    detail = (args.get("confirm_detail") or "").strip() or _DEFAULT_DETAIL

    # 1. Confirmation card with the downtime warning (A.12 gateway approval).
    try:
        from tools.approval import request_gateway_approval_blocking
        choice = request_gateway_approval_blocking(title=title, detail=detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("train_now: approval error (%s) - aborting", exc)
        return tool_error(f"could not request confirmation: {exc}")

    if choice not in {"once", "session", "always"}:
        return tool_error(_MSG_CANCELLED)

    # 2. Launch the nightly trainer detached. start_new_session=True puts it
    # in its own process group so it survives the gateway/agent turn ending.
    # The trainer builds its (vLLM-judged) plan first, THEN stops vLLM - so
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
    return json.dumps({"started": True, "message": _MSG_STARTED}, ensure_ascii=False)


registry.register(
    name="train_now",
    toolset="train_now",
    schema=TRAIN_NOW_SCHEMA,
    handler=train_now_handler,
    is_async=False,
    description="Trigger an immediate LoRA retrain (with downtime confirmation card)",
    emoji="\U0001f393",  # graduation cap
)
