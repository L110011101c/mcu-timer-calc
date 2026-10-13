#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""timer_calc 的单元测试。

测试基准全部来自手工推算，作为回归防线：
- 12MHz / 12T / 方式1 / 10ms      -> 初值 0xD8F0
- 11.0592MHz / 12T / 方式1 / 10ms -> 初值 0xDC00（最经典的验证值）
- 12MHz / 12T / 方式2 / 100us     -> 初值 0x9C
- 12MHz / 12T / 方式0 / 1ms       -> 初值 7192 = 0x1C18（TH0=0xE0, TL0=0x18）
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "scripts"))

import timer_calc as tc  # noqa: E402


class TestParsing(unittest.TestCase):
    def test_frequency_units(self):
        self.assertAlmostEqual(tc.parse_frequency("12MHz"), 12e6)
        self.assertAlmostEqual(tc.parse_frequency("12M"), 12e6)
        self.assertAlmostEqual(tc.parse_frequency("12mhz"), 12e6)
        self.assertAlmostEqual(tc.parse_frequency("11059200"), 11059200)
        self.assertAlmostEqual(tc.parse_frequency("11.0592MHz"), 11059200)
        self.assertAlmostEqual(tc.parse_frequency("8kHz"), 8000)

    def test_frequency_invalid(self):
        for bad in ("abc", "12GHz", "", "MHz", "0"):
            with self.assertRaises(tc.CalcError):
                tc.parse_frequency(bad)

    def test_time_units(self):
        self.assertAlmostEqual(tc.parse_time("10ms"), 0.01)
        self.assertAlmostEqual(tc.parse_time("50us"), 50e-6)
        self.assertAlmostEqual(tc.parse_time("1s"), 1.0)
        self.assertAlmostEqual(tc.parse_time("500ns"), 500e-9)
        self.assertAlmostEqual(tc.parse_time("10 mS"), 0.01)

    def test_time_requires_unit(self):
        for bad in ("10", "abc", "10x", "0ms"):
            with self.assertRaises(tc.CalcError):
                tc.parse_time(bad)

    def test_prescaler_normalization(self):
        self.assertEqual(tc.normalize_prescaler("12t"), "12T")
        self.assertEqual(tc.normalize_prescaler("12"), "12T")
        self.assertEqual(tc.normalize_prescaler(" 6T "), "6T")
        self.assertEqual(tc.normalize_prescaler("1"), "1T")
        for bad in ("4T", "24T", "abc"):
            with self.assertRaises(tc.CalcError):
                tc.normalize_prescaler(bad)


class TestClassicBenchmarks(unittest.TestCase):
    def test_12mhz_12t_mode1_10ms(self):
        r = tc.calc_from_time(12e6, "12T", 1, 0.01)
        self.assertEqual(r["count"], 10000)
        self.assertEqual(r["init"], 55536)
        self.assertEqual(r["init_hex"], "D8F0")
        self.assertEqual(r["th_hex"], "D8")
        self.assertEqual(r["tl_hex"], "F0")
        self.assertAlmostEqual(r["machine_cycle"], 1e-6, places=15)
        self.assertAlmostEqual(r["actual_time"], 0.01, places=12)
        self.assertAlmostEqual(r["error_percent"], 0.0, places=9)

    def test_110592mhz_12t_mode1_10ms(self):
        r = tc.calc_from_time(11059200, "12T", 1, 0.01)
        self.assertEqual(r["count"], 9216)
        self.assertEqual(r["init"], 56320)
        self.assertEqual(r["init_hex"], "DC00")
        self.assertEqual(r["th_hex"], "DC")
        self.assertEqual(r["tl_hex"], "00")
        self.assertAlmostEqual(r["error_percent"], 0.0, places=9)

    def test_12mhz_12t_mode2_100us(self):
        r = tc.calc_from_time(12e6, "12T", 2, 100e-6)
        self.assertEqual(r["count"], 100)
        self.assertEqual(r["init"], 156)
        self.assertEqual(r["init_hex"], "9C")
        self.assertEqual(r["th_hex"], "9C")
        self.assertEqual(r["tl_hex"], "9C")

    def test_12mhz_12t_mode0_1ms(self):
        r = tc.calc_from_time(12e6, "12T", 0, 1e-3)
        self.assertEqual(r["count"], 1000)
        self.assertEqual(r["init"], 7192)
        self.assertEqual(r["init_hex"], "1C18")
        self.assertEqual(r["th"], 224)
        self.assertEqual(r["tl"], 24)
        self.assertEqual(r["th_hex"], "E0")
        self.assertEqual(r["tl_hex"], "18")

    def test_12mhz_1t_mode1_1ms(self):
        r = tc.calc_from_time(12e6, "1T", 1, 1e-3)
        self.assertEqual(r["count"], 12000)
        self.assertEqual(r["init"], 53536)
        self.assertEqual(r["init_hex"], "D120")

    def test_12mhz_6t_mode1_1ms(self):
        r = tc.calc_from_time(12e6, "6T", 1, 1e-3)
        self.assertEqual(r["count"], 2000)
        self.assertEqual(r["init"], 63536)
        self.assertEqual(r["init_hex"], "F830")

    def test_prescaler_ratio(self):
        """同一目标时间下，12T/6T/1T 的计数值应严格成比例。"""
        counts = [tc.calc_from_time(12e6, p, 1, 5e-3)["count"] for p in ("12T", "6T", "1T")]
        self.assertEqual(counts[1], counts[0] * 2)
        self.assertEqual(counts[2], counts[0] * 12)


class TestBoundaries(unittest.TestCase):
    def test_mode1_upper_boundary_ok(self):
        r = tc.calc_from_time(12e6, "12T", 1, 65536e-6)
        self.assertEqual(r["count"], 65536)
        self.assertEqual(r["init"], 0)
        self.assertEqual(r["init_hex"], "0000")

    def test_mode1_over_range(self):
        with self.assertRaises(tc.CalcError):
            tc.calc_from_time(12e6, "12T", 1, 70e-3)

    def test_mode0_upper_boundary_ok(self):
        r = tc.calc_from_time(12e6, "12T", 0, 8192e-6)
        self.assertEqual(r["init"], 0)

    def test_mode0_over_range(self):
        with self.assertRaises(tc.CalcError):
            tc.calc_from_time(12e6, "12T", 0, 9e-3)

    def test_mode2_upper_boundary_ok(self):
        r = tc.calc_from_time(12e6, "12T", 2, 256e-6)
        self.assertEqual(r["count"], 256)
        self.assertEqual(r["init"], 0)

    def test_mode2_over_range(self):
        with self.assertRaises(tc.CalcError):
            tc.calc_from_time(12e6, "12T", 2, 300e-6)

    def test_too_short(self):
        with self.assertRaises(tc.CalcError):
            tc.calc_from_time(12e6, "12T", 1, 0.1e-6)


class TestQuantization(unittest.TestCase):
    def test_quantization_error_reported(self):
        r = tc.calc_from_time(12e6, "12T", 1, 10.6e-6)
        self.assertEqual(r["count"], 11)
        self.assertAlmostEqual(r["actual_time"], 11e-6, places=15)
        self.assertAlmostEqual(r["error_percent"], (11 - 10.6) / 10.6 * 100, places=9)
        self.assertTrue(any("量化" in n for n in r["notes"]))
        self.assertTrue(any("误差" in n for n in r["notes"]))

    def test_exact_time_has_no_quantization_note(self):
        r = tc.calc_from_time(12e6, "12T", 1, 0.01)
        self.assertEqual(r["notes"], [])
        self.assertAlmostEqual(r["error_percent"], 0.0, places=9)

    def test_half_rounds_up(self):
        """11.5 个机器周期应进位到 12，而非 Python 内置银行家舍入的 12/10。"""
        r = tc.calc_from_time(12e6, "12T", 1, 11.5e-6)
        self.assertEqual(r["count"], 12)

    def test_mode0_and_mode2_notes(self):
        self.assertTrue(any("低 5 位" in n for n in tc.calc_from_time(12e6, "12T", 0, 1e-3)["notes"]))
        self.assertTrue(any("自动重装" in n for n in tc.calc_from_time(12e6, "12T", 2, 100e-6)["notes"]))


class TestReverse(unittest.TestCase):
    def test_reverse_mode1(self):
        r = tc.calc_from_init(12e6, "12T", 1, 55536)
        self.assertEqual(r["count"], 10000)
        self.assertAlmostEqual(r["actual_time"], 0.01, places=12)
        self.assertIsNone(r["error_percent"])
        self.assertIsNone(r["target_time"])

    def test_reverse_mode2(self):
        r = tc.calc_from_init(12e6, "12T", 2, 156)
        self.assertEqual(r["count"], 100)
        self.assertAlmostEqual(r["actual_time"], 100e-6, places=15)

    def test_reverse_out_of_range(self):
        with self.assertRaises(tc.CalcError):
            tc.calc_from_init(12e6, "12T", 1, 65536)
        with self.assertRaises(tc.CalcError):
            tc.calc_from_init(12e6, "12T", 1, -1)
        with self.assertRaises(tc.CalcError):
            tc.calc_from_init(12e6, "12T", 2, 256)

    def test_round_trip(self):
        """正向算出的初值，反向应还原出原始定时时间。"""
        for mode in (0, 1, 2):
            with self.subTest(mode=mode):
                forward = tc.calc_from_time(12e6, "12T", mode, 100e-6)
                backward = tc.calc_from_init(12e6, "12T", mode, forward["init"])
                self.assertAlmostEqual(backward["actual_time"], forward["actual_time"], places=15)


class TestFormatting(unittest.TestCase):
    def test_fmt_time(self):
        self.assertEqual(tc.fmt_time(1e-6), "1 us")
        self.assertEqual(tc.fmt_time(0.01), "10 ms")
        self.assertEqual(tc.fmt_time(1.0), "1 s")
        self.assertEqual(tc.fmt_time(500e-9), "500 ns")

    def test_fmt_freq(self):
        self.assertEqual(tc.fmt_freq(12e6), "12 MHz")
        self.assertEqual(tc.fmt_freq(11059200), "11.0592 MHz")
        self.assertEqual(tc.fmt_freq(8000), "8 kHz")


class TestCli(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tc.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_cli_text_output(self):
        code, out, _ = self._run(
            ["--fosc", "12MHz", "--mode", "1", "--prescaler", "12T", "--time", "10ms"]
        )
        self.assertEqual(code, 0)
        self.assertIn("0xD8F0", out)
        self.assertIn("TH0", out)
        self.assertIn("方式1", out)

    def test_cli_json_output(self):
        code, out, _ = self._run(
            ["--fosc", "12MHz", "--mode", "1", "--prescaler", "12T", "--time", "10ms", "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["init"], 55536)
        self.assertEqual(data["th_hex"], "D8")
        self.assertEqual(data["tl_hex"], "F0")
        self.assertEqual(data["prescaler"], "12T")

    def test_cli_reverse_mode(self):
        code, out, _ = self._run(
            ["--fosc", "12MHz", "--mode", "1", "--prescaler", "12T", "--init", "55536", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 10000)

    def test_cli_out_of_range_exit_code(self):
        code, _, err = self._run(
            ["--fosc", "12MHz", "--mode", "1", "--prescaler", "12T", "--time", "70ms"]
        )
        self.assertEqual(code, 2)
        self.assertIn("超出", err)

    def test_cli_rejects_mode3(self):
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                tc.main(["--fosc", "12MHz", "--mode", "3", "--prescaler", "12T", "--time", "1ms"])
        self.assertEqual(ctx.exception.code, 2)

    def test_cli_requires_time_or_init(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                tc.main(["--fosc", "12MHz", "--mode", "1", "--prescaler", "12T"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
