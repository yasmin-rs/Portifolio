"""Gera os dados pré-calculados que o dashboard lê (sem chamar modelo).

Uso:
    uv run python -m ifood.exportar_dashboard                      # usa o resultado de agentes mais completo
    uv run python -m ifood.exportar_dashboard --arquivo outputs/agentes/resultados_xxx.csv

Saídas em outputs/dashboard/ (estas VÃO para o git e para o demo):
    reviews.parquet   uma linha por review analisada — SEM o texto original da review
    lojas.parquet     uma linha por loja, com indicadores da base completa (sem IA)
    qualidade.csv     comparativo das versões de prompt (da avaliação)
    meta.json         de onde vieram os dados, modelo, versões, custo
"""
import argparse
import ast
import json
from datetime import datetime

import pandas as pd

from ifood import config

PASTA_AGENTES = config.OUTPUTS_DIR / "agentes"
PASTA_DASHBOARD = config.OUTPUTS_DIR / "dashboard"

# Colunas publicadas. O texto original ("review") fica de fora de propósito:
# a licença do dataset não está especificada, então só publicamos dados derivados.
COLUNAS_REVIEWS = [
    "review_id", "store_id", "store_label", "city", "state", "latitude", "longitude", "rating_num",
    "tema", "sentimento", "gravidade", "resumo", "acao_decisor", "acao_final", "motivo", "status",
    "trava_aplicada", "categorias_alerta", "resposta", "revisao_aprovada", "problemas_revisao",
    "tentativas_redacao", "n_chamadas", "custo_usd",
]


def escolher_arquivo_resultados() -> str:
    """Prefere resultados da amostra; senão o do gabarito. Ignora testes com --limite."""
    candidatos = [p for p in PASTA_AGENTES.glob("resultados_*.csv") if "_limite" not in p.name]
    if not candidatos:
        raise FileNotFoundError("Nenhum resultado de agentes. Rode: uv run python -m ifood.agentes")
    candidatos.sort(key=lambda p: ("_amostra_" in p.name, len(pd.read_csv(p, usecols=["review_id"]))))
    return str(candidatos[-1])


def categorias_alerta(valor) -> str:
    """'[{"categoria": "saude", ...}]' -> 'saude' (só a categoria; o trecho é texto da review)."""
    try:
        alertas = ast.literal_eval(valor) if isinstance(valor, str) else (valor or [])
    except (ValueError, SyntaxError):
        return ""
    return ", ".join(sorted({a["categoria"] for a in alertas}))


def montar_reviews(caminho: str) -> pd.DataFrame:
    df = pd.read_csv(caminho)
    df["categorias_alerta"] = df["alertas_seguranca"].map(categorias_alerta)
    amostra = pd.read_csv(config.SAMPLE_PATH, usecols=["review_id", "time_bucket"])
    df = df.merge(amostra, on="review_id", how="left")
    return df[COLUNAS_REVIEWS + ["time_bucket"]]


def montar_lojas() -> pd.DataFrame:
    """Indicadores por loja na base limpa inteira (23 mil reviews) — pandas puro, sem modelo."""
    base = pd.read_parquet(config.CLEAN_PATH)
    lojas = base.groupby(["store_id", "store_label", "store_address", "city", "state"], dropna=False).agg(
        latitude=("latitude", "first"), longitude=("longitude", "first"),
        store_rating_count=("store_rating_count", "first"),
        reviews_base=("review_id", "size"),
        nota_media_base=("rating_num", "mean"),
        pct_negativas_base=("rating_num", lambda s: (s <= 2).mean()),
    ).reset_index()
    lojas["nota_media_base"] = lojas["nota_media_base"].round(2)
    lojas["pct_negativas_base"] = lojas["pct_negativas_base"].round(3)
    return lojas


def principal() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arquivo", help="CSV de resultados dos agentes (padrão: o mais completo)")
    args = parser.parse_args()

    caminho = args.arquivo or escolher_arquivo_resultados()
    reviews = montar_reviews(caminho)
    lojas = montar_lojas()

    PASTA_DASHBOARD.mkdir(parents=True, exist_ok=True)
    reviews.to_parquet(PASTA_DASHBOARD / "reviews.parquet", index=False)
    lojas.to_parquet(PASTA_DASHBOARD / "lojas.parquet", index=False)

    comparativos = sorted((config.OUTPUTS_DIR / "avaliacao").glob("comparativo_prompts_*.csv"))
    comparativos = [c for c in comparativos if "_limite" not in c.name]
    if comparativos:
        pd.concat(pd.read_csv(c) for c in comparativos).to_csv(PASTA_DASHBOARD / "qualidade.csv", index=False)

    custos = reviews["custo_usd"]
    meta = {
        "gerado_em": datetime.now().isoformat(timespec="minutes"),
        "fonte": caminho.replace("\\", "/").split("/")[-1],
        "reviews_analisadas": len(reviews),
        "lojas_na_base": len(lojas),
        "reviews_na_base": int(lojas["reviews_base"].sum()),
        "modelo": config.env("MODEL_NAME"),
        "prompt_classificador": config.env("CLASSIFIER_PROMPT_VERSION", "v2"),
        "chamadas_llm": int(reviews["n_chamadas"].sum()),
        "custo_total_usd": None if custos.isna().any() else round(float(custos.sum()), 4),
    }
    (PASTA_DASHBOARD / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    principal()
