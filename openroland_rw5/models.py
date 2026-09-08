"""Raw RW5 record models.

These are low-level, format-specific records. They preserve every RW5
field the parser recognizes, independent of the canonical survey point
model in ``openroland-survey-core``. Consumers that need CAD-oriented behavior
(DXF export, point renumbering, and similar) can use these raw records
directly; :mod:`openroland_rw5.extractor` builds the canonical model from
them separately so canonical-model decisions never leak into parsing.
"""

from __future__ import annotations

import datetime
import enum
from collections import OrderedDict
from typing import List, Optional, Union

import attrs


class RecordKind(enum.Enum):
    """Discriminates the two record shapes produced by the RW5 parser.

    Attributes:
        BASE_POINT: A base/reference station record.
        GPS_POINT: A rover/GPS observation record.
    """

    BASE_POINT = "base_point"
    GPS_POINT = "gps_point"


@attrs.define
class Rw5GpsPoint:
    """One rover/GPS observation extracted from an RW5 file.

    Attributes:
        kind: Always :attr:`RecordKind.GPS_POINT`.
        name: Point name/number, from the ``GPS``/``GS``/``SP`` ``PN``
            tag.
        comment: Point comment/code, from the ``GPS`` or ``SP`` ``--``
            tag.
        latitude: Geographic latitude reported by the ``GPS``/``BP``/``EP``
            record, in decimal degrees (SurvCE ``DD.MMSSsssss`` decoded).
        longitude: Geographic longitude reported by the ``GPS``/``BP``/``EP``
            record, in decimal degrees (SurvCE ``DD.MMSSsssss`` decoded).
        elevation: Raw ellipsoidal height reported by the ``GPS`` record,
            in metres.
        north: Projected north coordinate, from the ``GS`` or ``SP``
            record.
        east: Projected east coordinate, from the ``GS`` or ``SP``
            record.
        height: Projected elevation, from the ``GS`` or ``SP`` record.
        code: Point code, from the ``GS`` or ``SP`` ``--`` tag. Usually
            equal to ``comment``; kept separate because the two records
            are not guaranteed to agree.
        moment: Local observation moment, from the ``G0`` record.
        local_time: Naive local timestamp assembled from ``DT``/``TM``
            note lines, when present.
        method: Positioning method, from the ``G0`` base-ID text (for
            example ``"Average"`` or an explicit RTK method name).
        status: Fix/quality status, from the ``HSIG``/``HSDV``/``HRMS``
            note line.
        sat_count: Number of satellites used, from the same note line.
        hsdv: Horizontal standard deviation/RMS, in metres.
        vsdv: Vertical standard deviation/RMS, in metres.
        hdop: Horizontal dilution of precision.
        vdop: Vertical dilution of precision.
        pdop: Position dilution of precision.
        tdop: Time dilution of precision.
        gdop: Geometric dilution of precision.
        north_avg: Averaged north coordinate over the occupation.
        north_max: Maximum north coordinate observed.
        north_min: Minimum north coordinate observed.
        north_sd: Standard deviation of the north coordinate.
        east_avg: Averaged east coordinate over the occupation.
        east_max: Maximum east coordinate observed.
        east_min: Minimum east coordinate observed.
        east_sd: Standard deviation of the east coordinate.
        elev_avg: Averaged elevation over the occupation.
        elev_max: Maximum elevation observed.
        elev_min: Minimum elevation observed.
        elev_sd: Standard deviation of the elevation.
        hrms_avg: Averaged horizontal RMS.
        hrms_max: Maximum horizontal RMS observed.
        hrms_min: Minimum horizontal RMS observed.
        hrms_sd: Standard deviation of the horizontal RMS.
        vrms_avg: Averaged vertical RMS.
        vrms_max: Maximum vertical RMS observed.
        vrms_min: Minimum vertical RMS observed.
        vrms_sd: Standard deviation of the vertical RMS.
        age_avg: Averaged GNSS age of differential, when present.
        age_min: Minimum GNSS age of differential observed.
        age_max: Maximum GNSS age of differential observed.
        nrms_avg: Averaged north RMS from ``NRMS Avg`` notes.
        nrms_max: Maximum north RMS observed.
        nrms_min: Minimum north RMS observed.
        nrms_sd: Standard deviation of the north RMS.
        erms_avg: Averaged east RMS from ``ERMS Avg`` notes.
        erms_max: Maximum east RMS observed.
        erms_min: Minimum east RMS observed.
        erms_sd: Standard deviation of the east RMS.
        nr_of_sat_avg: Averaged satellite count over the occupation.
        nr_of_sat_max: Maximum satellite count observed.
        nr_of_sat_min: Minimum satellite count observed.
        fixed_readings: Number of fixed readings taken.
        float_readings: Number of float readings taken.
        dgps_readings: Number of DGPS readings taken.
        auto_readings: Number of autonomous readings taken.
        valid_readings: Number of valid readings taken.
        hdop_avg: Averaged HDOP over the occupation.
        hdop_max: Maximum HDOP observed.
        hdop_min: Minimum HDOP observed.
        vdop_avg: Averaged VDOP over the occupation.
        vdop_max: Maximum VDOP observed.
        vdop_min: Minimum VDOP observed.
        pdop_avg: Averaged PDOP over the occupation.
        pdop_max: Maximum PDOP observed.
        pdop_min: Minimum PDOP observed.
        delta_x: Baseline delta X, from the ``G1`` record.
        delta_y: Baseline delta Y, from the ``G1`` record.
        delta_z: Baseline delta Z, from the ``G1`` record.
        g2_velocity_x: Baseline velocity X, from the ``G2`` record.
        g2_velocity_y: Baseline velocity Y, from the ``G2`` record.
        g2_velocity_z: Baseline velocity Z, from the ``G2`` record.
        g3_xy: Baseline covariance term XY, from the ``G3`` record.
        g3_xz: Baseline covariance term XZ, from the ``G3`` record.
        g3_yz: Baseline covariance term YZ, from the ``G3`` record.
        entered_antenna_height: Antenna height entered by the operator,
            from the job's ``Entered HR``/``Entered Rover HR`` note.
        entered_antenna_method: How the entered antenna height was
            measured (for example vertical or slant).
        true_antenna_height: Antenna height after correction, from the
            ``LS`` record.
        off_azimuth: Offset shot azimuth, from the ``OF`` record.
        off_distance: Offset shot horizontal distance, from the ``OF``
            record.
        off_delta_z: Offset shot vertical delta, from the ``OF`` record.
        off_horizontal_distances: Additional offset horizontal distances
            reported on ``OF,HD`` note lines.
        start_time_utc: Aware UTC start time of the GPS observation
            window, decoded from the ``GT`` record's GPS week/seconds.
        end_time_utc: Aware UTC end time of the GPS observation window.
        stk_source: Stakeout source design file, from a ``CTSSn`` note.
        stk_labels: Stakeout column labels, from ``CTSD0`` note lines.
        stk_values: Stakeout column values, from ``CTSD`` note lines.
        stk_ctsd_wrapped: Internal parser flag: the last ``CTSD`` fragment
            ended with SurvCE's trailing-comma wrap marker.
        gnss_statistics_rt: South/Cube real-time GNSS statistics note.
        gnss_statistics_pp: South/Cube post-processed GNSS statistics note.
        pp_time: South/Cube post-processed time window note.
        antenna_note: South/Cube ``Antenna:`` note (distinct from job
            ``Antenna Type``).
        attribute_note: SurvCE GIS ``Attribute:`` note (parcel/owner text).
        instrument_selected: South/Cube instrument profile note.
        gnss_profile_tolerance_rt: South/Cube real-time profile tolerance
            note.
        gnss_profile_tolerance_pp: South/Cube post-processed profile
            tolerance note.
        initialization_time: South/Cube initialization-time note.
        base: The :class:`Rw5BasePoint` this observation was taken from.
    """

    kind: RecordKind = attrs.field(default=RecordKind.GPS_POINT, init=False)

    name: Optional[str] = None
    comment: Optional[str] = None

    latitude: Optional[float] = None
    longitude: Optional[float] = None
    elevation: Optional[float] = None

    north: Optional[float] = None
    east: Optional[float] = None
    height: Optional[float] = None
    code: Optional[str] = None

    moment: Optional[datetime.datetime] = None
    local_time: Optional[datetime.datetime] = None

    method: Optional[str] = None
    status: Optional[str] = None
    #: Canonical collection kind for non-measured SP imports (``imported``).
    collection_kind: Optional[str] = None
    sat_count: Optional[int] = None

    hsdv: Optional[float] = None
    vsdv: Optional[float] = None
    hdop: Optional[float] = None
    vdop: Optional[float] = None
    pdop: Optional[float] = None
    tdop: Optional[float] = None
    gdop: Optional[float] = None

    north_avg: Optional[float] = None
    north_max: Optional[float] = None
    north_min: Optional[float] = None
    north_sd: Optional[float] = None
    east_avg: Optional[float] = None
    east_max: Optional[float] = None
    east_min: Optional[float] = None
    east_sd: Optional[float] = None
    elev_avg: Optional[float] = None
    elev_max: Optional[float] = None
    elev_min: Optional[float] = None
    elev_sd: Optional[float] = None

    hrms_avg: Optional[float] = None
    hrms_max: Optional[float] = None
    hrms_min: Optional[float] = None
    hrms_sd: Optional[float] = None
    vrms_avg: Optional[float] = None
    vrms_max: Optional[float] = None
    vrms_min: Optional[float] = None
    vrms_sd: Optional[float] = None

    age_avg: Optional[float] = None
    age_min: Optional[float] = None
    age_max: Optional[float] = None
    nrms_avg: Optional[float] = None
    nrms_max: Optional[float] = None
    nrms_min: Optional[float] = None
    nrms_sd: Optional[float] = None
    erms_avg: Optional[float] = None
    erms_max: Optional[float] = None
    erms_min: Optional[float] = None
    erms_sd: Optional[float] = None

    nr_of_sat_avg: Optional[int] = None
    nr_of_sat_max: Optional[int] = None
    nr_of_sat_min: Optional[int] = None

    fixed_readings: Optional[int] = None
    float_readings: Optional[int] = None
    dgps_readings: Optional[int] = None
    auto_readings: Optional[int] = None
    valid_readings: Optional[int] = None

    hdop_avg: Optional[float] = None
    hdop_max: Optional[float] = None
    hdop_min: Optional[float] = None
    vdop_avg: Optional[float] = None
    vdop_max: Optional[float] = None
    vdop_min: Optional[float] = None
    pdop_avg: Optional[float] = None
    pdop_max: Optional[float] = None
    pdop_min: Optional[float] = None

    delta_x: Optional[float] = None
    delta_y: Optional[float] = None
    delta_z: Optional[float] = None

    g2_velocity_x: Optional[float] = None
    g2_velocity_y: Optional[float] = None
    g2_velocity_z: Optional[float] = None

    g3_xy: Optional[float] = None
    g3_xz: Optional[float] = None
    g3_yz: Optional[float] = None

    entered_antenna_height: Optional[float] = None
    entered_antenna_method: Optional[str] = None
    true_antenna_height: Optional[float] = None

    off_azimuth: Optional[float] = None
    off_distance: Optional[float] = None
    off_delta_z: Optional[float] = None
    off_horizontal_distances: List[float] = attrs.field(factory=list)

    start_time_utc: Optional[datetime.datetime] = None
    end_time_utc: Optional[datetime.datetime] = None

    stk_source: Optional[str] = None
    stk_labels: Optional[List[str]] = None
    stk_values: Optional[List[str]] = None
    #: Whether the last ``CTSD`` fragment ended with SurvCE's wrap comma.
    stk_ctsd_wrapped: bool = attrs.field(default=False, repr=False)

    gnss_statistics_rt: Optional[str] = None
    gnss_statistics_pp: Optional[str] = None
    pp_time: Optional[str] = None
    antenna_note: Optional[str] = None
    attribute_note: Optional[str] = None
    instrument_selected: Optional[str] = None
    gnss_profile_tolerance_rt: Optional[str] = None
    gnss_profile_tolerance_pp: Optional[str] = None
    initialization_time: Optional[str] = None

    base: Optional["Rw5BasePoint"] = attrs.field(default=None, repr=False)

    def is_stakeout_point(self) -> bool:
        """Return whether this observation carries stakeout data."""
        return self.stk_values is not None

    def stakeout_data(self) -> "OrderedDict[str, str]":
        """Return the stakeout label/value pairs for this observation.

        Returns:
            An ordered mapping of stakeout column label to value.

        Raises:
            ValueError: If this observation has no stakeout data, or the
                labels and values have mismatched lengths.
        """
        if self.stk_labels is None or self.stk_values is None:
            raise ValueError("point %r has no stakeout data" % (self.name,))
        if len(self.stk_labels) != len(self.stk_values):
            raise ValueError(
                "stakeout labels/values length mismatch for point %r"
                % (self.name,)
            )
        return OrderedDict(zip(self.stk_labels, self.stk_values))


@attrs.define
class Rw5BasePoint:
    """One base/reference station and the observations taken from it.

    Attributes:
        kind: Always :attr:`RecordKind.BASE_POINT`.
        name: Base station name, usually the RTK network name.
        number: Base station point number, from the ``BP`` ``PN`` tag.
        latitude: Base station latitude, in decimal degrees (SurvCE
            ``DD.MMSSsssss`` decoded).
        longitude: Base station longitude, in decimal degrees (SurvCE
            ``DD.MMSSsssss`` decoded).
        elevation: Base station elevation, in metres (``EL``, ``HT``, or
            SurvCE ``ET`` on ``BP``).
        north: Projected north from a base ``GS`` (often ``--GS`` with
            ``--Base``), when present.
        east: Projected east from a base ``GS``, when present.
        height: Projected height from a base ``GS``, when present.
        local_time: Local date/time from ``--DT``/``--TM`` when the base
            was configured before any rover shot under this ``BP``.
        configured_by_gps_position: Whether SurvCE wrote a base-configuration
            note (``Reading GPS Position``, ``Entering State Plane
            Coordinates``, or ``Previously Surveyed``).
        point_used: Previously surveyed point name from ``--Point Used``,
            when the base was configured from an existing survey point.
        entered_base_hr: Antenna height from ``--Entered Base HR``, when
            present.
        base_id: Base station identifier read at the rover, from the
            ``G0`` record's base-ID text.
        unknown_ag: Unlabeled ``AG`` value from the ``BP`` record.
        unknown_pa: Unlabeled ``PA`` value from the ``BP`` record.
        points: Observations taken while occupying this base station.
    """

    kind: RecordKind = attrs.field(default=RecordKind.BASE_POINT, init=False)

    name: Optional[str] = None
    number: Optional[Union[int, str]] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    elevation: Optional[float] = None
    north: Optional[float] = None
    east: Optional[float] = None
    height: Optional[float] = None
    local_time: Optional[datetime.datetime] = None
    configured_by_gps_position: bool = False
    point_used: Optional[str] = None
    entered_base_hr: Optional[float] = None
    base_id: Optional[str] = None
    unknown_ag: Optional[str] = None
    unknown_pa: Optional[str] = None

    points: List[Rw5GpsPoint] = attrs.field(factory=list)


@attrs.define
class Rw5PointCodeNote:
    """One free-text ``{PN}-{code}…`` note targeting a point by number.

    SurvCE operators sometimes type notes like ``603-ST DRUM DREAPTA``
    or ``605-ST-PRIMA-JOS`` as ``--`` lines. When the named point exists
    in the same file the parser attaches the text to that point; otherwise
    the note is kept as an orphan on the parser.

    Attributes:
        point_name: Target point number from the note prefix.
        text: Remainder after ``{PN}-`` (feature code + description).
        raw: Full note line without the ``--`` prefix.
    """

    point_name: str
    text: str
    raw: str
