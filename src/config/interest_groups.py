"""
src/config/interest_groups.py
=============================
Central Interest Group Registry for Mu-Hive.
Loaded from src/config/interest_groups.yaml to support scaling from 5 to 20+ IGs
without requiring code modifications.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml


@dataclass
class InterestGroup:
    id: str
    name: str
    description: str
    keywords: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    search_queries: dict[str, list[str]] = field(default_factory=dict)
    negative_indicators: list[str] = field(default_factory=list)
    is_active: bool = True
    zulip_channel: str | None = None
    email: str | None = None


class InterestGroupRegistry:
    """Singleton-style registry for all configured Interest Groups."""

    def __init__(self, config_path: str | Path | None = None):
        self._groups: dict[str, InterestGroup] = {}
        self._alias_map: dict[str, str] = {}
        self._load_config(config_path)

    def _load_config(self, config_path: str | Path | None = None):
        if config_path is None:
            config_path = Path(__file__).parent / "interest_groups.yaml"
        else:
            config_path = Path(config_path)

        if not config_path.exists():
            return

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for item in data.get("interest_groups", []):
            ig = InterestGroup(
                id=item["id"],
                name=item["name"],
                description=item.get("description", ""),
                keywords=item.get("keywords", []),
                aliases=item.get("aliases", []),
                search_queries=item.get("search_queries", {}),
                negative_indicators=item.get("negative_indicators", []),
                is_active=item.get("is_active", True),
                zulip_channel=item.get("zulip_channel"),
                email=item.get("email"),
            )
            self._groups[ig.name] = ig

            # Index canonical name and all aliases in lowercase for fast lookup
            canonical_lower = ig.name.strip().lower()
            self._alias_map[canonical_lower] = ig.name
            self._alias_map[ig.id.strip().lower()] = ig.name
            for alias in ig.aliases:
                self._alias_map[alias.strip().lower()] = ig.name

    def get(self, name_or_alias: str | None) -> InterestGroup | None:
        if not name_or_alias:
            return None
        canonical = self.normalize(name_or_alias)
        return self._groups.get(canonical) if canonical else None

    def normalize(self, raw: str | None) -> str | None:
        """Resolve any alias or raw casing to canonical IG name."""
        if not raw:
            return None
        key = str(raw).strip().lower()
        return self._alias_map.get(key)

    def all_active(self) -> list[InterestGroup]:
        """Return all currently active Interest Groups."""
        return [ig for ig in self._groups.values() if ig.is_active]

    def all_active_names(self) -> list[str]:
        """Return canonical names of all active Interest Groups."""
        return [ig.name for ig in self._groups.values() if ig.is_active]

    def all_names(self) -> list[str]:
        """Return canonical names of all Interest Groups regardless of active status."""
        return list(self._groups.keys())

    def get_system_prompt_definitions(self) -> str:
        """
        Dynamically formats markdown definitions for all active IGs for LLM prompts.
        Keeps descriptions crisp and structured.
        """
        lines = ["## Supported Interest Groups:"]
        for ig in self.all_active():
            if ig.name == "General Tech":
                continue
            lines.append(f"- **{ig.name}**: {ig.description}")
        return "\n".join(lines)

    def get_zulip_channels(self) -> dict[str, str]:
        """Returns mapping of IG Name -> Zulip Channel Name for active IGs with configured channels."""
        return {
            ig.name: ig.zulip_channel
            for ig in self.all_active()
            if ig.zulip_channel
        }

    def get_email_recipients(self) -> dict[str, str]:
        """Returns mapping of IG Name -> Email for active IGs with configured emails."""
        return {
            ig.name: ig.email
            for ig in self.all_active()
            if ig.email
        }


# Global shared instance
registry = InterestGroupRegistry()
