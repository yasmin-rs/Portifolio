"""Testes do orquestrador com agentes FALSOS (nenhuma chamada real)."""
import pytest

from ifood import agentes, llm
from ifood.seguranca import verificar_seguranca


def resultado(dados, ok=True):
    return llm.ResultadoChamada(ok=ok, dados=dados, modelo="ollama/falso")


@pytest.fixture
def falsos(monkeypatch):
    """Configura respostas falsas por agente; cada chave recebe uma lista consumida em ordem."""
    respostas = {}

    def falso_chamar(prompt_sistema, mensagem_usuario, schema=None, etapa="", modelo=None, usar_cache=True):
        return respostas[etapa].pop(0)

    monkeypatch.setattr(llm, "chamar_modelo", falso_chamar)
    return respostas


CLASSIF_MEDIA = {"tema": "demora", "sentimento": "negativo", "gravidade": "media", "resumo": "Fila longa."}
RESPOSTA_OK = {"resposta": "We are sorry about the long wait. We will share this with the store team. "
                            "Please contact us so we can look into it. The Restaurant Team"}


def test_caso_normal_responde_e_envia(falsos):
    falsos["agente_classificador"] = [resultado(CLASSIF_MEDIA)]
    falsos["agente_decisor"] = [resultado({"acao": "responder", "motivo": "Reclamação comum."})]
    falsos["agente_redator"] = [resultado(RESPOSTA_OK)]
    falsos["agente_revisor"] = [resultado({"aprovado": True, "problemas": []})]
    s = agentes.processar_review("Waited 30 minutes in line.")
    assert s["status"] == "resposta_enviada" and not s["trava_aplicada"] and s["n_chamadas"] == 4


def test_trava_escala_caso_grave_mesmo_se_decisor_discordar(falsos):
    falsos["agente_classificador"] = [resultado(CLASSIF_MEDIA)]  # modelo achou 'media'
    falsos["agente_decisor"] = [resultado({"acao": "responder", "motivo": "x"})]
    falsos["agente_redator"] = [resultado(RESPOSTA_OK)]
    falsos["agente_revisor"] = [resultado({"aprovado": True, "problemas": []})]
    s = agentes.processar_review("Slow service and a fruit fly in my orange juice.")
    assert s["gravidade"] == "alta" and s["trava_aplicada"]
    assert s["acao_final"] == "escalar" and s["status"] == "escalado_para_humano"


def test_ignorar_nao_chama_redator(falsos):
    falsos["agente_classificador"] = [resultado({**CLASSIF_MEDIA, "tema": "elogio_sem_problema",
                                                 "sentimento": "positivo", "gravidade": "baixa"})]
    falsos["agente_decisor"] = [resultado({"acao": "ignorar", "motivo": "Elogio genérico."})]
    s = agentes.processar_review("Good")
    assert s["status"] == "ignorado" and s["n_chamadas"] == 2


def test_revisor_reprova_duas_vezes_escala(falsos):
    falsos["agente_classificador"] = [resultado(CLASSIF_MEDIA)]
    falsos["agente_decisor"] = [resultado({"acao": "responder", "motivo": "x"})]
    falsos["agente_redator"] = [resultado(RESPOSTA_OK), resultado(RESPOSTA_OK)]
    falsos["agente_revisor"] = [resultado({"aprovado": False, "problemas": ["tom frio"]})] * 2
    s = agentes.processar_review("Waited 30 minutes.")
    assert s["tentativas_redacao"] == 2 and s["status"] == "escalado_para_humano"


def test_falha_do_classificador_escala(falsos):
    falsos["agente_classificador"] = [resultado(None, ok=False)]
    s = agentes.processar_review("???")
    assert s["status"] == "escalado_para_humano" and s["n_chamadas"] == 1


def test_decisor_falha_usa_regra_de_reserva(falsos):
    falsos["agente_classificador"] = [resultado({**CLASSIF_MEDIA, "tema": "elogio_sem_problema", "gravidade": "baixa"})]
    falsos["agente_decisor"] = [resultado(None, ok=False)]
    s = agentes.processar_review("Nice")
    assert s["acao_final"] == "ignorar" and "reserva" in s["motivo"]


def test_checagens_em_codigo_pegam_promessa_e_marca():
    assert agentes.checagens_em_codigo("Feel free to contact us. The Restaurant Team") == []
    assert any("Promessa" in p for p in agentes.checagens_em_codigo("We will send you a refund."))
    assert any("marca" in p for p in agentes.checagens_em_codigo("Thanks for visiting McDonald's!"))


@pytest.mark.parametrize("texto,categoria", [
    ("I got food poisoning", "saude"), ("hair in my burger", "corpo_estranho"),
    ("the cashier was racist", "discriminacao"), ("he threatened me", "violencia_ameaca"),
    ("people doing drugs in the bathroom", "seguranca_local"),
])
def test_regra_de_seguranca_detecta(texto, categoria):
    assert categoria in {a["categoria"] for a in verificar_seguranca(texto)}


@pytest.mark.parametrize("texto", ["great hospitality", "Mac attack Thursdays", "feel free", "sick of waiting? no"])
def test_regra_de_seguranca_sem_falso_positivo(texto):
    assert verificar_seguranca(texto) == []
