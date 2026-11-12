"""Comparação modelo local x API: mesmo prompt, mesmas reviews (gabarito), mesma métrica.

Uso:
    uv run python -m ifood.comparar_modelos                    # MODEL_NAME x COMPARE_MODEL_NAME, prompts v2 e v4
    uv run python -m ifood.comparar_modelos --versoes v2

Os modelos vêm do .env (MODEL_NAME e COMPARE_MODEL_NAME) — nenhum nome no código.
Se um modelo já foi avaliado (outputs/avaliacao/predicoes_<modelo>.csv), reaproveita
as predições em vez de chamar de novo (o local leva ~20 min por versão em CPU).
Antes de chamar um modelo pago, mostra a estimativa de custo e pede confirmação.

Saída: outputs/avaliacao/comparativo_modelos.csv e .md
"""
import argparse

import pandas as pd

from ifood import config, llm
from ifood.avaliar_prompts import (PASTA_SAIDA, TOKENS_SAIDA_ESTIMADOS, calcular_metricas, classificar_gabarito,
                                   nome_arquivo_modelo, tabela_markdown)
from ifood.gabarito import carregar_gabarito
from ifood.prompts import carregar_prompt


def predicoes_do_modelo(modelo: str, versoes: list[str], gabarito: pd.DataFrame, assumir_sim: bool) -> pd.DataFrame:
    arquivo = PASTA_SAIDA / f"predicoes_{nome_arquivo_modelo(modelo)}.csv"
    anteriores = pd.read_csv(arquivo) if arquivo.exists() else pd.DataFrame(columns=["versao"])
    prontas = [v for v in versoes if (anteriores["versao"] == v).sum() == len(gabarito)]
    faltam = [v for v in versoes if v not in prontas]
    if prontas:
        print(f"{modelo}: reaproveitando predições salvas de {prontas}")
    if not faltam:
        return anteriores[anteriores["versao"].isin(versoes)]

    tokens = max(llm.estimar_tokens_entrada(carregar_prompt("classificacao", v), gabarito["review"].tolist(), modelo)
                 for v in faltam)
    estimativa = llm.estimar_custo(len(faltam) * len(gabarito), tokens, TOKENS_SAIDA_ESTIMADOS, modelo)
    if not llm.confirmar_execucao(estimativa, assumir_sim=assumir_sim):
        raise SystemExit("Cancelado.")
    novas = pd.concat([classificar_gabarito(v, gabarito, modelo) for v in faltam], ignore_index=True)
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    todas = pd.concat([anteriores[~anteriores["versao"].isin(faltam)], novas], ignore_index=True)
    todas.to_csv(arquivo, index=False, encoding="utf-8")
    return todas[todas["versao"].isin(versoes)]


def principal() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--versoes", nargs="*", default=["v2", "v4"])
    parser.add_argument("--sim", action="store_true", help="não pedir confirmação de custo")
    args = parser.parse_args()

    modelos = [config.env("MODEL_NAME"), config.env("COMPARE_MODEL_NAME")]
    if not all(modelos):
        raise SystemExit("Defina MODEL_NAME e COMPARE_MODEL_NAME no .env (veja .env.example).")
    gabarito = carregar_gabarito()

    linhas = []
    for modelo in modelos:
        pred = predicoes_do_modelo(modelo, args.versoes, gabarito, args.sim)
        for _, grupo in pred.groupby("versao"):
            m = calcular_metricas(grupo)
            m["custo_por_1000_reviews_usd"] = (None if m["custo_usd"] is None
                                               else round(m["custo_usd"] / m["n"] * 1000, 2))
            linhas.append(m)

    tabela = pd.DataFrame(linhas)[[
        "modelo", "versao", "n", "acuracia_tema", "acuracia_gravidade", "recall_gravidade_alta",
        "concordancia_sent_nota", "taxa_json_invalido", "chamadas_falhas", "latencia_mediana_s",
        "tokens_entrada_medio", "custo_usd", "custo_por_1000_reviews_usd"]]
    tabela.to_csv(PASTA_SAIDA / "comparativo_modelos.csv", index=False, encoding="utf-8")
    (PASTA_SAIDA / "comparativo_modelos.md").write_text(tabela_markdown(tabela), encoding="utf-8")
    print("\n=== Local x API ===")
    print(tabela.to_string(index=False))
    llm.imprimir_resumo()


if __name__ == "__main__":
    principal()
