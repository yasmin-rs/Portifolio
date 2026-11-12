"""Testes das perguntas em linguagem natural do dashboard (sem modelo)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from consultas import interpretar, responder  # noqa: E402


def test_interpreta_tema_e_agrupamento():
    i = interpretar("Quais lojas têm mais reclamação de demora?")
    assert (i.filtro, i.agrupar_por, i.percentual, i.top) == ("demora", "store_label", False, 5)
    i = interpretar("Top 10 cidades com maior percentual de problemas de limpeza")
    assert (i.filtro, i.agrupar_por, i.percentual, i.top) == ("limpeza", "city", True, 10)


def test_termo_mais_especifico_vence():
    assert interpretar("lojas com mais pedido errado").filtro == "pedido_incorreto"


def test_graves_escalados_e_ingles():
    assert interpretar("Quais cidades têm mais casos graves?").filtro == "grave"
    assert interpretar("lojas com mais casos escalados").filtro == "escalado"
    assert interpretar("which stores have the most slow service?").filtro == "demora"


def test_pergunta_sem_sentido_retorna_none():
    assert interpretar("qual a capital da França?") is None
    assert interpretar("") is None


def test_responder_conta_e_ordena():
    df = pd.DataFrame({"store_label": ["A", "A", "B", "B", "B"],
                       "tema": ["demora", "limpeza", "demora", "demora", "elogio_sem_problema"],
                       "gravidade": "media", "status": "ignorado", "sentimento": "negativo"})
    r = responder(df, interpretar("lojas com mais demora"))
    assert r["loja"].tolist() == ["B", "A"] and r["casos"].tolist() == [2, 1]
    r = responder(df, interpretar("lojas com maior percentual de demora"))
    assert r.iloc[0]["percentual"] == round(2 / 3, 3)
