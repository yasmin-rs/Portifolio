"""Testes da automação com agentes FALSOS (nenhuma chamada a modelo)."""
import json

import pandas as pd
import pytest

from ifood import pipeline

GRAVE = {"status": "escalado_para_humano", "gravidade": "alta", "tema": "limpeza", "resumo": "Inseto na bebida.",
         "motivo": "Trava.", "resposta": "Sorry.", "alertas_seguranca": [{"categoria": "corpo_estranho", "trecho": "bug"}]}
NORMAL = {"status": "resposta_enviada", "gravidade": "media", "tema": "demora", "resumo": "Fila.", "motivo": "x",
          "resposta": "Sorry.", "alertas_seguranca": []}


@pytest.fixture
def pastas(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PASTA_ENTRADA", tmp_path / "inbox")
    monkeypatch.setattr(pipeline, "PASTA_PROCESSADOS", tmp_path / "processed")
    monkeypatch.setattr(pipeline, "ARQUIVO_RESULTADOS", tmp_path / "out" / "resultados.jsonl")
    monkeypatch.setattr(pipeline, "ARQUIVO_ALERTAS", tmp_path / "out" / "alerts.jsonl")
    monkeypatch.setattr(pipeline.agentes, "estimar_lote", lambda textos, modelo=None: {
        "modelo": "ollama/falso", "local": True, "chamadas": 1, "tokens_entrada": 1, "tokens_saida": 1,
        "custo_estimado_usd": 0.0})
    monkeypatch.setattr(pipeline.agentes, "processar_review",
                        lambda texto, modelo=None: GRAVE if "bug" in texto else NORMAL)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    (tmp_path / "inbox").mkdir()
    return tmp_path


def test_processa_inbox_gera_alerta_so_para_grave_e_move_arquivo(pastas):
    pd.DataFrame({"review_id": [1, 2], "store_label": ["S01", "S02"], "city": ["Austin", "Miami"],
                  "rating_num": [1, 2], "review": ["There was a bug in my drink", "Slow line"]}
                 ).to_csv(pastas / "inbox" / "novos.csv", index=False)
    assert pipeline.processar_inbox(assumir_sim=True) == 1
    alertas = [json.loads(l) for l in (pastas / "out" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(alertas) == 1 and alertas[0]["review_id"] == 1
    assert alertas[0]["categorias_seguranca"] == ["corpo_estranho"] and alertas[0]["webhook_enviado"] is None
    assert len((pastas / "out" / "resultados.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert not (pastas / "inbox" / "novos.csv").exists() and (pastas / "processed" / "novos.csv").exists()


def test_arquivo_sem_coluna_review_fica_na_inbox(pastas):
    pd.DataFrame({"texto": ["x"]}).to_csv(pastas / "inbox" / "ruim.csv", index=False)
    pipeline.processar_inbox(assumir_sim=True)
    assert (pastas / "inbox" / "ruim.csv").exists()


def test_webhook_enviado_e_falha_nao_derruba(pastas, monkeypatch):
    enviados = []

    class RespostaFalsa:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen_falso(pedido, timeout):
        enviados.append(json.loads(pedido.data))
        return RespostaFalsa()

    monkeypatch.setattr(pipeline.urllib.request, "urlopen", urlopen_falso)
    alerta = pipeline.montar_alerta({"review_id": 9, "store_label": "S09", "city": "X", "rating_num": 1}, GRAVE, "f.csv")
    assert pipeline.enviar_webhook(alerta, "https://exemplo/webhook") is True
    assert "Caso grave" in enviados[0]["text"] and enviados[0]["alerta"]["review_id"] == 9

    def urlopen_quebrado(pedido, timeout):
        raise TimeoutError("fora do ar")
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", urlopen_quebrado)
    assert pipeline.enviar_webhook(alerta, "https://exemplo/webhook") is False


def test_criterio_de_caso_grave():
    assert pipeline.eh_caso_grave(GRAVE)
    assert not pipeline.eh_caso_grave(NORMAL)
    assert not pipeline.eh_caso_grave({**GRAVE, "status": "resposta_enviada"})
