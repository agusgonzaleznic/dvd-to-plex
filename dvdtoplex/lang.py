"""ISO 639-1 codes as the IFO stores them, to the ISO 639-2/B codes Matroska and Plex expect."""

ISO639_2B = {
    "ar": "ara", "bg": "bul", "ca": "cat", "cs": "cze", "da": "dan", "de": "ger", "el": "gre",
    "en": "eng", "es": "spa", "et": "est", "fi": "fin", "fr": "fre", "he": "heb", "hi": "hin",
    "hr": "hrv", "hu": "hun", "is": "ice", "it": "ita", "ja": "jpn", "ko": "kor", "lt": "lit",
    "lv": "lav", "nl": "dut", "no": "nor", "pl": "pol", "pt": "por", "ro": "rum", "ru": "rus",
    "sk": "slo", "sl": "slv", "sr": "srp", "sv": "swe", "th": "tha", "tr": "tur", "uk": "ukr",
    "zh": "chi",
}

NAMES = {
    "ar": "Arabic", "bg": "Bulgarian", "ca": "Catalan", "cs": "Czech", "da": "Danish", "de": "German",
    "el": "Greek", "en": "English", "es": "Spanish", "et": "Estonian", "fi": "Finnish", "fr": "French",
    "he": "Hebrew", "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian", "is": "Icelandic",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "lt": "Lithuanian", "lv": "Latvian",
    "nl": "Dutch", "no": "Norwegian", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian",
    "ru": "Russian", "sk": "Slovak", "sl": "Slovenian", "sr": "Serbian", "sv": "Swedish",
    "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "zh": "Chinese",
}


def matroska(code: str) -> str:
    """'de' -> 'ger'; an unknown or empty code -> 'und' (undetermined)."""
    return ISO639_2B.get(code.lower(), "und")


def name(code: str) -> str:
    return NAMES.get(code.lower(), "Unknown language")
