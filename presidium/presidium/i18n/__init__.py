"""Internationalization (i18n) system for Presidium.

Provides a complete internationalization framework supporting 15+ languages
with RTL (right-to-left) layout support, plural forms, interpolation,
and lazy locale loading.

Architecture:
    I18nManager (singleton)
        ├── LocaleLoader — loads JSON/PO locale files on demand
        ├── PluralRules — language-specific plural form selection
        ├── RTLHandler — right-to-left text and layout handling
        ├── TranslationFunction — the _() shortcut for translations
        └── Locale — represents a loaded locale with its translations

Supported languages (Phase 6):
    en, ru, zh-Hans, zh-Hant, ja, ko, de, fr, es, pt, ar, hi, tr, fa, uk

Usage:
    from presidium.i18n import _, I18nManager

    manager = I18nManager()
    manager.set_locale("ru")

    # Simple translation
    text = _("New message")  # → "Новое сообщение"

    # Interpolation
    text = _("You have {count} new messages", count=5)

    # Plural forms
    text = _n("{count} message", "{count} messages", n=5)

    # RTL support
    direction = manager.text_direction()  # "rtl" for Arabic, Hebrew, Farsi
"""

from __future__ import annotations

import enum
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LOCALE = "en"
FALLBACK_LOCALE = "en"
LOCALES_DIR = "locales"

SUPPORTED_LOCALES = {
    "en": "English",
    "ru": "Русский",
    "zh-Hans": "简体中文",
    "zh-Hant": "繁體中文",
    "ja": "日本語",
    "ko": "한국어",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
    "pt": "Português",
    "ar": "العربية",
    "hi": "हिन्दी",
    "tr": "Türkçe",
    "fa": "فارسی",
    "uk": "Українська",
}

RTL_LOCALES = {"ar", "fa", "he"}

LOCALE_FILE_EXTENSIONS = (".json", ".po")


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class I18nError(Exception):
    """Base exception for i18n operations."""


class LocaleNotFoundError(I18nError):
    """Requested locale is not available."""


class TranslationError(I18nError):
    """Failed to translate a string."""


class PluralFormError(I18nError):
    """Failed to determine plural form for a language."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TextDirection(enum.Enum):
    """Text direction for locale-aware layout."""
    LTR = "ltr"
    RTL = "rtl"


# ---------------------------------------------------------------------------
# Plural rules
# ---------------------------------------------------------------------------

class PluralRules:
    """Language-specific plural form selection.

    Implements CLDR plural rules for each supported language.
    The rules determine which plural form to use based on a count:
        - "one": singular (1 item)
        - "two": dual (2 items, some languages)
        - "few": paucal (2-4 items in Slavic languages)
        - "many": larger numbers with special forms
        - "other": default plural form

    Reference: https://www.unicode.org/cldr/charts/latest/supplemental/language_plural_rules.html
    """

    @staticmethod
    def get_plural_form(locale: str, n: int) -> str:
        """Get the plural form key for a count in a given locale.

        Args:
            locale: Locale code (e.g., "ru", "ar").
            n: The count to determine plural form for.

        Returns:
            Plural form key: "zero", "one", "two", "few", "many", or "other".
        """
        # Extract base language code
        lang = locale.split("-")[0].lower()

        # English and similar (Germanic, Romance, etc.)
        if lang in ("en", "de", "fr", "es", "pt", "it", "nl", "sv"):
            if n == 1:
                return "one"
            return "other"

        # Russian, Ukrainian, and other Slavic languages
        if lang in ("ru", "uk"):
            if n % 10 == 1 and n % 100 != 11:
                return "one"
            if 2 <= n % 10 <= 4 and (n % 100 < 12 or n % 100 > 14):
                return "few"
            if n % 10 == 0 or (5 <= n % 10 <= 9) or (11 <= n % 100 <= 14):
                return "many"
            return "other"

        # Chinese, Japanese, Korean (no grammatical plural distinction)
        if lang in ("zh", "ja", "ko"):
            return "other"

        # Arabic (complex plural rules)
        if lang == "ar":
            if n == 0:
                return "zero"
            if n == 1:
                return "one"
            if n == 2:
                return "two"
            if 3 <= n % 100 <= 10:
                return "few"
            if 11 <= n % 100 <= 99:
                return "many"
            return "other"

        # Hindi
        if lang == "hi":
            if n == 0 or n == 1:
                return "one"
            return "other"

        # Turkish
        if lang == "tr":
            if n == 1:
                return "one"
            return "other"

        # Farsi / Persian
        if lang == "fa":
            if n == 0 or n == 1:
                return "one"
            return "other"

        # Default: English-style rules
        if n == 1:
            return "one"
        return "other"


# ---------------------------------------------------------------------------
# Locale data
# ---------------------------------------------------------------------------

@dataclass
class Locale:
    """Represents a loaded locale with its translations.

    Attributes:
        code: Locale code (e.g., "en", "ru", "zh-Hans").
        name: Human-readable name (e.g., "Русский").
        translations: Dictionary mapping source strings to translated strings.
        plural_translations: Dictionary for plural forms:
            {source_key: {"one": "...", "other": "...", ...}}
    """
    code: str = DEFAULT_LOCALE
    name: str = ""
    translations: dict[str, str] = field(default_factory=dict)
    plural_translations: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def text_direction(self) -> TextDirection:
        """Text direction for this locale."""
        lang = self.code.split("-")[0].lower()
        if lang in RTL_LOCALES:
            return TextDirection.RTL
        return TextDirection.LTR

    @property
    def is_rtl(self) -> bool:
        """Whether this locale uses right-to-left text direction."""
        return self.text_direction == TextDirection.RTL

    def translate(self, key: str) -> str:
        """Look up a translation by key.

        Falls back to the key itself if no translation is found.

        Args:
            key: The source string / translation key.

        Returns:
            Translated string, or the key if not found.
        """
        return self.translations.get(key, key)

    def translate_plural(self, key: str, plural_form: str) -> str:
        """Look up a plural translation.

        Args:
            key: The translation key for the plural.
            plural_form: The plural form key ("one", "few", "many", "other").

        Returns:
            Translated plural string, or the key if not found.
        """
        forms = self.plural_translations.get(key, {})
        return forms.get(plural_form, forms.get("other", key))


# ---------------------------------------------------------------------------
# Locale Loader
# ---------------------------------------------------------------------------

class LocaleLoader:
    """Loads locale files from disk on demand (lazy loading).

    Locale files are stored as JSON in the locales directory:
        presidium/i18n/locales/en.json
        presidium/i18n/locales/ru.json
        ...

    JSON format:
        {
            "metadata": {
                "code": "ru",
                "name": "Русский"
            },
            "translations": {
                "New message": "Новое сообщение",
                "Settings": "Настройки",
                ...
            },
            "plural_translations": {
                "{count} message": {
                    "one": "{count} сообщение",
                    "few": "{count} сообщения",
                    "many": "{count} сообщений",
                    "other": "{count} сообщений"
                }
            }
        }
    """

    def __init__(self, locales_dir: Optional[Path] = None) -> None:
        if locales_dir is None:
            locales_dir = Path(__file__).parent / LOCALES_DIR
        self._locales_dir = locales_dir
        self._loaded: dict[str, Locale] = {}
        self._available: Optional[list[str]] = None

    def discover_locales(self) -> list[str]:
        """Discover available locale files in the locales directory.

        Returns:
            List of available locale codes.
        """
        if self._available is not None:
            return self._available

        available: list[str] = []
        if self._locales_dir.is_dir():
            for ext in LOCALE_FILE_EXTENSIONS:
                for path in self._locales_dir.glob(f"*{ext}"):
                    locale_code = path.stem
                    if locale_code in SUPPORTED_LOCALES:
                        available.append(locale_code)

        # Always include default locale
        if DEFAULT_LOCALE not in available:
            available.insert(0, DEFAULT_LOCALE)

        self._available = sorted(set(available))
        return self._available

    def load(self, locale_code: str) -> Locale:
        """Load a locale from disk.

        Args:
            locale_code: Locale code to load.

        Returns:
            Loaded Locale object.

        Raises:
            LocaleNotFoundError: If the locale file doesn't exist.
        """
        # Check cache
        if locale_code in self._loaded:
            return self._loaded[locale_code]

        # Try to load from disk
        for ext in LOCALE_FILE_EXTENSIONS:
            path = self._locales_dir / f"{locale_code}{ext}"
            if path.is_file():
                locale = self._load_json(path)
                self._loaded[locale_code] = locale
                logger.debug("Loaded locale '%s' from %s.", locale_code, path)
                return locale

        raise LocaleNotFoundError(
            f"Locale '{locale_code}' not found in {self._locales_dir}."
        )

    def _load_json(self, path: Path) -> Locale:
        """Load a locale from a JSON file.

        Args:
            path: Path to the JSON locale file.

        Returns:
            Parsed Locale object.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            raise I18nError(f"Failed to load locale from {path}: {exc}") from exc

        metadata = data.get("metadata", {})
        translations = data.get("translations", {})
        plural_translations = data.get("plural_translations", {})

        return Locale(
            code=metadata.get("code", path.stem),
            name=metadata.get("name", SUPPORTED_LOCALES.get(path.stem, path.stem)),
            translations=translations,
            plural_translations=plural_translations,
        )

    def preload(self, locale_codes: list[str]) -> None:
        """Preload multiple locales into cache.

        Args:
            locale_codes: List of locale codes to preload.
        """
        for code in locale_codes:
            try:
                self.load(code)
            except LocaleNotFoundError:
                logger.warning("Failed to preload locale '%s'.", code)

    def is_loaded(self, locale_code: str) -> bool:
        """Check if a locale is already loaded in cache."""
        return locale_code in self._loaded

    def clear_cache(self) -> None:
        """Clear the locale cache, forcing re-read from disk."""
        self._loaded.clear()
        self._available = None


# ---------------------------------------------------------------------------
# I18n Manager (singleton)
# ---------------------------------------------------------------------------

class I18nManager:
    """Centralized internationalization manager for Presidium.

    Manages the current locale, provides the _() translation function,
    and handles RTL layout detection. Supports runtime locale switching
    with automatic UI retranslation.

    Usage:
        manager = I18nManager()
        manager.set_locale("ru")

        # Get translation function
        _ = manager.translate
        text = _("Settings")  # → "Настройки"

        # Plural forms
        text = manager.translate_plural("{count} message", n=5)

        # RTL detection
        if manager.is_rtl():
            layout.setDirection(Qt.RightToLeft)
    """

    _instance: Optional[I18nManager] = None

    def __init__(self, locales_dir: Optional[Path] = None) -> None:
        self._loader = LocaleLoader(locales_dir)
        self._current_locale: Locale = Locale()  # Default English
        self._fallback_locale: Locale = Locale()
        self._on_locale_change_callbacks: list[Callable[[str], None]] = []

        # Load fallback locale
        try:
            self._fallback_locale = self._loader.load(FALLBACK_LOCALE)
            self._current_locale = self._fallback_locale
        except LocaleNotFoundError:
            # Use built-in English translations as fallback
            self._current_locale = Locale(code=DEFAULT_LOCALE, name="English")
            self._fallback_locale = self._current_locale

    @classmethod
    def instance(cls) -> I18nManager:
        """Get the singleton I18nManager instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -------------------------------------------------------------------
    # Locale management
    # -------------------------------------------------------------------

    def set_locale(self, locale_code: str) -> None:
        """Set the active locale.

        Args:
            locale_code: Locale code to switch to (e.g., "ru", "zh-Hans").

        Raises:
            LocaleNotFoundError: If the locale is not available.
        """
        if locale_code == self._current_locale.code:
            return

        new_locale = self._loader.load(locale_code)
        self._current_locale = new_locale

        logger.info("Locale changed to '%s' (%s).", locale_code, new_locale.name)

        # Notify callbacks
        for callback in self._on_locale_change_callbacks:
            try:
                callback(locale_code)
            except Exception as exc:
                logger.warning("Locale change callback error: %s", exc)

    def get_current_locale(self) -> str:
        """Get the current locale code."""
        return self._current_locale.code

    def get_available_locales(self) -> list[str]:
        """Get list of available locale codes."""
        return self._loader.discover_locales()

    def on_locale_change(self, callback: Callable[[str], None]) -> None:
        """Register a callback for locale changes.

        Args:
            callback: Function called with the new locale code.
        """
        self._on_locale_change_callbacks.append(callback)

    # -------------------------------------------------------------------
    # Translation
    # -------------------------------------------------------------------

    def translate(self, key: str, **kwargs: Any) -> str:
        """Translate a string with optional interpolation.

        This is the primary translation function, typically aliased as _().

        Args:
            key: The source string / translation key.
            **kwargs: Variables for string interpolation.

        Returns:
            Translated and interpolated string.
        """
        # Look up in current locale
        translated = self._current_locale.translate(key)

        # Fallback to English if not found and current locale isn't English
        if translated == key and self._current_locale.code != FALLBACK_LOCALE:
            translated = self._fallback_locale.translate(key)

        # Apply interpolation
        if kwargs:
            try:
                translated = translated.format(**kwargs)
            except (KeyError, IndexError, ValueError) as exc:
                logger.debug(
                    "Interpolation failed for '%s' with %s: %s",
                    key, kwargs, exc,
                )

        return translated

    def translate_plural(self, key: str, n: int, **kwargs: Any) -> str:
        """Translate a plural string based on count.

        Selects the appropriate plural form based on the current locale's
        plural rules and interpolates the count.

        Args:
            key: The translation key for the plural string.
            n: The count to determine plural form.
            **kwargs: Additional interpolation variables.

        Returns:
            Translated plural string.
        """
        plural_form = PluralRules.get_plural_form(
            self._current_locale.code, n
        )

        translated = self._current_locale.translate_plural(key, plural_form)

        # Fallback
        if translated == key and self._current_locale.code != FALLBACK_LOCALE:
            translated = self._fallback_locale.translate_plural(key, plural_form)

        # Interpolate
        kwargs["count"] = n
        try:
            translated = translated.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass

        return translated

    # -------------------------------------------------------------------
    # RTL support
    # -------------------------------------------------------------------

    def text_direction(self) -> TextDirection:
        """Get the text direction for the current locale."""
        return self._current_locale.text_direction

    def is_rtl(self) -> bool:
        """Check if the current locale uses RTL text direction."""
        return self._current_locale.is_rtl

    def bidi_transform(self, text: str) -> str:
        """Apply bidirectional text markers for mixed LTR/RTL content.

        Uses Unicode bidi control characters to ensure correct rendering
        of mixed-direction text (e.g., Arabic text containing English words).

        Args:
            text: The text to apply bidi markers to.

        Returns:
            Text with appropriate bidi control characters.
        """
        if not self.is_rtl():
            return text

        # Wrap with RLE (Right-to-Left Embedding) and PDF (Pop Directional Format)
        return f"\u202B{text}\u202C"


# ---------------------------------------------------------------------------
# Convenience functions (module-level)
# ---------------------------------------------------------------------------

def _(key: str, **kwargs: Any) -> str:
    """Shorthand for I18nManager.instance().translate().

    This is the primary way to mark strings for translation throughout
    the codebase. It uses the singleton I18nManager instance.

    Usage:
        from presidium.i18n import _
        label.setText(_("Settings"))
        msg = _("You have {count} new messages", count=5)
    """
    return I18nManager.instance().translate(key, **kwargs)


def _n(key: str, n: int, **kwargs: Any) -> str:
    """Shorthand for plural translation.

    Usage:
        from presidium.i18n import _n
        text = _n("{count} message", n=5)
    """
    return I18nManager.instance().translate_plural(key, n, **kwargs)
