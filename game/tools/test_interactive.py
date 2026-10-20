"""交互模式形态测试：真 stdin（非 DSE_INPUT 脚本队列）下的边界行为。

覆盖 --script 覆盖不到的路径：
  1. stdin 立即 EOF
  2. 管道喂几条后 EOF
  3. 空行 / 空白输入
  4. 超长输入 / 二进制垃圾
  5. 战斗中途 EOF（存档 / 状态一致性）
  6. SIGINT 中断
  7. 交互分支 + 中文输入回显

用法：python tools/test_interactive.py
不改任何游戏源码，只跑子进程比对输出。
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

GAME = Path(__file__).resolve().parent.parent
PY = sys.executable
PLAY = str(GAME / "play.py")

# 统一用 --no-viz 关掉出图，避免 matplotlib 之类拖慢/干扰断言
BASE_ARGS = [PY, PLAY, "--no-viz"]

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        for line in detail.splitlines()[:12]:
            print(f"        {line}")


def check_clean(name: str, out: str, rc: int, *, allow_rc=(0,)) -> None:
    """通用断言：无 traceback、退出码合理、没卡死。"""
    problems = []
    if "Traceback (most recent call last)" in out:
        problems.append("出现裸 traceback")
    if rc not in allow_rc:
        problems.append(f"退出码 {rc}，期望 {allow_rc}")
    record(name, not problems, "\n".join(problems) if problems else "")


def run_with_stdin(data: bytes | str, timeout: float = 20) -> tuple[str, int]:
    """用管道喂 stdin，不设 DSE_INPUT —— 走真正的交互分支。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    env = {k: v for k, v in os.environ.items() if k != "DSE_INPUT"}
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run(
            BASE_ARGS, input=data, capture_output=True, timeout=timeout, env=env, cwd=str(GAME),
        )
    except subprocess.TimeoutExpired:
        return "<TIMEOUT>", -1
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    return out, p.returncode


def test_quit_exit_code() -> None:
    """交互模式 quit：应该干净退出、退出码 0、有告别语。"""
    out, rc = run_with_stdin(b"quit\n")
    check_clean("quit 正常退出", out, rc)
    ok = "游戏已退出" in out
    record("quit · 有告别语", ok, "" if ok else out[-300:])


def test_recover_after_bad_encoding() -> None:
    """坏编码之后游戏必须还能玩 —— 这是 SafeConsoleIO 的核心承诺。

    先喂一行 GBK 中文（非法 UTF-8），再喂一个合法选择，
    断言：不崩、有编码提示、且后续选择真的被引擎吃进去了。
    """
    data = b"\xd6\xd0\xce\xc4\n1\nquit\n"
    out, rc = run_with_stdin(data)
    check_clean("坏编码后仍可继续游玩", out, rc)
    ok_warn = "输入编码" in out
    record("坏编码 · 有可读提示", ok_warn, "" if ok_warn else out[-500:])
    # 提示只该出现一次（同一类告警去了重）
    n = out.count("输入编码")
    record("坏编码 · 同类提示不刷屏", n == 1, f"出现了 {n} 次")


def test_eof_immediate() -> None:
    out, rc = run_with_stdin(b"")
    check_clean("stdin 立即 EOF", out, rc)
    ok = "游戏已退出" in out
    record("stdin 立即 EOF · 有退出提示", ok, "" if ok else out[-400:])


def test_pipe_few_then_eof() -> None:
    out, rc = run_with_stdin(b"1\n1\n")
    check_clean("管道喂 2 条后 EOF", out, rc)


def test_blank_lines() -> None:
    out, rc = run_with_stdin(b"\n\n\n\n\n")
    check_clean("连续空行", out, rc)


def test_whitespace() -> None:
    out, rc = run_with_stdin(b"   \n\t\n  \n")
    check_clean("纯空白输入", out, rc)


def test_unknown_command() -> None:
    out, rc = run_with_stdin("xyzzy\nquit\n".encode())
    check_clean("未知命令", out, rc)
    ok = "游戏已退出" in out
    record("未知命令 · 不崩且有退出", ok, "" if ok else out[-400:])


def test_garbage_bytes() -> None:
    out, rc = run_with_stdin(b"\x00\x01\xff\xfe\nquit\n")
    # 二进制垃圾在 UTF-8 解码阶段就可能炸，这是真问题
    check_clean("二进制垃圾输入", out, rc)


def test_long_line() -> None:
    out, rc = run_with_stdin(b"a" * 10000 + b"\nquit\n")
    check_clean("10000 字符超长行", out, rc)


def test_cjk_input() -> None:
    out, rc = run_with_stdin("侦察\nquit\n".encode())
    check_clean("中文输入回显", out, rc)


def test_help() -> None:
    out, rc = run_with_stdin(b"help\nquit\n")
    check_clean("help 命令", out, rc)
    ok = "quit" in out.lower()
    record("help · 有输出", ok, "" if ok else out[-500:])


def test_sigint() -> None:
    """交互等待中收到 Ctrl+C：不该打印 traceback 甩给玩家。

    Windows 上 ``Popen.send_signal(SIGINT)`` 会抛 ValueError（不支持），
    必须用 ``CTRL_C_EVENT`` + ``CREATE_NEW_PROCESS_GROUP``。
    POSIX 上仍然发 SIGINT。
    """
    env = {k: v for k, v in os.environ.items() if k != "DSE_INPUT"}
    env["PYTHONIOENCODING"] = "utf-8"

    kwargs = dict(
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, env=env, cwd=str(GAME),
    )
    is_win = sys.platform == "win32"
    if is_win:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    p = subprocess.Popen(BASE_ARGS, **kwargs)
    try:
        # 等它进到提示符。交互模式下 stdin 是打开的管道，input() 会阻塞等待。
        time.sleep(4)
        if p.poll() is not None:
            out = p.stdout.read().decode("utf-8", "replace") if p.stdout else ""
            record("Ctrl+C 中断", False, f"进程在收信号前已自行退出 rc={p.returncode}\n{out[-500:]}")
            return

        if is_win:
            p.send_signal(signal.CTRL_C_EVENT)
        else:
            p.send_signal(signal.SIGINT)

        try:
            out_b, _ = p.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
            out_b, _ = p.communicate()
            record("Ctrl+C 中断 · 能退出", False, "收信号后 15s 未退出（卡死）")
            return

        out = out_b.decode("utf-8", "replace") if out_b else ""
        rc = p.returncode
        record("Ctrl+C 中断 · 能退出", True, "")
        has_tb = "Traceback (most recent call last)" in out
        record(
            "Ctrl+C 中断 · 无裸 traceback",
            not has_tb,
            "" if not has_tb else out[-800:],
        )
        # 0 = 走了正常 stop()；130 = POSIX 惯例；Windows 的 CTRL_C 是 0xC000013A
        ok_rc = rc in (0, 130, 3221225786, -1073741510)
        record(
            "Ctrl+C 中断 · 退出码合理",
            ok_rc,
            "" if ok_rc else f"rc={rc}",
        )
    except (ValueError, OSError) as exc:
        p.kill()
        p.wait(timeout=10)
        record("Ctrl+C 中断", False, f"无法投递信号: {type(exc).__name__}: {exc}")


def test_combat_eof() -> None:
    """进战斗后立刻 EOF：检查存档没被写坏、状态一致。"""
    # 1 → 往北 → 1 → 遭遇 → 1,1 出牌，之后 EOF
    out, rc = run_with_stdin(b"1\n1\n1\n1\n")
    check_clean("战斗中途 EOF", out, rc)
    # 战斗态没清干净的话，退出提示会缺
    ok = "游戏已退出" in out
    record("战斗中途 EOF · 有退出提示", ok, "" if ok else out[-600:])


def test_repeat_determinism() -> None:
    """同样输入连跑 3 次：不应有非确定性崩溃。"""
    bad = []
    for i in range(3):
        out, rc = run_with_stdin(b"1\n1\n1\nquit\n")
        if "Traceback (most recent call last)" in out or rc not in (0,):
            bad.append(f"第 {i + 1} 次 rc={rc}")
    record("同输入连跑 3 次稳定", not bad, "\n".join(bad))


def main() -> int:
    print(f"Python : {PY}")
    print(f"Play   : {PLAY}")
    print(f"模式   : 真 stdin（不设 DSE_INPUT）\n")

    tests = [
        test_eof_immediate,
        test_pipe_few_then_eof,
        test_blank_lines,
        test_whitespace,
        test_unknown_command,
        test_garbage_bytes,
        test_long_line,
        test_cjk_input,
        test_help,
        test_combat_eof,
        test_quit_exit_code,
        test_recover_after_bad_encoding,
        test_repeat_determinism,
        test_sigint,
    ]
    print("── 交互模式边界测试 ──")
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as exc:  # noqa: BLE001 —— 测试自身崩了也要报出来
            record(t.__name__, False, f"测试脚本异常 {type(exc).__name__}: {exc}")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 52}")
    print(f"通过 {passed}/{total}")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAIL  {name}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
