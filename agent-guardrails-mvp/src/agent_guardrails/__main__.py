"""``python -m agent_guardrails`` 的入口：与安装后的 ``guardrails`` 命令完全等价。

存在的理由是"不安装也能跑通"—— 读代码的人 clone 下来就能 ``python -m`` 试一把，
不需要先配虚拟环境、先装依赖。
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
