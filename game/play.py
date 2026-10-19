"""《灰烬信标》启动器。

    python play.py                # 正常玩
    python play.py --script "1,1,f"   # 非交互跑一串输入（自测 / CI 用）
    python play.py --check        # 只做内容体检（悬空跳转、可达结局）

玩法：
    数字 / 编号  选择
    stats        看角色（allin-engine 的 TRPG 角色卡）
    inv          看背包
    use <物品>   用东西，例如 use medkit、use keycard
    answer <数>  回答谜题，例如 answer 4173
    check <技能> <DC>  临时掷一次骰，例如 check 侦察 11
    save / load <槽> / slots  存档
    rollback [n] 回退 n 个选择点
    quit         退出
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# dse.yml 里写的是 bridge.AllinBridge，必须让工程目录可 import
sys.path.insert(0, str(ROOT))

if True:  # allin_engine 顶层会往 stderr 打 tkinter 缺失提示，别脏了开局画面
    import contextlib
    import io as _io

    with contextlib.redirect_stderr(_io.StringIO()):
        import bridge  # noqa: F401 —— 预热导入，顺带验证依赖完整


def main() -> int:
    ap = argparse.ArgumentParser(description="《灰烬信标》—— dse-core × allin-engine")
    ap.add_argument("--script", help="逗号分隔的输入序列，非交互跑完")
    ap.add_argument("--check", action="store_true", help="只体检内容，不进入游戏")
    ap.add_argument("--entry", help="覆盖起始场景")
    args = ap.parse_args()

    from dse_core import create_engine, inspect_project

    if args.check:
        return _report(inspect_project(ROOT))

    if args.script:
        # ConsoleIO 按 [,或换行] 切分、只 strip 首尾 —— 别去中间空格，
        # 否则 "answer 4173" 会被压成 "answer4173"。
        os.environ["DSE_INPUT"] = args.script

    ctx = create_engine(ROOT, entry=args.entry)
    # 抓引用必须在 engine.start() 之前：stop() 会 dispose() 并清空 services
    state = ctx.services["state"]
    engine = ctx.services["engine"]
    engine.start()

    print(f"\n[note] 终局场景 {engine.current} | 走过的地方 {state.get('allin.locales')}")
    return int(engine.exit_code or 0)


def _report(rep: dict) -> int:
    print(f"工程根目录   : {rep['root']}")
    print(f"内容包       : {len(rep['packFiles'])} 个")
    print(f"场景总数     : {rep['scenes']}")
    print(f"结局数       : {rep['endings']}")
    print(f"结局可达     : {rep['endingsReachable']}")
    print(f"不可达场景   : {rep['unreachable'] or '（无）'}")
    bad = rep["dangling"]
    if bad:
        print("悬空跳转     :")
        for line in bad:
            print("   ", line)
        return 1
    print("悬空跳转     :（无）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
