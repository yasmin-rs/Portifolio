"""Leitura dos prompts versionados em prompts/ (um arquivo por versão)."""
from ifood import config


def listar_versoes(prefixo: str = "classificacao") -> list[str]:
    """Ex.: ['v1', 'v2', 'v3'] para prompts/classificacao_v1.md, ..."""
    arquivos = sorted(config.PROMPTS_DIR.glob(f"{prefixo}_v*.md"))
    return [a.stem.removeprefix(f"{prefixo}_") for a in arquivos]


def carregar_prompt(prefixo: str, versao: str) -> str:
    caminho = config.PROMPTS_DIR / f"{prefixo}_{versao}.md"
    if not caminho.exists():
        raise FileNotFoundError(f"Prompt não encontrado: {caminho}")
    return caminho.read_text(encoding="utf-8").strip()
