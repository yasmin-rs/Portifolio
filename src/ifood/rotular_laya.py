"""Laya (classificador local, não generativo) para tema e gravidade — avaliação e pré-rotulagem.

O Laya não escreve texto: escolhe uma opção e devolve a probabilidade de cada uma.
Por isso não existe "JSON inválido" e cada review leva milissegundos.
Usos:
    1. comparar com os LLMs no gabarito (mesmas métricas);
    2. sugerir rótulos para ampliar o gabarito — a pessoa revisa primeiro as de baixa confiança.

Instalação (opcional, traz o PyTorch; não vai para o deploy do dashboard):
    uv sync --extra rotulagem

Uso:
    uv run --extra rotulagem python -m ifood.rotular_laya            # avalia nas 50 do gabarito
    uv run --extra rotulagem python -m ifood.rotular_laya --amostra  # sugere rótulos para as 500

Custo: US$ 0 (roda local). O modelo (~1,7 GB) é baixado do Hugging Face na 1ª vez,
na revisão fixada em laya.PINNED_REVISIONS (repo oficial convaiinnovations/laya).
"""
import argparse
import sys
import time
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from ifood import config
from ifood.gabarito import carregar_gabarito
from ifood.seguranca import verificar_seguranca

PASTA_AVALIACAO = config.OUTPUTS_DIR / "avaliacao"

# O modelo é em inglês e as reviews também: opções em inglês, traduzidas de volta para os temas do projeto.
TEMA_PT = {
    "staff_attitude": "atendimento", "slow_service": "demora", "cleanliness": "limpeza",
    "food_quality": "comida_errada_ou_fria", "price": "preco", "wrong_order": "pedido_incorreto",
    "praise_no_problem": "elogio_sem_problema", "other": "outro",
}
GRAVIDADE_PT = {"low": "baixa", "medium": "media", "high": "alta"}


class ClassificacaoLaya(BaseModel):
    """Mesmos critérios do prompt classificacao_v2, em inglês."""
    topic: Literal["staff_attitude", "slow_service", "cleanliness", "food_quality", "price",
                   "wrong_order", "praise_no_problem", "other"] = Field(description=(
        "Main topic of this fast-food restaurant review. staff_attitude: rude, careless or unfriendly staff/manager. "
        "slow_service: long wait, slow line or drive-thru. cleanliness: dirty store, restroom or tables; hair, insect "
        "or foreign object in food. food_quality: cold, raw, stale, badly made or bad-tasting food. price: expensive, "
        "overcharged, promotion not applied. wrong_order: missing, swapped or wrong items. praise_no_problem: positive "
        "or neutral review with no problem. other: any other problem (hours, app, payment, parking, safety) or no information."))
    severity: Literal["low", "medium", "high"] = Field(description=(
        "Severity of what the customer reports. low: no problem or a minor annoyance. medium: a real problem that "
        "ruined the experience. high: health or safety risk, discrimination, threat, assault or serious overcharge."))


def carregar_laya():
    import laya  # import aqui: o resto do projeto não depende do PyTorch
    return laya, laya.load()


def classificar(reviews: list[str], tamanho_lote: int = 16) -> pd.DataFrame:
    laya, agente = carregar_laya()
    inicio = time.perf_counter()
    resultados = laya.decide_batch(agente, reviews, schema=ClassificacaoLaya, return_details=True, batch_size=tamanho_lote)
    segundos = time.perf_counter() - inicio
    linhas = []
    for texto, r in zip(reviews, resultados):
        tema, grav = TEMA_PT[r.values["topic"]], GRAVIDADE_PT[r.values["severity"]]
        alertas = verificar_seguranca(texto)  # mesma trava em código do pipeline
        linhas.append({
            "tema": tema, "gravidade_modelo": grav,
            "gravidade": "alta" if alertas else grav, "trava_aplicada": bool(alertas) and grav != "alta",
            "confianca_tema": r.answer_confidence["topic"], "confianca_gravidade": r.answer_confidence["severity"],
            "segundo_tema": TEMA_PT[sorted(r.probabilities["topic"], key=r.probabilities["topic"].get)[-2]],
            "truncada": r.usage.get("truncated", False),
        })
    df = pd.DataFrame(linhas)
    df.attrs["segundos_por_review"] = segundos / max(len(reviews), 1)
    return df


def avaliar_gabarito() -> None:
    g = carregar_gabarito()
    previsto = classificar(g["review"].tolist())
    pred = pd.concat([g[["review_id", "review", "rating_num", "tema_gabarito", "gravidade_gabarito"]], previsto], axis=1)
    com_grav = pred[pred["gravidade_gabarito"].notna()]
    altas = pred[pred["gravidade_gabarito"] == "alta"]
    metricas = {
        "modelo": "laya (local)", "n": len(pred),
        "acuracia_tema": round((pred["tema"] == pred["tema_gabarito"]).mean(), 3),
        "acuracia_gravidade_sem_trava": round((com_grav["gravidade_modelo"] == com_grav["gravidade_gabarito"]).mean(), 3),
        "acuracia_gravidade": round((com_grav["gravidade"] == com_grav["gravidade_gabarito"]).mean(), 3),
        "recall_gravidade_alta": f"{(altas['gravidade'] == 'alta').sum()}/{len(altas)}",
        "reviews_truncadas": int(pred["truncada"].sum()),
        "segundos_por_review": round(previsto.attrs["segundos_por_review"], 3),
    }
    PASTA_AVALIACAO.mkdir(parents=True, exist_ok=True)
    pred.to_csv(PASTA_AVALIACAO / "predicoes_laya.csv", index=False)  # tem texto de review: fica fora do git

    print("\n=== Laya no gabarito ===")
    for k, v in metricas.items():
        print(f"  {k}: {v}")
    acertou = pred["tema"] == pred["tema_gabarito"]
    print(f"  confiança média do tema — quando acerta: {pred.loc[acertou, 'confianca_tema'].mean():.2f}"
          f" | quando erra: {pred.loc[~acertou, 'confianca_tema'].mean():.2f}")
    print("\nErros de tema (gabarito -> Laya):")
    print(pred.loc[~acertou].groupby(["tema_gabarito", "tema"]).size().sort_values(ascending=False).to_string())


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--amostra", action="store_true", help="sugerir rótulos para as 500 da amostra")
    args = parser.parse_args()
    if args.amostra:
        raise SystemExit("Ainda não: primeiro vemos como o Laya vai no gabarito.")
    avaliar_gabarito()


if __name__ == "__main__":
    principal()
