#!/usr/bin/env python3
"""reel 旧版命令行（Mac/CPU）。现为 reelcore 的薄壳，参数兼容旧版，另需 --src。
新工作流请用 reel.py（JSON plan、bbox 推近、5090 远程执行）。"""
import sys
from _legacy_cli import main

if __name__ == "__main__":
    sys.exit(main("cpu", __doc__))
