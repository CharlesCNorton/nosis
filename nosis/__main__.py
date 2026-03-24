"""Allow ``python -m nosis`` with deterministic hash seed."""
import os
import subprocess
import sys

if os.environ.get("PYTHONHASHSEED") is None:
    os.environ["PYTHONHASHSEED"] = "0"
    raise SystemExit(subprocess.call([sys.executable, "-m", "nosis"] + sys.argv[1:],
                                     env={**os.environ, "PYTHONHASHSEED": "0"}))

from nosis.cli import main  # noqa: E402
raise SystemExit(main())
