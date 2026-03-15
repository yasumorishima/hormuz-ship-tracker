"""MMSI Maritime Identification Digits (MID) to country/flag mapping.

The first 3 digits of a 9-digit MMSI encode the vessel's flag state.
This module covers flags commonly seen in the Persian Gulf region plus
major open registries (Panama, Liberia, Marshall Islands, etc.).
"""

# (country_code, country_name)
MID_TO_COUNTRY: dict[int, tuple[str, str]] = {
    # --- Persian Gulf / Middle East ---
    403: ("SA", "Saudi Arabia"),
    408: ("BH", "Bahrain"),
    412: ("CN", "China"),
    413: ("CN", "China"),
    414: ("CN", "China"),
    416: ("SA", "Saudi Arabia"),
    419: ("IN", "India"),
    422: ("IR", "Iran"),
    431: ("JP", "Japan"),
    432: ("JP", "Japan"),
    440: ("KR", "South Korea"),
    441: ("KR", "South Korea"),
    445: ("KR", "South Korea"),
    447: ("KW", "Kuwait"),
    450: ("OM", "Oman"),
    457: ("MN", "Mongolia"),
    461: ("KW", "Kuwait"),
    466: ("QA", "Qatar"),
    470: ("AE", "UAE"),
    471: ("AE", "UAE"),
    472: ("TJ", "Tajikistan"),
    473: ("AE", "UAE"),
    477: ("HK", "Hong Kong"),
    478: ("BA", "Bosnia"),

    # --- Open registries (most tankers/cargo use these) ---
    209: ("BS", "Bahamas"),
    210: ("BS", "Bahamas"),
    211: ("BS", "Bahamas"),
    256: ("MT", "Malta"),
    319: ("KY", "Cayman Islands"),
    325: ("AG", "Antigua & Barbuda"),
    341: ("KN", "St Kitts"),
    351: ("PA", "Panama"),
    352: ("PA", "Panama"),
    353: ("PA", "Panama"),
    354: ("PA", "Panama"),
    355: ("PA", "Panama"),
    356: ("PA", "Panama"),
    357: ("PA", "Panama"),
    370: ("PA", "Panama"),
    371: ("PA", "Panama"),
    372: ("PA", "Panama"),
    373: ("PA", "Panama"),
    374: ("PA", "Panama"),
    375: ("VC", "St Vincent"),
    376: ("VC", "St Vincent"),
    377: ("VC", "St Vincent"),
    378: ("VG", "British Virgin Is."),
    525: ("ID", "Indonesia"),
    533: ("MY", "Malaysia"),
    538: ("MH", "Marshall Islands"),
    548: ("PH", "Philippines"),
    563: ("SG", "Singapore"),
    564: ("SG", "Singapore"),
    565: ("SG", "Singapore"),
    566: ("SG", "Singapore"),
    620: ("KM", "Comoros"),
    621: ("KM", "Comoros"),
    636: ("LR", "Liberia"),
    637: ("LR", "Liberia"),
    667: ("TZ", "Tanzania"),

    # --- European maritime ---
    219: ("DK", "Denmark"),
    220: ("DK", "Denmark"),
    224: ("ES", "Spain"),
    225: ("ES", "Spain"),
    226: ("FR", "France"),
    227: ("FR", "France"),
    228: ("FR", "France"),
    229: ("MT", "Malta"),
    230: ("FI", "Finland"),
    231: ("FI", "Finland"),
    235: ("GB", "United Kingdom"),
    236: ("GB", "United Kingdom"),
    237: ("GR", "Greece"),
    238: ("HR", "Croatia"),
    239: ("GR", "Greece"),
    240: ("GR", "Greece"),
    241: ("GR", "Greece"),
    244: ("NL", "Netherlands"),
    245: ("NL", "Netherlands"),
    246: ("NL", "Netherlands"),
    247: ("IT", "Italy"),
    248: ("MT", "Malta"),
    249: ("MT", "Malta"),
    255: ("PT", "Portugal"),
    261: ("PL", "Poland"),
    271: ("TR", "Turkey"),
    272: ("TR", "Turkey"),
    273: ("RU", "Russia"),

    # --- Americas ---
    303: ("US", "United States"),
    316: ("CA", "Canada"),
    338: ("US", "United States"),
    366: ("US", "United States"),
    367: ("US", "United States"),
    368: ("US", "United States"),
    369: ("US", "United States"),

    # --- Other Asian ---
    501: ("FR", "French Southern"),
    503: ("AU", "Australia"),
    508: ("PW", "Palau"),
    510: ("MQ", "Micronesia"),
    512: ("NZ", "New Zealand"),
    514: ("KH", "Cambodia"),
    515: ("KH", "Cambodia"),
    516: ("AU", "Christmas Is."),
    572: ("TH", "Thailand"),
    574: ("VN", "Vietnam"),

    # --- Africa ---
    601: ("ZA", "South Africa"),
    618: ("EG", "Egypt"),
    619: ("EG", "Egypt"),
    622: ("MR", "Mauritania"),
    624: ("DJ", "Djibouti"),
    625: ("ER", "Eritrea"),
    627: ("GM", "Gambia"),
    649: ("CD", "Congo"),
    650: ("SO", "Somalia"),
    657: ("MG", "Madagascar"),
    669: ("IQ", "Iraq"),
    671: ("TG", "Togo"),
    672: ("SY", "Syria"),
    677: ("YE", "Yemen"),
}


def mmsi_to_flag(mmsi: int | None) -> tuple[str, str]:
    """Extract country code and name from MMSI via MID lookup.

    Returns (country_code, country_name) or ("", "") if unknown.
    """
    if not mmsi or mmsi < 200000000 or mmsi > 799999999:
        return ("", "")

    mid = mmsi // 1000000  # first 3 of 9 digits
    return MID_TO_COUNTRY.get(mid, ("", ""))
