"""终端 IO 加固层：替换 dse-core 内置的 ConsoleIO。

为什么需要它
------------
``dse_core/plugins/console_io.py`` 的交互分支只写了::

    try:
        return input(prompt).strip()
    except EOFError:
        return "quit"

``EOFError`` 只覆盖「流结束」，覆盖不了 **解码失败**。实测（Windows，
ACP=936 而 Python 3.13 默认 UTF-8 模式）：

* 输入 GBK 字节 ``\\xd6\\xd0``（中文 Windows 控制台最常见的一种）
  → ``UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd6``
* 输入任意无效 UTF-8 序列、或管道里混进二进制
  → 同样 ``UnicodeDecodeError``

这个异常会一路冒到 ``EngineService.start()`` 的兜底 except，结果是
**整局游戏被中止**：打印 traceback（玩家看不懂）+ 退出码 1。
玩家只是敲了个自己输入法打出来的字，游戏就没了。

它还覆盖不了另外两件事：

* ``KeyboardInterrupt`` —— Ctrl+C。上游完全没处理，在 ``input()`` 处
  按 Ctrl+C 会直接把 traceback 甩到玩家脸上。
* 终端失联（``OSError`` / ``IOError``，如 Windows 上 ConPTY 断开）。

这个子类把这几类异常都收成「返回一个可理解的字符串」，让引擎继续跑
正常的命令分发，而不是整个进程崩掉。

替换方式
--------
``dse.yml`` 里把 ``- id: io`` 改成::

    - id: io
      name: console_safe.SafeConsoleIO

``sdk._resolve_plugin`` 支持「模块.类名」形式的第三方插件，
``cls.name`` 仍是 ``"io"``，所以对引擎透明。
"""

from __future__ import annotations

import sys

from dse_core.plugins.console_io import ConsoleIO


class SafeConsoleIO(ConsoleIO):
    """把交互输入里的解码 / 中断 / IO 异常收成普通返回值的 ConsoleIO。"""

    # 继承 name = "io"，保证注册键不变、替换透明
    name = "io"
    inject = ()

    def __init__(self, ctx, config):
        super().__init__(ctx, config)
        # 实例级去重集合：放在 __init__ 里而不是类属性，
        # 否则多个引擎实例（测试里连续 create_engine）会共享同一份，
        # 前一个实例提示过的告警在后一个里就不再出现了。
        self._warned: set[str] = set()

    def ask(self, prompt: str = "> ") -> str:
        # 脚本模式（DSE_INPUT 预置队列）由父类处理，不受终端影响
        if self.scripted is not None:
            return super().ask(prompt)

        try:
            return input(prompt).strip()

        except EOFError:
            # Ctrl+D / Ctrl+Z / 管道读完：正常退出
            return "quit"

        except UnicodeDecodeError:
            # 输入字节不是合法 UTF-8 —— 中文 Windows 控制台发 GBK 时必然遇到。
            # 不能让整局游戏陪葬：提示一次，然后把这次输入当作空输入（刷新界面）。
            #
            # 注意：被吞掉的这一行对玩家是「我打的字没了」，所以必须说清楚，
            # 否则他会以为自己按错了、反复重打。
            self._warn_once(
                "  [输入编码] 这一行不是 UTF-8，已忽略。"
                "若终端是 GBK，请改用 UTF-8 编码的终端，或直接输入数字/英文命令。"
            )
            return ""

        except KeyboardInterrupt:
            # Ctrl+C：当成 say goodbye，而不是甩 traceback
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.print("  [中断] 已退出。")
            return "quit"

        except (OSError, IOError):
            # 终端失联（ConPTY 断开、SSH 掉线、stdin 被关闭）
            # 读不到就退出，别在这里死循环
            self._warn_once("  [输入流] 终端已断开，游戏退出。")
            return "quit"

    # ---------------------------------------------------------------- 内部

    def _warn_once(self, msg: str) -> None:
        """同类告警只提示一次 —— 玩家连敲几行坏编码时别刷屏。"""
        if msg in self._warned:
            return
        self._warned.add(msg)
        self.print(msg)
        sys.stdout.flush()
