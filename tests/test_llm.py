"""Testes da camada de modelos com um litellm FALSO (nenhuma chamada real)."""
from types import SimpleNamespace

import pytest

from ifood import llm
from ifood.schemas import Classificacao

VALIDO = '{"tema": "demora", "sentimento": "negativo", "gravidade": "media", "resumo": "Fila longa."}'


def resposta_falsa(texto: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=texto))],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Cache e log em pasta temporária; modelo local fictício."""
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "LOG_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setenv("MODEL_NAME", "ollama/modelo-teste")
    monkeypatch.setenv("JSON_MAX_RETRIES", "2")
    monkeypatch.setattr(llm, "suporta_modo_json", lambda m: False)


def fila_de_respostas(monkeypatch, respostas):
    chamadas = []

    def falso(**kwargs):
        chamadas.append(kwargs)
        item = respostas.pop(0)
        if isinstance(item, Exception):
            raise item
        return resposta_falsa(item)

    monkeypatch.setattr(llm.litellm, "completion", falso)
    return chamadas


def test_json_invalido_gera_retry_e_depois_valida(ambiente, monkeypatch):
    chamadas = fila_de_respostas(monkeypatch, ["isto não é json", '{"tema": "xyz"}', VALIDO])
    r = llm.chamar_modelo("sys", "review", Classificacao)
    assert r.ok and r.dados["tema"] == "demora"
    assert r.falhas_json == 2 and len(chamadas) == 3
    assert r.tokens_entrada == 300  # soma das 3 tentativas


def test_desiste_apos_maximo_de_retries(ambiente, monkeypatch):
    fila_de_respostas(monkeypatch, ["ruim"] * 3)
    r = llm.chamar_modelo("sys", "review", Classificacao)
    assert not r.ok and r.falhas_json == 3 and "JSON inválido" in r.erro


def test_erro_de_rede_nao_derruba_o_lote(ambiente, monkeypatch):
    fila_de_respostas(monkeypatch, [TimeoutError("sem resposta")])
    r = llm.chamar_modelo("sys", "review", Classificacao)
    assert not r.ok and "TimeoutError" in r.erro


def test_cache_evita_segunda_chamada(ambiente, monkeypatch):
    chamadas = fila_de_respostas(monkeypatch, [VALIDO])
    primeira = llm.chamar_modelo("sys", "review", Classificacao)
    segunda = llm.chamar_modelo("sys", "review", Classificacao)
    assert primeira.ok and segunda.do_cache and len(chamadas) == 1
    resumo = llm.resumo_execucao()["ollama/modelo-teste"]
    assert resumo["chamadas"] == 2 and resumo["do_cache"] == 1 and resumo["taxa_falha_json"] == 0


def test_extrair_json_com_cercas_e_texto():
    assert llm.extrair_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.extrair_json('Aqui está: {"a": 2} pronto') == {"a": 2}


def test_modelo_pago_pede_confirmacao(monkeypatch):
    estimativa = {"modelo": "x", "local": False, "chamadas": 10, "tokens_entrada": 1,
                  "tokens_saida": 1, "custo_estimado_usd": 0.01}
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert llm.confirmar_execucao(estimativa) is False
    monkeypatch.setattr("builtins.input", lambda _: "s")
    assert llm.confirmar_execucao(estimativa) is True


def test_api_base_do_ollama_nao_vai_para_modelo_na_nuvem(ambiente, monkeypatch):
    monkeypatch.setenv("API_BASE", "http://localhost:11434")
    chamadas = fila_de_respostas(monkeypatch, [VALIDO, VALIDO])
    llm.chamar_modelo("sys", "review", Classificacao, usar_cache=False)
    llm.chamar_modelo("sys", "review", Classificacao, modelo="gemini/modelo-teste", usar_cache=False)
    assert chamadas[0]["api_base"] == "http://localhost:11434"
    assert "api_base" not in chamadas[1]


def test_modelo_que_recusa_temperature_tenta_de_novo_sem(ambiente, monkeypatch):
    monkeypatch.setattr(llm, "_MODELOS_SEM_TEMPERATURA", set())
    chamadas = fila_de_respostas(monkeypatch, [ValueError("temperature is not supported"), VALIDO, VALIDO])
    assert llm.chamar_modelo("sys", "a", Classificacao, modelo="nuvem/x", usar_cache=False).ok
    assert "temperature" in chamadas[0] and "temperature" not in chamadas[1]
    llm.chamar_modelo("sys", "b", Classificacao, modelo="nuvem/x", usar_cache=False)
    assert "temperature" not in chamadas[2]  # lembrou na chamada seguinte


def test_cota_diaria_esgotada_para_o_lote(ambiente, monkeypatch):
    erro = RuntimeError('429 quotaId: "GenerateRequestsPerDayPerProjectPerModel-FreeTier"')
    fila_de_respostas(monkeypatch, [erro])
    with pytest.raises(llm.CotaEsgotada):
        llm.chamar_modelo("sys", "review", Classificacao, modelo="nuvem/x", usar_cache=False)


def test_limite_por_minuto_nao_para_o_lote(ambiente, monkeypatch):
    fila_de_respostas(monkeypatch, [RuntimeError("429 rate limit per minute")])
    r = llm.chamar_modelo("sys", "review", Classificacao, modelo="nuvem/x", usar_cache=False)
    assert not r.ok and "429" in r.erro
