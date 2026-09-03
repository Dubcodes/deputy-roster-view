from __future__ import annotations

"""Single release identity used by web requests and background jobs."""

import os


APP_VERSION = "0.5.15"
APP_BUILD = os.getenv("GIT_SHA", "").strip()[:12] or APP_VERSION
