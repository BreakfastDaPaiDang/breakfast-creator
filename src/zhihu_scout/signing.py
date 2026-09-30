from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .access import ZhihuError


def headers(url, cookie):
    node = shutil.which("node")
    if not node:
        raise ZhihuError("missing_dependency", "回答与问题接口签名需要Node.js；不需要浏览器。")
    pairs = dict(p.strip().split("=", 1) for p in cookie.split(";") if "=" in p)
    try:
        result = subprocess.run([node, str(Path(__file__).parent / "vendor/sign-stdin.mjs")],
                                input=json.dumps({"url": url, "dc0": pairs["d_c0"]}),
                                capture_output=True, text=True, encoding="utf-8", timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        value = json.loads(result.stdout) if result.returncode == 0 else {}
        if (value.get("x-zse-93") != "101_3_3.0"
                or not re.fullmatch(r"2\.0_[A-Za-z0-9/+=]{64}", value.get("x-zse-96", ""))):
            raise ValueError
        return value
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        raise ZhihuError("signing_error", "本地签名计算失败；未发送请求，未输出凭据或子进程诊断原文。") from None
