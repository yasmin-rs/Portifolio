"""Testes da limpeza (sem modelo, sem ler o CSV real)."""
import pandas as pd

from ifood.data_prep import alocar, extrair_cidade_estado, faixa_de_tempo, limpar_review

F = "\ufffd"


def test_apostrofo_reparado_e_emoji_removido():
    assert limpar_review(f"Didn{F*3}t like it") == "Didn't like it"
    assert limpar_review(f"Great {F*6} food") == "Great food"
    assert limpar_review(F * 5) == ""


def test_faixas_de_tempo():
    assert faixa_de_tempo("5 days ago") == "< 1 mês"
    assert faixa_de_tempo("a month ago") == "1-11 meses"
    assert faixa_de_tempo("a year ago") == "1-2 anos"
    assert faixa_de_tempo("4 years ago") == "3-5 anos"
    assert faixa_de_tempo("10 years ago") == "6+ anos"
    assert faixa_de_tempo("ontem") == "desconhecido"


def test_cidade_estado():
    assert extrair_cidade_estado("429 7th Ave, New York, NY 10001, United States") == ("New York", "NY")
    assert extrair_cidade_estado("2476 Kal???") == ("Desconhecida", "??")


def test_alocacao_soma_exata():
    w = {1: 0.35, 2: 0.22, 3: 0.17, 4: 0.13, 5: 0.13}
    for total in (10, 500, 777, 2000):
        assert sum(alocar(total, w).values()) == total


def test_rating_regex():
    s = pd.Series(["1 star", "4 stars", "x"]).str.extract(r"(\d)")[0]
    assert s.tolist()[:2] == ["1", "4"] and pd.isna(s.iloc[2])
