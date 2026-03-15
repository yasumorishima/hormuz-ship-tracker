"""Normalize messy AIS destination fields into canonical port/area names.

AIS destination strings are free-text and wildly inconsistent:
  "DUBAI", "AE DXB", "AEDXB", "DMC DUBAI", "DMC  DUBAI", etc.

This module maps known variants to clean canonical names, plus
assigns a geographic region for analytics grouping.
"""

import re

# Canonical destination → list of known variants (case-insensitive)
_DESTINATION_VARIANTS: dict[str, list[str]] = {
    "Dubai": [
        "DUBAI", "AE DXB", "AEDXB", "DXB", "AE DUBAI",
        "DUBAI PORT", "PORT OF DUBAI",
    ],
    "Dubai Maritime City": [
        "DMC", "DMC DUBAI", "DMC-DUBAI", "DMC  DUBAI", "DMC UAE",
        "DUBAI MARITIME CITY", "DUBAI MARITIME",
    ],
    "Jebel Ali": [
        "JEBEL ALI", "AE JEA", "AEJEA", "JEA", "JEBEL ALI PORT",
        "JEBELALI", "JEBEL ALI UAE", "AE JEBEL ALI",
    ],
    "Port Rashid": [
        "PORT RASHID", "RASHID", "AE RSH",
    ],
    "Sharjah": [
        "SHARJAH", "SHARJAH OPL", "SHARJAH ANCHORAGE",
        "AE SHJ", "AESHJ", "SHJ",
    ],
    "Hamriyah": [
        "HAMRIYAH", "HAMRIYAH OPL", "HAMRIYAH FZ",
        "HAMRIYAH FREE ZONE",
    ],
    "Fujairah": [
        "FUJAIRAH", "AE FJR", "AEFJR", "FJR", "FUJAIRAH OPL",
        "FUJAIRAH ANCHORAGE", "FUJAIRAH ANH", "FUJAIRAH ANCH",
        "FUJAIRAH PORT", "FUJAIRAH ANK",
    ],
    "Khor Fakkan": [
        "KHOR FAKKAN", "KHORFAKKAN", "KHOR FAKKAN PORT",
        "AE KFK", "KFK",
    ],
    "Abu Dhabi": [
        "ABU DHABI", "AE AUH", "AEAUH", "AUH",
        "ABU DHABI PORT",
    ],
    "Muscat": [
        "MUSCAT", "OM MSH", "MUSCAT PORT", "SULTAN QABOOS PORT",
    ],
    "Sohar": [
        "SOHAR", "OM SOH", "SOHAR PORT",
    ],
    "Bandar Abbas": [
        "BANDAR ABBAS", "BND ABBAS", "IR BND", "SHAHID RAJAEE",
        "RAJAEE", "BANDARABBAS", "BANDARE ABBAS",
    ],
    "Ras Al Khaimah": [
        "RAS AL KHAIMAH", "RAK", "AE RAK",
        "SAQR PORT", "SAQR",
    ],
    "Kuwait": [
        "KUWAIT", "KW KWI", "KWI", "SHUWAIKH", "SHUAIBA",
        "AHMADI", "MINA AL AHMADI",
    ],
    "Dammam": [
        "DAMMAM", "SA DMM", "DMM", "KING ABDULAZIZ PORT",
    ],
    "Jubail": [
        "JUBAIL", "AL JUBAIL", "SA JUB", "JUBAIL COMMERCIAL",
        "JUBAIL INDUSTRIAL",
    ],
    "Ras Tanura": [
        "RAS TANURA", "RASTANURA", "RAS TANNURA",
    ],
    "Yanbu": [
        "YANBU", "YANBU AL BAHR",
    ],
    "Doha": [
        "DOHA", "QA DOH", "HAMAD PORT", "MESAIEED",
    ],
    "Bahrain": [
        "BAHRAIN", "BH BAH", "KHALIFA BIN SALMAN",
        "MINA SALMAN",
    ],
    "Basra": [
        "BASRA", "UMM QASR", "IQ BSR", "KHOR AL ZUBAIR",
        "AL FAO", "FAO",
    ],
    "Mumbai": [
        "MUMBAI", "IN BOM", "NHAVA SHEVA", "JNPT",
        "JAWAHARLAL NEHRU",
    ],
    "Karachi": [
        "KARACHI", "PK KHI",
    ],
    "Singapore": [
        "SINGAPORE", "SG SIN", "SIN",
    ],
    "For Orders": [
        "FOR ORDERS", "FOR ORDER", "F/O", "F.O.",
        "AWAITING ORDERS", "TBA", "TBN", "T.B.A.",
    ],
}

# Build reverse lookup: uppercase variant → canonical name
_VARIANT_MAP: dict[str, str] = {}
for canonical, variants in _DESTINATION_VARIANTS.items():
    for v in variants:
        _VARIANT_MAP[v.upper()] = canonical

# Region mapping for analytics
DESTINATION_REGION: dict[str, str] = {
    "Dubai": "UAE",
    "Dubai Maritime City": "UAE",
    "Jebel Ali": "UAE",
    "Port Rashid": "UAE",
    "Sharjah": "UAE",
    "Hamriyah": "UAE",
    "Fujairah": "UAE",
    "Khor Fakkan": "UAE",
    "Abu Dhabi": "UAE",
    "Ras Al Khaimah": "UAE",
    "Muscat": "Oman",
    "Sohar": "Oman",
    "Bandar Abbas": "Iran",
    "Kuwait": "Kuwait",
    "Dammam": "Saudi Arabia",
    "Jubail": "Saudi Arabia",
    "Ras Tanura": "Saudi Arabia",
    "Yanbu": "Saudi Arabia",
    "Doha": "Qatar",
    "Bahrain": "Bahrain",
    "Basra": "Iraq",
    "Mumbai": "India",
    "Karachi": "Pakistan",
    "Singapore": "Singapore",
    "For Orders": "Unspecified",
}


def normalize_destination(raw: str | None) -> str:
    """Normalize a raw AIS destination string to a canonical name.

    Returns the canonical name if matched, otherwise returns the
    cleaned-up original string (trimmed, collapsed whitespace).
    """
    if not raw:
        return ""
    cleaned = re.sub(r"\s+", " ", raw.strip().upper())
    if not cleaned:
        return ""
    if cleaned in _VARIANT_MAP:
        return _VARIANT_MAP[cleaned]
    # Try partial matching: check if any variant is contained in the string
    for variant, canonical in sorted(
        _VARIANT_MAP.items(), key=lambda x: -len(x[0])
    ):
        if variant in cleaned:
            return canonical
    return raw.strip()


def get_destination_region(canonical_dest: str) -> str:
    """Get the region for a canonical destination name."""
    return DESTINATION_REGION.get(canonical_dest, "Other")
