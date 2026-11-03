"""Caricamento configurazione e utilità HTTP condivise."""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "criteria.yaml"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

# SEC chiede un User-Agent con nome e contatto. Impostalo come secret SEC_USER_AGENT
# nel repository (es. "EquityScreening tuonome@esempio.com").
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "EquityScreening research script")
GENERIC_UA = "Mozilla/5.0 (compatible; EquityScreening/1.0)"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def http_get(url: str, headers: dict | None = None, retries: int = 3, timeout: int = 30) -> requests.Response:
    """GET con retry ed esponenziale (2s, 4s, 8s)."""
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=headers or {"User-Agent": GENERIC_UA}, timeout=timeout)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code} per {url}")
            if r.status_code in (403, 404):
                break
        except requests.RequestException as e:
            last = e
        time.sleep(2 ** (i + 1))
    raise last  # type: ignore[misc]
