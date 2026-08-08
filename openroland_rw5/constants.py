"""English and Romanian record labels used by SurvCE-written RW5 files.

SurvCE writes free-text labels (for example ``"Equipment"`` or its
Romanian translation ``"Echipament"``) inside ``--`` note lines instead of
using a fixed set of tags for every value. :mod:`openroland_rw5.parser` looks
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
        "AGE Avg": "AGE Avg",
        "AUTO Readings": "AUTO Readings",
        "Averaged Points": "Averaged Points",
        "Antenna": "Antenna",
        "Antenna Type": "Antenna Type",
        "Attribute": "Attribute",
        "CRD": "CRD",
        "Date (creation)": "Date (creation)",
        "Date (last modification)": "Date (last modification)",
        "Device": "Device",
        "Eas Avg": "Eas Avg",
        "Eas Min": "Eas Min",
        "Elv Avg": "Elv Avg",
        "Elv Min": "Elv Min",
        "Entered HR": "Entered HR",
        "Entered Rover HR": "Entered Rover HR",
        "Entered Base HR": "Entered Base HR",
        "ERMS Avg": "ERMS Avg",
        "Equipment": "Echipament",
        "Fixed Readings": "Fixed Readings",
        "Float Readings": "Float Readings",
        "DGPS Readings": "DGPS Readings",
        "Geoid Separation File": "Fisierul de separare al geoidului",
        "GNSS Profile Tolerance PP": "GNSS Profile Tolerance PP",
        "GNSS Profile Tolerance RT": "GNSS Profile Tolerance RT",
        "GNSS Statistics PP": "GNSS Statistics PP",
        "GNSS Statistics RT": "GNSS Statistics RT",
        "GPS Scale": "Scara GPS",
        "Gnss Device": "Gnss Device",
        "Grid Adjustment File": "Grid Adjustment File",
        "HDOP Avg": "HDOP Avg",
        "Instrument Model": "Instrument Model",
        "Instrument Selected": "Instrument Selected",
        "Antenna height": "Antenna height",
        "HRMS Avg": "HRMS Avg",
        "HSIG Avg": "HSIG Avg",
        "HSDV Avg": "HSDV Avg",
        "Localization File": "Fisierul de localizare",
        "Localization Type": "Localization Type",
        "Network": "Network",
        "Reference System": "Reference System",
        "Nor Avg": "Nor Avg",
        "Nor Min": "Nor Min",
        "NRMS Avg": "NRMS Avg",
        "Number of Satellites Avg": "Number of Satellites Avg",
        "PDOP Avg": "PDOP Avg",
        "PP Time": "PP Time",
        "Point Used": "Point Used",
        "RTK Method": "RTK Method",
        "Station": "Station",
        "StdDev E": "StdDev E",
        "StdDev N": "StdDev N",
        "StdDev Z": "StdDev Z",
        "Translate": "Translate",
        "TS Angles": "TS Angles",
        "TS Scale": "TS Scale",
        "EDM Mode": "EDM Mode",
        "P.C. mm Applied": "P.C. mm Applied",
        "User Defined": "Definit de utilizator",
        "Valid Readings": "Valid Readings",
        "VDOP Avg": "VDOP Avg",
        "VRMS Avg": "VRMS Avg",
        "VSIG Avg": "VSIG Avg",
        "VSDV Avg": "VSDV Avg",
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
    "TDS RW5",
    "X-change",
    "Date (creation)",
    "Instrument Model",
    "Antenna height",
)

#: Free-text note prefixes recognized before locale-specific labels apply.
NOTE_SURVCE_VERSION = "Stonex SurvCE Version "
NOTE_SURVCE_VERSION_NO_SPACE = "SurvCE Version "
NOTE_SURVX = "SurvX "
NOTE_STONEX_CUBE_A = "Stonex Cube-a "
NOTE_TDS_RW5 = "TDS RW5"
NOTE_GPS_SURVEY = "GPS Survey "
NOTE_SCALE_POINT = "Scale Point"
NOTE_BASE_CONFIGURATION = "Base Configuration by Reading GPS Position"
NOTE_BASE_CONFIGURATION_STATE_PLANE = (
    "Base Configuration by Entering State Plane Coordinates"
)
NOTE_BASE_CONFIGURATION_PREVIOUSLY_SURVEYED = (
    "Base Configuration by Previously Surveyed"
)
NOTE_INITIALIZATION_TIME = "Initialization time "
NOTE_GNSS_POSITION_ADJUSTMENT = "GNSS Position Adjustment"
NOTE_GPS_REFERENCE_STATION = "GPS Reference station"

#: Maximum number of leading lines inspected while auto-detecting locale
#: before falling back to :data:`DEFAULT_LOCALE`.
LOCALE_DETECTION_LINE_LIMIT = 20

#: Locale used when auto-detection does not find a recognizable marker.
DEFAULT_LOCALE = "en"
