#!/usr/bin/env python3
"""
Batch SEO Audit Runner
Edit the AUDITS list below and run: python3 seo-audit-batch.py

Configuration is loaded from .env file. See .env.example for options.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    script_dir = Path(__file__).resolve().parent
    env_file = script_dir / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, rely on system env vars

# --- Configuration (loaded from .env) ----------------------------------------

# AI provider: "claude", "openai", or "auto"
AI_PROVIDER = os.getenv("AI_PROVIDER", "auto")

# Override model (leave empty for defaults)
AI_MODEL = os.getenv("OPENAI_MODEL", "") or os.getenv("CLAUDE_MODEL", "")

# Path to the main auditor script
SCRIPT = os.getenv("AUDITOR_SCRIPT_PATH", str(Path(__file__).resolve().parent / "seo-auditor.py"))

# Delay between audits to avoid Google rate limits
DELAY_BETWEEN = int(os.getenv("BATCH_DELAY", "30"))

# --- Audits to run -----------------------------------------------------------
# Format: (keyword, domain, optional specific URL)
AUDITS = [
    ("oak flooring uk", "flooringsuppliescentre.co.uk", ""),
    ("engineered wood flooring", "oakparquetflooring.co.uk", ""),
    ("laminate flooring uk", "tradescentre.co.uk", ""),
    ("vinyl flooring uk", "flooringsuppliescentre.co.uk", ""),
    # ("solid wood flooring", "oakparquetflooring.co.uk", ""),
    # ("flooring supplies", "flooringsuppliescentre.co.uk", ""),
    # ("спално бельо", "hrimarcomfort.com", ""),  # Bulgarian keywords work too
    # Add more as needed...
]

# -----------------------------------------------------------------------------

for i, (keyword, domain, url) in enumerate(AUDITS):
    print(f"\n{'='*60}")
    print(f"Audit {i+1}/{len(AUDITS)}: '{keyword}' on {domain}")
    print(f"Provider: {AI_PROVIDER}" + (f" (model: {AI_MODEL})" if AI_MODEL else ""))
    print(f"{'='*60}")

    cmd = [
        sys.executable, SCRIPT,
        "--keyword", keyword,
        "--domain", domain,
        "--provider", AI_PROVIDER,
    ]
    if url:
        cmd.extend(["--url", url])
    if AI_MODEL:
        cmd.extend(["--model", AI_MODEL])

    subprocess.run(cmd)

    if i < len(AUDITS) - 1:
        print(f"\nWaiting {DELAY_BETWEEN}s before next audit...")
        time.sleep(DELAY_BETWEEN)

print(f"\n{'='*60}")
print(f"All {len(AUDITS)} audits complete!")
print(f"{'='*60}")
