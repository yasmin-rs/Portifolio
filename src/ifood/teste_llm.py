"""Teste rápido da camada de modelos: classifica 3 reviews da amostra.

Uso: uv run python -m ifood.teste_llm
(O prompt aqui é provisório; as versões oficiais ficam em prompts/.)
"""
import pandas as pd

from ifood import config, llm
from ifood.schemas import TEMAS, Classificacao

PROMPT_TESTE = (
    "You classify fast-food restaurant reviews. Reply ONLY with a JSON object with keys: "
    f"tema (one of {list(TEMAS)}), sentimento (positivo|neutro|negativo), "
    "gravidade (baixa|media|alta), resumo (one line in Portuguese)."
)


def escolher_reviews(n_por_nota: dict = {1: 1, 3: 1, 5: 1}) -> pd.DataFrame:
    amostra = pd.read_csv(config.SAMPLE_PATH)
    longas = amostra[~amostra["is_short"]]
    return pd.concat(longas[longas["rating_num"] == nota].head(n) for nota, n in n_por_nota.items())


def principal() -> None:
    reviews = escolher_reviews()
    tokens = llm.estimar_tokens_entrada(PROMPT_TESTE, reviews["review"].tolist())
    estimativa = llm.estimar_custo(len(reviews), tokens, 80)
    if not llm.confirmar_execucao(estimativa):
        return
    for _, linha in reviews.iterrows():
        r = llm.chamar_modelo(PROMPT_TESTE, linha["review"], Classificacao, etapa="teste")
        print(f"\n[{linha['rating_num']}★] {linha['review'][:110]!r}")
        print(f"  -> ok={r.ok} cache={r.do_cache} {r.latencia_s}s falhas_json={r.falhas_json}")
        print(f"  -> {r.dados if r.ok else r.erro}")
    llm.imprimir_resumo()


if __name__ == "__main__":
    principal()
