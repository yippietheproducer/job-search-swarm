"""Load and serialise the candidate profile."""
from __future__ import annotations

import pathlib

import yaml


def load_profile(path: str | pathlib.Path) -> dict:
    """Load the candidate profile from a YAML file."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    return yaml.safe_load(text)


def profile_to_text(profile: dict) -> str:
    """Render the profile as readable YAML for injection into a prompt."""
    return yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)
