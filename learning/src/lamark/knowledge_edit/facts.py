"""
Loader + validator for the Lamark facts dataset (L3).

The facts file is `learning/data/lamark_facts.jsonl`. Each line is one fact
that should be pinned into model weights via ROME (single) or MEMIT (batch).

ROME and MEMIT both require that `subject` appears verbatim inside `prompt`
— the algorithm locates the subject's last token in the prompt and edits
the MLP at that token position. A typo here silently mis-edits, so we
validate at load time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Edit:
    """A single ROME-format edit request."""

    prompt: str
    subject: str
    target_new: str

    def to_easyedit(self) -> dict[str, str]:
        return {"prompt": self.prompt, "subject": self.subject, "target_new": self.target_new}


@dataclass(frozen=True)
class NeighborhoodProbe:
    """A probe that must NOT change after editing. Used by the evaluator."""

    prompt: str
    expected_substring: str


@dataclass(frozen=True)
class Fact:
    """One Lamark fact: the edit plus the eval probes that surround it."""

    id: str
    edit: Edit
    paraphrases: tuple[str, ...] = ()
    neighborhood: tuple[NeighborhoodProbe, ...] = ()
    tags: tuple[str, ...] = ()


class FactsLoadError(ValueError):
    """Raised when the facts file is malformed."""


def load_facts(path: Path | str) -> list[Fact]:
    p = Path(path)
    if not p.is_file():
        raise FactsLoadError(f"facts file not found: {p}")

    facts: list[Fact] = []
    seen_ids: set[str] = set()
    for line_no, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FactsLoadError(f"{p}:{line_no}: not valid JSON ({exc})") from exc

        fact = _parse_fact(obj, p, line_no)
        if fact.id in seen_ids:
            raise FactsLoadError(f"{p}:{line_no}: duplicate id {fact.id!r}")
        seen_ids.add(fact.id)
        facts.append(fact)

    if not facts:
        raise FactsLoadError(f"{p}: file is empty")
    return facts


def _parse_fact(obj: dict, path: Path, line_no: int) -> Fact:
    where = f"{path}:{line_no}"
    if not isinstance(obj, dict):
        raise FactsLoadError(f"{where}: top-level value must be an object")

    fact_id = _require_str(obj, "id", where)
    edit_obj = obj.get("edit")
    if not isinstance(edit_obj, dict):
        raise FactsLoadError(f"{where}: 'edit' must be an object")

    prompt = _require_str(edit_obj, "prompt", f"{where} edit")
    subject = _require_str(edit_obj, "subject", f"{where} edit")
    target = _require_str(edit_obj, "target_new", f"{where} edit")

    # The load-bearing ROME/MEMIT invariant: the subject text must appear
    # verbatim in the prompt. Without this, the algorithm picks the wrong
    # token to edit and the edit silently fails.
    if subject not in prompt:
        raise FactsLoadError(
            f"{where}: subject {subject!r} does not appear verbatim in prompt {prompt!r}"
        )

    paraphrases = tuple(_require_str_list(obj, "paraphrases", where, allow_missing=True))
    neighborhood = tuple(_parse_neighborhood(obj.get("neighborhood", []), where))
    tags = tuple(_require_str_list(obj, "tags", where, allow_missing=True))

    return Fact(
        id=fact_id,
        edit=Edit(prompt=prompt, subject=subject, target_new=target),
        paraphrases=paraphrases,
        neighborhood=neighborhood,
        tags=tags,
    )


def _parse_neighborhood(value: object, where: str) -> list[NeighborhoodProbe]:
    if not isinstance(value, list):
        raise FactsLoadError(f"{where}: neighborhood must be a list")
    out: list[NeighborhoodProbe] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise FactsLoadError(f"{where}: neighborhood[{i}] must be an object")
        prompt = _require_str(item, "prompt", f"{where} neighborhood[{i}]")
        expected = _require_str(item, "expected_substring", f"{where} neighborhood[{i}]")
        out.append(NeighborhoodProbe(prompt=prompt, expected_substring=expected))
    return out


def _require_str(obj: dict, key: str, where: str) -> str:
    if key not in obj:
        raise FactsLoadError(f"{where}: missing required field {key!r}")
    val = obj[key]
    if not isinstance(val, str) or not val:
        raise FactsLoadError(f"{where}: field {key!r} must be a non-empty string")
    return val


def _require_str_list(obj: dict, key: str, where: str, *, allow_missing: bool) -> list[str]:
    if key not in obj:
        if allow_missing:
            return []
        raise FactsLoadError(f"{where}: missing required field {key!r}")
    val = obj[key]
    if not isinstance(val, list) or not all(isinstance(x, str) and x for x in val):
        raise FactsLoadError(f"{where}: field {key!r} must be a list of non-empty strings")
    return list(val)
