"""Qwen (local) x Gemini (API) nas 500 reviews da amostra — sem chamar modelo.

Fora do gabarito não existe "resposta certa", então medimos CONCORDÂNCIA entre os dois:
onde concordam, a classificação é provavelmente confiável; onde discordam, um humano
precisa olhar. As discordâncias viram a fila de revisão para ampliar o gabarito.

Uso:
    uv run python -m ifood.concordancia_modelos

Entradas: outputs/agentes/resultados_amostra_<qwen>.csv e resultados_amostra_<gemini>_classificacao.csv
Saídas:
    outputs/avaliacao/comparativo_concordancia.md   resumo agregado (sem texto de review; vai para o git)
    outputs/avaliacao/comparativo_concordancia.csv  contagens por combinação de rótulos (lido pelo dashboard)
    data/revisao_gabarito.xlsx                       fila de revisão COM texto (fora do git)
"""
import sys

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from ifood import config
from ifood.avaliar_prompts import nome_arquivo_modelo
from ifood.gabarito import GRAVIDADES, carregar_gabarito
from ifood.schemas import TEMAS

PASTA_AGENTES = config.OUTPUTS_DIR / "agentes"
ARQUIVO_RESUMO = config.OUTPUTS_DIR / "avaliacao" / "comparativo_concordancia.md"
ARQUIVO_CONTAGENS = config.OUTPUTS_DIR / "avaliacao" / "comparativo_concordancia.csv"
ARQUIVO_REVISAO = config.DATA_DIR / "revisao_gabarito.xlsx"
QWEN, GEMINI = "ollama/qwen2.5:7b", "gemini/gemini-3.5-flash-lite"


def kappa(a: pd.Series, b: pd.Series) -> float:
    """Kappa de Cohen: concordância descontando a que aconteceria por acaso (1 = total, 0 = acaso)."""
    observada = (a == b).mean()
    acaso = sum(a.eq(c).mean() * b.eq(c).mean() for c in set(a) | set(b))
    return round((observada - acaso) / (1 - acaso), 3) if acaso < 1 else 1.0


def carregar_pares() -> pd.DataFrame:
    qwen = pd.read_csv(PASTA_AGENTES / f"resultados_amostra_{nome_arquivo_modelo(QWEN)}.csv")
    gem = pd.read_csv(PASTA_AGENTES / f"resultados_amostra_{nome_arquivo_modelo(GEMINI)}_classificacao.csv")
    colunas = ["review_id", "tema", "gravidade", "trava_aplicada"]
    pares = qwen[["review_id", "rating_num", "review", "status"] + colunas[1:]].merge(
        gem[colunas + ["status"]], on="review_id", suffixes=("_qwen", "_gemini"))
    validos = pares["tema_qwen"].notna() & pares["status_gemini"].eq("classificado")
    return pares[validos].reset_index(drop=True)


def resumir(p: pd.DataFrame, g: pd.DataFrame) -> str:
    tema_ok = p["tema_qwen"] == p["tema_gemini"]
    grav_ok = p["gravidade_qwen"] == p["gravidade_gemini"]
    alta_q, alta_g = p["gravidade_qwen"].eq("alta"), p["gravidade_gemini"].eq("alta")
    linhas = [
        f"# Qwen 2.5 7B (local) × Gemini 3.5 Flash-Lite (API) — {len(p)} reviews da amostra\n",
        "| Medida | Valor |", "|---|---|",
        f"| Concordam no tema | {tema_ok.mean():.0%} (kappa {kappa(p['tema_qwen'], p['tema_gemini'])}) |",
        f"| Concordam na gravidade | {grav_ok.mean():.0%} (kappa {kappa(p['gravidade_qwen'], p['gravidade_gemini'])}) |",
        f"| Concordam nos dois | {(tema_ok & grav_ok).mean():.0%} |",
        f"| Gravidade alta — Qwen / Gemini / ambos | {alta_q.sum()} / {alta_g.sum()} / {(alta_q & alta_g).sum()} |",
        f"| Alta só para um dos dois (revisar primeiro) | {(alta_q ^ alta_g).sum()} |",
        "", "Gravidade já inclui a trava em código (regra de segurança), igual nos dois.",
        "Kappa desconta a concordância por acaso: >0,6 é boa, >0,8 muito boa.", "",
    ]
    if len(g):  # checagem: nas reviews do gabarito, quem acerta?
        m = g.merge(p, on="review_id")
        linhas += [f"## Nas {len(m)} reviews do gabarito (rótulo humano)\n", "| | Qwen | Gemini |", "|---|---|---|",
                   f"| Tema | {(m['tema_qwen'] == m['tema_gabarito']).mean():.0%} | {(m['tema_gemini'] == m['tema_gabarito']).mean():.0%} |"]
        mg = m[m["gravidade_gabarito"].notna()]
        linhas += [f"| Gravidade | {(mg['gravidade_qwen'] == mg['gravidade_gabarito']).mean():.0%} | "
                   f"{(mg['gravidade_gemini'] == mg['gravidade_gabarito']).mean():.0%} |", ""]
    cruz = pd.crosstab(p["tema_qwen"], p["tema_gemini"])
    tabela = "| Qwen \\ Gemini | " + " | ".join(cruz.columns) + " |\n|---|" + "---|" * len(cruz.columns) + "\n"
    tabela += "\n".join(f"| {t} | " + " | ".join(str(v) for v in cruz.loc[t]) + " |" for t in cruz.index)
    linhas += ["## Tema: Qwen (linhas) × Gemini (colunas)\n", tabela]
    return "\n".join(linhas)


def montar_fila_revisao(p: pd.DataFrame, g: pd.DataFrame) -> pd.DataFrame:
    """Reviews ainda sem rótulo humano, das que mais precisam de olho para as mais tranquilas."""
    fila = p[~p["review_id"].isin(g["review_id"])].copy()
    alta_diverge = fila["gravidade_qwen"].eq("alta") ^ fila["gravidade_gemini"].eq("alta")
    tema_diverge = fila["tema_qwen"] != fila["tema_gemini"]
    grav_diverge = fila["gravidade_qwen"] != fila["gravidade_gemini"]
    fila["prioridade"] = (alta_diverge * 4 + tema_diverge * 2 + grav_diverge * 1)
    fila["motivo"] = [", ".join(m for m, s in (("alta só para um", a), ("tema diverge", t), ("gravidade diverge", gv)) if s)
                      or "concordam" for a, t, gv in zip(alta_diverge, tema_diverge, grav_diverge)]
    fila = fila.sort_values(["prioridade", "review_id"], ascending=[False, True])
    # sugestão = valor em que os dois concordam; se discordam, fica o do Gemini (melhor no tema) para o humano corrigir
    fila["tema_gabarito"] = fila["tema_gemini"]
    fila["gravidade_gabarito"] = fila["gravidade_gemini"]
    fila["conferido"] = ""
    return fila[["review_id", "rating_num", "review", "motivo", "tema_qwen", "tema_gemini", "gravidade_qwen",
                 "gravidade_gemini", "tema_gabarito", "gravidade_gabarito", "conferido"]]


def salvar_planilha(fila: pd.DataFrame) -> None:
    fila.to_excel(ARQUIVO_REVISAO, sheet_name="revisar", index=False)
    wb = load_workbook(ARQUIVO_REVISAO)
    ws = wb["revisar"]
    for col, larg in zip("ABCDEFGHIJK", (9, 6, 80, 22, 18, 18, 10, 10, 22, 12, 10)):
        ws.column_dimensions[col].width = larg
    amarelo, vermelho = PatternFill("solid", fgColor="FFF2CC"), PatternFill("solid", fgColor="F8D7DA")
    for i, linha in enumerate(ws.iter_rows(min_row=2), start=2):
        for c in linha:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for c in linha[8:11]:
            c.fill = amarelo
        if fila.iloc[i - 2]["motivo"] != "concordam":
            linha[3].fill = vermelho
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "D2"
    n = len(fila) + 1
    for coluna, opcoes in (("I", TEMAS), ("J", GRAVIDADES), ("K", ("sim",))):
        dv = DataValidation(type="list", formula1='"' + ",".join(opcoes) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{coluna}2:{coluna}{n}")
    wb.save(ARQUIVO_REVISAO)


def principal() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = carregar_pares()
    g = carregar_gabarito()[["review_id", "tema_gabarito", "gravidade_gabarito"]]
    resumo = resumir(p, g)
    ARQUIVO_RESUMO.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_RESUMO.write_text(resumo, encoding="utf-8")
    # contagens por combinação de rótulos (sem texto): é o que o dashboard lê
    colunas = ["tema_qwen", "tema_gemini", "gravidade_qwen", "gravidade_gemini"]
    p.groupby(colunas).size().reset_index(name="n").to_csv(ARQUIVO_CONTAGENS, index=False)
    print(resumo)

    if ARQUIVO_REVISAO.exists():
        print(f"\n{ARQUIVO_REVISAO} já existe; não sobrescrevi (pode ter revisões suas).")
        return
    fila = montar_fila_revisao(p, g)
    salvar_planilha(fila)
    print(f"\nFila de revisão: {ARQUIVO_REVISAO} — {len(fila)} reviews, "
          f"{(fila['motivo'] != 'concordam').sum()} com discordância no topo.")


if __name__ == "__main__":
    principal()
