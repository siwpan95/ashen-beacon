"""真实键盘事件测试：把按键真正送进游戏进程的控制台输入缓冲区。

它补的是 ``--script`` 和 stdin 管道都覆盖不到的一层
（``tools/test_interactive.py`` 走管道，绕过了终端把按键变成一行输入的过程）。

两条通道
--------
1. **WriteConsoleInput（默认）**
   直接往目标进程控制台的输入缓冲区写 ``INPUT_RECORD``，不需要窗口焦点。

2. **pynput（``--via-pynput``）**
   发真实 ``SendInput``，最接近玩家手打，但必须有窗口焦点。

⚠️ 关键前提：目标进程的 stdin 必须**真的挂在控制台上**
------------------------------------------------------------------
``WriteConsoleInput`` 写的是「某个控制台」的输入缓冲区。如果目标进程的
stdin 其实是一根管道（被重定向 / 继承自 ConPTY 父进程），那它根本不读
那个缓冲区 —— 写入会返回成功（``ok=1, n=4``），但游戏毫无反应。

本机实测的判定依据（对子进程调 ``GetConsoleMode(GetStdHandle(-16))``）：

* stdin 是真控制台 → ``GetConsoleMode`` 成功，``isatty()`` 为 True
* stdin 是管道     → ``GetConsoleMode`` 返回 0（失败），``isatty()`` 为 False

所以脚本会**先探测、再决定跑不跑**，探测不过就明确报「环境不满足」并
以退出码 2 结束 —— 绝不把「写成功但没生效」当成通过。

用法
----
    python tools/test_keyboard.py              # WriteConsoleInput
    python tools/test_keyboard.py --via-pynput # 真实 SendInput（需焦点）

退出码：0 通过 / 1 失败 / 2 环境不满足（跳过，不算失败）
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

GAME = Path(__file__).resolve().parent.parent
PLAY = str(GAME / "play.py")

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok and detail:
        for line in detail.splitlines()[:8]:
            print(f"        {line}")


# ─────────────────────────────────────────── 结构体与投递


class _KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", wintypes.BOOL),
        ("wRepeatCount", wintypes.WORD),
        ("wVirtualKeyCode", wintypes.WORD),
        ("wVirtualScanCode", wintypes.WORD),
        ("uChar", wintypes.WCHAR),
        ("dwControlKeyState", wintypes.DWORD),
    ]


class _UNION(ctypes.Union):
    _fields_ = [("KeyEvent", _KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _UNION)]


def _build_records(text: str) -> tuple[object, int]:
    """把字符串转成 INPUT_RECORD 数组。\\r 当回车（VK_RETURN=0x0D）。

    每个字符发「按下 + 抬起」两条：只发按下时，行缓冲不一定认。
    """
    chars = ["\r" if ch == "\n" else ch for ch in text]
    recs = (INPUT_RECORD * (len(chars) * 2))()
    i = 0
    for ch in chars:
        vk = 0x0D if ch == "\r" else (ord(ch.upper()) if ch.isalpha() else 0)
        for down in (True, False):
            recs[i].EventType = 0x0001  # KEY_EVENT
            ke = recs[i].Event.KeyEvent
            ke.bKeyDown = down
            ke.wRepeatCount = 1
            ke.wVirtualKeyCode = vk
            ke.wVirtualScanCode = 0
            ke.uChar = ch if down else "\x00"
            ke.dwControlKeyState = 0
            i += 1
    return recs, i


def stdin_is_console(pid: int) -> tuple[bool, str]:
    """探测目标进程的 stdin 是不是真挂在控制台上。

    做法：AttachConsole 过去，取 ``GetStdHandle(-10)``（-10 = STD_INPUT_HANDLE），
    再试 ``GetConsoleMode``。管道句柄会失败。
    """
    k32 = ctypes.windll.kernel32
    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        return False, f"AttachConsole 失败 err={k32.GetLastError()}"
    try:
        h = k32.GetStdHandle(-10)
        if not h or h == -1:
            return False, "GetStdHandle(STD_INPUT) 无效"
        mode = wintypes.DWORD()
        ok = k32.GetConsoleMode(h, ctypes.byref(mode))
        if not ok:
            return False, (
                "目标进程 stdin 不是控制台（GetConsoleMode 失败）——"
                "它被重定向成管道了，WriteConsoleInput 写进去不会被读"
            )
        return True, f"控制台模式 0x{mode.value:X}"
    finally:
        k32.FreeConsole()


def send_via_console(pid: int, text: str) -> tuple[bool, str]:
    """把 text 写进 pid 对应进程的控制台输入缓冲区。"""
    k32 = ctypes.windll.kernel32
    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        return False, f"AttachConsole 失败 err={k32.GetLastError()}"
    h = k32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
    if not h or h == -1:
        k32.FreeConsole()
        return False, f"CreateFileW(CONIN$) 失败 err={k32.GetLastError()}"
    try:
        recs, n = _build_records(text)
        written = wintypes.DWORD()
        ok = k32.WriteConsoleInputW(h, recs, n, ctypes.byref(written))
        if not ok:
            return False, f"WriteConsoleInputW 失败 err={k32.GetLastError()}"
        if written.value != n:
            return False, f"只写入 {written.value}/{n} 条"
        return True, f"写入 {n} 条"
    finally:
        k32.CloseHandle(h)
        k32.FreeConsole()


# ─────────────────────────────────────────── 通道 1


def run_via_console() -> int:
    proc = subprocess.Popen(
        [sys.executable, PLAY, "--no-viz"],
        cwd=str(GAME),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        close_fds=True,
    )
    try:
        time.sleep(4)
        if proc.poll() is not None:
            record("游戏进程启动", False, f"提前退出 rc={proc.returncode}")
            return 1
        record("游戏进程启动", True)

        # 先探测 stdin 归属 —— 这是本通道能否成立的前提
        ok_console, why = stdin_is_console(proc.pid)
        if not ok_console:
            print(f"  [skip] {why}")
            print("        这是运行环境限制（脚本自身跑在 ConPTY 里，子进程继承了管道），")
            print("        不是游戏缺陷。在真实终端里手动跑同一个脚本即可。")
            return 2
        record("目标 stdin 是真控制台", True, why)

        ok, msg = send_via_console(proc.pid, "1\r")
        record("投递 '1'+回车", ok, msg)
        if not ok:
            return 1

        time.sleep(2)
        # 按键是否生效：进程仍在跑说明它吃掉了这行、进到了下一个场景；
        # 接着投 quit 应该能让它干净退出 —— 这才是「按键真的进游戏了」的证据。
        if proc.poll() is not None:
            record("投递后游戏仍在运行", False, f"已退出 rc={proc.returncode}")
            return 1
        record("投递后游戏仍在运行", True)

        ok2, msg2 = send_via_console(proc.pid, "quit\r")
        record("投递 quit", ok2, msg2)

        try:
            rc = proc.wait(timeout=15)
            record("quit 生效后自行退出", rc == 0, f"rc={rc}")
        except subprocess.TimeoutExpired:
            record("quit 生效后自行退出", False, "15s 未退出 —— 按键可能没被读到")
            return 1
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


# ─────────────────────────────────────────── 通道 2


def run_via_pynput() -> int:
    try:
        from pynput.keyboard import Controller, Key
    except Exception as exc:  # noqa: BLE001
        print(f"  [skip] pynput 不可用：{exc}（pip install pynput）")
        return 2

    u32 = ctypes.windll.user32

    def find_console(pid: int, timeout: float = 10.0):
        hit: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(h, _lp):
            wp = wintypes.DWORD()
            u32.GetWindowThreadProcessId(h, ctypes.byref(wp))
            if wp.value == pid and u32.IsWindowVisible(h):
                hit.append(h)
                return False
            return True

        end = time.time() + timeout
        while time.time() < end:
            hit.clear()
            u32.EnumWindows(cb, 0)
            if hit:
                return hit[0]
            time.sleep(0.2)
        return None

    proc = subprocess.Popen(
        [sys.executable, PLAY, "--no-viz"],
        cwd=str(GAME),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    try:
        hwnd = find_console(proc.pid)
        if not hwnd:
            print("  [skip] 找不到游戏窗口")
            return 2
        record("找到游戏窗口", True)

        # 前台锁：SetForegroundWindow 可能返回 1 却无效，必须复核实际前台
        u32.ShowWindow(hwnd, 9)
        u32.SetForegroundWindow(hwnd)
        time.sleep(0.4)
        if u32.GetForegroundWindow() != hwnd:
            u32.keybd_event(0x12, 0, 0, 0)      # Alt 按下
            u32.keybd_event(0x12, 0, 0x0002, 0)  # Alt 抬起
            time.sleep(0.2)
            u32.SetForegroundWindow(hwnd)
            time.sleep(0.4)
        if u32.GetForegroundWindow() != hwnd:
            print("  [skip] 拿不到前台焦点（被其他程序反复抢占）——环境限制，非游戏缺陷")
            return 2
        record("窗口获得焦点", True)

        kb = Controller()
        time.sleep(1.5)
        for ch in "1":
            kb.press(ch)
            kb.release(ch)
            time.sleep(0.08)
        kb.press(Key.enter)
        kb.release(Key.enter)
        time.sleep(1.5)
        for ch in "quit":
            kb.press(ch)
            kb.release(ch)
            time.sleep(0.08)
        kb.press(Key.enter)
        kb.release(Key.enter)

        try:
            rc = proc.wait(timeout=15)
            record("SendInput 后自行退出", rc == 0, f"rc={rc}")
        except subprocess.TimeoutExpired:
            record("SendInput 后自行退出", False, "15s 未退出")
            return 1
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


# ─────────────────────────────────────────── 入口


def main() -> int:
    ap = argparse.ArgumentParser(description="真实键盘事件测试")
    ap.add_argument("--via-pynput", action="store_true",
                    help="改用 pynput 发真实 SendInput（需窗口焦点）")
    args = ap.parse_args()

    print("── 真实键盘事件测试 ──")
    if sys.platform != "win32":
        print(f"  [skip] 当前平台 {sys.platform}：本脚本只实现了 Windows 路径。")
        return 2

    if args.via_pynput:
        print("  通道：pynput SendInput（需要焦点）")
        code = run_via_pynput()
    else:
        print("  通道：WriteConsoleInput（无需焦点）")
        code = run_via_console()

    if code == 2:
        print("\n结果：环境不满足，已跳过（不是失败）。")
        return 2

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 52}\n通过 {passed}/{len(results)}")
    return 0 if passed == len(results) and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
