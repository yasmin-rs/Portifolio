"""Automação: simula a chegada de reviews novos e processa tudo sozinho.

Fluxo:
    data/inbox/*.csv  ->  limpeza  ->  4 agentes  ->  outputs/pipeline/resultados.jsonl
                                                   ->  casos graves: outputs/alerts.jsonl (+ webhook opcional)
    arquivo processado é movido para data/processed/

Uso:
    uv run python -m ifood.pipeline --simular 5            # cria um CSV com 5 reviews "novos" na inbox
    uv run python -m ifood.pipeline --simular 5 --com-grave   # idem, garantindo 1 caso grave
    uv run python -m ifood.pipeline                         # processa o que estiver na inbox e termina
    uv run python -m ifood.pipeline --observar --intervalo 60  # fica observando a pasta

O CSV de entrada precisa da coluna "review"; "review_id", "store_label"/"store_id",
"city" e "rating_num" são opcionais.
"""
import argparse
import json
import shutil
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ifood import agentes, config, llm
from ifood.data_prep import limpar_review
from ifood.seguranca import verificar_seguranca

PASTA_ENTRADA = config.DATA_DIR / "inbox"
PASTA_PROCESSADOS = config.DATA_DIR / "processed"
ARQUIVO_RESULTADOS = config.OUTPUTS_DIR / "pipeline" / "resultados.jsonl"
ARQUIVO_ALERTAS = config.OUTPUTS_DIR / "alerts.jsonl"


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- simulação de chegada
def simular_chegada(quantidade: int, com_grave: bool = False, seed: int | None = None) -> Path:
    """Sorteia reviews da base limpa que NÃO estão na amostra (são 'novos') e grava na inbox."""
    base = pd.read_parquet(config.CLEAN_PATH)
    amostra = set(pd.read_csv(config.SAMPLE_PATH, usecols=["review_id"])["review_id"])
    novos = base[~base["review_id"].isin(amostra) & ~base["is_short"]]
    seed = seed if seed is not None else int(time.time())
    escolhidos = novos.sample(quantidade, random_state=seed)
    if com_grave:
        graves = novos[novos["review"].map(lambda t: bool(verificar_seguranca(t)))]
        escolhidos = pd.concat([escolhidos.iloc[: quantidade - 1], graves.sample(1, random_state=seed)])
    PASTA_ENTRADA.mkdir(parents=True, exist_ok=True)
    caminho = PASTA_ENTRADA / f"reviews_{datetime.now():%Y%m%d_%H%M%S}.csv"
    escolhidos[["review_id", "store_id", "store_label", "city", "rating_num", "review"]].to_csv(
        caminho, index=False, encoding="utf-8")
    print(f"Simulado: {len(escolhidos)} reviews novos em {caminho.name}")
    return caminho


# ---------------------------------------------------------------- alertas
def eh_caso_grave(resultado: dict) -> bool:
    return resultado.get("status") == "escalado_para_humano" and (
        resultado.get("gravidade") == "alta" or bool(resultado.get("alertas_seguranca")))


def montar_alerta(linha: dict, resultado: dict, arquivo: str) -> dict:
    categorias = sorted({a["categoria"] for a in resultado.get("alertas_seguranca") or []})
    return {
        "momento": agora(), "arquivo": arquivo, "review_id": linha.get("review_id"),
        "loja": linha.get("store_label") or linha.get("store_id"), "cidade": linha.get("city"),
        "nota": linha.get("rating_num"), "tema": resultado.get("tema"), "gravidade": resultado.get("gravidade"),
        "categorias_seguranca": categorias, "motivo": resultado.get("motivo"), "resumo": resultado.get("resumo"),
        "rascunho_resposta": resultado.get("resposta"),
    }


def texto_do_alerta(alerta: dict) -> str:
    seg = f" | segurança: {', '.join(alerta['categorias_seguranca'])}" if alerta["categorias_seguranca"] else ""
    return (f"🚨 Caso grave — {alerta['loja']} ({alerta['cidade']}), nota {alerta['nota']}★\n"
            f"Tema: {alerta['tema']} | gravidade: {alerta['gravidade']}{seg}\n"
            f"Resumo: {alerta['resumo']}\nMotivo: {alerta['motivo']}")


def enviar_webhook(alerta: dict, url: str) -> bool:
    """POST JSON. Usa 'text' (Slack/Teams) e 'content' (Discord) para funcionar nos mais comuns."""
    corpo = json.dumps({"text": texto_do_alerta(alerta), "content": texto_do_alerta(alerta)[:1900],
                        "alerta": alerta}, ensure_ascii=False).encode("utf-8")
    pedido = urllib.request.Request(url, data=corpo, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(pedido, timeout=10) as resposta:
            return 200 <= resposta.status < 300
    except Exception as e:  # webhook fora do ar não pode derrubar o pipeline
        print(f"  ! webhook falhou: {type(e).__name__}: {e}")
        return False


def registrar_alerta(alerta: dict) -> None:
    ARQUIVO_ALERTAS.parent.mkdir(parents=True, exist_ok=True)
    url = config.env("ALERT_WEBHOOK_URL")
    alerta["webhook_enviado"] = enviar_webhook(alerta, url) if url else None
    with ARQUIVO_ALERTAS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(alerta, ensure_ascii=False, default=str) + "\n")
    print("  " + texto_do_alerta(alerta).replace("\n", "\n  "))


# ---------------------------------------------------------------- processamento
def processar_arquivo(caminho: Path, assumir_sim: bool = False, modelo: str | None = None) -> dict | None:
    df = pd.read_csv(caminho)
    if "review" not in df.columns:
        print(f"  ! {caminho.name}: falta a coluna 'review'; arquivo ignorado.")
        return None
    df["review"] = df["review"].map(limpar_review)
    df = df[df["review"] != ""]

    if not llm.confirmar_execucao(agentes.estimar_lote(df["review"].tolist(), modelo), assumir_sim=assumir_sim):
        print("  Cancelado; o arquivo continua na inbox.")
        return None

    contagem = {"reviews": 0, "alertas": 0}
    ARQUIVO_RESULTADOS.parent.mkdir(parents=True, exist_ok=True)
    with ARQUIVO_RESULTADOS.open("a", encoding="utf-8") as saida:
        for linha in df.to_dict("records"):
            try:
                resultado = agentes.processar_review(linha["review"], modelo)
            except llm.CotaEsgotada as e:
                print(f"  ! {e} O arquivo continua na inbox.")
                return None
            registro = {"momento": agora(), "arquivo": caminho.name,
                        **{k: v for k, v in linha.items() if k != "review"}, **resultado}
            saida.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
            contagem["reviews"] += 1
            print(f"  id={linha.get('review_id')} -> {resultado['status']}")
            if eh_caso_grave(resultado):
                registrar_alerta(montar_alerta(linha, resultado, caminho.name))
                contagem["alertas"] += 1

    PASTA_PROCESSADOS.mkdir(parents=True, exist_ok=True)
    shutil.move(str(caminho), PASTA_PROCESSADOS / caminho.name)
    return contagem


def processar_inbox(assumir_sim: bool = False, modelo: str | None = None) -> int:
    arquivos = sorted(PASTA_ENTRADA.glob("*.csv")) if PASTA_ENTRADA.exists() else []
    for caminho in arquivos:
        print(f"\n== {caminho.name} ==")
        contagem = processar_arquivo(caminho, assumir_sim, modelo)
        if contagem:
            print(f"  OK: {contagem['reviews']} reviews, {contagem['alertas']} alerta(s) grave(s).")
    return len(arquivos)


def principal() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--simular", type=int, metavar="N", help="cria um CSV com N reviews novos na inbox")
    parser.add_argument("--com-grave", action="store_true", help="na simulação, garante 1 caso grave")
    parser.add_argument("--observar", action="store_true", help="fica observando a inbox")
    parser.add_argument("--intervalo", type=int, default=60, help="segundos entre verificações (--observar)")
    parser.add_argument("--modelo", help="sobrescreve MODEL_NAME do .env")
    parser.add_argument("--sim", action="store_true", help="não pedir confirmação de custo")
    args = parser.parse_args()

    if args.simular:
        simular_chegada(args.simular, args.com_grave)
        if not args.observar:
            print("Rode sem --simular para processar a inbox.")
            return

    if args.observar:
        print(f"Observando {PASTA_ENTRADA} a cada {args.intervalo}s (Ctrl+C para parar)...")
        try:
            while True:
                if processar_inbox(args.sim, args.modelo):
                    llm.imprimir_resumo()
                time.sleep(args.intervalo)
        except KeyboardInterrupt:
            print("\nParado.")
    else:
        if processar_inbox(args.sim, args.modelo) == 0:
            print(f"Nada na inbox ({PASTA_ENTRADA}).")
        llm.imprimir_resumo()


if __name__ == "__main__":
    principal()
