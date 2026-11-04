import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def carregar_config(caminho: Path | None = None) -> dict:
    caminho = caminho or RAIZ / "config.toml"
    with open(caminho, "rb") as f:
        return tomllib.load(f)
