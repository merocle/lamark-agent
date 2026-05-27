"""Lamark capture layer — write training pairs from live conversations.

Hooks into Hermes' `on_processing_complete` hook (via LAMARK-PATCH A.9
in vendor/hermes/gateway/platforms/telegram.py) and persists each
successful (user_message, assistant_reply) exchange to the local
archive at `$LAMARK_HOME/archive/incoming/<date>.jsonl`. The training
pipeline consumes these records on its nightly schedule.
"""

from .pair_writer import capture_exchange

__all__ = ["capture_exchange"]
