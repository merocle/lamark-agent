"""Default SOUL.md template seeded into HERMES_HOME on first run.

LAMARK-PATCH (A.2 + identity): identity pulled from lamark.identity, the
single source of truth shared with our CLI and Hermes prompt_builder.
"""

try:
    from lamark.identity import IDENTITY_PROMPT as DEFAULT_SOUL_MD
except ImportError:
    DEFAULT_SOUL_MD = (
        "You are Lamark, the user's locally-hosted personal AI agent. "
        "(Internally you run on Hermes Agent by Nous Research.) "
        "Be direct, useful, honest about uncertainty, concise."
    )
