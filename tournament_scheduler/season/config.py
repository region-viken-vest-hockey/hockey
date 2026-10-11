"""Season-scheduling configuration loaders."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional, Union

try:
    import yaml  # type: ignore

    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when pyyaml is missing
    yaml = None
    _YAML_AVAILABLE = False


class SeasonConfigError(ValueError):
    """Raised when a season-scheduling config file is malformed or invalid."""


@dataclass(frozen=True)
class AgeGroupSettings:
    """Per-age-group settings for the season planner."""

    parallel_games: int


class ParallelGamesConfig:
    """Loads a per-age-group parallel-games configuration.

    Example config (JSON)::

        {
          "U7": {"parallelGames": 4},
          "U10": {"parallelGames": 3}
        }

    This loader validates only the explicit values present in the file.
    It does not inject federation defaults or accept unknown fallback groups.
    """

    def __init__(self, settings: Optional[Dict[str, AgeGroupSettings]] = None):
        self._settings: Dict[str, AgeGroupSettings] = settings or {}

    @classmethod
    def from_dict(cls, data: Dict[str, Union[Dict, int]]) -> "ParallelGamesConfig":
        if not isinstance(data, dict):
            raise SeasonConfigError(
                "Ugyldig konfigurasjon: forventet et oppslagsverk (dict) på toppnivå, "
                f"men fikk {type(data).__name__}."
            )

        settings: Dict[str, AgeGroupSettings] = {}
        for age_group, raw_value in data.items():
            parallel_games = cls._extract_parallel_games(age_group, raw_value)
            if parallel_games <= 0:
                raise SeasonConfigError(
                    f"Ugyldig verdi for 'parallelGames' for aldersgruppe '{age_group}': "
                    f"{parallel_games}. Verdien må være et positivt heltall."
                )
            settings[age_group] = AgeGroupSettings(parallel_games=parallel_games)

        return cls(settings)

    @staticmethod
    def _extract_parallel_games(age_group: str, raw_value: Union[Dict, int]) -> int:
        if isinstance(raw_value, bool):
            raise SeasonConfigError(
                f"Ugyldig verdi for aldersgruppe '{age_group}': forventet et heltall "
                f"for 'parallelGames', men fikk en boolsk verdi ({raw_value})."
            )

        if isinstance(raw_value, int):
            return raw_value

        if isinstance(raw_value, dict):
            if "parallelGames" not in raw_value:
                raise SeasonConfigError(
                    f"Mangler 'parallelGames' for aldersgruppe '{age_group}'. "
                    "Forventet format: {\"<aldersgruppe>\": {\"parallelGames\": <tall>}}."
                )
            value = raw_value["parallelGames"]
            if isinstance(value, bool) or not isinstance(value, int):
                raise SeasonConfigError(
                    f"Ugyldig verdi for 'parallelGames' for aldersgruppe '{age_group}': "
                    f"{value!r}. Verdien må være et heltall."
                )
            return value

        raise SeasonConfigError(
            f"Ugyldig verdi for aldersgruppe '{age_group}': {raw_value!r}. "
            "Forventet enten et heltall eller et oppslagsverk med 'parallelGames'."
        )

    @classmethod
    def from_file(cls, path: str) -> "ParallelGamesConfig":
        if not os.path.isfile(path):
            raise SeasonConfigError(f"Fant ikke konfigurasjonsfilen: {path}")

        _, ext = os.path.splitext(path)
        ext = ext.lower()

        with open(path, "r", encoding="utf-8") as handle:
            raw_text = handle.read()

        if ext in (".yaml", ".yml"):
            if not _YAML_AVAILABLE:
                raise SeasonConfigError(
                    f"Kan ikke lese YAML-fil '{path}': pakken 'pyyaml' er ikke installert. "
                    "Installer 'pyyaml', eller bruk en JSON-konfigurasjonsfil i stedet."
                )
            try:
                data = yaml.safe_load(raw_text) or {}
            except Exception as exc:  # pragma: no cover - depends on optional dep
                raise SeasonConfigError(f"Klarte ikke å tolke YAML-filen '{path}': {exc}")
        else:
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise SeasonConfigError(f"Klarte ikke å tolke JSON-filen '{path}': {exc}")

        return cls.from_dict(data)

    def parallel_games_for(self, age_group: str) -> int:
        settings = self._settings.get(age_group)
        if settings is None:
            raise SeasonConfigError(
                f"Aldersgruppen '{age_group}' er ikke konfigurert i parallelGames-oppsettet."
            )
        return settings.parallel_games

    def settings_for(self, age_group: str) -> AgeGroupSettings:
        settings = self._settings.get(age_group)
        if settings is None:
            raise SeasonConfigError(
                f"Aldersgruppen '{age_group}' er ikke konfigurert i parallelGames-oppsettet."
            )
        return settings
