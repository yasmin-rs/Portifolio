"""Configuração central: tudo vem do .env (nenhum nome de modelo ou chave no código)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


DATA_DIR = ROOT / "data"
OUTPUTS_DIR = ROOT / "outputs"
PROMPTS_DIR = ROOT / "prompts"

RAW_CSV = ROOT / env("RAW_CSV", "data/raw/McDonald_s_Reviews.csv")
CLEAN_PATH = DATA_DIR / "clean.parquet"
SAMPLE_PATH = DATA_DIR / "sample.csv"

# Tamanho da amostra: único lugar configurável (via .env)
SAMPLE_SIZE = int(env("SAMPLE_SIZE", "500"))
SEED = int(env("SEED", "42"))
