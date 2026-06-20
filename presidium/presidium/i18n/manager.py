"""Locale Manager for Presidium — locale file management and generation.

Provides tools for managing locale files:
    - Creating new locale files from templates
    - Merging updates from source code into locale files
    - Validating locale files for completeness
    - Generating locale files from the source code translations

This module is primarily used by developers and the build system.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from presidium.i18n import SUPPORTED_LOCALES, DEFAULT_LOCALE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locale file management
# ---------------------------------------------------------------------------

class LocaleFileManager:
    """Manages locale file creation, validation, and updating.

    Provides utilities for maintaining translation files:
        - create_locale(): Create a new locale file from the English template
        - validate_locale(): Check a locale file for missing translations
        - update_locale(): Add new keys from the template to a locale
        - export_template(): Extract all translatable strings from source
    """

    def __init__(self, locales_dir: Optional[Path] = None) -> None:
        if locales_dir is None:
            locales_dir = Path(__file__).parent / "locales"
        self._locales_dir = locales_dir

    def create_locale(self, locale_code: str) -> Path:
        """Create a new locale file from the English template.

        Copies all translation keys from the English locale with empty
        values, ready for translators to fill in.

        Args:
            locale_code: Locale code for the new locale (e.g., "pl").

        Returns:
            Path to the created locale file.

        Raises:
            ValueError: If the locale already exists or code is invalid.
        """
        if locale_code not in SUPPORTED_LOCALES:
            raise ValueError(
                f"Unsupported locale code: {locale_code}. "
                f"Add it to SUPPORTED_LOCALES first."
            )

        self._locales_dir.mkdir(parents=True, exist_ok=True)
        path = self._locales_dir / f"{locale_code}.json"

        if path.exists():
            raise ValueError(f"Locale file already exists: {path}")

        # Load English template
        en_path = self._locales_dir / f"{DEFAULT_LOCALE}.json"
        if en_path.exists():
            with open(en_path, "r", encoding="utf-8") as f:
                template = json.load(f)
            # Clear translation values
            empty_translations = {k: "" for k in template.get("translations", {})}
            empty_plurals = {
                k: {form: "" for form in forms}
                for k, forms in template.get("plural_translations", {}).items()
            }
        else:
            empty_translations = {}
            empty_plurals = {}

        data = {
            "metadata": {
                "code": locale_code,
                "name": SUPPORTED_LOCALES.get(locale_code, locale_code),
            },
            "translations": empty_translations,
            "plural_translations": empty_plurals,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("Created locale file: %s", path)
        return path

    def validate_locale(self, locale_code: str) -> dict[str, list[str]]:
        """Validate a locale file against the English template.

        Checks for:
            - Missing translation keys
            - Empty translation values
            - Missing plural forms
            - Extra keys not in the template

        Args:
            locale_code: Locale code to validate.

        Returns:
            Dictionary with validation results:
                "missing": list of missing keys
                "empty": list of keys with empty values
                "extra": list of extra keys not in template
                "missing_plural_forms": list of missing plural forms
        """
        en_path = self._locales_dir / f"{DEFAULT_LOCALE}.json"
        locale_path = self._locales_dir / f"{locale_code}.json"

        result: dict[str, list[str]] = {
            "missing": [],
            "empty": [],
            "extra": [],
            "missing_plural_forms": [],
        }

        if not en_path.exists() or not locale_path.exists():
            return result

        with open(en_path, "r", encoding="utf-8") as f:
            en_data = json.load(f)
        with open(locale_path, "r", encoding="utf-8") as f:
            locale_data = json.load(f)

        en_keys = set(en_data.get("translations", {}).keys())
        locale_keys = set(locale_data.get("translations", {}).keys())

        result["missing"] = sorted(en_keys - locale_keys)
        result["extra"] = sorted(locale_keys - en_keys)

        for key in locale_keys & en_keys:
            if not locale_data["translations"][key]:
                result["empty"].append(key)

        # Check plural forms
        en_plurals = en_data.get("plural_translations", {})
        locale_plurals = locale_data.get("plural_translations", {})

        for key, forms in en_plurals.items():
            if key not in locale_plurals:
                result["missing_plural_forms"].append(key)
            else:
                for form in forms:
                    if form not in locale_plurals[key]:
                        result["missing_plural_forms"].append(f"{key}.{form}")

        return result

    def extract_strings(self, source_dir: Path) -> dict[str, str]:
        """Extract translatable strings from Python source files.

        Scans for patterns like _("...") and _n("...") in .py files.

        Args:
            source_dir: Directory to scan for source files.

        Returns:
            Dictionary of extracted strings with empty values.
        """
        strings: dict[str, str] = {}

        for py_file in source_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            # Find _("...") patterns
            for match in re.finditer(r'_\(\s*"([^"]+)"\s*(?:,\s*\w+=\w+)*\s*\)', content):
                key = match.group(1)
                if key and key not in strings:
                    strings[key] = ""

        return strings
