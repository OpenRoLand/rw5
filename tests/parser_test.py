"""Tests for :mod:`siscadro_rw5.parser`."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from siscadro_survey.records import IssueSeverity

from siscadro_rw5.models import Rw5GpsPoint
from siscadro_rw5.parser import Rw5Parser, leap_seconds, time_from_gps


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
