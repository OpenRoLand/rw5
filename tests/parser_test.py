"""Tests for :mod:`openroland_rw5.parser`."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from openroland_survey.records import IssueSeverity

from openroland_rw5.models import Rw5GpsPoint
from openroland_rw5.parser import (
    Rw5Parser,
    leap_seconds,
    survce_angle_to_decimal,
    time_from_gps,
)

BARCANI_RW5_PATH = Path(
    r"E:\Advaita\LUCRARI\Barcani\Intabulare strazi 2020"
    r"\Masuratori\Barcani strazi.raw.rw5"
)


class TestSurvceAngleToDecimal:
    """Tests for SurvCE ``DD.MMSSsssss`` geographic-angle decoding."""

    def test_decodes_cube_a_packed_latitude(self):
        # Real Cube-a sample: 46.35192371889 → 46°35'19.2371889″.
        assert survce_angle_to_decimal(46.35192371889) == pytest.approx(
            46.58867699691667
        )

    def test_decodes_cube_a_packed_longitude(self):
        assert survce_angle_to_decimal(25.38245203479) == pytest.approx(
            25.64014454108333
        )

    def test_leaves_south_ep_decimal_degrees_unchanged(self):
        # South/Cube EP uses true decimal degrees; minute field 70 is
        # invalid for DD.MMSS, so the value is returned as-is.
        assert survce_angle_to_decimal(45.7061877684) == pytest.approx(
            45.7061877684
        )

    def test_decodes_whole_degrees(self):
        assert survce_angle_to_decimal(46.0) == pytest.approx(46.0)

    def test_decodes_exact_half_degree(self):
        # 46.3 is 46°30'00″; must not be defeated by binary float noise.
        assert survce_angle_to_decimal(46.3) == pytest.approx(46.5)

    def test_preserves_sign_for_southern_hemisphere(self):
        assert survce_angle_to_decimal(-33.550000) == pytest.approx(
            -33.9166666667
        )


class TestTimeFromGps:
    """Tests for :func:`time_from_gps` and :func:`leap_seconds`."""

    def test_returns_the_gps_epoch_for_week_zero(self):
        result = time_from_gps(week=0, milliseconds=0)

        assert result == datetime.datetime(
            1980, 1, 6, tzinfo=datetime.timezone.utc
        )

    def test_subtracts_leap_seconds_after_2016(self):
        # 2427 weeks and 291930000 ms after the GPS epoch is exactly
        # 2026-07-15T09:05:30 before leap-second correction; 18 leap
        # seconds have accumulated since the GPS epoch by then.
        result = time_from_gps(week=2427, milliseconds=291930000)

        assert result == datetime.datetime(
            2026, 7, 15, 9, 5, 12, tzinfo=datetime.timezone.utc
        )

    def test_leap_seconds_is_zero_before_first_boundary(self):
        assert leap_seconds(datetime.datetime(1980, 6, 1)) == 0

    def test_leap_seconds_accumulates_after_every_boundary(self):
        assert leap_seconds(datetime.datetime(2020, 1, 1)) == 18


class TestLocaleDetection:
    """Tests for automatic and explicit locale selection."""

    def test_detects_english_locale(self, english_minimal_path: Path):
        parser = Rw5Parser().parse_file(english_minimal_path)

        assert parser.locale == "en"

    def test_detects_romanian_locale(self, romanian_minimal_path: Path):
        parser = Rw5Parser().parse_file(romanian_minimal_path)

        assert parser.locale == "ro"

    def test_falls_back_to_default_locale_without_crashing(self):
        content = "\n".join(f"--Note line {i}" for i in range(25))

        parser = Rw5Parser().parse_text(content)

        assert parser.locale == "en"
        assert any(
            issue.severity == IssueSeverity.WARNING for issue in parser.issues
        )

    def test_explicit_locale_still_parses_every_line(
        self, english_minimal_path: Path
    ):
        # Regression test for the historical bug where supplying an
        # explicit locale skipped the line-processing loop entirely.
        content = english_minimal_path.read_text(encoding="utf-8")

        parser = Rw5Parser(locale="en").parse_text(content)

        assert parser.point_count() == 2

    def test_explicit_romanian_locale_uses_romanian_dispatch(
        self, romanian_minimal_path: Path
    ):
        content = romanian_minimal_path.read_text(encoding="utf-8")

        parser = Rw5Parser(locale="ro").parse_text(content)

        assert parser.equipment == "Trimble R12i"

    def test_recognizes_stonex_cube_a_version_banner(self):
        content = "\n".join(
            [
                "--Stonex Cube-a v6.3.20.2024.07.24",
                "JB,NMJOB,DT07-15-2026,TM09:00:00",
                "MO,UN1,SF1.00000000,EC1,EO0.000,AU0",
                "--User Defined: Local Grid System",
                "--Equipment: S9 III GNSS",
                "BP,PN1,LA46.300000000,LN24.300000000,EL500.0,AG0.000,PA0.000",
                "GPS,LA46.300036000,LN24.300072000,EL500.5,PN101,--CP1",
                "GS,PN101,N 500000.1,E 300000.2,EL120.3,--CP1",
            ]
        )

        parser = Rw5Parser().parse_text(content)

        assert parser.survce_version == "v6.3.20.2024.07.24"


class TestEncodingFallback:
    """Tests for the UTF-8-first, Windows-1252-fallback decode strategy."""

    def test_reads_utf8_file(self, tmp_path: Path):
        path = tmp_path / "utf8.rw5"
        path.write_text(
            "--Equipment: Café Total Station\nBP,PN1,LA1,LN1,EL1,AG0,PA0\n",
            encoding="utf-8",
        )

        parser = Rw5Parser().parse_file(path)

        assert parser.equipment == "Café Total Station"

    def test_falls_back_to_windows_1252(self, tmp_path: Path):
        path = tmp_path / "legacy.rw5"
        # 0x92 is a right single quotation mark in Windows-1252 and is not
        # valid standalone UTF-8.
        path.write_bytes(
            b"--Equipment: Operator\x92s Station\nBP,PN1,LA1,LN1,EL1,AG0,PA0\n"
        )

        parser = Rw5Parser().parse_file(path)

        assert parser.equipment == "Operator\u2019s Station"


class TestEnglishMinimalFixture:
    """End-to-end parsing of the English-locale minimal fixture."""

    @pytest.fixture
    def parsed(self, english_minimal_path: Path) -> Rw5Parser:
        return Rw5Parser().parse_file(english_minimal_path)

    def test_parses_job_header(self, parsed: Rw5Parser):
        assert parsed.job_name == "JOB-EN-01"
        assert parsed.job_datetime == datetime.datetime(2026, 7, 15, 9, 0, 0)

    def test_parses_mode_setup(self, parsed: Rw5Parser):
        assert parsed.units == "metric"
        assert parsed.scale_factor == pytest.approx(1.0)
        assert parsed.earth_curvature_on is True

    def test_groups_points_under_one_base(self, parsed: Rw5Parser):
        assert len(parsed.base_points) == 1
        base = parsed.base_points[0]
        assert len(base.points) == 2
        assert base.base_id == "1"

    def test_maps_projected_gs_values(self, parsed: Rw5Parser):
        point = parsed.base_points[0].points[0]
        assert point.north == pytest.approx(500000.123)
        assert point.east == pytest.approx(300000.456)
        assert point.height == pytest.approx(120.789)
        assert point.code == "CP1"

    def test_maps_quality_values(self, parsed: Rw5Parser):
        point = parsed.base_points[0].points[0]
        assert point.hsdv == pytest.approx(0.015)
        assert point.vsdv == pytest.approx(0.025)
        assert point.status == "FIXED"
        assert point.sat_count == 12
        assert point.pdop == pytest.approx(1.8)
        assert point.hdop == pytest.approx(1.2)
        assert point.vdop == pytest.approx(1.4)
        assert point.tdop == pytest.approx(1.1)
        assert point.gdop == pytest.approx(2.2)

    def test_maps_gps_time_record(self, parsed: Rw5Parser):
        point = parsed.base_points[0].points[0]
        assert point.start_time_utc == datetime.datetime(
            2026, 7, 15, 9, 5, 12, tzinfo=datetime.timezone.utc
        )
        assert point.end_time_utc == datetime.datetime(
            2026, 7, 15, 9, 9, 42, tzinfo=datetime.timezone.utc
        )

    def test_maps_local_time_from_dt_and_tm_notes(self, parsed: Rw5Parser):
        point = parsed.base_points[0].points[0]
        assert point.local_time == datetime.datetime(2026, 7, 15, 9, 5, 12)

    def test_maps_entered_and_true_antenna_height(self, parsed: Rw5Parser):
        point = parsed.base_points[0].points[0]
        assert point.true_antenna_height == pytest.approx(1.8)
        assert point.entered_antenna_height == pytest.approx(1.8)
        assert point.entered_antenna_method == "Vertical"

    def test_continues_after_an_unrecognized_line(self, parsed: Rw5Parser):
        assert parsed.point_count() == 2

    def test_records_an_issue_for_a_malformed_known_record(
        self, parsed: Rw5Parser
    ):
        second_point = parsed.base_points[0].points[1]
        # The malformed ``G0`` line for the second point is missing its
        # base-id field, which must not prevent the point's later,
        # well-formed ``GS`` line from being captured.
        assert second_point.north == pytest.approx(500010.321)
        assert any(
            issue.severity == IssueSeverity.WARNING for issue in parsed.issues
        )


class TestRomanianMinimalFixture:
    """End-to-end parsing of the Romanian-locale minimal fixture."""

    @pytest.fixture
    def parsed(self, romanian_minimal_path: Path) -> Rw5Parser:
        return Rw5Parser().parse_file(romanian_minimal_path)

    def test_parses_romanian_labels(self, parsed: Rw5Parser):
        assert parsed.equipment == "Trimble R12i"
        assert parsed.localization_file == "RO_LOC.dat"
        assert parsed.gps_scale == pytest.approx(1.000050)

    def test_parses_rtk_method_note(self, parsed: Rw5Parser):
        assert parsed.rtk_method == "Network RTK"
        assert parsed.rtk_device == "Trimble"
        assert parsed.rtk_network == "NTRIP ROMPOS"

    def test_base_name_uses_the_rtk_network(self, parsed: Rw5Parser):
        assert parsed.base_points[0].name == "ROMPOS"

    def test_groups_both_points_under_one_base(self, parsed: Rw5Parser):
        assert len(parsed.base_points) == 1
        assert len(parsed.base_points[0].points) == 2
        assert parsed.base_points[0].base_id == "ROMPOS-BASE"

    def test_maps_method_from_base_id_note(self, parsed: Rw5Parser):
        assert parsed.base_points[0].points[0].method == "RTK Fixed"

    def test_maps_averaged_and_range_quality_values(self, parsed: Rw5Parser):
        point = parsed.base_points[0].points[0]
        assert point.north_avg == pytest.approx(500100.500)
        assert point.north_sd == pytest.approx(0.005)
        assert point.north_min == pytest.approx(500100.100)
        assert point.north_max == pytest.approx(500100.900)
        assert point.east_avg == pytest.approx(300100.750)
        assert point.elev_avg == pytest.approx(130.250)
        assert point.hrms_avg == pytest.approx(0.010)
        assert point.hrms_sd == pytest.approx(0.002)
        assert point.hrms_min == pytest.approx(0.005)
        assert point.hrms_max == pytest.approx(0.015)
        assert point.vrms_avg == pytest.approx(0.018)
        assert point.hdop_avg == pytest.approx(1.500)
        assert point.hdop_min == pytest.approx(1.200)
        assert point.hdop_max == pytest.approx(1.800)
        assert point.vdop_avg == pytest.approx(1.200)
        assert point.pdop_avg == pytest.approx(2.000)
        assert point.nr_of_sat_avg == 8
        assert point.nr_of_sat_min == 6
        assert point.nr_of_sat_max == 10

    def test_maps_reading_counts(self, parsed: Rw5Parser):
        point = parsed.base_points[0].points[0]
        assert point.valid_readings == 30
        assert point.fixed_readings == 28
        assert point.float_readings == 2
        assert point.dgps_readings == 0


class TestQualityAndOffsetsFixture:
    """End-to-end parsing of the offsets/G1-G3/stakeout fixture."""

    @pytest.fixture
    def parsed(self, quality_and_offsets_path: Path) -> Rw5Parser:
        return Rw5Parser().parse_file(quality_and_offsets_path)

    def test_parses_antenna_type(self, parsed: Rw5Parser):
        assert parsed.antenna_type == "Trimble Zephyr"
        assert parsed.antenna_radius == "0.075"
        assert parsed.antenna_slant_height == "0.045"
        assert parsed.antenna_l1_offset == "0.045"
        assert parsed.antenna_l2_offset == "0.035"
        assert parsed.antenna_description == "Standard antenna description"

    def test_splits_into_two_base_points_on_base_id_change(
        self, parsed: Rw5Parser
    ):
        assert len(parsed.base_points) == 2
        assert parsed.base_points[0].base_id == "BASE-A"
        assert len(parsed.base_points[0].points) == 1
        assert parsed.base_points[1].base_id == "BASE-B"
        assert len(parsed.base_points[1].points) == 1

    def test_maps_offset_shot_and_extra_distance(self, parsed: Rw5Parser):
        point = parsed.base_points[0].points[0]
        assert point.off_azimuth == pytest.approx(45.0)
        assert point.off_distance == pytest.approx(2.5)
        assert point.off_delta_z == pytest.approx(0.3)
        assert point.off_horizontal_distances == [pytest.approx(2.55)]

    def test_swaps_offset_distance_and_delta_z_when_distance_is_zero(self):
        parser = Rw5Parser(locale="en")
        parser.parse_text(
            "\n".join(
                [
                    "--Equipment: Test",
                    "BP,PN1,LA1,LN1,EL1,AG0,PA0",
                    "OF,AZ10.0000,HD0.000,CE1.250",
                    "GPS,LA1,LN1,EL1,PN1,--C",
                ]
            )
        )
        point = parser.base_points[0].points[0]
        assert point.off_distance == pytest.approx(1.25)
        assert point.off_delta_z == pytest.approx(0.0)

    def test_maps_g1_g2_g3_baseline_records(self, parsed: Rw5Parser):
        point = parsed.base_points[0].points[0]
        assert point.delta_x == pytest.approx(0.1)
        assert point.delta_y == pytest.approx(0.2)
        assert point.delta_z == pytest.approx(0.3)
        assert point.g2_velocity_x == pytest.approx(0.001)
        assert point.g2_velocity_y == pytest.approx(0.002)
        assert point.g2_velocity_z == pytest.approx(0.003)
        assert point.g3_xy == pytest.approx(0.0001)
        assert point.g3_xz == pytest.approx(0.0002)
        assert point.g3_yz == pytest.approx(0.0003)

    def test_maps_stakeout_data(self, parsed: Rw5Parser):
        point = parsed.base_points[0].points[0]

        assert point.is_stakeout_point()
        assert point.stk_source == "X DESIGN.crd"
        data = point.stakeout_data()
        assert data["Design Pt#"] == "2061"
        assert data["Stake Nor"] == "467000.596"
        assert data["Stake Eas"] == "543000.297"
        assert data["Delta Y"] == "0.066"

    def test_second_point_has_no_stakeout_data(self, parsed: Rw5Parser):
        point = parsed.base_points[1].points[0]
        assert not point.is_stakeout_point()
        with pytest.raises(ValueError):
            point.stakeout_data()

    def test_strips_survce_trailing_comma_wrap_on_ctsd_labels(self):
        """SurvCE marks CTSD wraps with a trailing comma, not an empty field."""
        parser = Rw5Parser(locale="en")
        parser.parse_text(
            "\n".join(
                [
                    "--Equipment: Test",
                    "BP,PN1,LA1,LN1,EL1,AG0,PA0",
                    "GPS,LA1,LN1,EL1,PN32,--STK8",
                    "GS,PN32,N 100.000,E 200.000,EL50.000,--STK8",
                    "--CTSS1:TrasareFoca.crd",
                    "--CTSD0:Design Pt#,Design Elv,Stake Elv,Cut,Fill,"
                    "Desc,Stake Pt#,Stake Nor,",
                    "--CTSD1:Stake Eas,Delta X,Delta Y",
                    "--CTSD0:8,550.000,525.496,,24.504,STK8,32,"
                    "469828.408,549024.108,0.004,0.001",
                ]
            )
        )
        point = parser.base_points[0].points[0]
        data = point.stakeout_data()
        assert list(data) == [
            "Design Pt#",
            "Design Elv",
            "Stake Elv",
            "Cut",
            "Fill",
            "Desc",
            "Stake Pt#",
            "Stake Nor",
            "Stake Eas",
            "Delta X",
            "Delta Y",
        ]
        assert data["Design Pt#"] == "8"
        assert data["Cut"] == ""
        assert data["Fill"] == "24.504"
        assert data["Stake Nor"] == "469828.408"
        assert data["Stake Eas"] == "549024.108"
        assert data["Delta Y"] == "0.001"

    def test_keeps_trailing_empty_values_on_final_ctsd_fragment(self):
        """Last CTSD values line may end with commas for empty Ref fields."""
        parser = Rw5Parser(locale="en")
        parser.parse_text(
            "\n".join(
                [
                    "--Equipment: Test",
                    "BP,PN1,LA1,LN1,EL1,AG0,PA0",
                    "GPS,LA1,LN1,EL1,PN100,--PP",
                    "GS,PN100,N 100.000,E 200.000,EL50.000,--PP",
                    "--CTSS2:",
                    "--CTSD0:Design Pt#,Design Sta,Design Off,Staked Sta,"
                    "Staked Off,Design Elv,",
                    "--CTSD1:Stake Elv,Cut,Fill,Desc,Stake Pt#,Stake Nor,"
                    "Stake Eas,Delta X,Delta Y,",
                    "--CTSD2:Design Off Ref,Design Elv Ref,Cut Ref,"
                    "Fill Ref,Staked Sta Ref,",
                    "--CTSD3:Staked Off Ref",
                    "--CTSD0:PP,0+00.000,Right 0.000,-0+00.013,"
                    "Right 0.001,0.000,107.396,107.396,,",
                    "--CTSD1:STA-0+00.013 R0.001 CUT 107.396,100,"
                    "330786.087,564775.635,0.007,0.010,,",
                    "--CTSD2:,,,,",
                ]
            )
        )
        point = parser.base_points[0].points[0]
        data = point.stakeout_data()
        assert len(data) == 21
        assert data["Design Pt#"] == "PP"
        assert data["Stake Pt#"] == "100"
        assert data["Staked Off Ref"] == ""

    def test_multiple_ctss_blocks_match_design_points(self):
        """Each CTSS block maps onto the Design Pt# SP, not only the last."""
        parser = Rw5Parser(locale="en")
        parser.parse_text(
            "\n".join(
                [
                    "--Equipment: Test",
                    "SP,PN1,N 100.0,E 200.0,EL10.0,--",
                    "SP,PN4,N 110.0,E 210.0,EL10.0,--",
                    "--CTSS1:job.crd",
                    "--CTSD0:Design Pt#,Design Elv,Stake Elv,Cut,Fill,"
                    "Desc,Stake Pt#,Stake Nor,",
                    "--CTSD1:Stake Eas,Delta X,Delta Y",
                    "--CTSD0:4,0.000,553.053,553.053,,STK4,,"
                    "465114.099,536395.185,0.001,0.010",
                    "--CTSS1:job.crd",
                    "--CTSD0:Design Pt#,Design Elv,Stake Elv,Cut,Fill,"
                    "Desc,Stake Pt#,Stake Nor,",
                    "--CTSD1:Stake Eas,Delta X,Delta Y",
                    "--CTSD0:1,0.000,553.181,553.181,,STK1,,"
                    "465161.807,536424.851,0.003,0.013",
                    "--CTSS2:",
                    "--CTSD0:Design Pt#,Design Sta,Design Off,Staked Sta,"
                    "Staked Off,Design Elv,",
                    "--CTSD1:Stake Elv,Cut,Fill,Desc,Stake Pt#,Stake Nor,"
                    "Stake Eas,Delta X,Delta Y,",
                    "--CTSD2:Design Off Ref,Design Elv Ref,Cut Ref,"
                    "Fill Ref,Staked Sta Ref,",
                    "--CTSD3:Staked Off Ref",
                    "--CTSD0:PP,0+00.000,Right 0.000,0+28.095,"
                    "Right 0.007,0.000,553.070,553.070,,",
                    "--CTSD1:STA0+28.095 R0.007,,465137.963,536410.028,"
                    "14.842,23.854,,,,,,",
                ]
            )
        )
        by_name = {str(p.name): p for p in parser.base_points[0].points}
        assert by_name["4"].stakeout_data()["Design Pt#"] == "4"
        assert by_name["1"].stakeout_data()["Design Pt#"] == "1"
        # Orphan alignment dump must not clobber matched stakeout on PN4.
        assert len(by_name["4"].stk_labels or []) == 11


class TestCommentedGsGtFixture:
    """Parsing when SurvCE writes ``GS``/``GT`` as ``--``-prefixed comments."""

    @pytest.fixture
    def parsed(self, commented_gs_gt_path: Path) -> Rw5Parser:
        return Rw5Parser().parse_file(commented_gs_gt_path)

    def test_reads_projected_coordinates_from_commented_gs(
        self, parsed: Rw5Parser
    ):
        assert parsed.point_count() == 2
        first, second = parsed.base_points[0].points
        assert first.name == 2 or first.name == "2"
        assert first.north == pytest.approx(472564.7539)
        assert first.east == pytest.approx(544392.1266)
        assert first.height == pytest.approx(505.3767)
        assert first.code == "STATIE"
        assert second.name == 100 or second.name == "100"
        assert second.north == pytest.approx(472564.6313)
        assert second.east == pytest.approx(544378.2518)
        assert second.height == pytest.approx(505.3416)

    def test_reads_gps_times_from_commented_gt(self, parsed: Rw5Parser):
        first = parsed.base_points[0].points[0]
        assert first.start_time_utc is not None
        assert first.end_time_utc is not None

    def test_parses_n_of_m_reading_counts(self, parsed: Rw5Parser):
        first, second = parsed.base_points[0].points
        assert first.valid_readings == 10
        assert first.fixed_readings == 10
        assert second.valid_readings == 2
        assert second.fixed_readings == 2


class TestStoredPointsFixture:
    """Parsing SurvCE ``SP`` stored grid-point records."""

    @pytest.fixture
    def parsed(self, stored_points_path: Path) -> Rw5Parser:
        return Rw5Parser().parse_file(stored_points_path)

    def test_includes_sp_points_alongside_gps(self, parsed: Rw5Parser):
        assert parsed.point_count() == 4
        names = [point.name for point in parsed.base_points[0].points]
        assert names == ["101", "1", "2", "3"]

    def test_maps_sp_projected_coordinates(self, parsed: Rw5Parser):
        stored = parsed.base_points[0].points[2]
        assert stored.name == "2"
        assert stored.north == pytest.approx(565943.097)
        assert stored.east == pytest.approx(549571.636)
        assert stored.height == pytest.approx(505.100)
        assert stored.code == "CP"
        assert stored.method is None
        assert stored.latitude is None
        assert stored.status is None
        assert stored.collection_kind == "imported"


class TestSouthCubeEpBlGsFixture:
    """Parsing South/Cube RW5 files that use ``EP``/``BL``/``GS`` blocks."""

    @pytest.fixture
    def parsed(self, south_cube_ep_bl_gs_path: Path) -> Rw5Parser:
        return Rw5Parser().parse_file(south_cube_ep_bl_gs_path)

    def test_parses_rover_points_without_gps_records(self, parsed: Rw5Parser):
        assert parsed.point_count() == 3
        names = [point.name for point in parsed.base_points[0].points]
        assert names == ["1", "2"]
        third = parsed.base_points[1].points[0]
        assert third.name == "10"

    def test_maps_ep_geographic_and_bl_baseline_fields(self, parsed: Rw5Parser):
        first = parsed.base_points[0].points[0]
        # South EP stores true decimal degrees (not SurvCE DD.MMSS).
        assert first.latitude == pytest.approx(45.7061877684)
        assert first.longitude == pytest.approx(26.0410416471)
        assert first.elevation == pytest.approx(739.5070)
        assert first.delta_x == pytest.approx(126.0453)
        assert first.delta_y == pytest.approx(38.8709)
        assert first.delta_z == pytest.approx(191.9634)
        assert first.hsdv == pytest.approx(0.0068)
        assert first.vsdv == pytest.approx(0.0128)

    def test_decodes_survce_ddmmss_latitude_on_gps_records(self):
        content = "\n".join(
            [
                "--Stonex Cube-a v6.3.20.2024.07.24",
                "--Equipment: S9 III GNSS",
                "BP,PN1,LA46.35192371889,LN25.38245203479,EL0,AG0,PA0",
                "GPS,PN1,LA46.35192371889,LN25.38245203479,EL996.498,--axvale",
                "GS,PN1,N 565649.3032,E 549168.2262,EL955.3617,--axvale",
            ]
        )

        parser = Rw5Parser().parse_text(content)
        point = parser.base_points[0].points[0]

        assert point.latitude == pytest.approx(46.58867699691667)
        assert point.longitude == pytest.approx(25.64014454108333)
        assert parser.base_points[0].latitude == pytest.approx(
            46.58867699691667
        )

    def test_maps_gs_projected_coordinates(self, parsed: Rw5Parser):
        first = parsed.base_points[0].points[0]
        assert first.north == pytest.approx(467911.1167)
        assert first.east == pytest.approx(581167.5693)
        assert first.height == pytest.approx(702.3579)
        assert first.code == "md"

    def test_skips_base_projected_gs_lines(self, parsed: Rw5Parser):
        assert parsed.base_points[0].number == "SBT-1498"
        assert all(
            point.name != "SBT-1498" for point in parsed.base_points[0].points
        )
        assert parsed.base_points[0].north == pytest.approx(467870.6170)
        assert parsed.base_points[0].east == pytest.approx(581188.5055)
        assert parsed.base_points[0].height == pytest.approx(473.9547)

    def test_skips_base_only_gt_without_inventing_placeholder(self):
        """Base ``GT`` after ``GS … --Base`` must not create a ``?`` rover."""
        content = "\n".join(
            [
                "--Equipment: Stonex, S9III+",
                "--Base Configuration by Entering State Plane Coordinates",
                "BP,PNBP001,LA44.381767044221,LN26.582728721507,EL73.5710,"
                "AG1.9308,PA0.1068,ATAPC,SRBASE,--",
                "--GS,PNBP001,N 350637.7785,E 656734.4090,EL37.7220,--Base",
                "--GT,PNBP001,SW-522,ST-258074000,EW-522,ET-258074000",
            ]
        )

        parser = Rw5Parser().parse_text(content)
        base = parser.base_points[0]

        assert base.number == "BP001"
        assert base.north == pytest.approx(350637.7785)
        assert base.east == pytest.approx(656734.4090)
        assert base.height == pytest.approx(37.7220)
        assert base.points == []
        assert all(
            str(point.name) != "?"
            for bp in parser.base_points
            for point in bp.points
        )

    def test_base_gt_applies_to_existing_rover_with_same_pn(self):
        """Shared BP/GPS ``PN``: ``GT`` still timestamps the rover shot."""
        content = "\n".join(
            [
                "--Equipment: Test",
                "BP,PN1,LA45.423898627956,LN24.555408575788,EL639.1000,"
                "AG1.9408,PA0.1068,ATUNK,SRROVER,--",
                "GPS,PN1,LA45.423872160060,LN24.555441819940,EL638.443000,"
                "--63",
                "--GS,PN1,N 467891.8541,E 494809.7377,EL595.8566,--63",
                "--GT,PN1,SW2121,ST1000,EW2121,ET2000",
            ]
        )

        parser = Rw5Parser().parse_text(content)
        point = parser.base_points[0].points[0]

        assert point.name == "1"
        assert point.north == pytest.approx(467891.8541)
        assert point.start_time_utc is not None
        assert point.end_time_utc is not None

    def test_parses_south_observation_notes(self, parsed: Rw5Parser):
        first = parsed.base_points[0].points[0]
        second = parsed.base_points[0].points[1]

        assert first.instrument_selected == "Type=GNSS,profile=South,Model=H5"
        assert first.antenna_note == "Desc=HX-CSX049A,True=2.118m,Meas=2.000m"
        assert second.instrument_selected == "Type=GNSS,profile=South,Model=H5"
        assert second.antenna_note is None

    def test_skips_base_gs_when_pn_matches_network_name(self):
        content = "\n".join(
            [
                "--RTK Method: RTCM V3.0,Device: Internal GSM,"
                "Network: NTRIP RO_VRS_3.1_GG",
                "JB,NMCompl2,DT09-29-2016,TM12:45:24",
                "BP,PN461,LA45.75981728500,LN25.15032520194,EL500.000,"
                "AG0.000,PA0.088",
                "GS,PNRO_VRS_3.1_GG,N 500000.0,E 300000.0,EL100.0,--",
                "GPS,PN1,LA45.74096737583,LN25.09619256111,EL579.903,--POD",
                "GS,PN1,N 471251.4086,E 507604.6572,EL537.9049,--POD",
            ]
        )

        parser = Rw5Parser().parse_text(content)

        assert parser.base_points[0].name == "RO_VRS_3.1_GG"
        assert all(
            point.name != "RO_VRS_3.1_GG"
            for point in parser.base_points[0].points
        )
        assert parser.base_points[0].points[0].name == "1"
        assert parser.base_points[0].points[0].north == pytest.approx(
            471251.4086
        )


class TestJobDatetimeFormats:
    """Tests for US and European ``JB`` / ``--DT`` day-month order."""

    def test_parses_us_month_day_job_stamp(self):
        content = "\n".join(
            [
                "JB,NMJOB-US,DT08-29-2020,TM10:12:18",
                "BP,PN1,LA46.5,LN24.5,EL500.0,AG0.000,PA0.000",
            ]
        )

        parser = Rw5Parser().parse_text(content)

        assert parser.job_datetime == datetime.datetime(2020, 8, 29, 10, 12, 18)

    def test_parses_european_day_month_job_stamp(self):
        # SurvX European jobs write DD-MM-YYYY; month=29 must not win.
        content = "\n".join(
            [
                "JB,NMBarcani strazi,DT29-08-2020,TM10:12:18",
                "BP,PN1,LA46.5,LN24.5,EL500.0,AG0.000,PA0.000",
            ]
        )

        parser = Rw5Parser().parse_text(content)

        assert parser.job_datetime == datetime.datetime(2020, 8, 29, 10, 12, 18)

    def test_ambiguous_stamp_keeps_us_order(self):
        # Both 04-08 and 08-04 are valid; prefer SurvCE US month-day.
        content = "\n".join(
            [
                "JB,NMJOB-AMB,DT04-08-2020,TM09:00:00",
                "BP,PN1,LA46.5,LN24.5,EL500.0,AG0.000,PA0.000",
            ]
        )

        parser = Rw5Parser().parse_text(content)

        assert parser.job_datetime == datetime.datetime(2020, 4, 8, 9, 0, 0)

    def test_parses_european_dt_local_date_note(self):
        content = "\n".join(
            [
                "JB,NMJOB,DT08-29-2020,TM09:00:00",
                "BP,PN1,LA46.5,LN24.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN101,LA46.5,LN24.5,EL500.0,--CP1",
                "--DT29-08-2020",
                "--TM01:02:03",
            ]
        )

        parser = Rw5Parser().parse_text(content)

        point = parser.base_points[0].points[0]
        assert point.local_time == datetime.datetime(2020, 8, 29, 1, 2, 3)

    def test_dt_tm_before_offset_attaches_to_next_rover(self):
        content = "\n".join(
            [
                "JB,NMJOB,DT08-08-2016,TM11:00:00",
                "BP,PN1,LA46.5,LN24.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN134,LA46.5,LN24.5,EL500.0,--CP",
                "GS,PN134,N 500000.0,E 300000.0,EL120.0,--CP",
                "--DT08-08-2016",
                "--TM11:13:49",
                "--Offset GPS by Distance/Angle",
                "OF,AZ90.0000,HD1.500,CE0.000",
                "GPS,PN135,LA46.5,LN24.5,EL500.1,--OFFSET",
                "GS,PN135,N 500001.5,E 300000.0,EL120.1,--OFFSET",
                "--DT08-08-2016",
                "--TM11:14:31",
                "--Offset GPS by Distance/Angle",
                "OF,AZ90.0000,HD1.500,CE0.000",
                "GPS,PN136,LA46.5,LN24.5,EL500.2,--OFFSET",
                "GS,PN136,N 500003.0,E 300000.0,EL120.2,--OFFSET",
            ]
        )

        parser = Rw5Parser().parse_text(content)

        pn134, pn135, pn136 = parser.base_points[0].points
        assert pn134.local_time is None
        assert pn135.local_time == datetime.datetime(2016, 8, 8, 11, 13, 49)
        assert pn136.local_time == datetime.datetime(2016, 8, 8, 11, 14, 31)

    def test_dt_tm_after_completed_shot_with_g0_attaches_to_next(
        self, offset_g0_moment_path: Path
    ):
        parser = Rw5Parser().parse_file(offset_g0_moment_path)

        by_name = {
            str(point.name): point for point in parser.base_points[0].points
        }
        assert by_name["142"].local_time is None
        assert by_name["143"].local_time == datetime.datetime(
            2015, 7, 9, 9, 39, 2
        )
        assert by_name["143"].moment == datetime.datetime(2015, 7, 9, 6, 39, 16)
        assert by_name["144"].local_time == datetime.datetime(
            2015, 7, 9, 9, 39, 44
        )


class TestBaseConfigurationFixture:
    """SurvCE base-config BP blocks and English HDOP Min:/Max: notes."""

    @pytest.fixture
    def parsed(self, base_configuration_path: Path) -> Rw5Parser:
        return Rw5Parser().parse_file(base_configuration_path)

    def test_parses_base_config_without_orphan_placeholders(
        self, parsed: Rw5Parser
    ):
        assert len(parsed.base_points) == 2
        config_base = parsed.base_points[0]
        assert config_base.number == 1508
        assert config_base.points == []
        assert config_base.configured_by_gps_position is True
        assert config_base.elevation == pytest.approx(511.2305)
        assert config_base.north == pytest.approx(467880.2788)
        assert config_base.east == pytest.approx(581155.2314)
        assert config_base.height == pytest.approx(474.0811)
        assert config_base.entered_base_hr == pytest.approx(0.0)
        assert config_base.local_time == datetime.datetime(
            2020, 8, 29, 10, 23, 3
        )
        assert not any(
            point.name in (None, "?", "")
            for base in parsed.base_points
            for point in base.points
        )
        assert parsed.issues == []

    def test_parses_survce_hdop_min_max_with_colons(self, parsed: Rw5Parser):
        rover = parsed.base_points[1].points[0]
        assert rover.name == "1"
        assert rover.hdop_avg == pytest.approx(0.6000)
        assert rover.hdop_min == pytest.approx(0.6000)
        assert rover.hdop_max == pytest.approx(0.6000)
        assert not any(
            "failed to parse" in issue.message for issue in parsed.issues
        )

    def test_parses_device_internet_mountpoint_subkey(self, parsed: Rw5Parser):
        assert parsed.rtk_device == "Device Internet"
        assert parsed.rtk_network == "NTRIP RTCM32-MSM"

    def test_parses_sd_min_max_quality_averages(self):
        content = "\n".join(
            [
                "JB,NMJOB,DT08-29-2020,TM09:00:00",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN1,LA46.5,LN25.5,EL500.0,--",
                "GS,PN1,N 500000.0,E 300000.0,EL100.0,--",
                "--HSDV Avg: 0.0074 SD: 0.0009 Min: 0.0065 Max: 0.0083",
                "--VSDV Avg: 0.0148 SD: 0.0018 Min: 0.0130 Max: 0.0167",
                "--AGE Avg: 1.0000 Min: 1.0000 Max: 1.0000",
                "--NRMS Avg: 0.0046 SD: 0.0003 Min: 0.0043 Max: 0.0048",
                "--ERMS Avg: 0.0046 SD: 0.0003 Min: 0.0043 Max: 0.0048",
            ]
        )

        parser = Rw5Parser().parse_text(content)
        point = parser.base_points[0].points[0]

        assert point.hrms_avg == pytest.approx(0.0074)
        assert point.hrms_sd == pytest.approx(0.0009)
        assert point.hrms_min == pytest.approx(0.0065)
        assert point.hrms_max == pytest.approx(0.0083)
        assert point.vrms_avg == pytest.approx(0.0148)
        assert point.age_avg == pytest.approx(1.0)
        assert point.age_min == pytest.approx(1.0)
        assert point.age_max == pytest.approx(1.0)
        assert point.nrms_avg == pytest.approx(0.0046)
        assert point.erms_avg == pytest.approx(0.0046)

    def test_sd_min_max_quality_averages_do_not_emit_unknown_label_debug(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        content = "\n".join(
            [
                "JB,NMJOB,DT08-29-2020,TM09:00:00",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN1,LA46.5,LN25.5,EL500.0,--",
                "GS,PN1,N 500000.0,E 300000.0,EL100.0,--",
                "--HSDV Avg: 0.0074 SD: 0.0009 Min: 0.0065 Max: 0.0083",
                "--RTK Method: Auto, Device: Phone Internet,"
                " Phone Internet: NTRIP RTCM32-MSM",
            ]
        )

        parser = Rw5Parser().parse_text(content)

        assert parser.rtk_network == "NTRIP RTCM32-MSM"
        messages = [record.getMessage() for record in caplog.records]
        assert not any("unknown label" in message for message in messages)
        assert not any(
            "unknown RTK method part" in message for message in messages
        )


class TestSurvceAttributeAndGluedNotes:
    """GIS Attribute notes, blank lines, and notes glued onto G3."""

    def test_parses_attribute_note_onto_following_gps(self):
        content = "\n".join(
            [
                "JB,NMJOB,DT08-29-2020,TM09:00:00",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "--Attribute:1,NUME:COLCERIU DANDU GAVRIL 324",
                "GPS,PN2,LA46.5,LN25.5,EL500.0,--F",
                "GS,PN2,N 500000.0,E 300000.0,EL100.0,--F",
            ]
        )

        parser = Rw5Parser().parse_text(content)
        point = parser.base_points[0].points[0]

        assert point.name == "2"
        assert point.attribute_note == "1,NUME:COLCERIU DANDU GAVRIL 324"

    def test_skips_blank_lines_without_unrecognized_debug(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        content = "\n".join(
            [
                "JB,NMJOB,DT08-29-2020,TM09:00:00",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "",
                "GPS,PN1,LA46.5,LN25.5,EL500.0,--",
                "GS,PN1,N 500000.0,E 300000.0,EL100.0,--",
            ]
        )

        Rw5Parser().parse_text(content)

        messages = [record.getMessage() for record in caplog.records]
        assert not any(
            "unrecognized RW5 record code" in message for message in messages
        )

    def test_splits_note_glued_onto_g3_without_newline(self):
        content = "\n".join(
            [
                "JB,NMJOB,DT08-29-2020,TM09:00:00",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN1,LA46.5,LN25.5,EL500.0,--",
                "G3,XY0.00118244,XZ0.00202838,"
                "YZ0.00076853--Number of Satellites Avg: 12 Min: 12 Max: 12",
                "GS,PN1,N 500000.0,E 300000.0,EL100.0,--",
            ]
        )

        parser = Rw5Parser().parse_text(content)
        point = parser.base_points[0].points[0]

        assert point.g3_yz == pytest.approx(0.00076853)
        assert point.nr_of_sat_avg == 12
        assert point.nr_of_sat_min == 12
        assert point.nr_of_sat_max == 12


class TestSurvXHeaderRecords:
    """SurvX banner, Gnss Device, CS/ES, and GNSS Position Adjustment."""

    _HEADER = "\n".join(
        [
            "--SurvX 4.0.200305.154342",
            "JB,NMBarcani strazi,DT29-08-2020,TM10:12:18",
            "MO,AD0,UN1,SF1.000000,EC0,EO0.0,AU0",
            "--Gnss Device: Model=,Serial=SG1197126313138,"
            " FirmwareVer=1.09.190808.RG11GL",
            "CS,CO1,ZGstereo,ZNGRS80:K25,DN",
            "ES,RD6378137.00000000,IF298.257222101000,EMGRS80",
            "--GNSS Position Adjustment",
            "BP,PNSBT-1498,LA45.70582088,LN26.04130373,HT511.1026,--",
            "GS,PNSBT-1498,N 467870.6170,E 581188.5055,EL473.9547,--",
            "EP,TM09:14:38.000,LA45.7061877684,LN26.0410416471,HT739.5070",
            "BL,DCROVER,PN1,DX126.0453,DY38.8709,DZ191.9634,--md",
            "GS,PN1,N 467911.1167,E 581167.5693,EL702.3579,--md",
        ]
    )

    def test_parses_survx_header_metadata(self):
        parser = Rw5Parser().parse_text(self._HEADER)

        assert parser.survce_version == "SurvX 4.0.200305.154342"
        assert parser.gnss_device.startswith("Model=,Serial=SG1197126313138")
        assert parser.cs_zone == "stereo"
        assert parser.ellipsoid == "GRS80"

    def test_survx_header_does_not_emit_unknown_debug(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")

        Rw5Parser().parse_text(self._HEADER)

        messages = [record.getMessage() for record in caplog.records]
        assert not any("unrecognized note line" in m for m in messages)
        assert not any("unknown label" in m for m in messages)
        assert not any("unrecognized RW5 record code" in m for m in messages)


class TestBarcaniIntegration:
    """Integration test against the real Barcani South/Cube RW5 export."""

    @pytest.mark.skipif(
        not BARCANI_RW5_PATH.is_file(),
        reason="Barcani RW5 file is not available on this machine",
    )
    def test_parses_thousands_of_rover_points(self):
        parser = Rw5Parser().parse_file(BARCANI_RW5_PATH)

        assert parser.point_count() >= 9800
        named = [
            point
            for point in parser.records(include_base=False)
            if point.name not in (None, "?", "")
        ]
        assert len(named) >= 9800
        assert all(
            point.north is not None and point.east is not None
            for point in named[:100]
        )

    @pytest.mark.skipif(
        not BARCANI_RW5_PATH.is_file(),
        reason="Barcani RW5 file is not available on this machine",
    )
    def test_parses_european_job_datetime(self):
        parser = Rw5Parser().parse_file(BARCANI_RW5_PATH)

        assert parser.job_datetime == datetime.datetime(2020, 8, 29, 10, 12, 18)


class TestRecordsAndPointCount:
    """Tests for :meth:`Rw5Parser.records` and :meth:`point_count`."""

    def test_records_yields_base_points_and_gps_points(
        self, english_minimal_path: Path
    ):
        parser = Rw5Parser().parse_file(english_minimal_path)

        records = list(parser.records())

        assert len(records) == 1 + 2

    def test_records_can_exclude_base_points(self, english_minimal_path: Path):
        parser = Rw5Parser().parse_file(english_minimal_path)

        records = list(parser.records(include_base=False))

        assert len(records) == 2

    def test_records_can_be_filtered_by_predicate(
        self, english_minimal_path: Path
    ):
        parser = Rw5Parser().parse_file(english_minimal_path)

        records = list(
            parser.records(
                include_base=False,
                predicate=lambda record: isinstance(record, Rw5GpsPoint)
                and record.name == "102",
            )
        )

        assert len(records) == 1
        assert records[0].name == "102"


class TestSurvceJobHeaderNotes:
    """Tests for SurvCE job-header note labels (parse-only metadata)."""

    _HEADER_NOTES = "\n".join(
        [
            "--TS Angles: GRAD(AU1)",
            "--Reference System: ETRS89/Stereographic 1970/Marea Neagra 1975",
            "--Localization Type: None",
            "--RTK Method: None, Device: None, Network:  ",
            "JB,NMJOB,DT10-07-2024,TM12:00:00",
            "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
            "GPS,PN1,LA46.5,LN25.5,EL500.0,--",
            "GS,PN1,N 500000.0,E 300000.0,EL100.0,--",
        ]
    )

    def test_parses_ts_angles_reference_system_localization_type(self):
        parser = Rw5Parser().parse_text(self._HEADER_NOTES)

        assert parser.ts_angles == "GRAD(AU1)"
        assert parser.reference_system == (
            "ETRS89/Stereographic 1970/Marea Neagra 1975"
        )
        assert parser.localization_type == "None"

    def test_parses_empty_rtk_network(self):
        parser = Rw5Parser().parse_text(self._HEADER_NOTES)

        assert parser.rtk_method == "None"
        assert parser.rtk_device == "None"
        assert parser.rtk_network == ""

    def test_ignores_trailing_bare_none_rtk_method_part(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        text = "\n".join(
            [
                "--RTK Method: Auto, Device: None, None",
                "JB,NMJOB,DT10-07-2024,TM12:00:00",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN1,LA46.5,LN25.5,EL500.0,--",
                "GS,PN1,N 500000.0,E 300000.0,EL100.0,--",
            ]
        )

        parser = Rw5Parser().parse_text(text)

        assert parser.rtk_method == "Auto"
        assert parser.rtk_device == "None"
        assert not any(
            "unknown RTK method part" in record.message
            for record in caplog.records
        )

    def test_parses_quality_note_age(self):
        text = "\n".join(
            [
                "JB,NMJOB,DT10-07-2024,TM12:00:00",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN1,LA46.5,LN25.5,EL500.0,--",
                "GS,PN1,N 500000.0,E 300000.0,EL100.0,--",
                (
                    "--HSDV:0.008, VSDV:0.013, STATUS:FIXED, SATS:16, "
                    "AGE:5, PDOP:1.2000, NSDV:0.0094, ESDV:0.0094"
                ),
            ]
        )

        point = Rw5Parser().parse_text(text).base_points[0].points[0]

        assert point.age_avg == pytest.approx(5.0)
        assert point.hsdv == pytest.approx(0.008)
        assert point.status == "FIXED"

    def test_parses_auto_readings(self):
        text = "\n".join(
            [
                "JB,NMJOB,DT10-07-2024,TM12:00:00",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN1,LA46.5,LN25.5,EL500.0,--",
                "GS,PN1,N 500000.0,E 300000.0,EL100.0,--",
                "--Valid Readings: 3 of 3",
                "--AUTO Readings: 0 of 3",
            ]
        )

        point = Rw5Parser().parse_text(text).base_points[0].points[0]

        assert point.valid_readings == 3
        assert point.auto_readings == 0

    def test_pre_bp_stakeout_does_not_invent_placeholder_point(self):
        text = "\n".join(
            [
                "--CTSS1:Halchiu.crd",
                "--CTSD0:Design Pt#,Design Elv,Stake Elv",
                "--CTSD0:51,0.000,527.065",
                "--RTK Method: RTCM V3.0, Device: Internal GSM, "
                "Network: NTRIP RO_VRS_MSM4",
                "BP,PN053,LA45.5,LN25.4,EL580.0,AG0.000,PA0.088,--",
                "GPS,PN100,LA45.4,LN25.2,EL567.0,--GM",
                "GS,PN100,N 475210.3,E 538893.5,EL527.0,--GM",
            ]
        )

        parser = Rw5Parser().parse_text(text)

        assert all(base.name != "?" for base in parser.base_points)
        assert all(
            point.name != "?"
            for base in parser.base_points
            for point in base.points
        )
        assert parser.base_points[0].points[0].name == "100"

    def test_english_equipment_in_romanian_locale_file(self):
        text = "\n".join(
            [
                "--Definit de utilizator: ROMANIA/Stereo",
                "--Equipment:   Stonex,  S9 GNSS, SN:1",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN1,LA46.5,LN25.5,EL500.0,--",
                "GS,PN1,N 500000.0,E 300000.0,EL100.0,--",
                "--HSIG Avg: 0.0136 SD: 0.0005 Min: 0.0127 Max: 0.0146",
                "--VSIG Avg: 0.0238 SD: 0.0010 Min: 0.0221 Max: 0.0258",
                (
                    "--HSIG:0.015, VSIG:0.026, STATUS:FIXED, SATS:15, "
                    "NSIG:0.011, ESIG:0.010"
                ),
            ]
        )

        parser = Rw5Parser().parse_text(text)
        point = parser.base_points[0].points[0]

        assert parser.locale == "ro"
        assert parser.equipment == "Stonex,  S9 GNSS, SN:1"
        assert point.hrms_avg == pytest.approx(0.0136)
        assert point.vrms_avg == pytest.approx(0.0238)
        assert point.hsdv == pytest.approx(0.015)

    def test_parses_localization_translate_and_ignores_from_pt(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        text = "\n".join(
            [
                "--Equipment: Test",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN100,LA46.5,LN25.5,EL500.0,--",
                "GS,PN100,N 500000.0,E 300000.0,EL100.0,--",
                "--From Pt100",
                "--Translate: dy=0.0000, dx=0.0000, dz=-2.2000",
            ]
        )

        parser = Rw5Parser().parse_text(text)

        assert parser.localization_translate_dy == pytest.approx(0.0)
        assert parser.localization_translate_dx == pytest.approx(0.0)
        assert parser.localization_translate_dz == pytest.approx(-2.2)
        messages = [record.getMessage() for record in caplog.records]
        assert not any("unknown label 'Translate'" in m for m in messages)
        assert not any("unrecognized note line: From Pt" in m for m in messages)

    def test_parses_averaged_points_stddev_and_station(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        text = "\n".join(
            [
                "--Equipment: Test",
                "SP,PN9,N 400125.0000,E 500034.8377,EL501.3000,--AVERAGED PT",
                "--Averaged position from 4 points",
                "--Averaged Points: 1,2,3,4",
                "--StdDev N: 216.506351",
                "--StdDev E: 40.907898",
                "--StdDev Z: 0.818535",
                "SP,PN10,N 400125.0000,E 500000.0000,EL500.3000,--STA",
                "--Calculated from Point Projection routine.",
                "--Station: 3+75.000, Offset L0.000",
                "--ABC:",
            ]
        )

        parser = Rw5Parser().parse_text(text)
        messages = [record.getMessage() for record in caplog.records]

        assert parser.averaged_points == "1,2,3,4"
        assert parser.stddev_n == pytest.approx(216.506351)
        assert parser.stddev_e == pytest.approx(40.907898)
        assert parser.stddev_z == pytest.approx(0.818535)
        assert parser.station_note == "3+75.000, Offset L0.000"
        assert not any("unknown label" in message for message in messages)
        assert not any(
            "unrecognized note line: Averaged position" in message
            for message in messages
        )
        assert not any(
            "unrecognized note line: Calculated from Point Projection"
            in message
            for message in messages
        )

    def test_parses_tds_xchange_job_header(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        text = "\n".join(
            [
                "JB,NM1409BRASOV,DT09-14-2019,TM09:53:56",
                "MO,AD0,UN1,SF1.00000000,EC1,EO0.0,AU0",
                "--TDS RW5 file created by X-change core engine v6.5.0",
                "--Date (creation): 09-14-2019 09:53:56",
                "--Date (last modification): 09-15-2019 20:19:46",
                "--GPS Reference station,SP,PN1101,N 485489.5281,"
                "E 561313.7138,EL542.7873,--",
                "--Instrument Model: CS15 Serial: 3497783 Name: 7783",
                "--GPS Survey 8.00",
                "--Antenna height: 2.000",
                "SP,PN1,N 462470.9084,E 547862.6213,EL561.0541,--TR",
                "--Antenna height: 1.800",
                "SP,PN2,N 462466.4775,E 547861.2731,EL561.0012,--TR",
            ]
        )

        parser = Rw5Parser().parse_text(text)
        messages = [record.getMessage() for record in caplog.records]
        by_name = {str(p.name): p for b in parser.base_points for p in b.points}

        assert parser.locale == "en"
        assert parser.date_creation == "09-14-2019 09:53:56"
        assert parser.date_last_modification == "09-15-2019 20:19:46"
        assert parser.instrument_model == ("CS15 Serial: 3497783 Name: 7783")
        assert parser.equipment == parser.instrument_model
        assert parser.survce_version == "GPS Survey 8.00"
        assert by_name["1101"].north == pytest.approx(485489.5281)
        assert by_name["1"].entered_antenna_height == pytest.approx(2.0)
        assert by_name["2"].entered_antenna_height == pytest.approx(1.8)
        assert not any("unknown label" in message for message in messages)
        assert not any(
            "could not determine RW5 locale" in issue.message
            for issue in parser.issues
        )

    def test_acknowledges_polyline_area_note_without_debug(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        text = "\n".join(
            [
                "--Equipment: Test",
                "SP,PN33,N 1.0,E 1.0,EL0.0,--",
                "SP,PN32,N 2.0,E 1.0,EL0.0,--",
                "--Calculate area of polyline 33,32,93,34: Area = 4.8734SM",
            ]
        )

        parser = Rw5Parser().parse_text(text)

        assert parser.polyline_area_note == (
            "Calculate area of polyline 33,32,93,34: Area = 4.8734SM"
        )
        assert not any(
            "unknown label" in record.getMessage() for record in caplog.records
        )

    def test_parses_sp_north_state_plane_base_config(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        text = "\n".join(
            [
                "--Equipment: Test",
                "SP,PN1,N 350637.7800,E 656734.4090,EL37.7220,--S101",
                "--Base Configuration by Entering State Plane Coordinates",
                "--SP North: 350637.780000, SP East: 656734.409000, "
                "Elv: 37.72200",
                "--Entered Base HR: 1.9950 m, Slant",
                "BP,PNBP001,LA44.3,LN26.5,EL73.5,AG1.9,PA0.1,--",
                "--GS,PNBP001,N 350637.7785,E 656734.4090,EL37.7220,--Base",
            ]
        )

        parser = Rw5Parser().parse_text(text)
        # SP creates a placeholder base; the following BP receives the
        # pending ``Base Configuration`` / ``Point Used`` flags.
        base = next(b for b in parser.base_points if b.number == "BP001")

        assert base.configured_by_gps_position is True
        assert parser.base_points[0].north == pytest.approx(350637.78)
        assert parser.base_points[0].east == pytest.approx(656734.409)
        assert parser.base_points[0].height == pytest.approx(37.722)
        assert not any(
            "unknown label 'SP North'" in record.getMessage()
            for record in caplog.records
        )

    def test_parses_point_used_previously_surveyed_base(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        text = "\n".join(
            [
                "--Equipment: Test",
                "BP,PN1,LA45.3,LN25.5,EL800.0,AG0.0,PA0.0,--",
                "GPS,PN10,LA45.3,LN25.5,EL800.0,--",
                "GS,PN10,N 453410.9,E 567486.0,EL780.1,--",
                "--Base Configuration by Previously Surveyed",
                "--Point Used: ST2",
                "--Entered Base HR: 2.5000 m, Vertical",
                "BP,PNST2,LA45.3,LN25.5,EL820.0,AG2.5,PA0.0,--",
                "--GS,PNST2,N 453410.9,E 567486.0,EL780.1,--Base",
            ]
        )

        parser = Rw5Parser().parse_text(text)
        base = next(b for b in parser.base_points if b.number == "ST2")

        assert base.configured_by_gps_position is True
        assert base.point_used == "ST2"
        assert not any(
            "unknown label 'Point Used'" in record.getMessage()
            for record in caplog.records
        )

    def test_acknowledges_total_station_records_without_debug(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        text = "\n".join(
            [
                "--Equipment: STS2RP",
                "--TS Scale: 1.00000000",
                "--EDM Mode: Standard",
                "--P.C. mm Applied: 0.0000 (Leica 0mm:backsight)",
                "OC,OP1,N 485104.52000,E 561942.01900,EL0.000,--",
                "BK,OP1,BP99,BS335.0300,BC0.0000",
                "BD,OP1,FP99,AR0.0000,ZE89.5323,SD39.870000,--",
                "--Calculated: AR0°00'00\", HD39.445, Z0.000",
                "--Measured: AR0°00'00\", HD39.870, Z0.098",
                "--Delta: AR0°00'00\", HD0.425, Z0.098",
                "SS,OP1,FP100,AR330.1909,ZE89.4555,SD39.807000,--CC",
            ]
        )

        parser = Rw5Parser().parse_text(text)
        messages = [record.getMessage() for record in caplog.records]

        assert parser.ts_scale == pytest.approx(1.0)
        assert parser.edm_mode == "Standard"
        assert parser.pc_mm_applied == "0.0000 (Leica 0mm:backsight)"
        assert not any("unknown label" in message for message in messages)
        assert not any(
            "unrecognized RW5 record code" in message for message in messages
        )

    def test_empty_base_id_keeps_prior_without_debug(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        text = "\n".join(
            [
                "--Equipment: Test",
                "BP,PN1,LA46.5,LN25.5,EL500.0,AG0.000,PA0.000",
                "GPS,PN10,LA46.5,LN25.5,EL500.0,--",
                "GS,PN10,N 500000.0,E 300000.0,EL100.0,--",
                "G0,12/31/2009 08:30:58,Base ID read at rover: 0055",
                "GPS,PN11,LA46.5,LN25.5,EL500.0,--",
                "GS,PN11,N 500001.0,E 300001.0,EL100.0,--",
                "G0,12/31/2009 08:55:20,Base ID read at rover: ",
            ]
        )

        parser = Rw5Parser().parse_text(text)

        assert parser.base_points[0].base_id == "0055"
        assert not any(
            "unrecognized base-id text" in record.getMessage()
            for record in caplog.records
        )

    def test_job_header_notes_do_not_emit_unknown_label_debug(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")

        Rw5Parser().parse_text(self._HEADER_NOTES)

        messages = [record.getMessage() for record in caplog.records]
        assert not any("unknown label" in message for message in messages)
        assert not any(
            "unknown RTK method part" in message for message in messages
        )


class TestSouthCubeGnssObservationNotes:
    """Tests for South/Cube per-observation GNSS note lines."""

    _OBSERVATION_BLOCK = "\n".join(
        [
            "--GNSS Statistics RT: Obs=2,Solution=RTK FIXED,PDOPMax=1.920",
            "--GNSS Statistics PP: Not Active",
            "--PP Time: StartWeek=2145,StartSec=569111.0,"
            "StopWeek=2145,StopSec=569113.0",
            "--Antenna: Desc=HX-CSX049A,True=2.318m,Meas=2.200m",
            "Initialization time 0,00s",
            "--Instrument Selected:Type=GNSS,profile=South,Model=H5",
            "--GNSS Profile Tolerance RT: Solution=RTK FIXED,PDOP=3.000",
            "--GNSS Profile Tolerance PP: Not Active",
        ]
    )

    _JOB_TAIL = "\n".join(
        [
            "EP,TM09:14:38.000,LA45.7061877684,LN26.0410416471,HT739.5070",
            "BL,DCROVER,PN1,DX126.0453,DY38.8709,DZ191.9634,--md",
            "GS,PN1,N 467911.1167,E 581167.5693,EL702.3579,--md",
        ]
    )

    def test_parses_full_south_gnss_observation_note_block(self):
        content = "\n".join(
            [
                "--Stonex Cube-a v6.3.20.2024.07.24",
                "JB,NMJOB,DT08-29-2020,TM09:00:00",
                "BP,PNSBT-1498,LA45.70582088,LN26.04130373,HT511.1026,--",
                self._OBSERVATION_BLOCK,
                self._JOB_TAIL,
            ]
        )

        parser = Rw5Parser().parse_text(content)
        point = parser.base_points[0].points[0]

        assert point.gnss_statistics_rt.startswith("Obs=2,Solution=RTK FIXED")
        assert point.gnss_statistics_pp == "Not Active"
        assert point.pp_time.startswith("StartWeek=2145")
        assert point.antenna_note.startswith("Desc=HX-CSX049A")
        assert point.initialization_time == "0,00s"
        assert point.instrument_selected == "Type=GNSS,profile=South,Model=H5"
        assert point.gnss_profile_tolerance_rt.startswith("Solution=RTK FIXED")
        assert point.gnss_profile_tolerance_pp == "Not Active"

    def test_south_gnss_notes_do_not_emit_unknown_label_debug(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        content = "\n".join(
            [
                "--Stonex Cube-a v6.3.20.2024.07.24",
                "JB,NMJOB,DT08-29-2020,TM09:00:00",
                "BP,PNSBT-1498,LA45.70582088,LN26.04130373,HT511.1026,--",
                self._OBSERVATION_BLOCK,
                self._JOB_TAIL,
            ]
        )

        Rw5Parser().parse_text(content)

        messages = [record.getMessage() for record in caplog.records]
        assert not any("unknown label" in message for message in messages)
        assert not any(
            "unrecognized RW5 record code" in message for message in messages
        )


class TestPointCodeNotes:
    """Tests for operator ``{PN}-{code}…`` free-text notes."""

    def test_attaches_point_code_note_to_matching_gps(
        self, caplog: pytest.LogCaptureFixture
    ):
        import logging

        caplog.set_level(logging.DEBUG, logger="openroland_rw5.parser")
        text = "\n".join(
            [
                "--Equipment: Test",
                "GPS,PN603,LA45.5,LN25.5,EL900.0,--",
                "GS,PN603,N 450000.0,E 540000.0,EL860.0,--",
                "--603-ST DRUM DREAPTA===",
            ]
        )

        parser = Rw5Parser().parse_text(text)
        point = parser.base_points[0].points[0]

        assert point.comment == "ST DRUM DREAPTA==="
        assert point.code == "ST DRUM DREAPTA==="
        assert parser.orphan_point_notes == []
        assert not any(
            "unrecognized note line" in record.getMessage()
            for record in caplog.records
        )

    def test_does_not_clobber_existing_comment(self):
        text = "\n".join(
            [
                "--Equipment: Test",
                "GPS,PN605,LA45.5,LN25.5,EL900.0,--Statie",
                "GS,PN605,N 450000.0,E 540000.0,EL860.0,--Statie",
                "--605-ST-PRIMA-JOS",
            ]
        )

        point = Rw5Parser().parse_text(text).base_points[0].points[0]

        assert point.comment == "Statie"
        assert point.code == "Statie"

    def test_does_not_attach_to_previous_unrelated_point(self):
        text = "\n".join(
            [
                "--Equipment: Test",
                "GPS,PN534,LA45.5,LN25.5,EL900.0,--Statie",
                "GS,PN534,N 450000.0,E 540000.0,EL860.0,--Statie",
                "--603-ST DRUM DREAPTA===",
                "--605-ST-PRIMA-JOS",
                "--608-ST-IN-PARAU",
                "--610-ST-LA-TRUNCHI",
                "--614-ST-SANT2",
                "--620-ST-FUNDU-VAII",
            ]
        )

        parser = Rw5Parser().parse_text(text)
        previous = parser.base_points[0].points[0]

        assert previous.comment == "Statie"
        assert [n.point_name for n in parser.orphan_point_notes] == [
            "603",
            "605",
            "608",
            "610",
            "614",
            "620",
        ]
        assert parser.orphan_point_notes[0].text == "ST DRUM DREAPTA==="
        assert parser.orphan_point_notes[1].text == "ST-PRIMA-JOS"

    def test_pending_note_attaches_when_point_follows(self):
        text = "\n".join(
            [
                "--Equipment: Test",
                "--608-ST-IN-PARAU",
                "SP,PN608,N 450100.0,E 540100.0,EL850.0,--",
            ]
        )

        parser = Rw5Parser().parse_text(text)
        point = parser.base_points[0].points[0]

        assert point.comment == "ST-IN-PARAU"
        assert point.code == "ST-IN-PARAU"
        assert parser.orphan_point_notes == []

    def test_matches_leading_zero_point_names(self):
        text = "\n".join(
            [
                "--Equipment: Test",
                "SP,PN0603,N 1.0,E 2.0,EL3.0,--",
                "--603-ST-LA-TRUNCHI",
            ]
        )

        point = Rw5Parser().parse_text(text).base_points[0].points[0]

        assert point.comment == "ST-LA-TRUNCHI"
