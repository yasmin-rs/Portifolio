"""Etapa 1: limpeza do CSV bruto e geração da amostra estratificada.

Não chama nenhum modelo. Uso:
    uv run python -m ifood.data_prep

Saídas:
    data/clean.parquet              base limpa (todas as reviews válidas)
    data/sample.csv                 amostra estratificada (SAMPLE_SIZE linhas)
    outputs/data_prep_report.json   números da limpeza (usados no README)
"""
import json
import re

import pandas as pd

from ifood import config

# Peso de cada nota na amostra: ênfase em notas baixas (onde estão as reclamações).
PESOS_POR_NOTA = {1: 0.35, 2: 0.22, 3: 0.17, 4: 0.13, 5: 0.13}
LIMITE_REVIEW_CURTA = 15   # abaixo disso a review é genérica ("Good", "Ok")
FRACAO_MAX_CURTAS = 0.05  # no máximo 5% da amostra
CIDADE_DESCONHECIDA = "Desconhecida"

FFFD = "�"  # caractere de substituição: emojis/apóstrofos perdidos na origem


# ---------------------------------------------------------------- leitura
def carregar_csv_bruto(path=config.RAW_CSV) -> tuple[pd.DataFrame, str]:
    """Tenta utf-8 estrito; se falhar, utf-8 com substituição.

    Inspeção mostrou que o arquivo é UTF-8, mas emojis e apóstrofos já vieram
    destruídos da origem (U+FFFD gravado no arquivo + bytes truncados).
    latin-1 'funciona' mas transforma cada U+FFFD em 'ï¿½', então não usamos.
    """
    try:
        return pd.read_csv(path, encoding="utf-8"), "utf-8"
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="utf-8", encoding_errors="replace"), "utf-8 (errors=replace)"


# ---------------------------------------------------------------- texto
def limpar_review(text: str) -> str:
    """Repara apóstrofos perdidos e remove restos de emoji."""
    if not isinstance(text, str):
        return ""
    # "Didn���t" -> "Didn't": sequência de U+FFFD entre letras era um apóstrofo tipográfico
    text = re.sub(rf"(?<=[A-Za-z]){FFFD}+(?=[A-Za-z])", "'", text)
    text = text.replace(FFFD, " ")             # demais sequências: emojis perdidos
    text = re.sub(r"[ \t]+", " ", text)         # espaços repetidos
    text = re.sub(r"\s*\n\s*", "\n", text)      # quebras de linha com espaço
    return text.strip()


def normalizar_para_dedup(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


# ---------------------------------------------------------------- tempo relativo
_TIME_RE = re.compile(r"^(a|an|\d+)\s+(hour|day|week|month|year)s?\s+ago$")
_MONTHS_PER_UNIT = {"hour": 0, "day": 1 / 30, "week": 0.25, "month": 1, "year": 12}


def faixa_de_tempo(value: str) -> str:
    """'3 years ago' -> '3-5 anos'. Sem datas exatas: só faixas aproximadas."""
    m = _TIME_RE.match(str(value).strip().lower())
    if not m:
        return "desconhecido"
    qty = 1 if m.group(1) in ("a", "an") else int(m.group(1))
    months = qty * _MONTHS_PER_UNIT[m.group(2)]
    if months < 1:
        return "< 1 mês"
    if months < 12:
        return "1-11 meses"
    if months < 36:
        return "1-2 anos"
    if months < 72:
        return "3-5 anos"
    return "6+ anos"


# ---------------------------------------------------------------- endereço
_ADDR_RE = re.compile(r",\s*([^,]+),\s*([A-Z]{2})\s+\d{5}")


def extrair_cidade_estado(address: str) -> tuple[str, str]:
    m = _ADDR_RE.search(str(address))
    return (m.group(1).strip(), m.group(2)) if m else (CIDADE_DESCONHECIDA, "??")


# ---------------------------------------------------------------- limpeza
def limpar(df: pd.DataFrame, report: dict) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]  # "latitude " tem espaço no final
    report["linhas_brutas"] = len(df)

    # category quase constante -> informar e descartar
    report["category_valores_unicos"] = df["category"].dropna().unique().tolist()
    if df["category"].nunique() <= 1:
        df = df.drop(columns=["category"])
    # store_name é sempre a mesma marca (com lixo de encoding em algumas linhas)
    df = df.drop(columns=["store_name"])

    # rating "4 stars" / "1 star" -> inteiro 1..5
    df["rating_num"] = pd.to_numeric(df["rating"].astype(str).str.extract(r"(\d)")[0], errors="coerce")
    df.loc[~df["rating_num"].between(1, 5), "rating_num"] = pd.NA
    df["rating_num"] = df["rating_num"].astype("Int64")
    report["rating_num_nulos"] = int(df["rating_num"].isna().sum())

    # review: reparar texto, marcar perda de emoji, remover vazias
    raw_review = df["review"].fillna("").astype(str)
    df["had_emoji_loss"] = raw_review.str.contains(FFFD, regex=False)
    df["review"] = raw_review.map(limpar_review)
    report["reviews_com_emoji_perdido"] = int(df["had_emoji_loss"].sum())
    empty = df["review"].eq("")
    report["reviews_vazias_removidas"] = int(empty.sum())
    df = df[~empty & df["rating_num"].notna()]

    # duplicatas: mesma loja + mesmo texto normalizado (decisão validada)
    key = df["store_address"].astype(str) + "||" + df["review"].map(normalizar_para_dedup)
    dup = key.duplicated(keep="first")
    report["duplicatas_removidas"] = int(dup.sum())
    df = df[~dup].copy()

    # tempo relativo -> faixa aproximada
    df["time_bucket"] = df["review_time"].map(faixa_de_tempo)

    # loja: cidade/estado; endereço corrompido vira "Desconhecida" (sem coordenadas)
    city_state = df["store_address"].map(extrair_cidade_estado)
    df["city"] = city_state.str[0]
    df["state"] = city_state.str[1]
    df.loc[df["city"].eq(CIDADE_DESCONHECIDA), "store_address"] = "Endereço corrompido na origem"
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    # rating_count é da LOJA (não da review); varia ±1..11 entre coletas -> usa o máximo
    rc = pd.to_numeric(df["rating_count"].astype(str).str.replace(",", "", regex=False), errors="coerce")
    df["store_rating_count"] = rc.groupby(df["store_address"]).transform("max").astype("Int64")
    df = df.drop(columns=["rating_count", "rating"])

    # identificador curto e legível da loja
    stores = sorted(df["store_address"].unique())
    store_ids = {addr: f"S{i + 1:02d}" for i, addr in enumerate(stores)}
    df["store_id"] = df["store_address"].map(store_ids)
    df["store_label"] = df["store_id"] + " · " + df["city"] + " · " + df["store_address"].str.split(",").str[0]

    df["review_len"] = df["review"].str.len()
    df["is_short"] = df["review_len"] < LIMITE_REVIEW_CURTA
    df = df.rename(columns={"reviewer_id": "review_id"})

    report["linhas_limpas"] = len(df)
    report["lojas"] = int(df["store_id"].nunique())
    report["cidades"] = int(df["city"].nunique())
    report["distribuicao_rating_limpo"] = {int(k): int(v) for k, v in df["rating_num"].value_counts().sort_index().items()}
    report["faixas_tempo"] = df["time_bucket"].value_counts().to_dict()
    report["reviews_curtas"] = int(df["is_short"].sum())

    cols = ["review_id", "store_id", "store_label", "store_address", "city", "state",
            "latitude", "longitude", "store_rating_count", "review_time", "time_bucket",
            "rating_num", "review", "review_len", "is_short", "had_emoji_loss"]
    return df[cols].reset_index(drop=True)


# ---------------------------------------------------------------- amostra
def alocar(total: int, weights: dict) -> dict:
    """Divide `total` pelos pesos garantindo que a soma bata exatamente."""
    raw = {k: total * w for k, w in weights.items()}
    alloc = {k: int(v) for k, v in raw.items()}
    leftover = total - sum(alloc.values())
    for k in sorted(raw, key=lambda k: raw[k] - alloc[k], reverse=True)[:leftover]:
        alloc[k] += 1
    return alloc


def amostra_estratificada(df: pd.DataFrame, size: int, seed: int, report: dict) -> pd.DataFrame:
    parts = []
    plan = {}
    for rating, n in alocar(size, PESOS_POR_NOTA).items():
        pool = df[df["rating_num"] == rating]
        n_short = round(n * FRACAO_MAX_CURTAS)
        short_pool, long_pool = pool[pool["is_short"]], pool[~pool["is_short"]]
        n_short = min(n_short, len(short_pool))
        n_long = min(n - n_short, len(long_pool))
        parts.append(short_pool.sample(n_short, random_state=seed))
        parts.append(long_pool.sample(n_long, random_state=seed))
        plan[rating] = {"pedido": n, "longas": n_long, "curtas": n_short}
    sample = pd.concat(parts).sample(frac=1, random_state=seed).reset_index(drop=True)
    report["amostra"] = {"tamanho": len(sample), "seed": seed, "por_nota": plan,
                         "curtas_total": int(sample["is_short"].sum()),
                         "lojas_cobertas": int(sample["store_id"].nunique())}
    return sample


def principal() -> None:
    config.DATA_DIR.mkdir(exist_ok=True)
    config.OUTPUTS_DIR.mkdir(exist_ok=True)
    report: dict = {}

    raw, encoding = carregar_csv_bruto()
    report["encoding_usado"] = encoding

    df = limpar(raw, report)
    df.to_parquet(config.CLEAN_PATH, index=False)

    sample = amostra_estratificada(df, config.SAMPLE_SIZE, config.SEED, report)
    sample.to_csv(config.SAMPLE_PATH, index=False, encoding="utf-8")

    report_path = config.OUTPUTS_DIR / "data_prep_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nOK: {config.CLEAN_PATH.name} ({len(df)} linhas), {config.SAMPLE_PATH.name} ({len(sample)} linhas)")


if __name__ == "__main__":
    principal()
