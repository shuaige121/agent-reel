#!/usr/bin/env python3
"""reel 旧版命令行（在 5090 本机运行：NVENC + libass）。现为 reelcore 的薄壳。
在 Mac 上想用 5090，直接 reel.py render --where 5090，不需要登录 5090。"""
import sys
from _legacy_cli import main

if __name__ == "__main__":
    sys.exit(main("nvenc", __doc__))
