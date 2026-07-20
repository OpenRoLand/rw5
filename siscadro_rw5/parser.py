"""Low-level parser for RW5 (Trimble/Carlson/SurvCE) survey files.

This module only turns RW5 text into the raw record models defined in
:mod:`siscadro_rw5.models`. It never depends on ``siscadro-survey``'s
canonical model, a database, or a CAD library; :mod:`siscadro_rw5.extractor`
is the layer that maps these raw records onto the canonical model.

RW5 files are line-oriented and comma-separated. Every line starts with a
two-or-three-letter record code (``GPS``, ``G0``, ``GS``, ...) followed by
fields tagged with a two-character sign (for example ``LA46.5`` is the tag
``LA`` with value ``46.5``). Free-text metadata (equipment name, antenna
type, and similar) is instead written as ``--``-prefixed note lines whose
label text depends on the file's locale (English or Romanian), which is why
most of this module's dispatch is label-driven rather than tag-driven.
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
from siscadro_survey.records import IssueSeverity, ParseIssue

from siscadro_rw5.constants import (
    DEFAULT_LOCALE,
    ENGLISH_LABELS,
    ENGLISH_LOCALE_MARKERS,
    LOCALE_DETECTION_LINE_LIMIT,
    NOTE_SCALE_POINT,
    NOTE_SURVCE_VERSION,
    NOTE_SURVCE_VERSION_NO_SPACE,
    ROMANIAN_LABELS,
    ROMANIAN_LOCALE_MARKERS,
)
from siscadro_rw5.models import Rw5BasePoint, Rw5GpsPoint

logger = logging.getLogger(__name__)

__all__ = ["Rw5Parser", "leap_seconds", "time_from_gps"]

_BASE_ID_RE = re.compile(r"Base ID read at rover: (.+)")
_BASE_ID_WITH_METHOD_RE = re.compile(r"\((.+)\) - Base ID read at rover: (.*)")
_HSIG_ITEM_RE = re.compile(r"\s*([A-Z]+)\s*:\s*(.+)")

#: RW5's default (undecorated) local moment format, plus the fallbacks
#: SurvCE has been observed to write depending on locale/version.
_MOMENT_FORMATS: tuple = (
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
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
        survce_version: SurvCE version string, from its banner note.
        scale_point: Scale point note text, when present.
        equipment: Equipment name, from the locale-specific note.
        coordinate_system: User-defined coordinate system name.
        localization_file: Localization file name, when present.
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
    coordinate_system: Optional[str] = attrs.field(default=None, init=False)
    localization_file: Optional[str] = attrs.field(default=None, init=False)
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
                exc_info=True,
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
        logger.debug(
            "parsed %d GPS records from %s", self.point_count(), source_path
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
            fields = line.split(",")
            handler = self._dispatch.get(fields[0])
            if handler is not None:
                handler(fields, line)
            elif line.startswith("--"):
                self._process_note(line[2:])
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
            self.job_datetime = datetime.datetime.strptime(
                fields[2] + fields[3], "DT%m-%d-%YTM%H:%M:%S"
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
            latitude=_to_float(tagged.get("LA")),
            longitude=_to_float(tagged.get("LN")),
            elevation=_to_float(tagged.get("EL")),
            number=_to_int_or_str(tagged.get("PN")),
            unknown_ag=tagged.get("AG"),
            unknown_pa=tagged.get("PA"),
        )
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
            latitude=_to_float(tagged.get("LA")),
            longitude=_to_float(tagged.get("LN")),
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
        base.points.append(gps)

    def _process_g0(self, fields: List[str], line: str) -> None:
        """Handle the ``G0`` observation moment/base-id record."""
        gps = self._current_gps_point()
        gps.moment = _parse_moment(fields[1], self._labels)

        with_method = _BASE_ID_WITH_METHOD_RE.match(fields[2])
        if with_method is not None:
            gps.method, base_id = with_method.groups()
        else:
            plain = _BASE_ID_RE.match(fields[2])
            if plain is None:
                logger.debug("unrecognized base-id text: %s", fields[2])
                base_id = fields[2]
            else:
                base_id = plain.group(1)
            gps.method = "Average"

        base = self._current_base_point()
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
        elif line.startswith(NOTE_SCALE_POINT):
            self.scale_point = line.removeprefix(NOTE_SCALE_POINT).strip()
        elif line.startswith("DT"):
            self._current_gps_point().local_time = datetime.datetime.strptime(
                line, "DT%m-%d-%Y"
            )
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

    def _process_local_time_of_day(self, line: str) -> None:
        """Add a ``TM<seconds-since-midnight>`` offset to the local date."""
        gps = self._current_gps_point()
        if gps.local_time is None:
            logger.debug("TM note line without a preceding DT line: %s", line)
            return
        hours, minutes, seconds = (int(part) for part in line[2:].split(":"))
        gps.local_time = gps.local_time + datetime.timedelta(
            hours=hours, minutes=minutes, seconds=seconds
        )

    def _process_stakeout_note(self, line: str) -> None:
        """Handle one ``CTSS1``/``CTSDn`` stakeout data note line."""
        gps = self._current_gps_point()
        prefix, separator, rest = line.partition(":")
        if not separator:
            logger.debug("malformed stakeout note line: %s", line)
            return
        if prefix == "CTSS1":
            gps.stk_source = rest
            return
        if not prefix.startswith("CTSD"):
            logger.debug("unrecognized stakeout note line: %s", line)
            return
        try:
            index = int(prefix.removeprefix("CTSD"))
        except ValueError:
            logger.debug("unrecognized stakeout note line: %s", line)
            return
        values = rest.split(",")
        if index == 0 and gps.stk_labels is None:
            gps.stk_labels = values
        elif index == 0:
            gps.stk_values = values
        elif gps.stk_values is not None:
            gps.stk_values.extend(values)
        elif gps.stk_labels is not None:
            gps.stk_labels.extend(values)
        else:
            logger.debug(
                "stakeout continuation line without a preceding CTSD0 "
                "line: %s",
                line,
            )

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
            elif label in ("NSDV", "ESDV"):
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
        labelled = line.split(": ", 1)
        if line.startswith("Offset "):
            # A secondary offset-record rendering; the ``OF`` record
            # already captured the values that matter.
            return
        if len(labelled) != 2:
            logger.debug("unrecognized note line: %s", line)
            return
        label, value = labelled
        if label == self._labels["RTK Method"]:
            # Unlike every other label, SurvCE repeats this label as the
            # sub-key of the first comma-separated part, so the handler
            # needs the whole line rather than just the value after
            # the first ``": "``.
            self._set_rtk_method(line)
            return
        handler = self._label_handlers().get(label)
        if handler is not None:
            handler(value)
        elif label == self._labels["Adjustments"]:
            logger.log(1, "ignoring adjustments label line: %s", line)
        else:
            logger.debug("unknown label %r in line: %s", label, line)

    def _label_handlers(self) -> Dict[str, Callable[[str], None]]:
        """Return the locale-resolved ``label -> setter`` dispatch table."""
        labels = self._labels
        return {
            labels["CRD"]: self._set_crd,
            labels["User Defined"]: self._set_coordinate_system,
            labels["Equipment"]: self._set_equipment,
            labels["Antenna Type"]: self._set_antenna_type,
            labels["Localization File"]: self._set_localization_file,
            labels["Geoid Separation File"]: self._set_geoid_file,
            labels["Grid Adjustment File"]: self._set_grid_adjustment_file,
            labels["GPS Scale"]: self._set_gps_scale,
            labels["Entered HR"]: self._set_entered_antenna_height,
            labels["Entered Rover HR"]: self._set_entered_rover_height,
            labels["Valid Readings"]: self._set_valid_readings,
            labels["Fixed Readings"]: self._set_fixed_readings,
            labels["Float Readings"]: self._set_float_readings,
            labels["DGPS Readings"]: self._set_dgps_readings,
            labels["Nor Min"]: self._set_north_range,
            labels["Eas Min"]: self._set_east_range,
            labels["Elv Min"]: self._set_elevation_range,
            labels["Nor Avg"]: self._set_north_average,
            labels["Eas Avg"]: self._set_east_average,
            labels["Elv Avg"]: self._set_elevation_average,
            labels["HRMS Avg"]: self._set_hrms_average,
            labels["VRMS Avg"]: self._set_vrms_average,
            labels["HDOP Avg"]: self._set_hdop_average,
            labels["VDOP Avg"]: self._set_vdop_average,
            labels["PDOP Avg"]: self._set_pdop_average,
            labels["Number of Satellites Avg"]: self._set_satellites_average,
        }

    def _set_crd(self, value: str) -> None:
        self.crd = value

    def _set_coordinate_system(self, value: str) -> None:
        self.coordinate_system = value

    def _set_equipment(self, value: str) -> None:
        self.equipment = value

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

    def _set_localization_file(self, value: str) -> None:
        self.localization_file = value

    def _set_geoid_file(self, value: str) -> None:
        self.geoid_file = value

    def _set_grid_adjustment_file(self, value: str) -> None:
        self.grid_adjustment_file = value

    def _set_gps_scale(self, value: str) -> None:
        self.gps_scale = _to_float(value)

    def _set_rtk_method(self, line: str) -> None:
        labels = self._labels
        for part in line.split(","):
            label_value = part.strip().split(": ", maxsplit=1)
            label = label_value[0]
            piece = label_value[1] if len(label_value) == 2 else ""
            if label == labels["RTK Method"]:
                self.rtk_method = piece
            elif label == labels["Device"]:
                self.rtk_device = piece
            elif label == labels["Network"]:
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

    def _set_valid_readings(self, value: str) -> None:
        self._current_gps_point().valid_readings = _to_int(value)

    def _set_fixed_readings(self, value: str) -> None:
        self._current_gps_point().fixed_readings = _to_int(value)

    def _set_float_readings(self, value: str) -> None:
        self._current_gps_point().float_readings = _to_int(value)

    def _set_dgps_readings(self, value: str) -> None:
        self._current_gps_point().dgps_readings = _to_int(value)

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

    def _set_vrms_average(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.vrms_avg = _to_float(parts[0])
        gps.vrms_sd = _to_float(parts[2])
        gps.vrms_min = _to_float(parts[4])
        gps.vrms_max = _to_float(parts[6])

    def _set_hdop_average(self, value: str) -> None:
        gps = self._current_gps_point()
        parts = value.split(" ")
        gps.hdop_avg = _to_float(parts[0])
        gps.hdop_min = _to_float(parts[3])
        gps.hdop_max = _to_float(parts[5])

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
        """Handle the ``GT`` GPS observation-window time record."""
        gps = self._current_gps_point()
        tagged = self._tagged_fields(fields)
        gps.start_time_utc = time_from_gps(
            week=int(tagged["SW"]), milliseconds=int(tagged["ST"])
        )
        gps.end_time_utc = time_from_gps(
            week=int(tagged["EW"]), milliseconds=int(tagged["ET"])
        )

    def _process_gs(self, fields: List[str], line: str) -> None:
        """Handle the ``GS`` projected coordinate record."""
        gps = self._current_gps_point()
        tagged = self._tagged_fields(fields)
        if gps.name is not None and tagged.get("PN") != str(gps.name):
            logger.debug(
                "GS point name %r does not match current point %r",
                tagged.get("PN"),
                gps.name,
            )
        gps.north = _to_float(tagged.get("N "))
        gps.east = _to_float(tagged.get("E "))
        gps.height = _to_float(tagged.get("EL"))
        gps.code = tagged.get("--")

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


def _strip_suffix(value: Optional[str]) -> Optional[str]:
    """Drop the trailing unit character SurvCE appends to some tags."""
    if value is None:
        return None
    return value[:-1]


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
