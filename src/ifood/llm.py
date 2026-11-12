"""Camada única de acesso a modelos (via litellm).

Regra do projeto: NENHUM outro módulo chama SDK de provedor ou litellm diretamente.
Tudo passa por `chamar_modelo()`, que cuida de:
  - ler modelo e parâmetros do .env (nenhum nome de modelo no código);
  - cache em disco (mesma entrada = sem nova chamada, custo zero);
  - retry de rede/API (litellm num_retries) e retry de formato JSON (validação Pydantic);
  - log por chamada em outputs/runs.jsonl (modelo, tokens, custo, latência, erros).

Também oferece `estimar_custo()` + `confirmar_execucao()` para usar ANTES de
qualquer execução em lote, e `resumo_execucao()` para mostrar o custo no final.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import litellm
from pydantic import BaseModel, ValidationError

from ifood import config

litellm.suppress_debug_info = True

CACHE_DIR = config.OUTPUTS_DIR / "cache"
LOG_PATH = config.OUTPUTS_DIR / "runs.jsonl"

# Identifica a execução atual (um processo = uma execução) para agrupar o log.
ID_EXECUCAO = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


# ---------------------------------------------------------------- configuração
def configuracao_modelo(modelo: str | None = None) -> dict:
    """Lê do .env tudo o que define uma chamada. `modelo` sobrescreve MODEL_NAME."""
    nome = modelo or config.env("MODEL_NAME")
    if not nome:
        raise RuntimeError("MODEL_NAME não definido no .env (veja .env.example).")
    return {
        "modelo": nome,
        "api_base": config.env("API_BASE") or None,
        "temperatura": float(config.env("TEMPERATURE", "0")),
        "max_tokens": int(config.env("MAX_TOKENS", "600")),
        "timeout": float(config.env("REQUEST_TIMEOUT", "120")),
        "max_retries_json": int(config.env("JSON_MAX_RETRIES", "2")),
        "retries_rede": int(config.env("NETWORK_RETRIES", "3")),
    }


def eh_modelo_local(modelo: str) -> bool:
    """Modelos servidos pelo Ollama rodam na máquina: custo zero."""
    return modelo.startswith(("ollama/", "ollama_chat/"))


def suporta_modo_json(modelo: str) -> bool:
    """Pergunta ao litellm se o provedor aceita response_format (JSON garantido)."""
    try:
        return "response_format" in (litellm.get_supported_openai_params(model=modelo) or [])
    except Exception:
        return False


# ---------------------------------------------------------------- resultado
@dataclass
class ResultadoChamada:
    """O que `chamar_modelo()` devolve (nunca levanta exceção em erro de modelo)."""
    ok: bool
    dados: dict | None = None     # JSON validado (se houve schema)
    texto: str = ""               # resposta bruta do modelo
    modelo: str = ""
    etapa: str = ""
    do_cache: bool = False
    tokens_entrada: int = 0
    tokens_saida: int = 0
    custo_usd: float | None = 0.0  # None = preço desconhecido para o modelo
    latencia_s: float = 0.0
    falhas_json: int = 0           # respostas com JSON inválido antes da válida
    erro: str | None = None
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- cache em disco
def _chave_cache(cfg: dict, mensagens: list, schema: type[BaseModel] | None) -> str:
    conteudo = json.dumps({
        "modelo": cfg["modelo"], "temperatura": cfg["temperatura"], "mensagens": mensagens,
        "schema": schema.model_json_schema() if schema else None,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def _ler_cache(chave: str) -> dict | None:
    caminho = CACHE_DIR / f"{chave}.json"
    if caminho.exists():
        try:
            return json.loads(caminho.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None  # cache corrompido: ignora e chama de novo
    return None


def _gravar_cache(chave: str, conteudo: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{chave}.json").write_text(json.dumps(conteudo, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- JSON
def extrair_json(texto: str) -> dict:
    """Aceita JSON puro, dentro de ```json ... ``` ou com texto em volta."""
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        inicio, fim = texto.find("{"), texto.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise
        return json.loads(texto[inicio:fim + 1])


# ---------------------------------------------------------------- log
def _registrar(resultado: ResultadoChamada) -> None:
    config.OUTPUTS_DIR.mkdir(exist_ok=True)
    linha = {"momento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "id_execucao": ID_EXECUCAO, **asdict(resultado)}
    for chave in ("dados", "texto", "extra"):  # log enxuto: sem o conteúdo das respostas
        linha.pop(chave)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(linha, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- chamada principal
def _custo(resposta, modelo: str) -> float | None:
    if eh_modelo_local(modelo):
        return 0.0
    try:
        return float(litellm.completion_cost(completion_response=resposta))
    except Exception:
        return None  # modelo sem preço cadastrado no litellm


def chamar_modelo(
    prompt_sistema: str,
    mensagem_usuario: str,
    schema: type[BaseModel] | None = None,
    etapa: str = "geral",
    modelo: str | None = None,
    usar_cache: bool = True,
) -> ResultadoChamada:
    """Chama o modelo configurado e (se `schema`) devolve JSON validado.

    - Se a resposta não validar no schema, reenvia com o erro como feedback
      (até JSON_MAX_RETRIES vezes) e conta as falhas de formato.
    - Erros de rede/API: o litellm tenta de novo (NETWORK_RETRIES); se ainda
      falhar, devolve ResultadoChamada(ok=False, erro=...) sem derrubar o lote.
    """
    cfg = configuracao_modelo(modelo)
    mensagens = [{"role": "system", "content": prompt_sistema},
                 {"role": "user", "content": mensagem_usuario}]
    resultado = ResultadoChamada(ok=False, modelo=cfg["modelo"], etapa=etapa)

    chave = _chave_cache(cfg, mensagens, schema)
    if usar_cache and (guardado := _ler_cache(chave)):
        resultado.ok, resultado.do_cache = True, True
        resultado.dados, resultado.texto = guardado.get("dados"), guardado.get("texto", "")
        resultado.custo_usd = 0.0
        _registrar(resultado)
        return resultado

    parametros = {
        "model": cfg["modelo"], "temperature": cfg["temperatura"], "max_tokens": cfg["max_tokens"],
        "timeout": cfg["timeout"], "num_retries": cfg["retries_rede"],
    }
    if cfg["api_base"]:
        parametros["api_base"] = cfg["api_base"]
    if schema and suporta_modo_json(cfg["modelo"]):
        parametros["response_format"] = {"type": "json_object"}

    conversa = list(mensagens)
    inicio = time.perf_counter()
    custo_total: float | None = 0.0
    try:
        for _tentativa in range(cfg["max_retries_json"] + 1):
            resposta = litellm.completion(messages=conversa, **parametros)
            texto = resposta.choices[0].message.content or ""
            uso = getattr(resposta, "usage", None)
            resultado.tokens_entrada += getattr(uso, "prompt_tokens", 0) or 0
            resultado.tokens_saida += getattr(uso, "completion_tokens", 0) or 0
            custo = _custo(resposta, cfg["modelo"])
            custo_total = None if (custo is None or custo_total is None) else custo_total + custo
            resultado.texto = texto

            if schema is None:
                resultado.ok = True
                break
            try:
                resultado.dados = schema.model_validate(extrair_json(texto)).model_dump()
                resultado.ok = True
                break
            except (json.JSONDecodeError, ValidationError) as e:
                resultado.falhas_json += 1
                resultado.erro = f"JSON inválido: {str(e)[:300]}"
                # devolve o erro ao modelo e pede só o JSON corrigido
                conversa += [
                    {"role": "assistant", "content": texto},
                    {"role": "user", "content": "Your previous answer was not valid for the required "
                                                f"JSON format. Error: {str(e)[:300]}\n"
                                                "Reply again with ONLY the corrected JSON object."},
                ]
    except Exception as e:  # rede, autenticação, timeout, limite de taxa...
        resultado.erro = f"{type(e).__name__}: {str(e)[:300]}"

    resultado.latencia_s = round(time.perf_counter() - inicio, 3)
    resultado.custo_usd = custo_total
    if resultado.ok:
        resultado.erro = None
        if usar_cache:
            _gravar_cache(chave, {"dados": resultado.dados, "texto": resultado.texto})
    _registrar(resultado)
    return resultado


# ---------------------------------------------------------------- controle de custo
def contar_tokens(texto: str, modelo: str | None = None) -> int:
    """Contagem aproximada de tokens (fallback: ~4 caracteres por token)."""
    try:
        return int(litellm.token_counter(model=modelo or config.env("MODEL_NAME"), text=texto))
    except Exception:
        return max(1, len(texto) // 4)


TOKENS_EXTRAS_POR_MENSAGEM = 10  # papéis, template de chat etc. (aproximação)


def estimar_tokens_entrada(prompt_sistema: str, textos: list[str], modelo: str | None = None) -> int:
    """Tokens de entrada MÉDIOS por chamada: prompt de sistema + texto médio + overhead."""
    if not textos:
        return 0
    prompt = contar_tokens(prompt_sistema, modelo)
    media_textos = sum(contar_tokens(t, modelo) for t in textos) / len(textos)
    return int(prompt + media_textos + 2 * TOKENS_EXTRAS_POR_MENSAGEM)


def estimar_custo(n_chamadas: int, tokens_entrada_medio: int, tokens_saida_medio: int,
                  modelo: str | None = None) -> dict:
    """Estimativa ANTES de rodar: nº de chamadas x tokens aproximados x preço."""
    nome = configuracao_modelo(modelo)["modelo"]
    entrada, saida = n_chamadas * tokens_entrada_medio, n_chamadas * tokens_saida_medio
    if eh_modelo_local(nome):
        custo = 0.0
    else:
        try:
            preco_in, preco_out = litellm.cost_per_token(model=nome, prompt_tokens=entrada,
                                                          completion_tokens=saida)
            custo = preco_in + preco_out
        except Exception:
            custo = None
    return {"modelo": nome, "local": eh_modelo_local(nome), "chamadas": n_chamadas,
            "tokens_entrada": entrada, "tokens_saida": saida, "custo_estimado_usd": custo}


def confirmar_execucao(estimativa: dict, assumir_sim: bool = False) -> bool:
    """Mostra a estimativa e, se o modelo for pago, pede confirmação no terminal."""
    custo = estimativa["custo_estimado_usd"]
    custo_txt = "desconhecido (modelo sem preço no litellm)" if custo is None else f"US$ {custo:.4f}"
    print("\n=== Estimativa de custo ===")
    print(f"Modelo:   {estimativa['modelo']}  ({'local, sem custo' if estimativa['local'] else 'API paga'})")
    print(f"Chamadas: {estimativa['chamadas']}  (sem contar retries; itens em cache não são cobrados)")
    print(f"Tokens:   ~{estimativa['tokens_entrada']:,} entrada + ~{estimativa['tokens_saida']:,} saída")
    print(f"Custo:    {custo_txt}")
    if estimativa["local"] or assumir_sim:
        return True
    return input("Confirmar execução? (s/N) ").strip().lower() in ("s", "sim", "y", "yes")


def resumo_execucao(id_execucao: str = ID_EXECUCAO) -> dict:
    """Agrega o log da execução por modelo: chamadas, cache, tokens, custo, latência, falhas."""
    if not LOG_PATH.exists():
        return {}
    linhas = [json.loads(l) for l in LOG_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    resumo: dict[str, dict] = {}
    for l in (l for l in linhas if l["id_execucao"] == id_execucao):
        r = resumo.setdefault(l["modelo"], {"chamadas": 0, "do_cache": 0, "erros": 0, "tokens_entrada": 0,
                                            "tokens_saida": 0, "custo_usd": 0.0, "latencia_total_s": 0.0,
                                            "respostas_json": 0, "falhas_json": 0})
        r["chamadas"] += 1
        r["do_cache"] += l["do_cache"]
        r["erros"] += 0 if l["ok"] else 1
        r["tokens_entrada"] += l["tokens_entrada"]
        r["tokens_saida"] += l["tokens_saida"]
        r["custo_usd"] = None if (r["custo_usd"] is None or l["custo_usd"] is None) else r["custo_usd"] + l["custo_usd"]
        r["latencia_total_s"] += l["latencia_s"]
        if not l["do_cache"]:
            r["respostas_json"] += l["falhas_json"] + (1 if l["ok"] else 0)
            r["falhas_json"] += l["falhas_json"]
    for r in resumo.values():
        novas = r["chamadas"] - r["do_cache"]
        r["latencia_media_s"] = round(r["latencia_total_s"] / novas, 2) if novas else 0.0
        r["taxa_falha_json"] = round(r["falhas_json"] / r["respostas_json"], 3) if r["respostas_json"] else 0.0
        del r["latencia_total_s"]
    return resumo


def imprimir_resumo(id_execucao: str = ID_EXECUCAO) -> None:
    """Controle de custo visível: chamar no fim de todo script que usa modelo."""
    print("\n=== Resumo da execução", id_execucao, "===")
    for modelo, r in resumo_execucao(id_execucao).items():
        custo = "desconhecido" if r["custo_usd"] is None else f"US$ {r['custo_usd']:.4f}"
        print(f"{modelo}: {r['chamadas']} chamadas ({r['do_cache']} do cache, {r['erros']} erros) | "
              f"tokens {r['tokens_entrada']}+{r['tokens_saida']} | custo {custo} | "
              f"latência média {r['latencia_media_s']}s | JSON inválido {r['taxa_falha_json']:.1%}")
