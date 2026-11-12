"""Orquestração dos 4 agentes: classificador -> decisor -> redator -> revisor.

Fluxo de UMA review (processar_review):

    (a) classificador (LLM, prompt classificacao_<CLASSIFIER_PROMPT_VERSION>)
         + regra de segurança em código (seguranca.py): se casar, gravidade = alta
    (b) decisor (LLM) -> ignorar | responder | escalar
         + trava em código: gravidade alta ou alerta de segurança => SEMPRE escalar
    (c) redator (LLM) -> resposta ao cliente           (não roda se 'ignorar')
    (d) revisor (LLM + checagens em código) -> aprovado?
         reprovado -> redator reescreve 1x com o feedback -> revisor de novo
         reprovado de novo -> escala para humano

Status final: ignorado | resposta_enviada (simulado) | escalado_para_humano

Uso em lote:
    uv run python -m ifood.agentes --fonte gabarito              # 50 reviews rotuladas
    uv run python -m ifood.agentes --fonte amostra --limite 100  # parte da amostra
"""
import argparse
import json
import re

import pandas as pd

from ifood import config, llm
from ifood.avaliar_prompts import nome_arquivo_modelo
from ifood.gabarito import carregar_gabarito
from ifood.prompts import carregar_prompt
from ifood.schemas import Classificacao, Decisao, RespostaCliente, Revisao
from ifood.seguranca import verificar_seguranca

PASTA_SAIDA = config.OUTPUTS_DIR / "agentes"
MAX_PALAVRAS_RESPOSTA = 110
# Versões dos prompts dos agentes (histórico em prompts/; v1 -> v2 corrigiu problemas vistos no gabarito)
VERSAO_DECISOR, VERSAO_REDATOR, VERSAO_REVISOR = "v1", "v2", "v2"
# Assinatura fixa adicionada em código: o modelo esqueceu de assinar em 43 de 45 respostas.
ASSINATURA = "\n\n— The Restaurant Team"
# Promessas proibidas e menção à marca (checagem em código, complementa o revisor LLM).
# Obs.: "feel free" é permitido; "free meal/food" não.
PADRAO_PROMESSA = re.compile(r"\brefund|\bcoupon|\bvoucher|\bcompensat|\bfree (meal|food|item|burger|order|drink)",
                             re.IGNORECASE)
PADRAO_MARCA = re.compile(r"mc\s?donald", re.IGNORECASE)


def versao_classificador() -> str:
    return config.env("CLASSIFIER_PROMPT_VERSION", "v2")


# ---------------------------------------------------------------- agentes individuais
def agente_classificador(review: str, modelo: str | None = None) -> llm.ResultadoChamada:
    prompt = carregar_prompt("classificacao", versao_classificador())
    return llm.chamar_modelo(prompt, review, Classificacao, etapa="agente_classificador", modelo=modelo)


def agente_decisor(review: str, classificacao: dict, alertas: list, modelo: str | None = None) -> llm.ResultadoChamada:
    entrada = json.dumps({"review": review, "classificacao": classificacao,
                          "alertas_de_seguranca": alertas}, ensure_ascii=False, indent=1)
    return llm.chamar_modelo(carregar_prompt("decisor", VERSAO_DECISOR), entrada, Decisao, etapa="agente_decisor", modelo=modelo)


def agente_redator(review: str, classificacao: dict, acao: str, feedback: list[str] | None = None,
                   modelo: str | None = None) -> llm.ResultadoChamada:
    dados = {"review": review, "classificacao": classificacao, "acao": acao}
    if feedback:
        dados["correcoes_pedidas_pelo_revisor"] = feedback
    entrada = json.dumps(dados, ensure_ascii=False, indent=1)
    return llm.chamar_modelo(carregar_prompt("redator", VERSAO_REDATOR), entrada, RespostaCliente,
                             etapa="agente_redator", modelo=modelo)


def checagens_em_codigo(resposta: str) -> list[str]:
    """Regras objetivas que não dependem do revisor LLM."""
    problemas = []
    if len(resposta.split()) > MAX_PALAVRAS_RESPOSTA:
        problemas.append(f"Resposta longa demais ({len(resposta.split())} palavras).")
    if achado := PADRAO_PROMESSA.search(resposta):
        problemas.append(f"Promessa proibida: '{achado.group(0)}'.")
    if PADRAO_MARCA.search(resposta):
        problemas.append("Menciona a marca (o projeto não tem vínculo com ela).")
    return problemas


def agente_revisor(review: str, resposta: str, modelo: str | None = None) -> tuple[bool, list[str], llm.ResultadoChamada]:
    entrada = json.dumps({"review": review, "resposta_proposta": resposta}, ensure_ascii=False, indent=1)
    r = llm.chamar_modelo(carregar_prompt("revisor", VERSAO_REVISOR), entrada, Revisao, etapa="agente_revisor", modelo=modelo)
    problemas = checagens_em_codigo(resposta)
    if r.ok:
        problemas += r.dados["problemas"] if not r.dados["aprovado"] else []
        aprovado = r.dados["aprovado"] and not problemas
    else:
        problemas.append("Revisor não respondeu em formato válido.")
        aprovado = False  # na dúvida, não "envia"
    return aprovado, problemas, r


# ---------------------------------------------------------------- regras de decisão em código
def decisao_reserva(classificacao: dict) -> str:
    """Usada se o decisor LLM falhar: regra simples e conservadora."""
    if classificacao.get("gravidade") == "alta":
        return "escalar"
    if classificacao.get("tema") == "elogio_sem_problema":
        return "ignorar"
    return "responder"


# ---------------------------------------------------------------- pipeline de uma review
def processar_review(review: str, modelo: str | None = None) -> dict:
    chamadas: list[llm.ResultadoChamada] = []
    saida = {"alertas_seguranca": [], "trava_aplicada": False, "resposta": None,
             "revisao_aprovada": None, "problemas_revisao": [], "tentativas_redacao": 0}

    # (a) classificador + regra de segurança
    r = agente_classificador(review, modelo)
    chamadas.append(r)
    alertas = verificar_seguranca(review)
    saida["alertas_seguranca"] = alertas
    if not r.ok:
        # sem classificação confiável: humano decide
        saida.update(tema=None, sentimento=None, gravidade=None, resumo=None,
                     acao_decisor=None, acao_final="escalar", motivo="Falha na classificação.",
                     status="escalado_para_humano")
        return _finalizar(saida, chamadas)
    classificacao = dict(r.dados)
    if alertas and classificacao["gravidade"] != "alta":
        classificacao["gravidade"] = "alta"
        saida["trava_aplicada"] = True
    saida.update(classificacao)

    # (b) decisor + trava
    r = agente_decisor(review, classificacao, alertas, modelo)
    chamadas.append(r)
    if r.ok:
        acao, motivo = r.dados["acao"], r.dados["motivo"]
    else:
        acao, motivo = decisao_reserva(classificacao), "Decisor falhou; regra de reserva aplicada."
    saida["acao_decisor"] = acao if r.ok else None
    if (classificacao["gravidade"] == "alta" or alertas) and acao != "escalar":
        acao, saida["trava_aplicada"] = "escalar", True
        motivo += " | Trava: caso grave sempre escala para humano."
    saida["acao_final"], saida["motivo"] = acao, motivo

    if acao == "ignorar":
        saida["status"] = "ignorado"
        return _finalizar(saida, chamadas)

    # (c) redator + (d) revisor, com 1 reescrita
    feedback = None
    for _tentativa in range(2):
        r = agente_redator(review, classificacao, acao, feedback, modelo)
        chamadas.append(r)
        saida["tentativas_redacao"] += 1
        if not r.ok:
            feedback = ["Resposta anterior veio em formato inválido."]
            continue
        texto = r.dados["resposta"].strip()
        aprovado, problemas, rev = agente_revisor(review, texto, modelo)
        saida["resposta"] = texto + ASSINATURA
        chamadas.append(rev)
        saida["revisao_aprovada"], saida["problemas_revisao"] = aprovado, problemas
        if aprovado:
            break
        feedback = problemas

    if acao == "escalar":
        saida["status"] = "escalado_para_humano"  # resposta fica como rascunho para o gerente
    elif saida["revisao_aprovada"]:
        saida["status"] = "resposta_enviada"
    else:
        saida["acao_final"] = "escalar"
        saida["motivo"] += " | Resposta reprovada pelo revisor 2x: escalado."
        saida["status"] = "escalado_para_humano"
    return _finalizar(saida, chamadas)


def _finalizar(saida: dict, chamadas: list[llm.ResultadoChamada]) -> dict:
    custos = [c.custo_usd for c in chamadas]
    saida["n_chamadas"] = len(chamadas)
    saida["chamadas_novas"] = sum(not c.do_cache for c in chamadas)
    saida["custo_usd"] = None if any(c is None for c in custos) else round(sum(custos), 6)
    saida["latencia_s"] = round(sum(c.latencia_s for c in chamadas), 2)
    saida["falhas_json"] = sum(c.falhas_json for c in chamadas)
    return saida


# ---------------------------------------------------------------- lote
CHAMADAS_MEDIAS_POR_REVIEW = 3.5  # classif + decisor + (redator + revisor) na maioria + reescritas


def estimar_lote(reviews: list[str], modelo: str | None = None) -> dict:
    """Estimativa conservadora: usa o prompt mais longo dos 4 agentes."""
    prompts = [carregar_prompt("classificacao", versao_classificador()), carregar_prompt("decisor", VERSAO_DECISOR),
               carregar_prompt("redator", VERSAO_REDATOR), carregar_prompt("revisor", VERSAO_REVISOR)]
    tokens = max(llm.estimar_tokens_entrada(p, reviews, modelo) for p in prompts) + 120  # + JSON de contexto
    n = round(len(reviews) * CHAMADAS_MEDIAS_POR_REVIEW)
    return llm.estimar_custo(n, tokens, 120, modelo)


def carregar_fonte(fonte: str) -> pd.DataFrame:
    if fonte == "gabarito":
        g = carregar_gabarito()
        amostra = pd.read_csv(config.SAMPLE_PATH)
        return g.merge(amostra.drop(columns=["review", "rating_num", "store_label"]), on="review_id", how="left")
    if fonte == "amostra":
        return pd.read_csv(config.SAMPLE_PATH)
    raise ValueError("fonte deve ser 'gabarito' ou 'amostra'")


def processar_lote(df: pd.DataFrame, modelo: str | None = None) -> pd.DataFrame:
    linhas = []
    for i, item in enumerate(df.itertuples(), start=1):
        resultado = processar_review(item.review, modelo)
        base = {c: getattr(item, c) for c in ("review_id", "store_id", "store_label", "city", "state",
                                               "latitude", "longitude", "rating_num", "review",
                                               "tema_gabarito", "gravidade_gabarito") if hasattr(item, c)}
        linhas.append({**base, **resultado})
        print(f"  {i}/{len(df)} id={item.review_id} -> {resultado['status']}"
              f"{' [TRAVA]' if resultado['trava_aplicada'] else ''} ({resultado['chamadas_novas']} chamadas novas)",
              flush=True)
    return pd.DataFrame(linhas)


def principal() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fonte", choices=["gabarito", "amostra"], default="gabarito")
    parser.add_argument("--limite", type=int, help="processar só as N primeiras reviews")
    parser.add_argument("--modelo", help="sobrescreve MODEL_NAME do .env")
    parser.add_argument("--sim", action="store_true", help="não pedir confirmação")
    args = parser.parse_args()

    df = carregar_fonte(args.fonte)
    if args.limite:
        df = df.head(args.limite)
    if not llm.confirmar_execucao(estimar_lote(df["review"].tolist(), args.modelo), assumir_sim=args.sim):
        print("Cancelado.")
        return

    print(f"\nProcessando {len(df)} reviews (classificador {versao_classificador()})...")
    resultado = processar_lote(df, args.modelo)

    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    nome_modelo = nome_arquivo_modelo(llm.configuracao_modelo(args.modelo)["modelo"])
    sufixo = f"{args.fonte}_{nome_modelo}" + (f"_limite{args.limite}" if args.limite else "")
    resultado.to_json(PASTA_SAIDA / f"resultados_{sufixo}.jsonl", orient="records", lines=True, force_ascii=False)
    resultado.to_csv(PASTA_SAIDA / f"resultados_{sufixo}.csv", index=False, encoding="utf-8")

    print("\n=== Resultado ===")
    print(resultado["status"].value_counts().to_string())
    print(f"Travas de segurança aplicadas: {int(resultado['trava_aplicada'].sum())}")
    if "gravidade_gabarito" in df.columns:
        graves = df["gravidade_gabarito"].eq("alta").values
        escalados = resultado["status"].eq("escalado_para_humano").values
        print(f"Casos graves do gabarito escalados: {int((graves & escalados).sum())}/{int(graves.sum())}")
    llm.imprimir_resumo()


if __name__ == "__main__":
    principal()
