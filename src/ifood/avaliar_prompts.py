"""Avaliação das versões do prompt de classificação contra o gabarito.

Uso:
    uv run python -m ifood.avaliar_prompts                 # todas as versões, gabarito inteiro
    uv run python -m ifood.avaliar_prompts --limite 3      # teste rápido (3 reviews por versão)
    uv run python -m ifood.avaliar_prompts --versoes v3 --modelo <outro modelo>

Métricas por versão:
    acuracia_tema            tema previsto == tema do gabarito
    acuracia_gravidade       gravidade prevista == gravidade do gabarito
    recall_gravidade_alta    dos casos 'alta' no gabarito, quantos o modelo marcou 'alta'
    concordancia_sent_nota   validação barata: sentimento x nota (1-2 neg, 3 neutro, 4-5 pos)
    taxa_json_invalido       respostas fora do formato / respostas recebidas
Chamadas que falharam contam como erro de classificação.

Saídas em outputs/avaliacao/:
    predicoes_<modelo>.csv            uma linha por review x versão (para análise de erros)
    comparativo_prompts_<modelo>.csv  tabela comparativa
    comparativo_prompts_<modelo>.md   a mesma tabela em markdown (vai para o README)
    confusao_<versao>_<modelo>.csv    matriz de confusão de tema
"""
import argparse
import re
from pathlib import Path

import pandas as pd

from ifood import config, llm
from ifood.gabarito import carregar_gabarito
from ifood.prompts import carregar_prompt, listar_versoes
from ifood.schemas import TEMAS, Classificacao

PASTA_SAIDA = config.OUTPUTS_DIR / "avaliacao"
TOKENS_SAIDA_ESTIMADOS = 70


def sentimento_esperado(nota: int) -> str:
    return "negativo" if nota <= 2 else ("neutro" if nota == 3 else "positivo")


def nome_arquivo_modelo(modelo: str) -> str:
    """'ollama/qwen2.5:7b' -> 'ollama-qwen2.5-7b' (seguro para nome de arquivo)."""
    return re.sub(r"[^A-Za-z0-9.]+", "-", modelo).strip("-")


def classificar_gabarito(versao: str, gabarito: pd.DataFrame, modelo: str | None = None) -> pd.DataFrame:
    """Roda uma versão do prompt em todas as reviews do gabarito."""
    prompt = carregar_prompt("classificacao", versao)
    linhas = []
    for i, item in enumerate(gabarito.itertuples(), start=1):
        r = llm.chamar_modelo(prompt, item.review, Classificacao, etapa=f"avaliacao_{versao}", modelo=modelo)
        dados = r.dados or {}
        linhas.append({
            "versao": versao, "modelo": r.modelo, "review_id": item.review_id,
            "rating_num": item.rating_num, "review": item.review,
            "tema_gabarito": item.tema_gabarito, "gravidade_gabarito": item.gravidade_gabarito,
            "tema": dados.get("tema"), "sentimento": dados.get("sentimento"),
            "gravidade": dados.get("gravidade"), "resumo": dados.get("resumo"),
            "ok": r.ok, "do_cache": r.do_cache, "falhas_json": r.falhas_json, "erro": r.erro,
            "latencia_s": r.latencia_s, "tokens_entrada": r.tokens_entrada,
            "tokens_saida": r.tokens_saida, "custo_usd": r.custo_usd,
        })
        marca = "ok" if r.ok else "FALHOU"
        print(f"  [{versao}] {i}/{len(gabarito)} {marca} {'(cache)' if r.do_cache else f'{r.latencia_s:.1f}s'}",
              flush=True)
    return pd.DataFrame(linhas)


def calcular_metricas(pred: pd.DataFrame) -> dict:
    novas = pred[~pred["do_cache"]]
    respostas = novas["falhas_json"].sum() + novas["ok"].sum()
    com_grav = pred[pred["gravidade_gabarito"].notna()]
    altas = pred[pred["gravidade_gabarito"] == "alta"]
    custos = pred["custo_usd"]
    return {
        "versao": pred["versao"].iloc[0],
        "modelo": pred["modelo"].iloc[0],
        "n": len(pred),
        "acuracia_tema": round((pred["tema"] == pred["tema_gabarito"]).mean(), 3),
        "acuracia_gravidade": round((com_grav["gravidade"] == com_grav["gravidade_gabarito"]).mean(), 3)
                              if len(com_grav) else None,
        "recall_gravidade_alta": f"{(altas['gravidade'] == 'alta').sum()}/{len(altas)}",
        "concordancia_sent_nota": round((pred["sentimento"] == pred["rating_num"].map(sentimento_esperado)).mean(), 3),
        "taxa_json_invalido": round(novas["falhas_json"].sum() / respostas, 3) if respostas else 0.0,
        "chamadas_falhas": int((~pred["ok"]).sum()),
        # mediana (não média): uma chamada que pegou o PC suspenso levou 19h e distorcia a média
        "latencia_mediana_s": round(novas["latencia_s"].median(), 2) if len(novas) else None,
        "tokens_entrada_medio": int(novas["tokens_entrada"].mean()) if len(novas) else None,
        "custo_usd": None if custos.isna().any() else round(custos.sum(), 4),
    }


def tabela_markdown(df: pd.DataFrame) -> str:
    colunas = list(df.columns)
    linhas = ["| " + " | ".join(colunas) + " |", "|" + "---|" * len(colunas)]
    for _, r in df.iterrows():
        linhas.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |")
    return "\n".join(linhas)


def principal() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--versoes", nargs="*", help="ex.: v1 v3 (padrão: todas em prompts/)")
    parser.add_argument("--limite", type=int, help="usar só as N primeiras reviews do gabarito (teste rápido)")
    parser.add_argument("--modelo", help="sobrescreve MODEL_NAME do .env")
    parser.add_argument("--sim", action="store_true", help="não pedir confirmação (só use com estimativa vista)")
    parser.add_argument("--recalcular", help="só recalcula as tabelas a partir de um predicoes_*.csv (sem modelo)")
    args = parser.parse_args()

    if args.recalcular:
        pred = pd.read_csv(args.recalcular)
        salvar_resultados(pred, Path(args.recalcular).stem.removeprefix("predicoes_"))
        return

    versoes = args.versoes or listar_versoes("classificacao")
    gabarito = carregar_gabarito()
    if args.limite:
        gabarito = gabarito.head(args.limite)

    # ---- estimativa de custo antes de qualquer chamada
    tokens_medios = sum(llm.estimar_tokens_entrada(carregar_prompt("classificacao", v), gabarito["review"].tolist(),
                                                   args.modelo) for v in versoes) // len(versoes)
    estimativa = llm.estimar_custo(len(versoes) * len(gabarito), tokens_medios, TOKENS_SAIDA_ESTIMADOS, args.modelo)
    if not llm.confirmar_execucao(estimativa, assumir_sim=args.sim):
        print("Cancelado.")
        return

    predicoes = []
    for versao in versoes:
        print(f"\n== Versão {versao} ==")
        predicoes.append(classificar_gabarito(versao, gabarito, args.modelo))
    pred = pd.concat(predicoes, ignore_index=True)

    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    sufixo = nome_arquivo_modelo(pred["modelo"].iloc[0]) + (f"_limite{args.limite}" if args.limite else "")
    arquivo = PASTA_SAIDA / f"predicoes_{sufixo}.csv"
    if arquivo.exists():  # mantém versões avaliadas antes (ex.: rodar só a v4 não apaga v1-v3)
        anteriores = pd.read_csv(arquivo)
        pred = pd.concat([anteriores[~anteriores["versao"].isin(versoes)], pred], ignore_index=True)
    pred.to_csv(arquivo, index=False, encoding="utf-8")
    salvar_resultados(pred, sufixo)
    llm.imprimir_resumo()


def salvar_resultados(pred: pd.DataFrame, sufixo: str) -> None:
    """Gera tabela comparativa (csv + md) e matrizes de confusão a partir das predições."""

    comparativo = pd.DataFrame([calcular_metricas(g) for _, g in pred.groupby("versao", sort=True)])
    comparativo.to_csv(PASTA_SAIDA / f"comparativo_prompts_{sufixo}.csv", index=False, encoding="utf-8")
    (PASTA_SAIDA / f"comparativo_prompts_{sufixo}.md").write_text(tabela_markdown(comparativo), encoding="utf-8")

    for versao, g in pred.groupby("versao"):
        confusao = pd.crosstab(g["tema_gabarito"], g["tema"].fillna("(falhou)"),
                               rownames=["gabarito"], colnames=["previsto"])
        confusao = confusao.reindex(index=[t for t in TEMAS if t in confusao.index])
        confusao.to_csv(PASTA_SAIDA / f"confusao_{versao}_{sufixo}.csv", encoding="utf-8")

    print("\n=== Comparativo das versões ===")
    print(comparativo.to_string(index=False))


if __name__ == "__main__":
    principal()
