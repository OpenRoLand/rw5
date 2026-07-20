"""English and Romanian record labels used by SurvCE-written RW5 files.

SurvCE writes free-text labels (for example ``"Equipment"`` or its
Romanian translation ``"Echipament"``) inside ``--`` note lines instead of
using a fixed set of tags for every value. :mod:`siscadro_rw5.parser` looks
up the label it expects in one of the two mappings below, selected by the
locale detected (or explicitly supplied) for a given file.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

#: Canonical lookup keys used throughout the parser, mapped to the literal
#: text SurvCE writes for a Romanian-locale RW5 file. The parser always
#: looks up a stable English key (for example ``"Equipment"``) here to
#: find the text that will actually appear in the file.
ROMANIAN_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "%Y/%m/%d %H:%M:%S": "%m/%d/%Y %H:%M:%S",
        "Adjustments": "Adjustments",
        "Antenna Type": "Antenna Type",
        "CRD": "CRD",
        "Device": "Device",
        "Eas Avg": "Eas Avg",
        "Eas Min": "Eas Min",
        "Elv Avg": "Elv Avg",
        "Elv Min": "Elv Min",
        "Entered HR": "Entered HR",
        "Entered Rover HR": "Entered Rover HR",
        "Equipment": "Echipament",
        "Fixed Readings": "Fixed Readings",
        "Float Readings": "Float Readings",
        "DGPS Readings": "DGPS Readings",
        "Geoid Separation File": "Fisierul de separare al geoidului",
        "GPS Scale": "Scara GPS",
        "Grid Adjustment File": "Grid Adjustment File",
        "HDOP Avg": "HDOP Avg",
        "HRMS Avg": "HRMS Avg",
        "Localization File": "Fisierul de localizare",
        "Network": "Network",
        "Nor Avg": "Nor Avg",
        "Nor Min": "Nor Min",
        "Number of Satellites Avg": "Number of Satellites Avg",
        "PDOP Avg": "PDOP Avg",
        "RTK Method": "RTK Method",
        "User Defined": "Definit de utilizator",
        "Valid Readings": "Valid Readings",
        "VDOP Avg": "VDOP Avg",
        "VRMS Avg": "VRMS Avg",
    }
)

#: The same lookup keys, mapped to themselves, for English-locale files.
ENGLISH_LABELS: Mapping[str, str] = MappingProxyType(
    {key: key for key in ROMANIAN_LABELS}
)

#: Substrings that identify a Romanian-locale file when found in any of
#: its first lines.
ROMANIAN_LOCALE_MARKERS: tuple = (
    "Definit de utilizator",
    "Echipament",
    "Fisierul de localizare",
)

#: Substrings that identify an English-locale file when found in any of
#: its first lines.
ENGLISH_LOCALE_MARKERS: tuple = (
    "User Defined",
    "Equipment",
    "Entered Base",
    "Base Configuration",
    "Gnss Device",
    "RTK Method",
)

#: Free-text note prefixes recognized before locale-specific labels apply.
NOTE_SURVCE_VERSION = "Stonex SurvCE Version "
NOTE_SURVCE_VERSION_NO_SPACE = "SurvCE Version "
NOTE_SCALE_POINT = "Scale Point"

#: Maximum number of leading lines inspected while auto-detecting locale
#: before falling back to :data:`DEFAULT_LOCALE`.
LOCALE_DETECTION_LINE_LIMIT = 20

#: Locale used when auto-detection does not find a recognizable marker.
DEFAULT_LOCALE = "en"
