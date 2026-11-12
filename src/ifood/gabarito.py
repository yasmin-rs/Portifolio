"""Gabarito: planilha de reviews rotuladas à mão para medir a acurácia dos prompts.

Uso:
    uv run python -m ifood.gabarito          # gera data/gabarito.xlsx (não sobrescreve se já existir)

Depois de preenchida, `carregar_gabarito()` lê a planilha para a avaliação.
"""
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from ifood import config
from ifood.schemas import DEFINICOES_TEMAS, TEMAS

GABARITO_PATH = config.DATA_DIR / "gabarito.xlsx"
TAMANHO_GABARITO = 50
GRAVIDADES = ("baixa", "media", "alta")


def selecionar_reviews(tamanho: int = TAMANHO_GABARITO, seed: int = config.SEED) -> pd.DataFrame:
    """Mesmo número de reviews por nota (1 a 5): o gabarito precisa cobrir elogios E reclamações."""
    amostra = pd.read_csv(config.SAMPLE_PATH)
    por_nota = tamanho // 5
    partes = [amostra[amostra["rating_num"] == nota].sample(por_nota, random_state=seed) for nota in range(1, 6)]
    return pd.concat(partes).sample(frac=1, random_state=seed).reset_index(drop=True)


def gerar_planilha_gabarito(sobrescrever: bool = False) -> None:
    if GABARITO_PATH.exists() and not sobrescrever:
        print(f"{GABARITO_PATH} já existe; não sobrescrevi (seus rótulos estão seguros).")
        return

    reviews = selecionar_reviews()
    planilha = reviews[["review_id", "store_label", "rating_num", "review"]].copy()
    planilha["tema_gabarito"] = ""
    planilha["gravidade_gabarito"] = ""
    planilha["observacao"] = ""

    instrucoes = pd.DataFrame(
        [{"tema": t, "quando usar": d} for t, d in DEFINICOES_TEMAS.items()]
        + [{"tema": "", "quando usar": ""},
           {"tema": "REGRA", "quando usar": "Se a review tiver vários problemas, escolha o PRINCIPAL "
                                            "(o mais enfatizado / o que motivou a nota)."},
           {"tema": "REGRA", "quando usar": "Rotule pelo TEXTO, não pela nota (há reviews 5★ reclamando)."},
           {"tema": "GRAVIDADE", "quando usar": "Opcional. alta = saúde/segurança, discriminação, ameaça, "
                                                "cobrança indevida grave; media = problema real que estragou "
                                                "a experiência; baixa = incômodo leve ou sem problema."}]
    )

    with pd.ExcelWriter(GABARITO_PATH, engine="openpyxl") as escritor:
        planilha.to_excel(escritor, sheet_name="rotular", index=False)
        instrucoes.to_excel(escritor, sheet_name="instrucoes", index=False)

    _formatar(GABARITO_PATH, n_linhas=len(planilha))
    print(f"OK: {GABARITO_PATH} com {len(planilha)} reviews "
          f"({reviews['rating_num'].value_counts().sort_index().to_dict()} por nota).")


def _formatar(caminho, n_linhas: int) -> None:
    """Deixa a planilha fácil de preencher: texto quebrado, cabeçalho fixo, listas suspensas."""
    wb = load_workbook(caminho)
    ws = wb["rotular"]
    larguras = {"A": 10, "B": 34, "C": 8, "D": 90, "E": 24, "F": 18, "G": 30}
    for col, largura in larguras.items():
        ws.column_dimensions[col].width = largura
    destaque = PatternFill("solid", fgColor="FFF2CC")
    for linha in ws.iter_rows(min_row=2, max_row=n_linhas + 1):
        for celula in linha:
            celula.alignment = Alignment(wrap_text=True, vertical="top")
        for celula in linha[4:6]:  # colunas a preencher
            celula.fill = destaque
    for celula in ws[1]:
        celula.font = Font(bold=True)
    ws.freeze_panes = "E2"

    for coluna, opcoes in (("E", TEMAS), ("F", GRAVIDADES)):
        dv = DataValidation(type="list", formula1='"' + ",".join(opcoes) + '"', allow_blank=True)
        dv.error, dv.errorTitle = "Escolha um valor da lista.", "Valor inválido"
        ws.add_data_validation(dv)
        dv.add(f"{coluna}2:{coluna}{n_linhas + 1}")

    wi = wb["instrucoes"]
    wi.column_dimensions["A"].width = 24
    wi.column_dimensions["B"].width = 110
    for linha in wi.iter_rows():
        for celula in linha:
            celula.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(caminho)


def carregar_gabarito() -> pd.DataFrame:
    """Lê os rótulos preenchidos e valida os valores."""
    df = pd.read_excel(GABARITO_PATH, sheet_name="rotular")
    df["tema_gabarito"] = df["tema_gabarito"].fillna("").astype(str).str.strip()
    rotuladas = df[df["tema_gabarito"] != ""]
    invalidos = sorted(set(rotuladas["tema_gabarito"]) - set(TEMAS))
    if invalidos:
        raise ValueError(f"Temas inválidos no gabarito: {invalidos}. Use: {list(TEMAS)}")
    return rotuladas.reset_index(drop=True)


if __name__ == "__main__":
    gerar_planilha_gabarito()
