from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class ResolvedLanguage:
    code: str
    name: str


_LANGUAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "ca": ("Catalan", ("cat",)),
    "da": ("Danish", ("dan",)),
    "de": ("German", ("deu", "ger")),
    "en": ("English", ("eng",)),
    "es": ("Spanish", ("spa",)),
    "fr": ("French", ("fra", "fre")),
    "it": ("Italian", ("ita",)),
    "ja": ("Japanese", ("jpn",)),
    "ko": ("Korean", ("kor",)),
    "nl": ("Dutch", ("nld", "dut")),
    "pt": ("Portuguese", ("por",)),
    "ro": ("Romanian", ("ron", "rum")),
    "ru": ("Russian", ("rus",)),
    "sv": ("Swedish", ("swe",)),
    "uk": ("Ukrainian", ("ukr",)),
    "zh": ("Chinese", ("zho", "chi")),
}
_ALIASES = {
    alias.casefold(): code
    for code, (name, aliases) in _LANGUAGES.items()
    for alias in (code, name, *aliases)
}
_DEEPL_TARGET_VARIANTS = {
    "en-gb": "EN-GB",
    "en-us": "EN-US",
    "es-419": "ES-419",
    "pt-br": "PT-BR",
    "pt-pt": "PT-PT",
    "zh-hans": "ZH-HANS",
    "zh-hant": "ZH-HANT",
}
_DEEPL_DEFAULT_TARGETS = {
    "en": "EN-US",
    "pt": "PT-PT",
    "zh": "ZH-HANS",
}


def resolve_language(language: str) -> ResolvedLanguage:
    if not isinstance(language, str) or not language.strip():
        raise ValueError("Language must be a non-empty name or ISO code")

    normalized = re.sub(r"\s+", " ", language.strip()).casefold()
    code = _ALIASES.get(normalized)
    if code is None:
        base = normalized.replace("_", "-").split("-", 1)[0]
        code = _ALIASES.get(base)
    if code is None:
        supported = ", ".join(
            name for name, _aliases in _LANGUAGES.values()
        )
        raise ValueError(
            f"Unsupported language {language!r}; supported languages: "
            f"{supported}"
        )

    name = _LANGUAGES[code][0]
    return ResolvedLanguage(code=code, name=name)


def resolve_deepl_target_language(language: str) -> str:
    """Return a DeepL v2 target code, preserving supported variants."""
    resolved = resolve_language(language)
    normalized = language.strip().casefold().replace("_", "-")
    variant = _DEEPL_TARGET_VARIANTS.get(normalized)
    if variant is not None:
        return variant
    return _DEEPL_DEFAULT_TARGETS.get(
        resolved.code,
        resolved.code.upper(),
    )
