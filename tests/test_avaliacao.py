"""Testes das métricas de avaliação e do carregamento de prompts (sem modelo)."""
import pandas as pd

from ifood.avaliar_prompts import calcular_metricas, nome_arquivo_modelo, sentimento_esperado
from ifood.prompts import carregar_prompt, listar_versoes


def test_sentimento_esperado_pela_nota():
    assert [sentimento_esperado(n) for n in range(1, 6)] == ["negativo", "negativo", "neutro", "positivo", "positivo"]


def test_metricas_contam_falha_como_erro():
    pred = pd.DataFrame({
        "versao": "v1", "modelo": "ollama/x", "rating_num": [1, 5, 3],
        "tema_gabarito": ["demora", "elogio_sem_problema", "limpeza"],
        "gravidade_gabarito": ["alta", "baixa", "media"],
        "tema": ["demora", "elogio_sem_problema", None],          # 3ª chamada falhou
        "sentimento": ["negativo", "positivo", None],
        "gravidade": ["media", "baixa", None],
        "ok": [True, True, False], "do_cache": [False, False, False],
        "falhas_json": [1, 0, 3], "latencia_s": [2.0, 4.0, 6.0],
        "tokens_entrada": [100, 100, 300], "custo_usd": [0.0, 0.0, 0.0],
    })
    m = calcular_metricas(pred)
    assert m["acuracia_tema"] == round(2 / 3, 3)
    assert m["recall_gravidade_alta"] == "0/1"
    assert m["chamadas_falhas"] == 1
    assert m["taxa_json_invalido"] == round(4 / 6, 3)  # 4 inválidas em 6 respostas recebidas


def test_nome_arquivo_modelo():
    assert nome_arquivo_modelo("ollama/qwen2.5:7b") == "ollama-qwen2.5-7b"


def test_prompts_versionados_existem_e_listam_todos_os_temas():
    assert {"v1", "v2", "v3"} <= set(listar_versoes("classificacao"))
    from ifood.schemas import TEMAS
    for versao in ("v1", "v2", "v3"):
        texto = carregar_prompt("classificacao", versao)
        assert all(t in texto for t in TEMAS), f"{versao} não lista todos os temas"
