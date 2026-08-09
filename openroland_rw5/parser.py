"""Low-level parser for RW5 (Trimble/Carlson/SurvCE) survey files.

This module only turns RW5 text into the raw record models defined in
:mod:`openroland_rw5.models`. It never depends on ``openroland-survey-core``'s
canonical model, a database, or a CAD library; :mod:`openroland_rw5.extractor`
is the layer that maps these raw records onto the canonical model.

RW5 files are line-oriented and comma-separated. Every line starts with a
two-or-three-letter record code (``GPS``, ``G0``, ``GS``, ...) followed by
fields tagged with a two-character sign (for example ``LA46.35192`` is the
tag ``LA`` with SurvCE packed ``DD.MMSSsssss`` value ``46.35192``).
Free-text metadata (equipment name, antenna type, and similar) is instead
written as ``--``-prefixed note lines whose label text depends on the
file's locale (English or Romanian), which is why most of this module's
dispatch is label-driven rather than tag-driven.
"""

from __future__ import annotations

import bisect
import datetime
import logging
import re
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Union

import arrow
import attrs
from openroland_survey.records import IssueSeverity, ParseIssue

from openroland_rw5.constants import (
    DEFAULT_LOCALE,
    ENGLISH_LABELS,
    ENGLISH_LOCALE_MARKERS,
    LOCALE_DETECTION_LINE_LIMIT,
    NOTE_BASE_CONFIGURATION,
    NOTE_BASE_CONFIGURATION_PREVIOUSLY_SURVEYED,
    NOTE_BASE_CONFIGURATION_STATE_PLANE,
    NOTE_GNSS_POSITION_ADJUSTMENT,
    NOTE_GPS_REFERENCE_STATION,
    NOTE_GPS_SURVEY,
    NOTE_INITIALIZATION_TIME,
    NOTE_SCALE_POINT,
    NOTE_STONEX_CUBE_A,
    NOTE_SURVCE_VERSION,
    NOTE_SURVCE_VERSION_NO_SPACE,
    NOTE_SURVX,
    NOTE_TDS_RW5,
    ROMANIAN_LABELS,
    ROMANIAN_LOCALE_MARKERS,
)
from openroland_rw5.models import Rw5BasePoint, Rw5GpsPoint, Rw5PointCodeNote

logger = logging.getLogger(__name__)

__all__ = [
    "Rw5Parser",
    "leap_seconds",
    "survce_angle_to_decimal",
    "time_from_gps",
]

_BASE_ID_RE = re.compile(r"Base ID read at rover:\s*(.*)$")
_BASE_ID_WITH_METHOD_RE = re.compile(
    r"\((.+)\) - Base ID read at rover:\s*(.*)$"
)
_HSIG_ITEM_RE = re.compile(r"\s*([A-Z]+)\s*:\s*(.+)")

#: Numeric tokens in SurvCE quality notes (``1.500``, ``0.6000``).
_NOTE_FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)")

#: SurvCE localization ``Translate: dy=…, dx=…, dz=…`` note.
_TRANSLATE_RE = re.compile(
    r"dy\s*=\s*(?P<dy>[-+]?\d+(?:\.\d+)?)\s*,\s*"
    r"dx\s*=\s*(?P<dx>[-+]?\d+(?:\.\d+)?)\s*,\s*"
    r"dz\s*=\s*(?P<dz>[-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

#: Operator free-text note ``{PN}-{code}…`` (for example ``603-ST DRUM``).
_POINT_CODE_NOTE_RE = re.compile(r"^(?P<pn>\d+)-(?P<text>[A-Za-z].*)$")

#: SurvCE base-setup ``SP North: …, SP East: …, Elv: …`` note.
_SP_NORTH_NOTE_RE = re.compile(
    r"SP North:\s*(?P<north>[-+]?\d+(?:\.\d+)?)\s*,\s*"
    r"SP East:\s*(?P<east>[-+]?\d+(?:\.\d+)?)\s*,\s*"
    r"Elv:\s*(?P<elev>[-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

#: SurvCE sometimes concatenates a ``--`` note onto a tagged record without
#: a newline (``G3,...YZ0.12--Number of Satellites Avg: ...``). Real comment
#: fields are always comma-prefixed (``,--md``).
_GLUED_NOTE_RE = re.compile(r"(?<!,)--(?=[A-Za-z])")

#: RW5's default (undecorated) local moment format, plus the fallbacks
#: SurvCE has been observed to write depending on locale/version.
_MOMENT_FORMATS: tuple = (
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
)

#: ``JB`` job stamp formats: SurvCE US order first, then European/SurvX.
_JOB_DATETIME_FORMATS: tuple[str, ...] = (
    "DT%m-%d-%YTM%H:%M:%S",
    "DT%d-%m-%YTM%H:%M:%S",
)

#: ``--DT`` local-date note formats (same day/month ambiguity as JB).
_DT_NOTE_FORMATS: tuple[str, ...] = (
    "DT%m-%d-%Y",
    "DT%d-%m-%Y",
)

_DT_PARTS_RE = re.compile(
    r"^DT(?P<a>\d{1,2})-(?P<b>\d{1,2})-(?P<year>\d{4})(?P<rest>.*)$"
)

#: End-of-June/December dates (23:59:59 UTC) on which a leap second was
#: historically inserted after GPS time began (1980-01-06).
_LEAP_SECOND_DATES: tuple = (
    (1981, 6, 30),
    (1982, 6, 30),
    (1983, 6, 30),
    (1985, 6, 30),
    (1987, 12, 31),
    (1989, 12, 31),
    (1990, 12, 31),
    (1992, 6, 30),
    (1993, 6, 30),
    (1994, 6, 30),
    (1995, 12, 31),
    (1997, 6, 30),
    (1998, 12, 31),
    (2005, 12, 31),
    (2008, 12, 31),
    (2012, 6, 30),
    (2015, 6, 30),
    (2016, 12, 31),
)
_LEAP_SECOND_BOUNDARIES = tuple(
    datetime.datetime(year, month, day, 23, 59, 59)
    for year, month, day in _LEAP_SECOND_DATES
)

_GPS_EPOCH = datetime.datetime(1980, 1, 6, 0, 0, 0)
_SECONDS_PER_WEEK = 604800


def leap_seconds(moment: datetime.datetime) -> int:
    """Return the number of leap seconds elapsed since the GPS epoch.

    Args:
        moment: A naive UTC-equivalent datetime, before leap seconds are
            subtracted.

    Returns:
        The number of leap-second insertions on or before ``moment``.
    """
    return bisect.bisect(_LEAP_SECOND_BOUNDARIES, moment)


def time_from_gps(week: int, milliseconds: int) -> datetime.datetime:
    """Convert a GPS week/millisecond-of-week pair to an aware UTC time.

    Args:
        week: GPS week number (for example ``2309``).
        milliseconds: Milliseconds elapsed since the start of ``week``, as
            written by SurvCE in the ``GT`` record.

    Returns:
        The corresponding aware UTC :class:`datetime.datetime`.
    """
    total_seconds, fractional_ms = divmod(milliseconds, 1000)
    naive = _GPS_EPOCH + datetime.timedelta(
        seconds=week * _SECONDS_PER_WEEK + total_seconds,
        milliseconds=fractional_ms,
    )
    corrected = naive - datetime.timedelta(seconds=leap_seconds(naive))
    return arrow.get(corrected, tzinfo="UTC").datetime


@attrs.define
class Rw5Parser:
    """Parses one RW5 file's text into raw base/GPS point records.

    Attributes:
        locale: Locale to assume (``"en"`` or ``"ro"``), or ``None`` to
            auto-detect it from the file's first lines.
        base_points: Base stations discovered, in file order, each with
            its own list of GPS observations.
        job_name: Job name, from the ``JB`` record.
        job_datetime: Job creation timestamp, from the ``JB`` record.
        survce_version: SurvCE/SurvX/Cube-a version string, from its
            banner note.
        scale_point: Scale point note text, when present.
        equipment: Equipment name, from the locale-specific note.
        gnss_device: SurvX ``Gnss Device`` note (model/serial/firmware).
        instrument_model: TDS/X-change ``Instrument Model`` note.
        date_creation: TDS ``Date (creation)`` note text.
        date_last_modification: TDS ``Date (last modification)`` note text.
        polyline_area_note: Last SurvCE ``Calculate area of polyline`` note.
        orphan_point_notes: ``{PN}-{code}…`` notes whose target point was
            never seen in this file (parse-only).
        coordinate_system: User-defined coordinate system name.
        cs_zone: Coordinate-system zone from the ``CS`` record, when
            present.
        ellipsoid: Ellipsoid name from the ``ES`` record, when present.
        reference_system: Reference system note, when present.
        ts_angles: Total-station angle units note, when present.
        ts_scale: Total-station scale factor note, when present.
        edm_mode: EDM mode note (for example ``Standard``), when present.
        pc_mm_applied: Prism-constant note (``P.C. mm Applied``), when present.
        localization_file: Localization file name, when present.
        localization_type: Localization type note, when present.
        localization_translate_dx: Localization easting shift (``dx``), when
            present on ``Translate`` notes.
        localization_translate_dy: Localization northing shift (``dy``).
        localization_translate_dz: Localization height shift (``dz``).
        averaged_points: Point numbers from an ``Averaged Points`` note.
        stddev_n: Northing std-dev from ``StdDev N`` (averaged SP).
        stddev_e: Easting std-dev from ``StdDev E``.
        stddev_z: Elevation std-dev from ``StdDev Z``.
        station_note: Alignment station/offset from a ``Station`` note.
        geoid_file: Geoid separation file name, when present.
        grid_adjustment_file: Grid adjustment file name, when present.
        gps_scale: GPS scale factor, when present.
        rtk_method: RTK method name, from the ``RTK Method`` note.
        rtk_device: RTK device name, from the same note.
        rtk_network: RTK network name, from the same note.
        crd: Coordinate system CRD file reference, when present.
        units: Distance unit note (``"metric"`` or the raw ``UN`` value).
        scale_factor: Scale factor, from the ``MO`` record.
        earth_curvature_on: Whether earth curvature correction is on.
        edm_offset: EDM offset, from the ``MO`` record.
        antenna_type: Antenna model name.
        antenna_radius: Antenna radius, when reported.
        antenna_slant_height: Antenna slant height offset, when reported.
        antenna_l1_offset: Antenna L1 phase center offset, when reported.
        antenna_l2_offset: Antenna L2 phase center offset, when reported.
        antenna_description: Free-text antenna description.
        issues: Diagnostics raised while parsing, in file order.
    """

    locale: Optional[str] = None

    base_points: List[Rw5BasePoint] = attrs.field(factory=list, init=False)
    issues: List[ParseIssue] = attrs.field(factory=list, init=False)

    job_name: Optional[str] = attrs.field(default=None, init=False)
    job_datetime: Optional[datetime.datetime] = attrs.field(
        default=None, init=False
    )
    survce_version: Optional[str] = attrs.field(default=None, init=False)
    scale_point: Optional[str] = attrs.field(default=None, init=False)
    equipment: Optional[str] = attrs.field(default=None, init=False)
    gnss_device: Optional[str] = attrs.field(default=None, init=False)
    instrument_model: Optional[str] = attrs.field(default=None, init=False)
    date_creation: Optional[str] = attrs.field(default=None, init=False)
    date_last_modification: Optional[str] = attrs.field(
        default=None, init=False
    )
    polyline_area_note: Optional[str] = attrs.field(default=None, init=False)
    orphan_point_notes: List[Rw5PointCodeNote] = attrs.field(
        factory=list, init=False
    )
    coordinate_system: Optional[str] = attrs.field(default=None, init=False)
    cs_zone: Optional[str] = attrs.field(default=None, init=False)
    ellipsoid: Optional[str] = attrs.field(default=None, init=False)
    reference_system: Optional[str] = attrs.field(default=None, init=False)
    ts_angles: Optional[str] = attrs.field(default=None, init=False)
    ts_scale: Optional[float] = attrs.field(default=None, init=False)
    edm_mode: Optional[str] = attrs.field(default=None, init=False)
    pc_mm_applied: Optional[str] = attrs.field(default=None, init=False)
    localization_file: Optional[str] = attrs.field(default=None, init=False)
    localization_type: Optional[str] = attrs.field(default=None, init=False)
    localization_translate_dx: Optional[float] = attrs.field(
        default=None, init=False
    )
    localization_translate_dy: Optional[float] = attrs.field(
        default=None, init=False
    )
    localization_translate_dz: Optional[float] = attrs.field(
        default=None, init=False
    )
    averaged_points: Optional[str] = attrs.field(default=None, init=False)
    stddev_n: Optional[float] = attrs.field(default=None, init=False)
    stddev_e: Optional[float] = attrs.field(default=None, init=False)
    stddev_z: Optional[float] = attrs.field(default=None, init=False)
    station_note: Optional[str] = attrs.field(default=None, init=False)
    geoid_file: Optional[str] = attrs.field(default=None, init=False)
    grid_adjustment_file: Optional[str] = attrs.field(default=None, init=False)
    gps_scale: Optional[float] = attrs.field(default=None, init=False)
    rtk_method: Optional[str] = attrs.field(default=None, init=False)
    rtk_device: Optional[str] = attrs.field(default=None, init=False)
    rtk_network: Optional[str] = attrs.field(default=None, init=False)
    crd: Optional[str] = attrs.field(default=None, init=False)
    units: Optional[str] = attrs.field(default=None, init=False)
    scale_factor: Optional[float] = attrs.field(default=None, init=False)
    earth_curvature_on: Optional[bool] = attrs.field(default=None, init=False)
    edm_offset: Optional[float] = attrs.field(default=None, init=False)

    antenna_type: Optional[str] = attrs.field(default=None, init=False)
    antenna_radius: Optional[str] = attrs.field(default=None, init=False)
    antenna_slant_height: Optional[str] = attrs.field(default=None, init=False)
    antenna_l1_offset: Optional[str] = attrs.field(default=None, init=False)
    antenna_l2_offset: Optional[str] = attrs.field(default=None, init=False)
    antenna_description: Optional[str] = attrs.field(default=None, init=False)

    _entered_antenna_height: Optional[float] = attrs.field(
        default=None, init=False
    )
    _entered_antenna_method: Optional[str] = attrs.field(
        default=None, init=False
    )
    _true_antenna_height: Optional[float] = attrs.field(
        default=None, init=False
    )
    _pending_offset_azimuth: Optional[float] = attrs.field(
        default=None, init=False
    )
    _pending_offset_distance: Optional[float] = attrs.field(
        default=None, init=False
    )
    _pending_offset_delta_z: Optional[float] = attrs.field(
        default=None, init=False
    )
    _pending_ep: Optional[Dict[str, Optional[float]]] = attrs.field(
        default=None, init=False
    )
    _pending_observation_notes: Dict[str, str] = attrs.field(
        factory=dict, init=False
    )
    _pending_local_time: Optional[datetime.datetime] = attrs.field(
        default=None, init=False
    )
    _pending_point_code_notes: Dict[str, Rw5PointCodeNote] = attrs.field(
        factory=dict, init=False
    )
    _pending_point_used: Optional[str] = attrs.field(default=None, init=False)
    _pending_base_configured: bool = attrs.field(default=False, init=False)
    _pending_stk_source: Optional[str] = attrs.field(default=None, init=False)
    _pending_stk_labels: Optional[List[str]] = attrs.field(
        default=None, init=False
    )
    _pending_stk_values: Optional[List[str]] = attrs.field(
        default=None, init=False
    )
    _pending_stk_wrapped: bool = attrs.field(default=False, init=False)
    _pending_stk_fallback: Optional[Rw5GpsPoint] = attrs.field(
        default=None, init=False, repr=False
    )

    _source_path: Path = attrs.field(
        default=Path("<rw5>"), init=False, repr=False
    )
    _labels: Mapping[str, str] = attrs.field(
        factory=lambda: ENGLISH_LABELS, init=False, repr=False
    )
    _dispatch: Dict[str, Callable] = attrs.field(
        factory=dict, init=False, repr=False
    )

    def __attrs_post_init__(self) -> None:
        """Bind the record-code dispatch table and apply an explicit locale."""
        self._dispatch = {
            "JB": self._process_job_record,
            "BP": self._process_base_point,
            "LS": self._process_line_of_sight,
            "MO": self._process_mode_setup,
            "OF": self._process_offset_shot,
            "GPS": self._process_gps_record,
            "G0": self._process_g0,
            "G1": self._process_g1,
            "G2": self._process_g2,
            "G3": self._process_g3,
            "GS": self._process_gs,
            "GT": self._process_gt,
            "SP": self._process_sp,
            "EP": self._process_ep,
            "BL": self._process_bl,
            "AH": self._process_ah,
            "CS": self._process_cs,
            "ES": self._process_es,
            "HCAT": self._process_hcat,
            "HCDP": self._process_hcdp,
            "HCRV": self._process_hcrv,
            "HCBS": self._process_hcbs,
            # Total-station records: acknowledged but not yet reduced to NEH.
            "OC": self._process_total_station_record,
            "BK": self._process_total_station_record,
            "BD": self._process_total_station_record,
            "SS": self._process_total_station_record,
        }
        if self.locale is not None:
            self._labels = (
                ROMANIAN_LABELS if self.locale == "ro" else ENGLISH_LABELS
            )

    def parse_file(self, path: Union[str, Path]) -> "Rw5Parser":
        """Parse the RW5 file at ``path`` and return ``self``.

        Decodes the file as UTF-8 first, falling back to Windows-1252 (the
        legacy encoding older SurvCE versions write), since RW5 files
        never declare their own encoding.

        Args:
            path: Path of the RW5 file to parse.

        Returns:
            This parser, populated with the parsed records/issues.
        """
        resolved = Path(path)
        raw_bytes = resolved.read_bytes()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            logger.debug(
                "%s is not valid UTF-8; decoding as Windows-1252",
                resolved,
            )
            text = raw_bytes.decode("windows-1252")
        return self.parse_text(text, source_path=resolved)

    def parse_text(
        self, content: str, *, source_path: Union[str, Path] = Path("<rw5>")
    ) -> "Rw5Parser":
        """Parse RW5 content already decoded to text, and return ``self``.

        Args:
            content: The full decoded content of one RW5 file.
            source_path: Path recorded on any :class:`ParseIssue` raised
                while parsing; does not have to exist on disk.

        Returns:
            This parser, populated with the parsed records/issues.
        """
        self._source_path = Path(source_path)
        lines = content.splitlines()
        if self.locale is None:
            self._detect_locale(lines)
        for line_number, line in enumerate(lines, start=1):
            self._read_line(line_number, line)
        self._flush_pending_stakeout()
        self._flush_orphan_point_code_notes()
        logger.debug(
            "parsed %d survey points from %s",
            self.point_count(),
            source_path,
        )
        return self

    def _detect_locale(self, lines: List[str]) -> None:
        """Select a locale by scanning the first lines for known markers."""
        for line in lines[:LOCALE_DETECTION_LINE_LIMIT]:
            if any(marker in line for marker in ROMANIAN_LOCALE_MARKERS):
                self.locale = "ro"
                self._labels = ROMANIAN_LABELS
                return
            if any(marker in line for marker in ENGLISH_LOCALE_MARKERS):
                self.locale = "en"
                self._labels = ENGLISH_LABELS
                return
        self._add_issue(
            "could not determine RW5 locale from the first %d lines; "
            "assuming %r" % (LOCALE_DETECTION_LINE_LIMIT, DEFAULT_LOCALE),
            severity=IssueSeverity.WARNING,
        )
        self.locale = DEFAULT_LOCALE
        self._labels = ENGLISH_LABELS

    def _add_issue(
        self,
        message: str,
        *,
        severity: IssueSeverity = IssueSeverity.WARNING,
        record_id: Optional[str] = None,
    ) -> None:
        """Append one :class:`ParseIssue` describing an unusable input."""
        self.issues.append(
            ParseIssue(
                source_path=self._source_path,
                severity=severity,
                message=message,
                record_id=record_id,
            )
        )

    def _read_line(self, line_number: int, line: str) -> None:
        """Dispatch one RW5 line, isolating failures to that single line."""
        try:
            if not line.strip():
                return
            # Split notes glued onto tagged records without a newline.
            if not line.startswith("--"):
                glued = _GLUED_NOTE_RE.search(line)
                if glued is not None:
                    self._read_line(line_number, line[: glued.start()])
                    self._process_note(line[glued.end() :])
                    return
            fields = line.split(",")
            record_code = fields[0]
            # SurvCE often writes projected ``GS``/``GT`` (and rarely other
            # tagged records) as ``--``-prefixed comment lines. Treat those
            # as real records when the stripped code is known.
            if record_code.startswith("--"):
                stripped = record_code[2:]
                is_of_hd_note = (
                    stripped == "OF"
                    and len(fields) > 1
                    and fields[1].startswith("HD")
                )
                if stripped in self._dispatch and not is_of_hd_note:
                    record_code = stripped
                    fields = [record_code, *fields[1:]]
                    line = line[2:]
            handler = self._dispatch.get(record_code)
            if handler is not None:
                handler(fields, line)
            elif line.startswith("--"):
                body = line[2:]
                # TDS/X-change embeds a base ``SP`` after a banner note.
                if body.startswith(NOTE_GPS_REFERENCE_STATION) and ",SP," in (
                    body
                ):
                    sp_at = body.index(",SP,") + 1
                    logger.log(
                        1,
                        "acknowledging GPS Reference station banner: %s",
                        body[:sp_at].rstrip(","),
                    )
                    self._read_line(line_number, body[sp_at:])
                    return
                self._process_note(body)
            elif line.startswith(NOTE_INITIALIZATION_TIME):
                self._set_observation_note(
                    "initialization_time",
                    line.removeprefix(NOTE_INITIALIZATION_TIME).strip(),
                )
            else:
                logger.debug("unrecognized RW5 record code in: %s", line)
        except Exception:
            logger.warning(
                "failed to parse RW5 line %d: %s",
                line_number,
                line,
                exc_info=True,
            )
            self._add_issue(
                "failed to parse line %d: %s" % (line_number, line),
                severity=IssueSeverity.WARNING,
                record_id=str(line_number),
            )

    @staticmethod
    def _tagged_fields(fields: List[str]) -> Dict[str, str]:
        """Split ``TAGvalue`` fields (all but the record code) into a dict."""
        return {field[0:2]: field[2:] for field in fields[1:]}

    def _current_base_point(self) -> Rw5BasePoint:
        """Return the current base point, creating a default one if none."""
        if not self.base_points:
            base = Rw5BasePoint(
                name=self._network_name(),
                latitude=0,
                longitude=0,
                elevation=0,
                number=0,
            )
            self.base_points.append(base)
        return self.base_points[-1]

    def _current_gps_point(self) -> Rw5GpsPoint:
        """Return the current GPS point, creating a placeholder if none."""
        base = self._current_base_point()
        if not base.points:
            base.points.append(Rw5GpsPoint(name=base.name, base=base))
        return base.points[-1]

    def _observation_note_is_pending(self, gps: Rw5GpsPoint) -> bool:
        """Return whether a note line belongs to the next rover observation."""
        base = self._current_base_point()
        if gps.north is not None and gps.east is not None:
            return True
        if (
            gps.name is not None
            and base.name is not None
            and str(gps.name) == str(base.name)
            and gps.north is None
        ):
            return True
        return False

    def _target_gps_for_observation_note(self) -> Optional[Rw5GpsPoint]:
        """Return the rover that should receive an observation note.

        Returns:
            The known rover point, or ``None`` when the note is pending.
        """
        base = self._current_base_point()
        if not base.points:
            return None
        gps = base.points[-1]
        if self._observation_note_is_pending(gps):
            return None
        return gps

    def _set_observation_note(self, attr: str, value: str) -> None:
        """Store one South/Cube per-observation note on the current rover."""
        gps = self._target_gps_for_observation_note()
        if gps is None:
            self._pending_observation_notes[attr] = value
            return
        setattr(gps, attr, value)

    def _flush_pending_observation_notes(self, gps: Rw5GpsPoint) -> None:
        """Apply buffered observation notes to a newly materialized rover."""
        for attr, value in self._pending_observation_notes.items():
            setattr(gps, attr, value)
        self._pending_observation_notes.clear()

    def _flush_pending_local_time(self, gps: Rw5GpsPoint) -> None:
        """Apply a buffered time stamp to a newly materialized rover."""
        if self._pending_local_time is None:
            return
        gps.local_time = self._pending_local_time
        self._pending_local_time = None

    @staticmethod
    def _point_names_equal(
        left: Optional[Union[str, int]],
        right: Optional[Union[str, int]],
    ) -> bool:
        """Return whether two SurvCE point names refer to the same PN."""
        if left is None or right is None:
            return False
        left_text = str(left)
        right_text = str(right)
        if left_text == right_text:
            return True
        if left_text.isdigit() and right_text.isdigit():
            return int(left_text) == int(right_text)
        return False

    def _find_gps_by_name(self, point_name: str) -> Optional[Rw5GpsPoint]:
        """Return the most recent GPS/SP point matching ``point_name``."""
        found: Optional[Rw5GpsPoint] = None
        for base in self.base_points:
            for point in base.points:
                if self._point_names_equal(point.name, point_name):
                    found = point
        return found

    def _apply_point_code_note(self, gps: Rw5GpsPoint, text: str) -> None:
        """Fill empty ``comment``/``code`` from a ``{PN}-{code}…`` note."""
        if not gps.comment:
            gps.comment = text
        if not gps.code:
            gps.code = text

    def _process_point_code_note(self, line: str) -> bool:
        """Handle one ``{PN}-{code}…`` free-text note, if it matches.

        Attaches to an existing point with that PN when present; otherwise
        buffers the note for a later ``GPS``/``SP`` (or orphan at EOF).
        Never attaches to the previous unrelated observation.
        """
        match = _POINT_CODE_NOTE_RE.match(line.strip())
        if match is None:
            return False
        point_name = match.group("pn")
        text = match.group("text").strip()
        note = Rw5PointCodeNote(
            point_name=point_name, text=text, raw=line.strip()
        )
        target = self._find_gps_by_name(point_name)
        if target is not None:
            self._apply_point_code_note(target, text)
            logger.log(
                1,
                "applied point-code note to PN%s: %s",
                point_name,
                text,
            )
            return True
        self._pending_point_code_notes[point_name] = note
        logger.log(
            1,
            "pending point-code note for PN%s: %s",
            point_name,
            text,
        )
        return True

    def _flush_pending_point_code_note(self, gps: Rw5GpsPoint) -> None:
        """Apply a buffered ``{PN}-{code}…`` note onto a new GPS/SP point."""
        if gps.name is None or not self._pending_point_code_notes:
            return
        matched_key: Optional[str] = None
        note: Optional[Rw5PointCodeNote] = None
        for pending_pn, pending_note in self._pending_point_code_notes.items():
            if self._point_names_equal(gps.name, pending_pn):
                matched_key = pending_pn
                note = pending_note
                break
        if matched_key is None or note is None:
            return
        self._apply_point_code_note(gps, note.text)
        del self._pending_point_code_notes[matched_key]

    def _flush_orphan_point_code_notes(self) -> None:
        """Move unmatched ``{PN}-{code}…`` notes onto ``orphan_point_notes``."""
        if not self._pending_point_code_notes:
            return
        self.orphan_point_notes.extend(self._pending_point_code_notes.values())
        self._pending_point_code_notes.clear()

    def _network_name(self) -> str:
        """Return the RTK network name, without its leading ``NTRIP`` tag."""
        if not self.rtk_network:
            return "?"
        return self.rtk_network.replace("NTRIP ", "")

    # -- Tagged record codes -------------------------------------------

    def _process_job_record(self, fields: List[str], line: str) -> None:
        """Handle the ``JB`` job header record."""
        self.job_name = fields[1][2:]
        try:
            self.job_datetime = _parse_dt_stamp(
                fields[2] + fields[3], _JOB_DATETIME_FORMATS
            )
        except (IndexError, ValueError):
            logger.debug(
                "could not parse job date/time from: %s", line, exc_info=True
            )

    def _process_base_point(self, fields: List[str], line: str) -> None:
        """Handle the ``BP`` base station record."""
        tagged = self._tagged_fields(fields)
        base = Rw5BasePoint(
            name=self._network_name(),
            latitude=_to_geographic_degrees(tagged.get("LA")),
            longitude=_to_geographic_degrees(tagged.get("LN")),
            elevation=_to_float(
                tagged.get("EL") or tagged.get("HT") or tagged.get("ET")
            ),
            number=_to_int_or_str(tagged.get("PN")),
            unknown_ag=tagged.get("AG"),
            unknown_pa=tagged.get("PA"),
            configured_by_gps_position=self._pending_base_configured,
            point_used=self._pending_point_used,
        )
        self._pending_base_configured = False
        self._pending_point_used = None
        self._pending_ep = None
        self.base_points.append(base)

    def _process_line_of_sight(self, fields: List[str], line: str) -> None:
        """Handle the ``LS`` line-of-sight (true antenna height) record."""
        tagged = self._tagged_fields(fields)
        self._true_antenna_height = _to_float(tagged.get("HR"))

    def _process_mode_setup(self, fields: List[str], line: str) -> None:
        """Handle the ``MO`` measurement mode setup record."""
        tagged = self._tagged_fields(fields)
        self.units = "metric" if tagged.get("UN") == "1" else tagged.get("UN")
        self.scale_factor = _to_float(tagged.get("SF"))
        self.earth_curvature_on = tagged.get("EC") == "1"
        self.edm_offset = _to_float(tagged.get("EO"))

    def _process_offset_shot(self, fields: List[str], line: str) -> None:
        """Handle the ``OF`` off-center-shot record, for the next point."""
        tagged = self._tagged_fields(fields)
        azimuth = _to_float(tagged.get("AZ"))
        distance = _to_float(tagged.get("HD"))
        delta_z = _to_float(tagged.get("CE"))
        if distance == 0.0:
            distance, delta_z = delta_z, distance
        self._pending_offset_azimuth = azimuth
        self._pending_offset_distance = distance
        self._pending_offset_delta_z = delta_z

    def _process_gps_record(self, fields: List[str], line: str) -> None:
        """Handle the ``GPS`` rover observation start record."""
        tagged = self._tagged_fields(fields)
        base = self._current_base_point()
        gps = Rw5GpsPoint(
            base=base,
            latitude=_to_geographic_degrees(tagged.get("LA")),
            longitude=_to_geographic_degrees(tagged.get("LN")),
            elevation=_to_float(tagged.get("EL")),
            name=tagged.get("PN"),
            comment=tagged.get("--"),
            entered_antenna_height=self._entered_antenna_height,
            entered_antenna_method=self._entered_antenna_method,
            true_antenna_height=self._true_antenna_height,
            off_azimuth=self._pending_offset_azimuth,
            off_distance=self._pending_offset_distance,
            off_delta_z=self._pending_offset_delta_z,
        )
        self._pending_offset_azimuth = None
        self._pending_offset_distance = None
        self._pending_offset_delta_z = None
        self._flush_pending_observation_notes(gps)
        self._flush_pending_local_time(gps)
        self._flush_pending_point_code_note(gps)
        base.points.append(gps)

    def _process_g0(self, fields: List[str], line: str) -> None:
        """Handle the ``G0`` observation moment/base-id record."""
        gps = self._current_gps_point()
        gps.moment = _parse_moment(fields[1], self._labels)

        with_method = _BASE_ID_WITH_METHOD_RE.match(fields[2])
        if with_method is not None:
            gps.method = with_method.group(1)
            base_id = with_method.group(2).strip() or None
        else:
            plain = _BASE_ID_RE.match(fields[2])
            if plain is None:
                logger.debug("unrecognized base-id text: %s", fields[2])
                base_id = fields[2].strip() or None
            else:
                base_id = plain.group(1).strip() or None
            gps.method = "Average"

        base = self._current_base_point()
        if base_id is None:
            # SurvCE sometimes leaves the ID blank; keep any prior base_id.
            return
        if base.base_id is None:
            base.base_id = base_id
        elif base.base_id != base_id:
            # SurvCE reused the previous base station's placeholder for
            # this shot; split it into its own base-point group.
            base.points = base.points[:-1]
            new_base = Rw5BasePoint(
                name=self._network_name(),
                latitude=-1,
                longitude=-1,
                elevation=-1,
                number=-1,
                base_id=base_id,
            )
            new_base.points = [gps]
            gps.base = new_base
            self.base_points.append(new_base)

    def _process_g1(self, fields: List[str], line: str) -> None:
        """Handle the ``G1`` baseline delta record."""
        gps = self._current_gps_point()
        tagged = self._tagged_fields(fields)
        gps.delta_x = _to_float(tagged.get("DX"))
        gps.delta_y = _to_float(tagged.get("DY"))
        gps.delta_z = _to_float(tagged.get("DZ"))

    def _process_g2(self, fields: List[str], line: str) -> None:
        """Handle the ``G2`` baseline velocity record."""
        gps = self._current_gps_point()
        tagged = self._tagged_fields(fields)
        gps.g2_velocity_x = _to_float(tagged.get("VX"))
        gps.g2_velocity_y = _to_float(tagged.get("VY"))
        gps.g2_velocity_z = _to_float(tagged.get("VZ"))

    def _process_g3(self, fields: List[str], line: str) -> None:
        """Handle the ``G3`` baseline covariance record."""
        gps = self._current_gps_point()
        tagged = self._tagged_fields(fields)
        gps.g3_xy = _to_float(tagged.get("XY"))
        gps.g3_xz = _to_float(tagged.get("XZ"))
        gps.g3_yz = _to_float(tagged.get("YZ"))

    # -- ``--`` note lines ------------------------------------------------

    def _process_note(self, line: str) -> None:
        """Handle one ``--``-prefixed free-text note line."""
        if line.startswith(NOTE_SURVCE_VERSION):
            self.survce_version = line.removeprefix(NOTE_SURVCE_VERSION)
        elif line.startswith(NOTE_SURVCE_VERSION_NO_SPACE):
            self.survce_version = line.removeprefix(
                NOTE_SURVCE_VERSION_NO_SPACE
            )
        elif line.startswith(NOTE_SURVX):
            self.survce_version = line.strip()
        elif line.startswith(NOTE_STONEX_CUBE_A):
            # Cube-a banners look like ``Stonex Cube-a v6.3.20.2024.07.24``.
            self.survce_version = line.removeprefix(NOTE_STONEX_CUBE_A).strip()
        elif line.startswith(NOTE_TDS_RW5):
            self.survce_version = line.strip()
        elif line.startswith(NOTE_GPS_SURVEY):
            self.survce_version = line.strip()
        elif line.startswith(NOTE_GPS_REFERENCE_STATION):
            logger.log(1, "ignoring GPS Reference station note: %s", line)
        elif line.startswith("Replaced point ID"):
            logger.log(1, "ignoring replaced-point-id note: %s", line)
        elif line.startswith(NOTE_SCALE_POINT):
            self.scale_point = line.removeprefix(NOTE_SCALE_POINT).strip()
        elif line.startswith(NOTE_BASE_CONFIGURATION):
            self._mark_pending_base_configured()
        elif line.startswith(NOTE_BASE_CONFIGURATION_STATE_PLANE):
            self._mark_pending_base_configured()
        elif line.startswith(NOTE_BASE_CONFIGURATION_PREVIOUSLY_SURVEYED):
            self._mark_pending_base_configured()
        elif line.startswith("SP North:"):
            self._set_sp_north_note(line)
        elif line.startswith(NOTE_GNSS_POSITION_ADJUSTMENT):
            logger.log(1, "ignoring GNSS position adjustment note: %s", line)
        elif line.startswith("From Pt"):
            # SurvCE localization report: ``From Pt100`` then ``Translate:``.
            logger.log(1, "ignoring localization From Pt note: %s", line)
        elif line.startswith("Calculate area of polyline"):
            # SurvCE COGO area report; point list is part of the label.
            self.polyline_area_note = line.strip() or None
            logger.log(1, "acknowledging polyline area note: %s", line)
        elif line.startswith("Averaged position from"):
            logger.log(1, "ignoring averaged-position banner: %s", line)
        elif line.startswith("Calculated from Point Projection"):
            logger.log(1, "ignoring point-projection banner: %s", line)
        elif line.startswith("DT"):
            self._process_dt_note(line)
        elif line.startswith("TM"):
            self._process_local_time_of_day(line)
        elif line.startswith("CTS"):
            self._process_stakeout_note(line)
        elif line.startswith(("HSIG:", "HSDV:", "HRMS:")):
            self._process_quality_note(line)
        elif line.startswith("OF,HD"):
            self._process_offset_distance_note(line)
        else:
            self._process_labelled_note(line)

    def _process_dt_note(self, line: str) -> None:
        """Apply a ``DT`` local-date note to the base or current rover.

        When the current rover already has projected coordinates (or is a
        base echo), buffer the stamp for the next ``GPS`` observation so
        pre-offset ``DT``/``TM`` pairs are not mis-attached to the previous
        completed shot.
        """
        try:
            stamp = _parse_dt_stamp(line, _DT_NOTE_FORMATS)
        except ValueError:
            logger.debug(
                "could not parse DT local-date note: %s",
                line,
                exc_info=True,
            )
            return
        base = self._current_base_point()
        if not base.points:
            # Base-config blocks timestamp the BP, not a rover stub.
            base.local_time = stamp
            return
        gps = base.points[-1]
        if self._observation_note_is_pending(gps):
            self._pending_local_time = stamp
            return
        gps.local_time = stamp

    def _process_local_time_of_day(self, line: str) -> None:
        """Add a ``TM`` time-of-day offset to the base or current rover."""
        base = self._current_base_point()
        if not base.points:
            target_time = base.local_time
            if target_time is None:
                logger.debug(
                    "TM note line without a preceding DT line: %s", line
                )
                return
            hours, minutes, seconds = (
                int(part) for part in line[2:].split(":")
            )
            base.local_time = target_time + datetime.timedelta(
                hours=hours, minutes=minutes, seconds=seconds
            )
            return

        gps = base.points[-1]
        if self._observation_note_is_pending(gps):
            if self._pending_local_time is None:
                logger.debug(
                    "TM note line without a preceding DT line: %s", line
                )
                return
            hours, minutes, seconds = (
                int(part) for part in line[2:].split(":")
            )
            self._pending_local_time = (
                self._pending_local_time
                + datetime.timedelta(
                    hours=hours, minutes=minutes, seconds=seconds
                )
            )
            return

        if gps.local_time is None:
            logger.debug("TM note line without a preceding DT line: %s", line)
            return
        hours, minutes, seconds = (int(part) for part in line[2:].split(":"))
        gps.local_time = gps.local_time + datetime.timedelta(
            hours=hours, minutes=minutes, seconds=seconds
        )

    def _process_stakeout_note(self, line: str) -> None:
        """Handle one ``CTSSn``/``CTSDn`` stakeout data note line.

        SurvCE wraps long ``CTSD`` rows across ``CTSD0``/``CTSD1``/... lines
        and marks a wrap with a trailing comma. That comma is a
        continuation marker, not an empty field. Middle empty fields
        (unused Cut/Fill, empty Ref columns) stay significant; the wrap
        marker is dropped only when the next ``CTSD`` fragment arrives.

        Each ``CTSS`` starts a new stakeout block. Blocks accumulate on the
        parser and are assigned to the matching ``Stake Pt#`` / ``Design
        Pt#`` observation when present, otherwise to the latest rover.
        Job-header dumps before any observation are ignored.
        """
        prefix, separator, rest = line.partition(":")
        if not separator:
            logger.debug("malformed stakeout note line: %s", line)
            return
        if prefix.startswith("CTSS"):
            self._flush_pending_stakeout()
            fallback = self._stakeout_target_gps()
            if fallback is None:
                logger.log(
                    1, "ignoring stakeout note before first GPS: %s", line
                )
                return
            self._pending_stk_source = rest
            self._pending_stk_fallback = fallback
            self._clear_pending_stakeout_rows()
            return
        if not prefix.startswith("CTSD"):
            logger.debug("unrecognized stakeout note line: %s", line)
            return
        if (
            self._pending_stk_fallback is None
            and self._stakeout_target_gps() is None
            and self._pending_stk_labels is None
        ):
            logger.log(1, "ignoring stakeout note before first GPS: %s", line)
            return
        try:
            index = int(prefix.removeprefix("CTSD"))
        except ValueError:
            logger.debug("unrecognized stakeout note line: %s", line)
            return

        if self._pending_stk_fallback is None:
            self._pending_stk_fallback = self._stakeout_target_gps()

        # Keep the raw split, including a trailing "" when rest ends with
        # "," — that marker is removed when the next fragment arrives.
        fields = rest.split(",")
        wrapped = rest.endswith(",")

        if index == 0 and self._pending_stk_labels is None:
            self._pending_stk_labels = fields
            self._pending_stk_wrapped = wrapped
        elif index == 0 and self._pending_stk_values is None:
            # Second CTSD0 starts values; close any open label wrap.
            _drop_pending_ctsd_wrap(self, using_values=False)
            self._pending_stk_values = fields
            self._pending_stk_wrapped = wrapped
        elif index == 0:
            # New label header without a CTSS (schema change mid-dump).
            self._flush_pending_stakeout()
            self._pending_stk_fallback = self._stakeout_target_gps()
            self._pending_stk_labels = fields
            self._pending_stk_values = None
            self._pending_stk_wrapped = wrapped
        elif self._pending_stk_values is not None:
            _drop_pending_ctsd_wrap(self, using_values=True)
            self._pending_stk_values.extend(fields)
            self._pending_stk_wrapped = wrapped
        elif self._pending_stk_labels is not None:
            _drop_pending_ctsd_wrap(self, using_values=False)
            self._pending_stk_labels.extend(fields)
            self._pending_stk_wrapped = wrapped
        else:
            logger.debug(
                "stakeout continuation line without a preceding CTSD0 "
                "line: %s",
                line,
            )

    def _clear_pending_stakeout_rows(self) -> None:
        """Clear pending CTSD label/value rows (keep source/fallback)."""
        self._pending_stk_labels = None
        self._pending_stk_values = None
        self._pending_stk_wrapped = False

    def _flush_pending_stakeout(self) -> None:
        """Assign a completed pending stakeout block onto a GPS point."""
        labels = self._pending_stk_labels
        values = self._pending_stk_values
        if labels is None and values is None:
            self._pending_stk_fallback = None
            return
        if self._pending_stk_wrapped:
            # A final values fragment may end with empty Ref columns; keep
            # those. A dangling label wrap (no continuation) is a phantom.
            if values is None and labels and labels[-1] == "":
                labels.pop()
            self._pending_stk_wrapped = False
        matched = self._stakeout_point_for_names(labels, values)
        gps = matched or self._pending_stk_fallback
        if gps is None:
            logger.log(
                1,
                "dropping stakeout block without a target GPS "
                "(labels=%s values=%s)",
                None if labels is None else len(labels),
                None if values is None else len(values),
            )
            self._clear_pending_stakeout_rows()
            self._pending_stk_fallback = None
            return
        if matched is None and gps.is_stakeout_point():
            # Orphan dumps (for example alignment ``PP``) must not replace
            # a stakeout already matched onto this observation.
            logger.log(
                1,
                "dropping unmatched stakeout block; %r already has stakeout",
                gps.name,
            )
            self._clear_pending_stakeout_rows()
            self._pending_stk_fallback = None
            return
        gps.stk_source = self._pending_stk_source
        gps.stk_labels = labels
        gps.stk_values = values
        gps.stk_ctsd_wrapped = False
        self._clear_pending_stakeout_rows()
        self._pending_stk_fallback = None

    def _stakeout_point_for_names(
        self,
        labels: Optional[List[str]],
        values: Optional[List[str]],
    ) -> Optional[Rw5GpsPoint]:
        """Return the GPS/SP named by the stakeout point fields."""
        if labels is None or values is None or len(labels) != len(values):
            return None
        mapping = dict(zip(labels, values))
        for key in ("Stake Pt#", "Design Pt#"):
            name = (mapping.get(key) or "").strip()
            if not name or name == "PP":
                continue
            found = self._find_gps_named(name)
            if found is not None:
                return found
        return None

    def _find_gps_named(self, name: str) -> Optional[Rw5GpsPoint]:
        """Return the first GPS/SP point whose name equals ``name``."""
        for base in self.base_points:
            for gps in base.points:
                if gps.name is not None and str(gps.name) == name:
                    return gps
        return None

    def _stakeout_target_gps(self) -> Optional[Rw5GpsPoint]:
        """Return the rover that should receive stakeout notes, if any.

        Returns:
            The latest real rover observation, or ``None`` when stakeout
            notes appear before the first GPS point (job-header dumps).
        """
        if not self.base_points:
            return None
        base = self.base_points[-1]
        if not base.points:
            return None
        gps = base.points[-1]
        # Auto-created placeholder that mirrors the base/network name.
        if (
            gps.north is None
            and gps.latitude is None
            and gps.name is not None
            and base.name is not None
            and str(gps.name) == str(base.name)
        ):
            return None
        return gps

    def _process_quality_note(self, line: str) -> None:
        """Handle one ``HSIG``/``HSDV``/``HRMS`` quality summary note."""
        gps = self._current_gps_point()
        for item in line.split(","):
            match = _HSIG_ITEM_RE.match(item)
            if match is None:
                logger.debug("unrecognized quality item: %s", item)
                continue
            label, value = match.groups()
            if label in ("HSDV", "HSIG", "HRMS"):
                gps.hsdv = _to_float(value)
            elif label in ("VSDV", "VSIG", "VRMS"):
                gps.vsdv = _to_float(value)
            elif label == "STATUS":
                gps.status = value
            elif label == "SATS":
                gps.sat_count = _to_int(value)
            elif label == "PDOP":
                gps.pdop = _to_float(value)
            elif label == "HDOP":
                gps.hdop = _to_float(value)
            elif label == "VDOP":
                gps.vdop = _to_float(value)
            elif label == "TDOP":
                gps.tdop = _to_float(value)
            elif label == "GDOP":
                gps.gdop = _to_float(value)
            elif label == "AGE":
                # Per-shot differential age (seconds); occupation ``AGE Avg``
                # notes use the labelled-average path instead.
                gps.age_avg = _to_float(value)
            elif label in ("NSDV", "ESDV", "NSIG", "ESIG"):
                logger.log(1, "ignoring quality item %s=%s", label, value)
            else:
                logger.warning("unknown quality item %r in: %s", label, line)

    def _process_offset_distance_note(self, line: str) -> None:
        """Handle one extra ``OF,HD<value>`` offset-distance note."""
        gps = self._current_gps_point()
        value = _to_float(line.removeprefix("OF,HD"))
        if value is not None:
            gps.off_horizontal_distances.append(value)

    def _process_labelled_note(self, line: str) -> None:
        """Handle one ``Label: value`` free-text note line."""
        if line.startswith("Offset "):
            # A secondary offset-record rendering; the ``OF`` record
            # already captured the values that matter.
            return
        # SurvCE sometimes omits the space after ``:`` (Entered Base HR).
        if ": " in line:
            label, value = line.split(": ", 1)
        elif ":" in line:
            label, value = line.split(":", 1)
            value = value.lstrip()
        else:
            if self._process_point_code_note(line):
                return
            logger.debug("unrecognized note line: %s", line)
            return
        if (
            label == self._labels["RTK Method"]
            or label == ENGLISH_LABELS["RTK Method"]
        ):
            # Unlike every other label, SurvCE repeats this label as the
            # sub-key of the first comma-separated part, so the handler
            # needs the whole line rather than just the value after
            # the first ``": "``.
            self._set_rtk_method(line)
            return
        handler = self._label_handlers().get(label)
        if handler is not None:
            handler(value)
        elif label in (
            self._labels["Adjustments"],
            ENGLISH_LABELS["Adjustments"],
        ):
            logger.log(1, "ignoring adjustments label line: %s", line)
        elif label in ("Calculated", "Measured", "Delta"):
            # Total-station backsight check summaries.
            logger.log(1, "ignoring TS backsight check note: %s", line)
        elif not value.strip():
            # Empty custom GIS/user fields (for example ``ABC:``).
            logger.log(1, "ignoring empty labelled note: %s", line)
        else:
            logger.debug("unknown label %r in line: %s", label, line)

    def _label_handlers(self) -> Dict[str, Callable[[str], None]]:
        """Return the ``label -> setter`` dispatch table.

        Registers both the active-locale spelling and the English spelling
        for each key so mixed-locale SurvCE jobs (Romanian markers with
        English ``Equipment`` / quality notes) still parse.
        """
        by_key: Dict[str, Callable[[str], None]] = {
            "CRD": self._set_crd,
            "User Defined": self._set_coordinate_system,
            "Equipment": self._set_equipment,
            "Instrument Model": self._set_instrument_model,
            "Date (creation)": self._set_date_creation,
            "Date (last modification)": self._set_date_last_modification,
            "Antenna height": self._set_antenna_height_note,
            "AGE Avg": self._set_age_average,
            "Antenna": self._set_antenna_note,
            "Attribute": self._set_attribute_note,
            "Antenna Type": self._set_antenna_type,
            "GNSS Profile Tolerance PP": (self._set_gnss_profile_tolerance_pp),
            "GNSS Profile Tolerance RT": (self._set_gnss_profile_tolerance_rt),
            "GNSS Statistics PP": self._set_gnss_statistics_pp,
            "GNSS Statistics RT": self._set_gnss_statistics_rt,
            "Instrument Selected": self._set_instrument_selected,
            "Localization File": self._set_localization_file,
            "Localization Type": self._set_localization_type,
            "Reference System": self._set_reference_system,
            "TS Angles": self._set_ts_angles,
            "TS Scale": self._set_ts_scale,
            "EDM Mode": self._set_edm_mode,
            "P.C. mm Applied": self._set_pc_mm_applied,
            "Translate": self._set_localization_translate,
            "Point Used": self._set_point_used,
            "Averaged Points": self._set_averaged_points,
            "StdDev N": self._set_stddev_n,
            "StdDev E": self._set_stddev_e,
            "StdDev Z": self._set_stddev_z,
            "Station": self._set_station_note,
            "Geoid Separation File": self._set_geoid_file,
            "Grid Adjustment File": self._set_grid_adjustment_file,
            "GPS Scale": self._set_gps_scale,
            "Gnss Device": self._set_gnss_device,
            "Entered HR": self._set_entered_antenna_height,
            "Entered Rover HR": self._set_entered_rover_height,
            "Entered Base HR": self._set_entered_base_hr,
            "Valid Readings": self._set_valid_readings,
            "Fixed Readings": self._set_fixed_readings,
            "Float Readings": self._set_float_readings,
            "DGPS Readings": self._set_dgps_readings,
            "AUTO Readings": self._set_auto_readings,
            "Nor Min": self._set_north_range,
            "Eas Min": self._set_east_range,
            "Elv Min": self._set_elevation_range,
            "Nor Avg": self._set_north_average,
            "Eas Avg": self._set_east_average,
            "Elv Avg": self._set_elevation_average,
            "ERMS Avg": self._set_erms_average,
            "HRMS Avg": self._set_hrms_average,
            "HSIG Avg": self._set_hsdv_average,
            "HSDV Avg": self._set_hsdv_average,
            "VRMS Avg": self._set_vrms_average,
            "VSIG Avg": self._set_vsdv_average,
            "VSDV Avg": self._set_vsdv_average,
            "NRMS Avg": self._set_nrms_average,
            "HDOP Avg": self._set_hdop_average,
            "VDOP Avg": self._set_vdop_average,
            "PDOP Avg": self._set_pdop_average,
            "PP Time": self._set_pp_time,
            "Number of Satellites Avg": self._set_satellites_average,
        }
        handlers: Dict[str, Callable[[str], None]] = {}
        for key, handler in by_key.items():
            handlers[self._labels[key]] = handler
            handlers[ENGLISH_LABELS[key]] = handler
        return handlers

    def _set_crd(self, value: str) -> None:
        self.crd = value

    def _set_coordinate_system(self, value: str) -> None:
        self.coordinate_system = value

    def _set_equipment(self, value: str) -> None:
        self.equipment = value.strip() or None

    def _set_instrument_model(self, value: str) -> None:
        """Store TDS/X-change ``Instrument Model`` (and fill equipment)."""
        cleaned = value.strip() or None
        self.instrument_model = cleaned
        if self.equipment is None:
            self.equipment = cleaned

    def _set_date_creation(self, value: str) -> None:
        self.date_creation = value.strip() or None

    def _set_date_last_modification(self, value: str) -> None:
        self.date_last_modification = value.strip() or None

    def _set_antenna_height_note(self, value: str) -> None:
        """Store TDS ``Antenna height`` for following SP/GPS points."""
        self._entered_antenna_height = _to_float(value.strip())
        self._entered_antenna_method = None

    def _set_antenna_type(self, value: str) -> None:
        parts = value.split(",")
        self.antenna_type = parts[0]
        # ``_tagged_fields`` skips its own first element, which here is
        # the antenna type name already extracted above.
        tagged = self._tagged_fields(parts)
        self.antenna_radius = _strip_suffix(tagged.get("RA"))
        self.antenna_slant_height = _strip_prefix_suffix(tagged.get("SH"))
        self.antenna_l1_offset = _strip_suffix(tagged.get("L1"))
        self.antenna_l2_offset = _strip_suffix(tagged.get("L2"))
        self.antenna_description = tagged.get("--")

    def _set_antenna_note(self, value: str) -> None:
        self._set_observation_note("antenna_note", value)

    def _set_attribute_note(self, value: str) -> None:
        self._set_observation_note("attribute_note", value)

    def _set_gnss_profile_tolerance_pp(self, value: str) -> None:
        self._set_observation_note("gnss_profile_tolerance_pp", value)

    def _set_gnss_profile_tolerance_rt(self, value: str) -> None:
        self._set_observation_note("gnss_profile_tolerance_rt", value)

    def _set_gnss_statistics_pp(self, value: str) -> None:
        self._set_observation_note("gnss_statistics_pp", value)

    def _set_gnss_statistics_rt(self, value: str) -> None:
        self._set_observation_note("gnss_statistics_rt", value)

    def _set_instrument_selected(self, value: str) -> None:
        self._set_observation_note("instrument_selected", value)

    def _set_pp_time(self, value: str) -> None:
        self._set_observation_note("pp_time", value)

    def _set_localization_file(self, value: str) -> None:
        self.localization_file = value

    def _set_localization_type(self, value: str) -> None:
        self.localization_type = value

    def _set_localization_translate(self, value: str) -> None:
        """Store SurvCE localization ``Translate: dy=…, dx=…, dz=…``."""
        match = _TRANSLATE_RE.search(value)
        if match is None:
            logger.debug("unrecognized Translate note value: %s", value)
            return
        self.localization_translate_dy = _to_float(match.group("dy"))
        self.localization_translate_dx = _to_float(match.group("dx"))
        self.localization_translate_dz = _to_float(match.group("dz"))

    def _mark_pending_base_configured(self) -> None:
        """Mark the current or next ``BP`` as configured from known coords.

        SurvCE may write the banner either just after a ``BP`` that has no
        rover shots yet, or just before a new ``BP`` (after an earlier
        occupation). Apply immediately in the first case; otherwise hold
        the flag until the following ``BP``.
        """
        if self.base_points and not self.base_points[-1].points:
            self.base_points[-1].configured_by_gps_position = True
            return
        self._pending_base_configured = True

    def _set_point_used(self, value: str) -> None:
        """Store ``Point Used`` on the current or next ``BP``."""
        cleaned = value.strip() or None
        if self.base_points and not self.base_points[-1].points:
            self.base_points[-1].point_used = cleaned
            return
        self._pending_point_used = cleaned

    def _set_averaged_points(self, value: str) -> None:
        """Store SurvCE ``Averaged Points: 1,2,3`` note text."""
        self.averaged_points = value.strip() or None

    def _set_stddev_n(self, value: str) -> None:
        self.stddev_n = _to_float(value)

    def _set_stddev_e(self, value: str) -> None:
        self.stddev_e = _to_float(value)

    def _set_stddev_z(self, value: str) -> None:
        self.stddev_z = _to_float(value)

    def _set_station_note(self, value: str) -> None:
        """Store SurvCE point-projection ``Station: …, Offset …`` text."""
        self.station_note = value.strip() or None

    def _set_sp_north_note(self, line: str) -> None:
        """Apply ``SP North: …, SP East: …, Elv: …`` to the current base.

        SurvCE writes this after ``Base Configuration by Entering State
        Plane Coordinates``, repeating the projected NEH used to plant
        the base.
        """
        match = _SP_NORTH_NOTE_RE.search(line)
        if match is None:
            logger.debug("unrecognized SP North note line: %s", line)
            return
        base = self._current_base_point()
        base.north = _to_float(match.group("north"))
        base.east = _to_float(match.group("east"))
        base.height = _to_float(match.group("elev"))

    def _set_reference_system(self, value: str) -> None:
        self.reference_system = value

    def _set_ts_angles(self, value: str) -> None:
        self.ts_angles = value

    def _set_ts_scale(self, value: str) -> None:
        self.ts_scale = _to_float(value)

    def _set_edm_mode(self, value: str) -> None:
        self.edm_mode = value.strip() or None

    def _set_pc_mm_applied(self, value: str) -> None:
        self.pc_mm_applied = value.strip() or None

    def _set_geoid_file(self, value: str) -> None:
        self.geoid_file = value

    def _set_grid_adjustment_file(self, value: str) -> None:
        self.grid_adjustment_file = value

    def _set_gps_scale(self, value: str) -> None:
        self.gps_scale = _to_float(value)

    def _set_gnss_device(self, value: str) -> None:
        self.gnss_device = value

    def _set_rtk_method(self, line: str) -> None:
        labels = self._labels
        for part in line.split(","):
            head, sep, tail = part.partition(":")
            if not sep:
                # SurvCE sometimes trails ``Device: None, None`` with a bare
                # sentinel that has no sub-key.
                if part.strip().lower() in ("", "none", "null"):
                    continue
                logger.debug("unknown RTK method part: %s", part)
                continue
            label = head.strip()
            piece = tail.strip()
            if label == labels["RTK Method"]:
                self.rtk_method = piece
            elif label == labels["Device"]:
                self.rtk_device = piece
            elif label == labels["Network"]:
                self.rtk_network = piece
            elif self.rtk_device and label == self.rtk_device:
                # SurvCE repeats the device name as the mountpoint sub-key
                # (for example ``Device Internet: NTRIP RTCM32-MSM``).
                self.rtk_network = piece
            else:
                logger.debug("unknown RTK method part: %s", part)

    def _set_entered_antenna_height(self, value: str) -> None:
        parts = value.split(",")
        self._entered_antenna_height = _to_float(parts[0])
        self._entered_antenna_method = (
            parts[1].strip() if len(parts) > 1 else None
        )

    def _set_entered_rover_height(self, value: str) -> None:
        parts = value.split(",")
        self._entered_antenna_height = _to_float(parts[0][:-2])
        self._entered_antenna_method = parts[1] if len(parts) > 1 else None

    def _set_entered_base_hr(self, value: str) -> None:
        """Store SurvCE ``Entered Base HR`` on the current base station."""
        # Values look like ``0.0000 m, Height to phase center``.
        head = value.split(",", 1)[0].strip()
        if head.endswith(" m"):
            head = head[:-2]
        self._current_base_point().entered_base_hr = _to_float(head)

    def _set_valid_readings(self, value: str) -> None:
        self._current_gps_point().valid_readings = _to_reading_count(value)

    def _set_fixed_readings(self, value: str) -> None:
        self._current_gps_point().fixed_readings = _to_reading_count(value)

    def _set_float_readings(self, value: str) -> None:
        self._current_gps_point().float_readings = _to_reading_count(value)

    def _set_dgps_readings(self, value: str) -> None:
        self._current_gps_point().dgps_readings = _to_reading_count(value)

    def _set_auto_readings(self, value: str) -> None:
        self._current_gps_point().auto_readings = _to_reading_count(value)

    def _set_north_range(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.north_min = _to_float(parts[0])
        gps.north_max = _to_float(parts[3])

    def _set_east_range(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.east_min = _to_float(parts[0])
        gps.east_max = _to_float(parts[3])

    def _set_elevation_range(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.elev_min = _to_float(parts[0])
        gps.elev_max = _to_float(parts[3])

    def _set_north_average(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.north_avg = _to_float(parts[0])
        gps.north_sd = _to_float(parts[3])

    def _set_east_average(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.east_avg = _to_float(parts[0])
        gps.east_sd = _to_float(parts[3])

    def _set_elevation_average(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.elev_avg = _to_float(parts[0])
        gps.elev_sd = _to_float(parts[3])

    def _set_hrms_average(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.hrms_avg = _to_float(parts[0])
        gps.hrms_sd = _to_float(parts[2])
        gps.hrms_min = _to_float(parts[4])
        gps.hrms_max = _to_float(parts[6])

    def _set_hsdv_average(self, value: str) -> None:
        _apply_sd_min_max_average(
            self._current_gps_point(),
            value,
            avg_attr="hrms_avg",
            sd_attr="hrms_sd",
            min_attr="hrms_min",
            max_attr="hrms_max",
        )

    def _set_vrms_average(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.vrms_avg = _to_float(parts[0])
        gps.vrms_sd = _to_float(parts[2])
        gps.vrms_min = _to_float(parts[4])
        gps.vrms_max = _to_float(parts[6])

    def _set_vsdv_average(self, value: str) -> None:
        _apply_sd_min_max_average(
            self._current_gps_point(),
            value,
            avg_attr="vrms_avg",
            sd_attr="vrms_sd",
            min_attr="vrms_min",
            max_attr="vrms_max",
        )

    def _set_age_average(self, value: str) -> None:
        gps = self._current_gps_point()
        numbers = _floats_from_note(value)
        if not numbers:
            return
        gps.age_avg = numbers[0]
        if len(numbers) >= 3:
            gps.age_min = numbers[1]
            gps.age_max = numbers[2]

    def _set_nrms_average(self, value: str) -> None:
        _apply_sd_min_max_average(
            self._current_gps_point(),
            value,
            avg_attr="nrms_avg",
            sd_attr="nrms_sd",
            min_attr="nrms_min",
            max_attr="nrms_max",
        )

    def _set_erms_average(self, value: str) -> None:
        _apply_sd_min_max_average(
            self._current_gps_point(),
            value,
            avg_attr="erms_avg",
            sd_attr="erms_sd",
            min_attr="erms_min",
            max_attr="erms_max",
        )

    def _set_hdop_average(self, value: str) -> None:
        gps = self._current_gps_point()
        numbers = _floats_from_note(value)
        if not numbers:
            return
        gps.hdop_avg = numbers[0]
        if len(numbers) >= 3:
            gps.hdop_min = numbers[1]
            gps.hdop_max = numbers[2]
        elif len(numbers) == 2:
            gps.hdop_min = numbers[1]

    def _set_vdop_average(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.vdop_avg = _to_float(parts[0])
        gps.vdop_min = _to_float(parts[2])
        gps.vdop_max = _to_float(parts[4])

    def _set_pdop_average(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.pdop_avg = _to_float(parts[0])
        gps.pdop_min = _to_float(parts[2])
        gps.pdop_max = _to_float(parts[4])

    def _set_satellites_average(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.nr_of_sat_avg = _to_int(parts[0])
        gps.nr_of_sat_min = _to_int(parts[2])
        gps.nr_of_sat_max = _to_int(parts[4])

    # -- GPS time and projected coordinate records ---------------------

    def _process_gt(self, fields: List[str], line: str) -> None:
        """Handle the ``GT`` GPS observation-window time record.

        When ``PN`` matches the current ``BP`` (same number or network
        name) and no rover with that name exists yet, this is a base-only
        occupation window after ``--GS … --Base``. Those lines must not
        invent a placeholder rover named ``?`` / the network mountpoint.
        """
        tagged = self._tagged_fields(fields)
        point_name = tagged.get("PN")
        base = self._current_base_point()
        if self._is_base_projected_gs(point_name, base):
            existing = None
            if point_name is not None:
                for point in reversed(base.points):
                    if str(point.name) == str(point_name):
                        existing = point
                        break
            if existing is None:
                # Base-config GT with no matching rover observation.
                return
            gps = existing
        else:
            gps = self._current_gps_point()
        gps.start_time_utc = time_from_gps(
            week=int(tagged["SW"]), milliseconds=int(tagged["ST"])
        )
        gps.end_time_utc = time_from_gps(
            week=int(tagged["EW"]), milliseconds=int(tagged["ET"])
        )

    def _process_gs(self, fields: List[str], line: str) -> None:
        """Handle the ``GS`` projected coordinate record."""
        tagged = self._tagged_fields(fields)
        point_name = tagged.get("PN")
        base = self._current_base_point()
        if self._is_base_projected_gs(point_name, base):
            existing = None
            if point_name is not None:
                for point in reversed(base.points):
                    if str(point.name) == str(point_name):
                        existing = point
                        break
            if existing is None:
                # Base-config / base-only projected coords (no rover PN yet).
                base.north = _to_float(tagged.get("N "))
                base.east = _to_float(tagged.get("E "))
                base.height = _to_float(tagged.get("EL"))
                return
            gps = existing
        else:
            gps = self._gps_point_for_pn(point_name, base)
        if (
            gps.name is not None
            and point_name is not None
            and str(point_name) != str(gps.name)
        ):
            logger.debug(
                "GS point name %r does not match current point %r",
                point_name,
                gps.name,
            )
        if point_name is not None and gps.name is None:
            gps.name = point_name
        gps.north = _to_float(tagged.get("N "))
        gps.east = _to_float(tagged.get("E "))
        gps.height = _to_float(tagged.get("EL"))
        code = tagged.get("--")
        if code:
            gps.code = code
            if gps.comment is None:
                gps.comment = code

    def _is_base_projected_gs(
        self,
        point_name: Optional[str],
        base: Rw5BasePoint,
    ) -> bool:
        """Return whether a ``GS``/``GT`` ``PN`` refers to the base station.

        Matches the preceding ``BP`` point number, or the RTK/VRS network
        name on :attr:`Rw5BasePoint.name` (not the placeholder ``?``).
        Used for projected ``GS`` coords and base-only ``GT`` windows.
        """
        if point_name is None:
            return False
        name = str(point_name)
        if base.number is not None and name == str(base.number):
            return True
        base_name = base.name
        if (
            base_name is not None
            and base_name != "?"
            and name == str(base_name)
        ):
            return True
        return False

    def _gps_point_for_pn(
        self,
        point_name: Optional[str],
        base: Rw5BasePoint,
    ) -> Rw5GpsPoint:
        """Return the named rover point, creating one when needed."""
        if point_name is not None:
            for point in reversed(base.points):
                if str(point.name) == str(point_name):
                    return point
        gps = Rw5GpsPoint(
            base=base,
            name=point_name,
            entered_antenna_height=self._entered_antenna_height,
            entered_antenna_method=self._entered_antenna_method,
            true_antenna_height=self._true_antenna_height,
            off_azimuth=self._pending_offset_azimuth,
            off_distance=self._pending_offset_distance,
            off_delta_z=self._pending_offset_delta_z,
            method="RTK",
        )
        self._pending_offset_azimuth = None
        self._pending_offset_distance = None
        self._pending_offset_delta_z = None
        self._apply_pending_ep(gps)
        self._flush_pending_observation_notes(gps)
        self._flush_pending_local_time(gps)
        self._flush_pending_point_code_note(gps)
        base.points.append(gps)
        return gps

    def _apply_pending_ep(self, gps: Rw5GpsPoint) -> None:
        """Copy a pending ``EP`` epoch position onto ``gps``."""
        if self._pending_ep is None:
            return
        pending = self._pending_ep
        gps.latitude = pending.get("latitude")
        gps.longitude = pending.get("longitude")
        gps.elevation = pending.get("elevation")
        if gps.hsdv is None:
            gps.hsdv = pending.get("hsdv")
        if gps.vsdv is None:
            gps.vsdv = pending.get("vsdv")
        if gps.hdop is None:
            gps.hdop = pending.get("hdop")
        if gps.vdop is None:
            gps.vdop = pending.get("vdop")
        self._pending_ep = None

    def _process_ep(self, fields: List[str], line: str) -> None:
        """Handle the ``EP`` South/Cube epoch-position record."""
        tagged = self._tagged_fields(fields)
        self._pending_ep = {
            # South/Cube EP stores true decimal degrees, not SurvCE DD.MMSS.
            "latitude": _to_float(tagged.get("LA")),
            "longitude": _to_float(tagged.get("LN")),
            "elevation": _to_float(tagged.get("HT")),
            "hsdv": _to_float(tagged.get("RH")),
            "vsdv": _to_float(tagged.get("RV")),
            "hdop": _to_float(tagged.get("DH")),
            "vdop": _to_float(tagged.get("DV")),
        }

    def _process_bl(self, fields: List[str], line: str) -> None:
        """Handle the ``BL`` South/Cube baseline record."""
        tagged = self._tagged_fields(fields)
        point_name = tagged.get("PN")
        base = self._current_base_point()
        gps = self._gps_point_for_pn(point_name, base)
        gps.delta_x = _to_float(tagged.get("DX"))
        gps.delta_y = _to_float(tagged.get("DY"))
        gps.delta_z = _to_float(tagged.get("DZ"))
        code = tagged.get("--")
        if code:
            gps.code = code
            if gps.comment is None:
                gps.comment = code
        hp = _to_float(tagged.get("HP"))
        vp = _to_float(tagged.get("VP"))
        if hp is not None and gps.hdop is None:
            gps.hdop = hp
        if vp is not None and gps.vdop is None:
            gps.vdop = vp
        if gps.method is None:
            gps.method = "RTK"

    def _process_ah(self, fields: List[str], line: str) -> None:
        """Handle the ``AH`` South/Cube antenna-height record."""
        tagged = self._tagged_fields(fields)
        measured = _to_float(tagged.get("MA"))
        true_height = _to_float(tagged.get("RA"))
        if measured is not None:
            self._entered_antenna_height = measured
        if true_height is not None:
            self._true_antenna_height = true_height

    def _process_cs(self, fields: List[str], line: str) -> None:
        """Handle the SurvX ``CS`` coordinate-system record."""
        tagged = self._tagged_fields(fields)
        zone = tagged.get("ZG") or tagged.get("ZN")
        if zone is not None:
            self.cs_zone = zone
        elif len(fields) > 2:
            self.cs_zone = fields[2]

    def _process_es(self, fields: List[str], line: str) -> None:
        """Handle the SurvX ``ES`` ellipsoid record."""
        tagged = self._tagged_fields(fields)
        self.ellipsoid = tagged.get("EM") or line

    def _process_hcat(self, fields: List[str], line: str) -> None:
        """Handle the ``HCAT`` South/Cube antenna catalog record."""
        if len(fields) > 1 and fields[1].startswith("AT "):
            self.antenna_type = fields[1][3:].strip()

    def _process_hcdp(self, fields: List[str], line: str) -> None:
        """Handle the ``HCDP`` South/Cube DOP summary record."""
        if self._pending_ep is None:
            self._pending_ep = {}
        for field in fields[1:]:
            if field.startswith("DOP "):
                self._pending_ep["hdop"] = _to_float(field[4:])

    def _process_hcrv(self, fields: List[str], line: str) -> None:
        """Handle the ``HCRV`` South/Cube raw-vector record (metadata only)."""

    def _process_hcbs(self, fields: List[str], line: str) -> None:
        """Handle the ``HCBS`` South/Cube base-station snapshot record."""

    def _process_total_station_record(
        self, fields: List[str], line: str
    ) -> None:
        """Acknowledge SurvCE total-station ``OC``/``BK``/``BD``/``SS`` lines.

        These records are recognized so they do not emit DEBUG noise, but
        polar reduction to projected NEH is not implemented yet.
        """
        logger.log(
            1,
            "acknowledged unsupported total-station record %s: %s",
            fields[0] if fields else "?",
            line,
        )

    def _process_sp(self, fields: List[str], line: str) -> None:
        """Handle the ``SP`` stored grid-point record.

        SurvCE writes ``SP`` for points that already have projected
        coordinates (keyed-in, design, or previously stored) without a
        matching ``GPS``/``GS`` observation pair. Each ``SP`` is a
        standalone point under the current base. These are not measured
        observations, so the parser leaves ``method`` / ``status`` empty
        and sets ``collection_kind="imported"`` (Type, not Method).
        """
        tagged = self._tagged_fields(fields)
        base = self._current_base_point()
        code = tagged.get("--")
        gps = Rw5GpsPoint(
            base=base,
            name=tagged.get("PN"),
            north=_to_float(tagged.get("N ")),
            east=_to_float(tagged.get("E ")),
            height=_to_float(tagged.get("EL")),
            code=code,
            comment=code,
            collection_kind="imported",
            entered_antenna_height=self._entered_antenna_height,
            entered_antenna_method=self._entered_antenna_method,
        )
        self._flush_pending_point_code_note(gps)
        base.points.append(gps)

    def records(
        self,
        *,
        include_base: bool = True,
        predicate: Optional[Callable] = None,
    ) -> Iterator[Union[Rw5BasePoint, Rw5GpsPoint]]:
        """Iterate over every parsed record, in file order.

        Args:
            include_base: Whether to yield each :class:`Rw5BasePoint`
                itself in addition to its observations.
            predicate: Optional callable used to filter yielded records.

        Yields:
            Each base point (when ``include_base``) and GPS observation,
            in file order, for which ``predicate`` is ``True`` or absent.
        """
        for base in self.base_points:
            if include_base and (predicate is None or predicate(base)):
                yield base
            for point in base.points:
                if predicate is None or predicate(point):
                    yield point

    def point_count(self) -> int:
        """Return the number of parsed GPS observations."""
        return sum(len(base.points) for base in self.base_points)


def _to_float(value: Optional[str]) -> Optional[float]:
    """Convert one tag value to ``float``, or ``None`` if unavailable."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        logger.debug("could not convert %r to float", value)
        return None


def _floats_from_note(value: str) -> List[float]:
    """Extract floats from a SurvCE quality note value string.

    Handles both ``1.500 m Min 1.200 to 1.800`` and
    ``0.6000 Min: 0.6000 Max: 0.6000``.
    """
    return [float(match.group(0)) for match in _NOTE_FLOAT_RE.finditer(value)]


def _apply_sd_min_max_average(
    gps: Rw5GpsPoint,
    value: str,
    *,
    avg_attr: str,
    sd_attr: str,
    min_attr: str,
    max_attr: str,
) -> None:
    """Parse ``Avg SD Min Max`` quality notes onto one rover record."""
    numbers = _floats_from_note(value)
    if not numbers:
        return
    setattr(gps, avg_attr, numbers[0])
    if " SD:" in value:
        if len(numbers) >= 4:
            setattr(gps, sd_attr, numbers[1])
            setattr(gps, min_attr, numbers[2])
            setattr(gps, max_attr, numbers[3])
        return
    if len(numbers) >= 3:
        setattr(gps, min_attr, numbers[1])
        setattr(gps, max_attr, numbers[2])


def survce_angle_to_decimal(value: float) -> float:
    """Convert a SurvCE ``DD.MMSSsssss`` packed angle to decimal degrees.

    SurvCE and Cube-a write ``LA``/``LN`` as degrees.minutesseconds packed
    into one float (for example ``46.35192371889`` means
    ``46°35'19.237...″``). Values whose packed minute or second field is
    out of range are treated as already-decimal degrees and returned
    unchanged (common on South/Cube ``EP`` lines).

    Parsing goes through a fixed-precision decimal string so binary float
    noise cannot turn a valid ``MM``/``SS`` pair into an out-of-range
    second field (for example ``46.3`` must stay ``46°30'00″``).

    Args:
        value: Packed SurvCE angle, or an already-decimal degree value.

    Returns:
        Decimal degrees.
    """
    sign = -1.0 if value < 0 else 1.0
    text = "%.10f" % abs(float(value))
    degrees_text, fraction_text = text.split(".")
    degrees = int(degrees_text)
    packed = (fraction_text + "0000000000")[:10]
    minutes = int(packed[0:2])
    seconds = float(packed[2:4] + "." + packed[4:])
    if minutes >= 60 or seconds >= 60.0:
        return float(value)
    return sign * (degrees + minutes / 60.0 + seconds / 3600.0)


def _to_geographic_degrees(value: Optional[str]) -> Optional[float]:
    """Parse one SurvCE packed ``LA``/``LN`` tag into decimal degrees."""
    parsed = _to_float(value)
    if parsed is None:
        return None
    return survce_angle_to_decimal(parsed)


def _to_int_or_str(value: Optional[str]) -> Optional[Union[int, str]]:
    """Convert one tag value to ``int`` when possible, else keep it as-is."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _to_int(value: Optional[str]) -> Optional[int]:
    """Convert one tag value to ``int``, or ``None`` if unavailable."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        logger.debug("could not convert %r to int", value)
        return None


def _to_reading_count(value: Optional[str]) -> Optional[int]:
    """Parse a SurvCE reading-count note value.

    SurvCE writes either a bare integer (``30``) or an ``N of M`` pair
    (``2 of 2``, ``10 of 20``). Only the first number is the count of
    interest for the canonical model.
    """
    if value is None or value == "":
        return None
    head = value.strip().split(None, 1)[0]
    return _to_int(head)


def _strip_suffix(value: Optional[str]) -> Optional[str]:
    """Drop the trailing unit character SurvCE appends to some tags."""
    if value is None:
        return None
    return value[:-1]


def _drop_pending_ctsd_wrap(parser: "Rw5Parser", *, using_values: bool) -> None:
    """Remove SurvCE's trailing-comma wrap marker from a pending CTSD list.

    Args:
        parser: Parser whose pending label or value list may end with a
            wrap marker left by the previous ``CTSD`` fragment.
        using_values: Drop from pending values when true, else labels.
    """
    target = (
        parser._pending_stk_values
        if using_values
        else parser._pending_stk_labels
    )
    if (
        parser._pending_stk_wrapped
        and target is not None
        and target
        and target[-1] == ""
    ):
        target.pop()
    parser._pending_stk_wrapped = False


def _strip_prefix_suffix(value: Optional[str]) -> Optional[str]:
    """Drop the ``"XXXX"``-style four-character prefix and unit suffix."""
    if value is None:
        return None
    return value[4:-1]


def _parse_moment(
    text: str, labels: Mapping[str, str]
) -> Optional[datetime.datetime]:
    """Parse a ``G0`` observation moment, trying every known format."""
    candidate_formats = [labels["%Y/%m/%d %H:%M:%S"]] + [
        fmt for fmt in _MOMENT_FORMATS if fmt != labels["%Y/%m/%d %H:%M:%S"]
    ]
    for date_format in candidate_formats:
        try:
            return datetime.datetime.strptime(text, date_format)
        except ValueError:
            continue
    logger.debug("could not parse observation moment: %s", text)
    return None


def _ordered_dt_formats(text: str, formats: tuple[str, ...]) -> tuple[str, ...]:
    """Prefer the unambiguous day/month order when one part is > 12.

    Ambiguous stamps (both parts ≤ 12) keep ``formats`` order so existing
    US-style SurvCE fixtures stay backward-compatible.
    """
    match = _DT_PARTS_RE.match(text)
    if match is None or len(formats) < 2:
        return formats

    first = int(match.group("a"))
    second = int(match.group("b"))
    us_fmt, eu_fmt = formats[0], formats[1]
    if first > 12 >= second:
        return (eu_fmt, us_fmt)
    if second > 12 >= first:
        return (us_fmt, eu_fmt)
    return formats


def _parse_dt_stamp(text: str, formats: tuple[str, ...]) -> datetime.datetime:
    """Parse a SurvCE ``DT…`` / ``DT…TM…`` stamp with US or European order.

    Args:
        text: Concatenated date (and optional time) token, for example
            ``DT29-08-2020TM10:12:18`` or ``DT08-29-2020``.
        formats: ``strptime`` patterns to try, usually
            :data:`_JOB_DATETIME_FORMATS` or :data:`_DT_NOTE_FORMATS`.

    Returns:
        The parsed naive local datetime.

    Raises:
        ValueError: If no format matches ``text``.
    """
    for date_format in _ordered_dt_formats(text, formats):
        try:
            return datetime.datetime.strptime(text, date_format)
        except ValueError:
            continue
    raise ValueError("unrecognized DT stamp: %s" % text)
