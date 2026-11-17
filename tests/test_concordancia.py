import pandas as pd

from ifood.concordancia_modelos import kappa, montar_fila_revisao


def test_kappa_total_e_acaso():
    a = pd.Series(["x", "y", "x", "y"])
    assert kappa(a, a) == 1.0
    assert kappa(a, pd.Series(["x", "x", "y", "y"])) == 0.0  # metade de acerto = o esperado por acaso


def test_fila_poe_alta_divergente_no_topo_e_tira_gabarito():
    p = pd.DataFrame({
        "review_id": [1, 2, 3, 4], "rating_num": [1, 1, 5, 2], "review": ["a", "b", "c", "d"],
        "tema_qwen": ["demora", "limpeza", "elogio_sem_problema", "preco"],
        "tema_gemini": ["demora", "limpeza", "elogio_sem_problema", "outro"],
        "gravidade_qwen": ["media", "media", "baixa", "media"],
        "gravidade_gemini": ["media", "alta", "baixa", "media"],
    })
    g = pd.DataFrame({"review_id": [3]})  # já rotulada à mão
    fila = montar_fila_revisao(p, g)
    assert list(fila["review_id"]) == [2, 4, 1]
    assert fila.iloc[0]["motivo"].startswith("alta só para um") and fila.iloc[-1]["motivo"] == "concordam"
