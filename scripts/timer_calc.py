#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""8051 定时器初值计算器。

给定晶振频率、分频模式与定时器工作方式，计算 8051 系列单片机（含 STC89 等
12T/6T/1T 兼容型号）定时器的初值，并输出 TH0/TL0 字节、实际定时时间与量化误差。
同时支持由初值反算定时时间。

仅依赖 Python 标准库。

公式依据
--------
1. 机器周期 T_machine = 分频系数 / fosc
   - 标准 8051 为 12T（12 个振荡周期构成 1 个机器周期）
   - STC 系列可由 STC-ISP 配置为 6T 或 1T，因此分频必须显式指定
2. 所需计数值 N = 目标定时时间 / T_machine（四舍五入取整）
3. 初值 = 2^位数 - N
4. 实际定时时间 = N * T_machine；量化误差 = (实际 - 目标) / 目标 * 100%
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys

__version__ = "1.0.0"

# ---------------------------------------------------------------- 常量表

#: 定时器工作方式 -> (中文名, 计数位宽, 说明)
TIMER_MODES: dict[int, tuple[str, int, str]] = {
    0: ("方式0", 13, "13 位定时器，TL0 仅低 5 位有效"),
    1: ("方式1", 16, "16 位定时器"),
    2: ("方式2", 8, "8 位自动重装定时器"),
}

#: 分频模式 -> 每个机器周期包含的振荡周期数
PRESCALERS: dict[str, int] = {"12T": 12, "6T": 6, "1T": 1}

_FREQ_UNITS = {"": 1.0, "hz": 1.0, "k": 1e3, "khz": 1e3, "m": 1e6, "mhz": 1e6}
_TIME_UNITS = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1.0}

_NUM_RE = re.compile(r"^([0-9]*\.?[0-9]+)([a-z]*)$")

#: 误差超过该百分比时给出提示
_ERROR_WARN_PERCENT = 2.0


class CalcError(Exception):
    """输入非法，或参数超出所选定时器方式的能力范围。"""


# ---------------------------------------------------------------- 输入解析


def parse_frequency(text: str) -> float:
    """解析晶振频率，返回 Hz。接受 ``12MHz`` / ``12M`` / ``11059200`` / ``8kHz`` 等写法。"""
    s = str(text).strip().lower().replace(" ", "").replace("_", "")
    m = _NUM_RE.match(s)
    if not m:
        raise CalcError(f"无法解析晶振频率 {text!r}，示例：12MHz / 11.0592MHz / 11059200")
    value, unit = float(m.group(1)), m.group(2)
    if unit not in _FREQ_UNITS:
        raise CalcError(f"不支持的频率单位 {unit!r}，支持 Hz / kHz / MHz")
    fosc = value * _FREQ_UNITS[unit]
    if fosc <= 0:
        raise CalcError("晶振频率必须大于 0")
    return fosc


def parse_time(text: str) -> float:
    """解析定时时间，返回秒。接受 ``10ms`` / ``50us`` / ``1s`` / ``500ns`` 等写法。"""
    s = str(text).strip().lower().replace(" ", "").replace("_", "")
    m = _NUM_RE.match(s)
    if not m:
        raise CalcError(f"无法解析定时时间 {text!r}，示例：10ms / 50us / 1s")
    value, unit = float(m.group(1)), m.group(2)
    if unit == "":
        raise CalcError("定时时间必须带单位，支持 ns / us / ms / s")
    if unit not in _TIME_UNITS:
        raise CalcError(f"不支持的时间单位 {unit!r}，支持 ns / us / ms / s")
    t = value * _TIME_UNITS[unit]
    if t <= 0:
        raise CalcError("定时时间必须大于 0")
    return t


def normalize_prescaler(text: str) -> str:
    """归一化分频写法，``12`` / ``12t`` -> ``12T``。"""
    s = str(text).strip().upper()
    if not s.endswith("T"):
        s += "T"
    if s not in PRESCALERS:
        raise CalcError(f"不支持的分频模式 {text!r}，支持 12T / 6T / 1T")
    return s


# ---------------------------------------------------------------- 核心计算


def machine_cycle(fosc: float, prescaler: str) -> float:
    """返回一个机器周期的时长（秒）。"""
    return PRESCALERS[prescaler] / fosc


def encode_init(mode: int, init: int) -> tuple[int, int]:
    """把初值拆成 (TH0, TL0)。

    方式0 为 13 位：TH0 存高 8 位，TL0 只存低 5 位。
    方式1 为 16 位：TH0 高 8 位，TL0 低 8 位。
    方式2 为 8 位自动重装：TH0 与 TL0 写入同一初值。
    """
    if mode == 0:
        return (init >> 5) & 0xFF, init & 0x1F
    if mode == 1:
        return (init >> 8) & 0xFF, init & 0xFF
    return init & 0xFF, init & 0xFF


def _base_result(fosc: float, prescaler: str, mode: int) -> dict:
    name, bits, desc = TIMER_MODES[mode]
    cyc = machine_cycle(fosc, prescaler)
    return {
        "fosc": fosc,
        "prescaler": prescaler,
        "machine_cycle": cyc,
        "mode": mode,
        "mode_name": name,
        "mode_desc": desc,
        "bits": bits,
        "count_max": 1 << bits,
        "notes": [],
    }


def _finalize(result: dict, init: int, count: int, th: int, tl: int) -> dict:
    result.update(
        {
            "count": count,
            "init": init,
            "init_hex": f"{init:0{(result['bits'] + 3) // 4}X}",
            "th": th,
            "th_hex": f"{th:02X}",
            "tl": tl,
            "tl_hex": f"{tl:02X}",
        }
    )
    return result


def calc_from_time(fosc: float, prescaler: str, mode: int, target_time: float) -> dict:
    """由目标定时时间计算定时器初值。"""
    result = _base_result(fosc, prescaler, mode)
    cyc = result["machine_cycle"]
    full = result["count_max"]

    raw = target_time / cyc
    # 显式采用四舍五入（Python 内置 round 为银行家舍入，会造成反直觉的结果）
    count = int(math.floor(raw + 0.5))

    if count < 1:
        raise CalcError(
            f"目标定时时间过短：{fmt_time(target_time)} 不足 1 个机器周期（{fmt_time(cyc)}）"
        )
    if count > full:
        raise CalcError(
            f"目标定时时间超出{result['mode_name']}能力范围："
            f"{result['mode_name']}最多计数 {full} 个机器周期，"
            f"当前分频下最大定时 {fmt_time(full * cyc)}"
        )

    init = full - count
    th, tl = encode_init(mode, init)
    actual = count * cyc
    error_percent = (actual - target_time) / target_time * 100.0

    result = _finalize(result, init, count, th, tl)
    result["target_time"] = target_time
    result["actual_time"] = actual
    result["error_percent"] = error_percent
    result["notes"] = _build_notes(mode, raw, count, error_percent)
    return result


def calc_from_init(fosc: float, prescaler: str, mode: int, init: int) -> dict:
    """由定时器初值反算定时时间。"""
    result = _base_result(fosc, prescaler, mode)
    full = result["count_max"]

    if not 0 <= init < full:
        raise CalcError(f"初值超出{result['mode_name']}范围：应为 0 ~ {full - 1}，当前 {init}")

    count = full - init
    th, tl = encode_init(mode, init)

    result = _finalize(result, init, count, th, tl)
    result["target_time"] = None
    result["actual_time"] = count * result["machine_cycle"]
    result["error_percent"] = None
    result["notes"] = _build_notes(mode, None, count, None)
    return result


def _build_notes(mode: int, raw, count: int, error_percent) -> list[str]:
    notes: list[str] = []
    if mode == 0:
        notes.append("方式0 为 13 位计数器，TL0 仅低 5 位有效，写初值时勿写入高 3 位")
    if mode == 2:
        notes.append("方式2 为 8 位自动重装，TH0 与 TL0 需写入同一初值")
    if raw is not None and not math.isclose(raw, count, rel_tol=0.0, abs_tol=1e-9):
        notes.append("目标定时时间不是机器周期的整数倍，误差来自不可避免的量化取整")
    if error_percent is not None and abs(error_percent) > _ERROR_WARN_PERCENT:
        notes.append(
            f"误差已达 {error_percent:+.4f}%，精确计时场景请改用更合适的晶振或缩短定时时间"
        )
    return notes


# ---------------------------------------------------------------- 输出格式化


def fmt_freq(hz: float) -> str:
    if hz >= 1e6:
        return f"{hz / 1e6:.6g} MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:.6g} kHz"
    return f"{hz:.6g} Hz"


def fmt_time(seconds: float) -> str:
    a = abs(seconds)
    if a < 1e-6:
        return f"{seconds * 1e9:.6g} ns"
    if a < 1e-3:
        return f"{seconds * 1e6:.6g} us"
    if a < 1.0:
        return f"{seconds * 1e3:.6g} ms"
    return f"{seconds:.6g} s"


def _disp_width(text: str) -> int:
    """粗略计算终端显示宽度：CJK 及全角标点按 2 列计。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


def _row(label: str, value: str, width: int = 15) -> str:
    return f"  {label}{' ' * max(1, width - _disp_width(label))}: {value}"


def render(result: dict) -> str:
    lines = [
        "8051 定时器初值计算",
        "=" * 34,
        "",
        "[输入]",
        _row("晶振频率", fmt_freq(result["fosc"])),
        _row("分频模式", result["prescaler"]),
        _row("机器周期", fmt_time(result["machine_cycle"])),
        _row("定时器方式", f"{result['mode_name']}（{result['mode_desc']}）"),
    ]
    if result["target_time"] is not None:
        lines.append(_row("目标定时时间", fmt_time(result["target_time"])))

    lines += [
        "",
        "[结果]",
        _row("计数值", f"{result['count']} 个机器周期"),
        _row("计数上限", str(result["count_max"])),
        _row("初值(十进制)", str(result["init"])),
        _row("初值(十六进制)", f"0x{result['init_hex']}"),
        _row("TH0", f"0x{result['th_hex']}  ({result['th']})"),
        _row("TL0", f"0x{result['tl_hex']}  ({result['tl']})"),
        "",
        "[定时]",
        _row("实际定时时间", fmt_time(result["actual_time"])),
    ]
    if result["error_percent"] is None:
        lines.append(_row("误差", "不适用（反向计算）"))
    else:
        lines.append(_row("误差", f"{result['error_percent']:+.4f} %"))

    if result["notes"]:
        lines += ["", "[提示]"]
        lines += [f"  - {n}" for n in result["notes"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="timer_calc",
        description="8051 定时器初值计算器：由晶振/分频/方式计算初值，或由初值反算定时时间。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  timer_calc.py --fosc 12MHz --mode 1 --prescaler 12T --time 10ms\n"
            "  timer_calc.py --fosc 11.0592MHz --mode 1 --prescaler 12T --time 10ms --json\n"
            "  timer_calc.py --fosc 12MHz --mode 2 --prescaler 12T --init 156\n"
        ),
    )
    parser.add_argument(
        "--fosc", required=True, help="晶振频率，如 12MHz / 11.0592MHz / 11059200"
    )
    parser.add_argument(
        "--mode", required=True, type=int, choices=sorted(TIMER_MODES), help="定时器工作方式：0 / 1 / 2"
    )
    parser.add_argument("--prescaler", required=True, help="分频模式：12T / 6T / 1T")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--time", help="目标定时时间，如 10ms / 50us / 1s")
    group.add_argument("--init", type=int, help="定时器初值（反向计算定时时间）")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        fosc = parse_frequency(args.fosc)
        prescaler = normalize_prescaler(args.prescaler)
        if args.time is not None:
            result = calc_from_time(fosc, prescaler, args.mode, parse_time(args.time))
        else:
            result = calc_from_init(fosc, prescaler, args.mode, args.init)
    except CalcError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
