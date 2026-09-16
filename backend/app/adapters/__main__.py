"""`python -m app.adapters` 入口。

README 与 `B34` CI 用的都是这条命令，所以必须存在（否则报
`No module named app.adapters.__main__`）。

用法：
    python -m app.adapters --check
"""

import sys

from app.adapters.loader import _main

if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
