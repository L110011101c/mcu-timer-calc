# mcu-timer-calc

[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-35%20passed-brightgreen)](tests/test_timer_calc.py)

8051 系列单片机定时器初值计算器 —— 零第三方依赖、可全量单元测试的命令行工具，同时是一个 Agent Skill。

## 为什么需要它

51 单片机定时器初值要经过"机器周期换算 → 计数值取整 → 2ⁿ 补码运算"三步，其中任何一步算错，定时就会整倍偏差。三个最常见的坑：

- **分频搞错**：标准 8051 是 12T，但 STC 系列可通过 STC-ISP 改成 6T/1T，按 12T 算出的初值会整倍错误。
- **方式 0 的 13 位编码**：TH0 存高 8 位、TL0 只存低 5 位，这是最容易写错的地方。
- **量化误差被忽略**：目标时间不是机器周期的整数倍时，误差客观存在，需要知道有多大。

本工具把这三件事都做成确定性计算，并如实输出误差，而不是"凑一个看起来对的数"。

## 特性

- 支持定时器**方式 0（13 位）/ 方式 1（16 位）/ 方式 2（8 位自动重装）**
- 支持 **12T / 6T / 1T** 分频，**必须显式指定**，不设默认值
- 正向计算：给定晶振 + 目标时间 → 初值、TH0/TL0 字节、实际时间、误差
- 反向计算：给定初值 → 实际定时时间
- 越界保护：超出方式能力范围时明确报错（例如方式 1 在 12MHz/12T 下最大 65.536 ms）
- 误差 > 2% 时给出提示；方式 0 / 方式 2 附带编码注意事项
- 支持 `--json` 机器可读输出
- **仅依赖 Python 标准库**，无需 pip 安装任何东西

## 环境要求

Python 3.8 或更高版本。无第三方依赖。

## 快速开始

```bash
git clone https://github.com/<your-name>/mcu-timer-calc.git
cd mcu-timer-calc
python scripts/timer_calc.py --fosc 12MHz --mode 1 --prescaler 12T --time 10ms
```

## 使用示例

### 1. 12MHz 晶振，方式 1，定时 10ms

```bash
python scripts/timer_calc.py --fosc 12MHz --mode 1 --prescaler 12T --time 10ms
```

```
8051 定时器初值计算
==================================

[输入]
  晶振频率       : 12 MHz
  分频模式       : 12T
  机器周期       : 1 us
  定时器方式     : 方式1（16 位定时器）
  目标定时时间   : 10 ms

[结果]
  计数值         : 10000 个机器周期
  计数上限       : 65536
  初值(十进制)   : 55536
  初值(十六进制) : 0xD8F0
  TH0            : 0xD8  (216)
  TL0            : 0xF0  (240)

[定时]
  实际定时时间   : 10 ms
  误差           : +0.0000 %
```

对应初始化代码：

```c
TMOD &= 0xF0;  TMOD |= 0x01;   // 定时器0，方式1
TH0 = 0xD8;    TL0 = 0xF0;     // 初值 55536
ET0 = 1;       EA = 1;         // 开定时器0中断
TR0 = 1;                       // 启动
```

### 2. 经典验证：11.0592MHz 晶振定时 10ms

```bash
python scripts/timer_calc.py --fosc 11.0592MHz --mode 1 --prescaler 12T --time 10ms
```

初值为 **0xDC00** —— 这是 51 教材里最常见的验证值，可直接用来判断工具是否正确。

### 3. 方式 0（13 位）的编码

```bash
python scripts/timer_calc.py --fosc 12MHz --mode 0 --prescaler 12T --time 1ms
```

初值 7192 = `0x1C18`，拆成 **TH0 = 0xE0、TL0 = 0x18**（TL0 只用低 5 位）。

### 4. 由初值反算定时时间

```bash
python scripts/timer_calc.py --fosc 12MHz --mode 1 --prescaler 12T --init 55536
```

### 5. JSON 输出

```bash
python scripts/timer_calc.py --fosc 12MHz --mode 1 --prescaler 12T --time 10ms --json
```

```json
{
  "fosc": 12000000.0,
  "prescaler": "12T",
  "machine_cycle": 1e-06,
  "mode": 1,
  "mode_name": "方式1",
  "count": 10000,
  "init": 55536,
  "init_hex": "D8F0",
  "th_hex": "D8",
  "tl_hex": "F0",
  "actual_time": 0.01,
  "error_percent": 0.0
}
```

## 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--fosc` | 是 | 晶振频率。接受 `12MHz` / `12M` / `11059200` / `8kHz` |
| `--mode` | 是 | 定时器工作方式：`0` / `1` / `2`（方式 3 拆分模式不支持） |
| `--prescaler` | 是 | 分频模式：`12T` / `6T` / `1T` |
| `--time` | 二选一 | 目标定时时间，必须带单位：`ns` / `us` / `ms` / `s` |
| `--init` | 二选一 | 定时器初值，触发反向计算 |
| `--json` | 否 | JSON 输出 |

**退出码**：`0` 成功；`2` 输入非法或超出方式能力范围。

## 计算原理

```
机器周期 T   = 分频系数 / fosc
计数值   N   = round(目标定时时间 / T)
初值         = 2^位数 - N
实际定时时间 = N × T
误差         = (实际 - 目标) / 目标 × 100%
```

初值拆字节：

| 方式 | 位宽 | TH0 | TL0 |
|---|---|---|---|
| 方式 0 | 13 位 | `初值 >> 5` | `初值 & 0x1F`（仅低 5 位） |
| 方式 1 | 16 位 | `初值 >> 8` | `初值 & 0xFF` |
| 方式 2 | 8 位 | `初值` | `初值`（自动重装） |

## 已知局限

- **不假设分频**：12T/6T/1T 必须由使用者确认。STC89C52 出厂默认 12T，但可被改写。
- **不做晶振误差分析**：假定晶振频率精确。实际晶振有 ±20ppm 量级的频差，未计入。
- **方式 3 不支持**：方式 3 把定时器 0 拆成两个 8 位计数器，行为与其余方式差异较大。
- **只覆盖 8051 家族**：STM32 等 ARM Cortex-M 的 PSC/ARR 计算不在本工具范围内。
- **误差只能量化、无法消除**：目标时间不是机器周期整数倍时，工具会如实报出误差，
  不会为了"看起来整除"而调整初值。

## 测试

```bash
python -m unittest discover -s tests -v
```

35 项测试全部通过，覆盖：

- 经典手算基准（12MHz / 11.0592MHz / 6T / 1T）
- 三种方式的上边界（计满一整圈）与越界报错
- 量化误差上报与四舍五入行为（避免 Python 银行家舍入带来的意外）
- 正向 ↔ 反向计算的往返一致性
- CLI 的文本输出、JSON 输出、退出码

## 作为 Agent Skill 使用

本仓库同时是一个 Skill 包：`SKILL.md` 描述了技能触发条件与调用方式，`scripts/timer_calc.py`
是确定性计算脚本。将整个目录放入 Agent 的 skills 目录即可使用。

## 许可证

[MIT](LICENSE)
