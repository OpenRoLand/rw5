"""Canonical survey-point extractor for RW5 files.

:class:`Rw5Extractor` is the adapter registered under the
``openroland_survey.extractors`` entry-point group. It maps the raw records
produced by :mod:`openroland_rw5.parser` onto
``openroland-survey-core``'s canonical
:class:`~openroland_survey.records.SurveyPointRecord` model;
canonical-model decisions live only here, never in the low-level parser.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openroland_survey import services
from openroland_survey.records import (
    ExtractionResult,
    IssueSeverity,
    ParseIssue,
    SourceFormat,
    SurveyPointRecord,
)

from openroland_rw5.models import Rw5BasePoint, Rw5GpsPoint
from openroland_rw5.parser import Rw5Parser

logger = logging.getLogger(__name__)

__all__ = ["Rw5Extractor"]


class Rw5Extractor:
    """Extracts canonical survey points from one RW5 file.

    Attributes:
        format_name: Stable format identifier, ``"rw5"``.
        extensions: File extensions this extractor claims.
    """

    format_name = "rw5"
    extensions = (".rw5",)

    def can_read(self, path: Path) -> bool:
        """Return whether ``path``'s extension is a recognized RW5 file."""
        return Path(path).suffix.lower() in self.extensions

    def extract(self, path: Path) -> ExtractionResult:
        """Parse and canonicalize one RW5 file.

        Args:
            path: Path of the RW5 file to read.

        Returns:
            The canonical records, source metadata, and diagnostics
            extracted from ``path``.
        """
        resolved = Path(path)
        parser = Rw5Parser().parse_file(resolved)
        source = services.build_source_metadata(
            resolved,
            SourceFormat.RW5,
            parser_metadata=_job_metadata(parser),
        )

        issues: List[ParseIssue] = list(parser.issues)
        records: List[SurveyPointRecord] = []
        for base in parser.base_points:
            base_record = _build_base_record(base, resolved)
            if base_record is not None:
                records.append(base_record)
            for gps in base.points:
                record, issue = _build_record(gps, base, resolved)
                if record is not None:
                    records.append(record)
                if issue is not None:
                    issues.append(issue)

        return ExtractionResult(source=source, records=records, issues=issues)


def _job_metadata(parser: Rw5Parser) -> Dict[str, Any]:
    """Collect job/instrument-level parser metadata for ``SourceMetadata``."""
    return _drop_none(
        {
            "locale": parser.locale,
            "job_name": parser.job_name,
            "job_datetime": parser.job_datetime,
            "survce_version": parser.survce_version,
            "scale_point": parser.scale_point,
            "equipment": parser.equipment,
            "coordinate_system": parser.coordinate_system,
            "localization_file": parser.localization_file,
            "geoid_file": parser.geoid_file,
            "grid_adjustment_file": parser.grid_adjustment_file,
            "gps_scale": parser.gps_scale,
            "rtk_method": parser.rtk_method,
            "rtk_device": parser.rtk_device,
            "rtk_network": parser.rtk_network,
            "crd": parser.crd,
            "units": parser.units,
            "scale_factor": parser.scale_factor,
            "earth_curvature_on": parser.earth_curvature_on,
            "edm_offset": parser.edm_offset,
            "antenna_type": parser.antenna_type,
            "antenna_radius": parser.antenna_radius,
            "antenna_slant_height": parser.antenna_slant_height,
            "antenna_l1_offset": parser.antenna_l1_offset,
            "antenna_l2_offset": parser.antenna_l2_offset,
            "antenna_description": parser.antenna_description,
        }
    )


def _is_base_station_observation(gps: Rw5GpsPoint, base: Rw5BasePoint) -> bool:
    """Return whether ``gps`` is a base/VRS echo, not a rover survey point.

    SurvCE often writes a geographic-only ``GPS`` line whose ``PN`` is the
    RTK network mountpoint (also stored as :attr:`Rw5BasePoint.name`) or
    the ``BP`` point number, with no following projected ``GS``. Those
    lines must not become canonical survey points or missing-NEH warnings.

    Args:
        gps: Candidate observation.
        base: Base station group the observation belongs to.

    Returns:
        ``True`` when the observation should be ignored silently.
    """
    if gps.name is None:
        return False
    name = str(gps.name)
    # Job-header stakeout / empty network placeholders are not survey points.
    if name == "?" and (
        gps.north is None or gps.east is None or gps.height is None
    ):
        return True
    base_name = base.name
    if base_name is not None and base_name != "?" and name == str(base_name):
        return True
    if base.number is not None and name == str(base.number):
        # Same PN as BP without projected coords is a base echo.
        return gps.north is None or gps.east is None or gps.height is None
    return False


def _build_base_record(
    base: Rw5BasePoint, source_path: Path
) -> Optional[SurveyPointRecord]:
    """Build a canonical ``kind=base`` record from a projected base setup.

    Args:
        base: Parsed base station.
        source_path: Path of the file being extracted.

    Returns:
        A survey point when ``base`` has projected NEH, otherwise ``None``.
    """
    if base.north is None or base.east is None or base.height is None:
        return None

    name = str(base.number) if base.number is not None else None
    source_values = _drop_none(
        {
            "base_name": base.name,
            "base_number": base.number,
            "configured_by_gps_position": (
                True if base.configured_by_gps_position else None
            ),
            "entered_base_hr": base.entered_base_hr,
            "base_unknown_ag": base.unknown_ag,
            "base_unknown_pa": base.unknown_pa,
            "local_time": _isoformat(base.local_time),
        }
    )
    return SurveyPointRecord(
        north=base.north,
        east=base.east,
        height=base.height,
        name=name,
        latitude=base.latitude,
        longitude=base.longitude,
        wgs84_altitude=base.elevation,
        observed_at_local=base.local_time,
        kind="base",
        entered_antenna_height=base.entered_base_hr,
        base_id=base.base_id or _to_str(base.number),
        base_latitude=base.latitude,
        base_longitude=base.longitude,
        base_height=base.elevation,
        source_record_id=name,
        source_values=source_values,
    )


def _build_record(
    gps: Rw5GpsPoint, base: Rw5BasePoint, source_path: Path
) -> Tuple[Optional[SurveyPointRecord], Optional[ParseIssue]]:
    """Build one canonical record from a raw GPS observation.

    Args:
        gps: The raw observation to canonicalize.
        base: The base station ``gps`` was observed from.
        source_path: Path of the file being extracted, for diagnostics.

    Returns:
        A tuple of the built record (or ``None`` when unusable) and an
        optional diagnostic explaining why it was skipped.
    """
    if _is_base_station_observation(gps, base):
        return None, None

    if gps.north is None or gps.east is None or gps.height is None:
        return None, ParseIssue(
            source_path=source_path,
            severity=IssueSeverity.WARNING,
            message=(
                "point %r is missing projected north/east/height "
                "coordinates; skipping" % (gps.name,)
            ),
            record_id=_record_id(gps),
        )

    source_values, stakeout_issue = _source_values(gps, base, source_path)

    try:
        # SP imports set collection_kind="imported"; measured GPS/GS
        # occupations are canonical GPS field observations.
        kind = gps.collection_kind or "gps"
        record = SurveyPointRecord(
            north=gps.north,
            east=gps.east,
            height=gps.height,
            name=str(gps.name) if gps.name is not None else None,
            code=gps.comment or gps.code,
            latitude=gps.latitude,
            longitude=gps.longitude,
            wgs84_altitude=gps.elevation,
            observed_at_utc=_observed_at_utc(gps),
            observed_at_local=gps.local_time,
            method=gps.method,
            status=gps.status,
            kind=kind,
            satellite_count=_first_not_none(gps.sat_count, gps.nr_of_sat_avg),
            hrms=_first_not_none(gps.hsdv, gps.hrms_avg),
            vrms=_first_not_none(gps.vsdv, gps.vrms_avg),
            hdop=_first_not_none(gps.hdop, gps.hdop_avg),
            vdop=_first_not_none(gps.vdop, gps.vdop_avg),
            pdop=_first_not_none(gps.pdop, gps.pdop_avg),
            tdop=gps.tdop,
            gdop=gps.gdop,
            antenna_measurement_method=gps.entered_antenna_method,
            entered_antenna_height=gps.entered_antenna_height,
            true_antenna_height=gps.true_antenna_height,
            base_id=base.base_id or _to_str(base.number),
            base_latitude=base.latitude,
            base_longitude=base.longitude,
            base_height=base.elevation,
            source_record_id=_record_id(gps),
            source_values=source_values,
        )
    except ValueError as exc:
        return None, ParseIssue(
            source_path=source_path,
            severity=IssueSeverity.WARNING,
            message="point %r has an invalid coordinate: %s"
            % (
                gps.name,
                exc,
            ),
            record_id=_record_id(gps),
        )
    return record, stakeout_issue


def _source_values(
    gps: Rw5GpsPoint,
    base: Rw5BasePoint,
    source_path: Path,
) -> Tuple[Dict[str, Any], Optional[ParseIssue]]:
    """Collect the RW5-specific values that have no canonical column.

    Stakeout label/value mismatches become a warning and omit the paired
    ``stakeout`` map; the observation itself is still imported.
    """
    values: Dict[str, Any] = {
        "comment": gps.comment,
        "gs_code": gps.code,
        "moment": _isoformat(gps.moment),
        "local_time": _isoformat(gps.local_time),
        "end_time_utc": _isoformat(gps.end_time_utc),
        "north_avg": gps.north_avg,
        "north_max": gps.north_max,
        "north_min": gps.north_min,
        "north_sd": gps.north_sd,
        "east_avg": gps.east_avg,
        "east_max": gps.east_max,
        "east_min": gps.east_min,
        "east_sd": gps.east_sd,
        "elev_avg": gps.elev_avg,
        "elev_max": gps.elev_max,
        "elev_min": gps.elev_min,
        "elev_sd": gps.elev_sd,
        "hrms_max": gps.hrms_max,
        "hrms_min": gps.hrms_min,
        "hrms_sd": gps.hrms_sd,
        "vrms_max": gps.vrms_max,
        "vrms_min": gps.vrms_min,
        "vrms_sd": gps.vrms_sd,
        "nr_of_sat_avg": gps.nr_of_sat_avg,
        "nr_of_sat_max": gps.nr_of_sat_max,
        "nr_of_sat_min": gps.nr_of_sat_min,
        "fixed_readings": gps.fixed_readings,
        "float_readings": gps.float_readings,
        "dgps_readings": gps.dgps_readings,
        "valid_readings": gps.valid_readings,
        "hdop_avg": gps.hdop_avg,
        "hdop_max": gps.hdop_max,
        "hdop_min": gps.hdop_min,
        "vdop_avg": gps.vdop_avg,
        "vdop_max": gps.vdop_max,
        "vdop_min": gps.vdop_min,
        "pdop_avg": gps.pdop_avg,
        "pdop_max": gps.pdop_max,
        "pdop_min": gps.pdop_min,
        "delta_x": gps.delta_x,
        "delta_y": gps.delta_y,
        "delta_z": gps.delta_z,
        "g2_velocity_x": gps.g2_velocity_x,
        "g2_velocity_y": gps.g2_velocity_y,
        "g2_velocity_z": gps.g2_velocity_z,
        "g3_xy": gps.g3_xy,
        "g3_xz": gps.g3_xz,
        "g3_yz": gps.g3_yz,
        "off_azimuth": gps.off_azimuth,
        "off_distance": gps.off_distance,
        "off_delta_z": gps.off_delta_z,
        "off_horizontal_distances": gps.off_horizontal_distances or None,
        "base_name": base.name,
        "base_number": base.number,
        "base_unknown_ag": base.unknown_ag,
        "base_unknown_pa": base.unknown_pa,
    }
    stakeout_issue: Optional[ParseIssue] = None
    if gps.is_stakeout_point():
        values["stakeout_source"] = gps.stk_source
        try:
            values["stakeout"] = dict(gps.stakeout_data())
        except ValueError as exc:
            logger.debug(
                "point %r stakeout data skipped: %s",
                gps.name,
                exc,
                exc_info=True,
            )
            values["stakeout_labels"] = list(gps.stk_labels or [])
            values["stakeout_values"] = list(gps.stk_values or [])
            stakeout_issue = ParseIssue(
                source_path=source_path,
                severity=IssueSeverity.WARNING,
                message="point %r stakeout data skipped: %s" % (gps.name, exc),
                record_id=_record_id(gps),
            )
    return _drop_none(values), stakeout_issue


def _record_id(gps: Rw5GpsPoint) -> Optional[str]:
    """Return a stable diagnostic identifier for one observation."""
    return str(gps.name) if gps.name is not None else None


def _observed_at_utc(gps: Rw5GpsPoint) -> Optional[datetime.datetime]:
    """Pick the best UTC observation time for one GPS record.

    Preference order:

    1. ``GT`` ``start_time_utc`` (authoritative GPS-week UTC)
    2. ``G0`` ``moment`` (receiver occupation start; treat naive as UTC)
    3. ``--DT``/``--TM`` ``local_time`` (controller wall clock; last resort)
    """
    if gps.start_time_utc is not None:
        return gps.start_time_utc
    if gps.moment is not None:
        return _as_utc(gps.moment)
    if gps.local_time is not None:
        return _as_utc(gps.local_time)
    return None


def _as_utc(
    dt: Optional[datetime.datetime],
) -> Optional[datetime.datetime]:
    """Return *dt* as an aware UTC datetime, normalizing naive values."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _to_str(value: Any) -> Optional[str]:
    """Convert a possibly-``None`` value to ``str``."""
    return None if value is None else str(value)


def _isoformat(value: Optional[datetime.datetime]) -> Optional[str]:
    """Convert a possibly-``None`` datetime to an ISO-8601 string."""
    return None if value is None else value.isoformat()


def _first_not_none(*values: Any) -> Any:
    """Return the first argument that is not ``None``."""
    for value in values:
        if value is not None:
            return value
    return None


def _drop_none(values: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``values`` without its ``None``-valued entries."""
    return {key: value for key, value in values.items() if value is not None}
