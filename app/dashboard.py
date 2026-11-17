"""Dashboard da central de operações de reviews.

Lê SOMENTE arquivos pré-calculados em outputs/dashboard/ (gerados por
`uv run python -m ifood.exportar_dashboard`). Não chama modelo por padrão:
o demo público não gera custo.

Rodar localmente:  uv run streamlit run app/dashboard.py
"""
import ast
import json
import os
from pathlib import Path

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

from consultas import EXEMPLOS, ROTULOS_TEMAS, interpretar, responder

PASTA_DADOS = Path(__file__).resolve().parents[1] / "outputs" / "dashboard"
MIN_REVIEWS_PADRAO = 3  # abaixo disso o percentual de uma loja é instável (1 review = 0% ou 100%)

# ---------------------------------------------------------------- identidade visual
# Tema visual vermelho: cada tema de reclamação tem UMA cor fixa em todo o dashboard, em tons quentes
# com claridades diferentes para continuar distinguível. "Elogio" é ausência de problema -> cinza neutro.
CORES_TEMA = {
    "atendimento": "#da291c", "demora": "#f08a1c", "limpeza": "#ffbc0d", "comida_errada_ou_fria": "#8f1810",
    "preco": "#f4a3a8", "pedido_incorreto": "#b45309", "outro": "#6b3a32", "elogio_sem_problema": "#a3a29c",
}
# Gravidade é um estado -> paleta de status, sempre com rótulo em texto.
# paleta dos gráficos: vermelho, amarelo e laranja
VERMELHO, AMARELO, LARANJA = "#da291c", "#ffbc0d", "#f08a1c"
RAMPA_QUENTE = ["#fff3c4", "#ffbc0d", "#f08a1c", "#da291c", "#8f1810"]  # pouco -> muito
CORES_GRAVIDADE = {"baixa": "#fab219", "media": "#ec835a", "alta": "#d03b3b"}
ROTULOS_GRAVIDADE = {"baixa": "Baixa", "media": "Média", "alta": "Alta"}
ROTULOS_STATUS = {"resposta_enviada": "Resposta enviada", "escalado_para_humano": "Escalado ao gerente",
                  "ignorado": "Ignorado"}
ROTULOS_ACAO = {"ignorar": "Ignorar", "responder": "Responder", "escalar": "Escalar"}

st.set_page_config(page_title="Central de reviews com IA", page_icon="🛰️", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&display=swap');
html, body, [class*="css"], .stMarkdown, .stText { font-family: 'Inter', sans-serif; }
/* textos internos do Streamlit sem tradução: esconde o inglês e escreve em português */
[data-testid$="Dropdown"] [role="option"] > span { font-size: 0; }
[data-testid$="Dropdown"] [role="option"] > span::after { content: "Nenhum resultado"; font-size: 0.9rem; }
[data-testid$="Dropdown"] [data-key="__select_all__"] > div { font-size: 0; }
[data-testid$="Dropdown"] [data-key="__select_all__"] > div::after { content: "Selecionar todos"; font-size: 0.9rem; }
[data-testid="InputInstructions"] > span:first-child { font-size: 0; }
[data-testid="InputInstructions"] > span:first-child::after { content: "Enter para aplicar"; font-size: 0.75rem; }
h1, h2, h3, .hero h1, .card .big { font-family: 'Space Grotesk', sans-serif !important; letter-spacing: -0.01em; }
.block-container { padding-top: 1.6rem; }
.hero { background: linear-gradient(120deg, #4a0707 0%, #8f1810 45%, #da291c 100%); color: #fff4f2;
        border-radius: 18px; padding: 28px 32px 22px; margin-bottom: 1rem; }
.hero h1 { color: #ffffff; font-size: 2.1rem; margin: 0 0 4px; }
.hero p { color: #fde0dc; margin: 0 0 18px; font-size: .95rem; }
.hero .nums { display: flex; flex-wrap: wrap; gap: 34px; }
.hero .num { font-family: 'Space Grotesk', sans-serif; font-size: 2.3rem; font-weight: 700; color: #fff; line-height: 1; }
.hero .lbl { font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; color: #f8c6bf; margin-top: 6px; }
.hero .accent { color: #ffbc0d; }
.card { background: #fff; border: 1px solid #e7e5df; border-radius: 14px; padding: 16px 18px; height: 100%;
        box-shadow: 0 1px 2px rgba(0,0,0,.04); }
.card .tag { font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: #6d6c66; }
.card .big { font-size: 1.55rem; font-weight: 700; margin: 4px 0 2px; color: #1b1b1a; }
.card .sub { font-size: .86rem; color: #52514e; }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 2px 10px; border-radius: 999px;
        font-size: .8rem; font-weight: 600; color: #1b1b1a; background: #f0efec; margin: 2px 4px 2px 0; }
.chip .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.step { background: #fff; border: 1px solid #e7e5df; border-top: 4px solid var(--c); border-radius: 12px;
        padding: 14px 16px; height: 100%; }
.step .n { font-family: 'Space Grotesk', sans-serif; font-weight: 700; color: var(--c); font-size: .8rem;
           text-transform: uppercase; letter-spacing: .08em; }
.step .t { font-weight: 600; margin: 2px 0 8px; }
.step .body { font-size: .9rem; color: #2d2d2b; }
/* caixas st.info (respostas geradas, avisos) no tom do tema, em vez do azul padrão */
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) { background-color: #fdecea !important; }
[data-testid="stAlertContentInfo"], [data-testid="stAlertContentInfo"] * { color: #6b1209 !important; }
/* código inline (modelo, arquivo) no tom do tema, em vez do verde padrão */
code { color: #8f1810 !important; background: #fdecea !important; border-radius: 4px; padding: 1px 5px; }
.fluxo { display: flex; flex-wrap: wrap; align-items: stretch; gap: 6px; margin: .4rem 0 .8rem; }
.fluxo .etapa { flex: 1 1 150px; background: #fff; border: 1px solid #f1d2cd; border-top: 4px solid var(--c);
                border-radius: 12px; padding: 12px 14px; }
.fluxo .etapa .quem { font-size: .7rem; text-transform: uppercase; letter-spacing: .08em; color: var(--c);
                      font-weight: 700; }
.fluxo .etapa b { display: block; font-family: 'Space Grotesk', sans-serif; font-size: 1.02rem; margin: 2px 0 4px; }
.fluxo .etapa span { font-size: .84rem; color: #52514e; }
.fluxo .seta { align-self: center; color: #da291c; font-size: 1.3rem; font-weight: 700; }
.saidas { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: .8rem; }
.saidas div { background: #fff; border: 1px solid #f1d2cd; border-radius: 999px; padding: 5px 14px; font-size: .85rem; }
.rodape { font-size: .82rem; color: #6d6c66; }
.aviso { background: #fff7e6; border: 1px solid #f5d38a; border-radius: 10px; padding: 8px 12px;
         font-size: .85rem; color: #5c4a14; margin-bottom: .6rem; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- dados
@st.cache_data
def carregar():
    reviews = pd.read_parquet(PASTA_DADOS / "reviews.parquet")
    lojas = pd.read_parquet(PASTA_DADOS / "lojas.parquet")
    meta = json.loads((PASTA_DADOS / "meta.json").read_text(encoding="utf-8"))
    caminho_q = PASTA_DADOS / "qualidade.csv"
    qualidade = pd.read_csv(caminho_q) if caminho_q.exists() else pd.DataFrame()
    caminho_m = PASTA_DADOS / "modelos.csv"
    modelos = pd.read_csv(caminho_m) if caminho_m.exists() else pd.DataFrame()
    caminho_c = PASTA_DADOS / "concordancia.csv"
    concordancia = pd.read_csv(caminho_c) if caminho_c.exists() else pd.DataFrame()
    reviews["tema_rotulo"] = reviews["tema"].map(ROTULOS_TEMAS).fillna("Sem classificação")
    reviews["gravidade_rotulo"] = reviews["gravidade"].map(ROTULOS_GRAVIDADE)
    reviews["status_rotulo"] = reviews["status"].map(ROTULOS_STATUS)
    reviews["tem_problema"] = reviews["tema"].notna() & reviews["tema"].ne("elogio_sem_problema")
    return reviews, lojas, meta, qualidade, modelos, concordancia


def configuracao(nome: str, padrao: str = "") -> str:
    """Lê de st.secrets (Streamlit Cloud) ou variável de ambiente."""
    try:
        if nome in st.secrets:
            return str(st.secrets[nome])
    except Exception:
        pass
    return os.getenv(nome, padrao)


def pct(x: float) -> str:
    return f"{x:.0%}"


def sem_travessao(texto: str) -> str:
    """Respostas geradas assinam "— The Restaurant Team": na tela, sem travessão."""
    return texto.replace(" — ", ", ").replace("\n— ", "\n").replace("— ", "").replace("—", "-")


def chip(texto: str, cor: str) -> str:
    return f'<span class="chip"><span class="dot" style="background:{cor}"></span>{texto}</span>'


def chip_tema(tema) -> str:
    return chip(ROTULOS_TEMAS.get(tema, "Sem tema"), CORES_TEMA.get(tema, "#a3a29c"))


def chip_gravidade(grav) -> str:
    return chip(f"Gravidade {ROTULOS_GRAVIDADE.get(grav, 'não definida').lower()}", CORES_GRAVIDADE.get(grav, "#a3a29c"))


def escala_temas(temas_presentes) -> alt.Scale:
    dominio = [ROTULOS_TEMAS[t] for t in CORES_TEMA if ROTULOS_TEMAS[t] in set(temas_presentes)]
    return alt.Scale(domain=dominio, range=[CORES_TEMA[t] for t in CORES_TEMA if ROTULOS_TEMAS[t] in dominio])


# ---------------------------------------------------------------- gráficos
def barras(df, categoria, valor, titulo_valor, tooltip, cor=VERMELHO, formato="d", altura_barra=28):
    """Barras horizontais ordenadas, com valor na ponta e tooltip. `cor` = hex ou alt.Color."""
    base = alt.Chart(df).encode(
        y=alt.Y(f"{categoria}:N", sort=alt.EncodingSortField(field=valor, op="max", order="descending"), title=None, axis=alt.Axis(labelLimit=320, labelFontSize=12)),
        x=alt.X(f"{valor}:Q", title=titulo_valor, axis=alt.Axis(format=formato, grid=True, tickCount=5)),
        tooltip=tooltip,
    )
    if isinstance(cor, str):
        marca = base.mark_bar(cornerRadiusEnd=4, height=altura_barra - 9, color=cor)
    else:
        marca = base.mark_bar(cornerRadiusEnd=4, height=altura_barra - 9).encode(color=cor)
    rotulo = base.mark_text(align="left", dx=5, fontWeight=600).encode(text=alt.Text(f"{valor}:Q", format=formato))
    return (marca + rotulo).properties(height=max(120, altura_barra * len(df)))


def desenhar(grafico, onde=None, **opcoes) -> None:
    """Mostra o gráfico com a altura dele: sem isso o Streamlit achata gráficos de poucas linhas (barras encavaladas)."""
    altura = grafico.to_dict().get("height")
    (onde or st).altair_chart(grafico, height=altura + 70 if isinstance(altura, int) else "content", **opcoes)


def mapa_de_calor(reviews: pd.DataFrame, max_cidades: int = 12) -> alt.Chart:
    """Cidades (linhas) x temas de problema (colunas): onde cada problema se concentra."""
    problemas = reviews[reviews["tem_problema"]]
    principais = problemas["city"].value_counts().head(max_cidades).index
    dados = (problemas[problemas["city"].isin(principais)].groupby(["city", "tema_rotulo"]).size()
             .rename("casos").reset_index())
    ordem_temas = [ROTULOS_TEMAS[t] for t in CORES_TEMA if t != "elogio_sem_problema"]
    base = alt.Chart(dados).encode(
        x=alt.X("tema_rotulo:N", sort=ordem_temas, title=None, axis=alt.Axis(labelAngle=-30, labelLimit=160)),
        y=alt.Y("city:N", sort=list(principais), title=None),
        tooltip=[alt.Tooltip("city:N", title="Cidade"), alt.Tooltip("tema_rotulo:N", title="Tema"),
                 alt.Tooltip("casos:Q", title="Reclamações")],
    )
    celulas = base.mark_rect(cornerRadius=4, stroke="#fffaf0", strokeWidth=2).encode(
        color=alt.Color("casos:Q", title="Reclamações",
                        scale=alt.Scale(range=RAMPA_QUENTE), legend=alt.Legend(orient="bottom")))
    texto = base.mark_text(fontWeight=600).encode(
        text="casos:Q",
        color=alt.condition(alt.datum.casos >= dados["casos"].max() * 0.65, alt.value("white"), alt.value("#1b1b1a")))
    return (celulas + texto).properties(height=max(160, 30 * len(principais)))


# ---------------------------------------------------------------- dados + filtros
reviews_todas, lojas, meta, qualidade, modelos, concordancia = carregar()

# Período: o dataset só traz a idade relativa da review ("há 3 anos"), não a data exata.
ORDEM_PERIODOS = ["6+ anos", "3-5 anos", "1-2 anos", "1-11 meses", "< 1 mês"]  # do mais antigo ao mais recente
FILTROS = {"f_periodos": [], "f_estados": [], "f_cidades": [], "f_notas": (1, 5), "f_temas": []}
for chave, padrao in FILTROS.items():
    st.session_state.setdefault(chave, padrao)


st.session_state.setdefault("versao_filtros", 0)


def limpar_filtros() -> None:
    for chave, padrao in FILTROS.items():
        st.session_state[chave] = padrao
    st.session_state["versao_filtros"] += 1  # recria o controle de notas já na faixa padrão


def ao_clicar_tema() -> None:
    """Clique numa barra de tema filtra o dashboard inteiro; clique no vazio limpa."""
    pontos = st.session_state["grafico_temas"].selection.get("sel_tema", [])
    st.session_state["f_temas"] = [p["tema_rotulo"] for p in pontos]


with st.container(border=True):
    f0, f1, f2, f3, f4, f5 = st.columns([2.6, 1.2, 1.6, 1.6, 1.3, 0.7], vertical_alignment="bottom")
    f0.pills("Período (idade da review)", [p for p in ORDEM_PERIODOS if p in set(reviews_todas["time_bucket"])],
             selection_mode="multi", key="f_periodos")
    f1.multiselect("Estado", sorted(reviews_todas["state"].dropna().unique()), key="f_estados", placeholder="Todos",
                   format_func=lambda uf: "Não informado" if uf == "??" else uf)
    base_cidades = (reviews_todas[reviews_todas["state"].isin(st.session_state["f_estados"])]
                    if st.session_state["f_estados"] else reviews_todas)
    f2.multiselect("Cidade", sorted(base_cidades["city"].dropna().unique()), key="f_cidades", placeholder="Todas")
    f3.multiselect("Tema", [ROTULOS_TEMAS[t] for t in CORES_TEMA], key="f_temas", placeholder="Todos")
    st.session_state["f_notas"] = f4.select_slider("Nota (estrelas)", options=[1, 2, 3, 4, 5],
                                                   value=tuple(st.session_state["f_notas"]),
                                                   key=f"w_notas_{st.session_state['versao_filtros']}")
    f5.button("Limpar", on_click=limpar_filtros, width="stretch", help="Remove todos os filtros")

# sem_tema = filtros de tudo, MENOS o tema: usado no gráfico de temas (para dar para trocar de tema clicando)
faixa_notas = st.session_state["f_notas"]
faixa_notas = tuple(faixa_notas) if isinstance(faixa_notas, (list, tuple)) else (faixa_notas, faixa_notas)
sem_tema = reviews_todas[reviews_todas["rating_num"].between(*faixa_notas)]
if st.session_state["f_periodos"]:
    sem_tema = sem_tema[sem_tema["time_bucket"].isin(st.session_state["f_periodos"])]
if st.session_state["f_estados"]:
    sem_tema = sem_tema[sem_tema["state"].isin(st.session_state["f_estados"])]
if st.session_state["f_cidades"]:
    sem_tema = sem_tema[sem_tema["city"].isin(st.session_state["f_cidades"])]
reviews = sem_tema[sem_tema["tema_rotulo"].isin(st.session_state["f_temas"])] if st.session_state["f_temas"] else sem_tema
if reviews.empty:
    st.warning("Nenhuma review com esses filtros.")
    st.button("Limpar filtros", on_click=limpar_filtros, key="limpar_vazio")
    st.stop()

# ---------------------------------------------------------------- hero
n = len(reviews)
graves = int(reviews["gravidade"].eq("alta").sum())
escaladas = int(reviews["status"].eq("escalado_para_humano").sum())
automatico = reviews["status"].isin(["resposta_enviada", "ignorado"]).mean()
filtrado = n < len(reviews_todas)
st.markdown(f"""
<div class="hero">
  <h1>Central de operações de reviews <span class="accent">com IA</span></h1>
  <p>Agentes de IA leem avaliações de clientes, classificam o problema, decidem a ação e escrevem a resposta.
     Casos graves sempre chegam a um humano.{' <b>Filtros ativos: os números refletem a seleção.</b>' if filtrado else ''}</p>
  <div class="nums">
    <div><div class="num">{n:,}</div><div class="lbl">reviews analisadas</div></div>
    <div><div class="num">{pct(reviews['tem_problema'].mean())}</div><div class="lbl">relatam um problema</div></div>
    <div><div class="num">{pct(automatico)}</div><div class="lbl">resolvidas sem humano</div></div>
    <div><div class="num accent">{escaladas}</div><div class="lbl">escaladas ao gerente</div></div>
    <div><div class="num">{graves}</div><div class="lbl">casos graves</div></div>
  </div>
</div>
""".replace(",", "."), unsafe_allow_html=True)

if n < 30:
    st.markdown(f'<div class="aviso">⚠️ Seleção com apenas {n} reviews: percentuais por loja e cidade ficam '
                'instáveis. Amplie os filtros para conclusões mais firmes.</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------- destaques
problemas = reviews[reviews["tem_problema"]]
d1, d2, d3, d4 = st.columns(4)
if not problemas.empty:
    tema_top = problemas["tema"].value_counts()
    t = tema_top.index[0]
    d1.markdown(f"""<div class="card"><div class="tag">Problema nº 1</div>
        <div class="big">{ROTULOS_TEMAS[t]}</div>
        <div class="sub">{pct(tema_top.iloc[0] / len(problemas))} das reclamações {chip('', CORES_TEMA[t])}</div></div>""",
                unsafe_allow_html=True)
    por_cidade = reviews.groupby("city").agg(n=("tem_problema", "size"), p=("tem_problema", "mean"))
    por_cidade = por_cidade[por_cidade["n"] >= MIN_REVIEWS_PADRAO]
    if not por_cidade.empty:
        cid = por_cidade.sort_values(["p", "n"], ascending=False).iloc[0]
        d2.markdown(f"""<div class="card"><div class="tag">Cidade mais crítica</div>
            <div class="big">{cid.name}</div>
            <div class="sub">{pct(cid['p'])} das reviews relatam problema (n={int(cid['n'])})</div></div>""",
                    unsafe_allow_html=True)
else:
    d1.markdown('<div class="card"><div class="tag">Problema nº 1</div><div class="big">Nenhum</div>'
                '<div class="sub">Sem reclamações nesta seleção.</div></div>', unsafe_allow_html=True)
travas = int(reviews["trava_aplicada"].sum())
d3.markdown(f"""<div class="card"><div class="tag">Rede de segurança</div>
    <div class="big">{graves} casos graves</div>
    <div class="sub">{travas} detectado(s) só pela regra em código: o modelo tinha classificado como não grave</div></div>""",
            unsafe_allow_html=True)
custo = meta.get("custo_total_usd")
d4.markdown(f"""<div class="card"><div class="tag">Custo da análise</div>
    <div class="big">{'n/d' if custo is None else f'US$ {custo:.2f}'}</div>
    <div class="sub">{meta['chamadas_llm']} chamadas ao modelo <code>{meta['modelo']}</code></div></div>""",
            unsafe_allow_html=True)
st.write("")

# ---------------------------------------------------------------- impacto: horas economizadas
# Estimativa, não medição: a premissa (minutos por review) fica visível e ajustável.
# Sem IA, uma pessoa trata TODAS as reviews; com IA, só as escaladas continuam com humano.
h1, h2 = st.columns([3, 1], gap="large", vertical_alignment="center")
minutos = h2.slider("Premissa: minutos de trabalho manual por review", 1, 10, 3,
                    help="Ler, classificar, decidir o que fazer e escrever a resposta.")
horas_sem_ia = n * minutos / 60
horas_com_ia = escaladas * minutos / 60
economia = horas_sem_ia - horas_com_ia


def num_br(x: float, casas: int = 0) -> str:
    """1234.5 -> '1.234,5' (formato brasileiro)."""
    return f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


h1.markdown(f"""<div class="card" style="border-left: 5px solid #da291c">
    <div class="tag">Horas economizadas (estimativa)</div>
    <div class="big">≈ {num_br(economia)} h de {num_br(horas_sem_ia)} h · {pct(economia / horas_sem_ia)} do trabalho manual</div>
    <div class="sub">Sem IA: {num_br(n)} reviews × {minutos} min = {num_br(horas_sem_ia, 1)} h de uma pessoa.
    Com IA: só as {escaladas} escaladas ainda passam por alguém ({num_br(horas_com_ia, 1)} h).
    O resto é triado e respondido sem ninguém operando.</div></div>""",
            unsafe_allow_html=True)
st.write("")

aba_geral, aba_agentes, aba_mapa, aba_ranking, aba_fila, aba_pergunta, aba_ia = st.tabs(
    ["📊 Visão geral", "🤖 Por dentro dos agentes", "🗺️ Mapa", "🏆 Lojas e cidades", "🚨 Casos escalados",
     "💬 Pergunte aos dados", "🧪 Qualidade da IA"])


def painel(titulo: str, valor: str, sub: str, cor: str = "#8f1810") -> str:
    return (f'<div class="card" style="border-left:5px solid {cor}; margin-bottom:12px"><div class="tag">{titulo}</div>'
            f'<div class="big">{valor}</div><div class="sub">{sub}</div></div>')


# ---------------------------------------------------------------- visão geral
with aba_geral:
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        st.subheader("Do que os clientes reclamam")
        st.caption("Clique numa barra para filtrar o dashboard inteiro por esse tema; clique no vazio para limpar.")
        temas = sem_tema.groupby(["tema", "tema_rotulo"]).size().rename("reviews").reset_index()
        temas["percentual"] = temas["reviews"] / len(sem_tema)
        escolhidos = st.session_state["f_temas"]
        temas["opacidade"] = [1.0 if not escolhidos or r in escolhidos else 0.25 for r in temas["tema_rotulo"]]
        sel = alt.selection_point(name="sel_tema", fields=["tema_rotulo"])
        cor = alt.Color("tema_rotulo:N", legend=None, scale=escala_temas(temas["tema_rotulo"]))
        grafico = barras(temas, "tema_rotulo", "reviews", "Reviews",
                         [alt.Tooltip("tema_rotulo:N", title="Tema"), alt.Tooltip("reviews:Q", title="Reviews"),
                          alt.Tooltip("percentual:Q", format=".0%", title="% das reviews")], cor=cor, altura_barra=36)
        grafico = grafico.encode(opacity=alt.Opacity("opacidade:Q", legend=None, scale=None)).add_params(sel)
        st.altair_chart(grafico, width="stretch", on_select=ao_clicar_tema, key="grafico_temas",
                        selection_mode="sel_tema")
    with c2:
        st.subheader("Gravidade")
        grav = reviews.groupby(["gravidade", "gravidade_rotulo"]).size().rename("reviews").reset_index()
        cor_g = alt.Color("gravidade:N", legend=None,
                          scale=alt.Scale(domain=list(CORES_GRAVIDADE), range=list(CORES_GRAVIDADE.values())))
        desenhar(barras(grav, "gravidade_rotulo", "reviews", "Reviews",
                               [alt.Tooltip("gravidade_rotulo:N", title="Gravidade"), "reviews:Q"], cor=cor_g,
                               altura_barra=34), width="stretch")
        st.subheader("O que os agentes fizeram")
        status = reviews.groupby("status_rotulo").size().rename("reviews").reset_index()
        desenhar(barras(status, "status_rotulo", "reviews", "Reviews",
                               [alt.Tooltip("status_rotulo:N", title="Status"), "reviews:Q"], cor=VERMELHO,
                               altura_barra=34), width="stretch")

    e1, e2 = st.columns([3, 2], gap="large")
    with e1:
        st.subheader("Como as reclamações mudam com o tempo")
        st.caption("Composição das reclamações em cada período (idade da review).")
        tempo = (reviews[reviews["tem_problema"]].groupby(["time_bucket", "tema_rotulo"]).size()
                 .rename("casos").reset_index())
        if tempo.empty:
            st.info("Sem reclamações nesta seleção.")
        else:
            tempo["total"] = tempo.groupby("time_bucket")["casos"].transform("sum")
            tempo["parte"] = tempo["casos"] / tempo["total"]
            periodos = [p for p in ORDEM_PERIODOS if p in set(tempo["time_bucket"])]
            evolucao = alt.Chart(tempo).mark_bar(stroke="white", strokeWidth=2, cornerRadius=2).encode(
                x=alt.X("time_bucket:N", sort=periodos, title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("casos:Q", stack="normalize", title=None, axis=alt.Axis(format=".0%", tickCount=4)),
                color=alt.Color("tema_rotulo:N", title=None, scale=escala_temas(tempo["tema_rotulo"]),
                                legend=alt.Legend(orient="bottom", columns=4)),
                order=alt.Order("tema_rotulo:N"),
                tooltip=[alt.Tooltip("time_bucket:N", title="Período"), alt.Tooltip("tema_rotulo:N", title="Tema"),
                         alt.Tooltip("casos:Q", title="Reclamações"), alt.Tooltip("parte:Q", format=".0%", title="% do período"),
                         alt.Tooltip("total:Q", title="Reclamações no período")])
            st.altair_chart(evolucao.properties(height=300), width="stretch")
    with e2:
        st.subheader("Distribuição das notas")
        st.caption("Quantas reviews em cada nota, e quantas delas relatam problema.")
        notas_df = reviews.groupby("rating_num").agg(reviews=("review_id", "size"),
                                                      com_problema=("tem_problema", "sum")).reset_index()
        notas_df["estrelas"] = notas_df["rating_num"].map(lambda x: "★" * int(x))
        notas_df["pct"] = notas_df["com_problema"] / notas_df["reviews"]
        base_n = alt.Chart(notas_df).encode(
            x=alt.X("estrelas:N", sort=[("★" * i) for i in range(1, 6)], title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("reviews:Q", title=None, axis=alt.Axis(tickCount=4)),
            tooltip=[alt.Tooltip("rating_num:Q", title="Nota"), alt.Tooltip("reviews:Q", title="Reviews"),
                     alt.Tooltip("pct:Q", format=".0%", title="Relatam problema")])
        st.altair_chart((base_n.mark_bar(cornerRadiusEnd=4, size=38, color=AMARELO)
                         + base_n.mark_text(dy=-8, fontWeight=600).encode(text="reviews:Q")).properties(height=300),
                        width="stretch")

    st.subheader("Onde cada problema se concentra")
    st.caption("Cidades com mais reclamações × tipo de problema. Passe o mouse para ver os números.")
    if problemas.empty:
        st.info("Sem reclamações com esses filtros.")
    else:
        desenhar(mapa_de_calor(reviews), width="stretch")

# ---------------------------------------------------------------- por dentro dos agentes
with aba_agentes:
    candidatos = reviews.copy()
    candidatos["prioridade"] = (candidatos["trava_aplicada"].astype(int) * 4
                                + candidatos["gravidade"].eq("alta").astype(int) * 2
                                + candidatos["tentativas_redacao"].fillna(0).gt(1).astype(int))
    candidatos = candidatos.sort_values("prioridade", ascending=False)
    opcoes = {f"#{r.review_id} · {r.tema_rotulo} · {r.rating_num}★ · {r.store_label}": r.review_id
              for r in candidatos.itertuples()}
    a1, a2 = st.columns([1, 2.4], gap="large")
    with a1:
        st.markdown("Escolha uma review e veja **cada decisão da IA**, passo a passo.")
        escolha = st.selectbox("Review", list(opcoes), help="As primeiras da lista são os casos mais interessantes.")
        caso = candidatos[candidatos["review_id"] == opcoes[escolha]].iloc[0]
        status_txt = ROTULOS_STATUS.get(caso["status"], "sem status")
        st.markdown(painel("Loja", str(caso["store_label"]), f"{caso['city']}, {caso['state']} · nota {caso['rating_num']}★"),
                    unsafe_allow_html=True)
        st.markdown(painel("Resultado", status_txt,
                           f"{int(caso['n_chamadas'] or 0)} chamadas ao modelo nesta review",
                           "#da291c" if caso["status"] == "escalado_para_humano" else "#ffbc0d"), unsafe_allow_html=True)
        st.caption("O texto original não é exibido (licença do dataset); o resumo foi escrito pelo agente classificador.")
    with a2:
        s1, s2 = st.columns(2, gap="small")
        s1.markdown(f"""<div class="step" style="--c:#c2410c"><div class="n">① Classificador</div>
            <div class="t">O que o cliente relatou</div><div class="body">
            {chip_tema(caso['tema'])}{chip_gravidade(caso['gravidade'])}
            {chip('Sentimento ' + str(caso['sentimento'] or 'não definido'), '#6d6c66')}
            <p style="margin-top:8px">“{caso['resumo'] or 'sem resumo'}”</p></div></div>""", unsafe_allow_html=True)
        alerta = caso["categorias_alerta"] or ""
        s2.markdown(f"""<div class="step" style="--c:#da291c"><div class="n">② Regra de segurança</div>
            <div class="t">Verificação em código (sem IA)</div><div class="body">
            {('🛡️ <b>Alerta:</b> ' + alerta.replace('_', ' ') + '<br/>Gravidade forçada para alta.') if alerta
             else '✅ Nenhum termo de saúde, segurança, discriminação ou ameaça.'}
            </div></div>""", unsafe_allow_html=True)
        st.write("")
        s3, s4 = st.columns(2, gap="small")
        acao_ia = ROTULOS_ACAO.get(caso["acao_decisor"], "nenhuma")
        acao_final = ROTULOS_ACAO.get(caso["acao_final"], "nenhuma")
        trava = (f"<br/><b>🔒 Trava aplicada:</b> a IA sugeriu <i>{acao_ia}</i>, o código forçou <i>{acao_final}</i>."
                 if caso["trava_aplicada"] and acao_ia != acao_final else "")
        s3.markdown(f"""<div class="step" style="--c:#8f1810"><div class="n">③ Decisor</div>
            <div class="t">Ação: {acao_final}</div><div class="body">{caso['motivo']}{trava}</div></div>""",
                    unsafe_allow_html=True)
        try:
            problemas_rev = (ast.literal_eval(caso["problemas_revisao"])
                             if isinstance(caso["problemas_revisao"], str) else [])
        except (ValueError, SyntaxError):
            problemas_rev = []
        if isinstance(caso["resposta"], str):
            aprov = "✅ Aprovada pelo revisor" if caso["revisao_aprovada"] else "❌ Reprovada pelo revisor"
            tent = int(caso["tentativas_redacao"] or 0)
            corpo = (f"<b>{aprov}</b> · {tent} tentativa(s)"
                     + (f"<br/><small>Apontamentos: {'; '.join(problemas_rev)}</small>" if problemas_rev else ""))
        else:
            corpo = "Nenhuma resposta: review ignorada (elogio genérico ou sem conteúdo)."
        s4.markdown(f"""<div class="step" style="--c:#b45309"><div class="n">④ Redator + revisor</div>
            <div class="t">Resposta ao cliente</div><div class="body">{corpo}</div></div>""", unsafe_allow_html=True)
        st.write("")
        if isinstance(caso["resposta"], str):
            st.markdown("**Resposta gerada**" + (" (rascunho para o gerente)" if caso["status"] == "escalado_para_humano"
                                                  else " (enviada automaticamente, simulado)"))
            st.info(sem_travessao(caso["resposta"]))

# ---------------------------------------------------------------- mapa
with aba_mapa:
    por_loja = reviews.groupby("store_id").agg(analisadas=("review_id", "size"),
                                               com_problema=("tem_problema", "sum"),
                                               graves=("gravidade", lambda s: int((s == "alta").sum()))).reset_index()
    mapa = lojas.merge(por_loja, on="store_id", how="inner").dropna(subset=["latitude", "longitude"])
    mapa["pct_problema"] = mapa["com_problema"] / mapa["analisadas"]
    mp1, mp2 = st.columns([2.3, 1], gap="large")
    with mp2:
        minimo_mapa = st.slider("Colorir só lojas com pelo menos N reviews analisadas", 1, 10, MIN_REVIEWS_PADRAO)
    confiavel = mapa["analisadas"] >= minimo_mapa
    # cor sequencial (um verde, claro -> escuro) pelo % com problema; amostra pequena = cinza
    apagado, intenso = (190, 232, 190), (18, 110, 50)
    mapa["cor"] = [
        [round(a + (b - a) * p) for a, b in zip(apagado, intenso)] + [235] if ok else [150, 150, 150, 160]
        for p, ok in zip(mapa["pct_problema"], confiavel)]
    mapa["pct_txt"] = [pct(p) if ok else f"amostra pequena ({a})" for p, ok, a
                       in zip(mapa["pct_problema"], confiavel, mapa["analisadas"])]
    mapa["nota_txt"] = mapa["nota_media_base"].map(lambda x: f"{x:.1f}")
    mapa["raio"] = 9000 + mapa["analisadas"] * 2500
    camada = pdk.Layer("ScatterplotLayer", data=mapa, get_position="[longitude, latitude]",
                       get_fill_color="cor", get_radius="raio", radius_min_pixels=6, radius_max_pixels=45,
                       stroked=True, get_line_color=[90, 90, 90], line_width_min_pixels=1,
                       pickable=True, auto_highlight=True)
    with mp1:
        st.pydeck_chart(pdk.Deck(
            layers=[camada], initial_view_state=pdk.ViewState(latitude=37.5, longitude=-96, zoom=3.2),
            map_provider="carto", map_style=pdk.map_styles.CARTO_LIGHT,
            tooltip={"html": "<b>{store_label}</b><br/>Reviews analisadas: {analisadas}<br/>"
                             "Com problema: {com_problema} · {pct_txt}<br/>Gravidade alta: {graves}<br/>"
                             "Nota média (base completa): {nota_txt}",
                     "style": {"backgroundColor": "#4a0707", "color": "white", "fontSize": "12px"}},
        ), width="stretch", height=560)
        st.caption("Tamanho = reviews analisadas · Cor = % das reviews com problema (verde mais escuro = pior) · "
                   "Cinza = amostra pequena demais para calcular %.")
    with mp2:
        criticas = mapa[confiavel].sort_values(["pct_problema", "com_problema"], ascending=False).head(8)
        st.markdown(painel("Lojas no mapa", f"{len(mapa)}",
                           f"{int(confiavel.sum())} com amostra suficiente para calcular %"), unsafe_allow_html=True)
        st.markdown("**Lojas mais críticas** · % das reviews com problema")
        if criticas.empty:
            st.info("Nenhuma loja com amostra suficiente. Diminua o mínimo acima.")
        else:
            desenhar(barras(criticas, "store_label", "pct_problema", None,
                                   [alt.Tooltip("store_label:N", title="Loja"), "analisadas:Q", "com_problema:Q",
                                    alt.Tooltip("pct_problema:Q", format=".0%", title="% com problema")],
                                   cor="#1e8a45", formato=".0%", altura_barra=40), width="stretch")
    with st.expander("Ver tabela"):
        st.dataframe(mapa[["store_label", "analisadas", "com_problema", "pct_problema", "graves",
                           "nota_media_base", "reviews_base"]].sort_values("com_problema", ascending=False),
                     hide_index=True, width="stretch",
                     column_config={"pct_problema": st.column_config.NumberColumn("% com problema", format="percent"),
                                    "nota_media_base": st.column_config.NumberColumn("Nota média (base)"),
                                    "reviews_base": st.column_config.NumberColumn("Reviews na base")})

# ---------------------------------------------------------------- ranking
with aba_ranking:
    r1, r2, r3, r4 = st.columns([2, 1, 1, 1])
    opcoes_tema = {"Qualquer problema": "problema", **{v: k for k, v in ROTULOS_TEMAS.items()
                                                      if k != "elogio_sem_problema"}}
    tema_escolhido = r1.selectbox("Tipo de problema", list(opcoes_tema))
    agrupamento = r2.segmented_control("Agrupar por", ["Loja", "Cidade"], default="Loja") or "Loja"
    metrica = r3.segmented_control("Métrica", ["Quantidade", "% das reviews"], default="Quantidade") or "Quantidade"
    minimo = r4.number_input("Mínimo de reviews analisadas", min_value=1, value=MIN_REVIEWS_PADRAO,
                             help="Com 1 review, uma loja só pode ter 0% ou 100%.")

    coluna = "store_label" if agrupamento == "Loja" else "city"
    filtro = opcoes_tema[tema_escolhido]
    alvo = reviews["tem_problema"] if filtro == "problema" else reviews["tema"].eq(filtro)
    completa = (reviews.assign(alvo=alvo).groupby(coluna)
                .agg(casos=("alvo", "sum"), analisadas=("alvo", "size")).reset_index())
    completa = completa[completa["analisadas"] >= minimo]
    completa["percentual"] = completa["casos"] / completa["analisadas"]
    valor = "casos" if metrica == "Quantidade" else "percentual"
    tabela = completa[completa["casos"] > 0].sort_values([valor, "casos"], ascending=False).head(15)
    cor_rank = VERMELHO

    k1, k2 = st.columns([2.3, 1], gap="large")
    with k1:
        if tabela.empty:
            st.info("Nenhum caso com esses critérios. Experimente diminuir o mínimo de reviews analisadas.")
        else:
            desenhar(barras(tabela, coluna, valor, "Casos" if valor == "casos" else "% das reviews analisadas",
                                   [alt.Tooltip(f"{coluna}:N", title=agrupamento), "casos:Q", "analisadas:Q",
                                    alt.Tooltip("percentual:Q", format=".0%", title="%")],
                                   cor=cor_rank, formato="d" if valor == "casos" else ".0%", altura_barra=32),
                            width="stretch")
    with k2:
        rotulo = "lojas" if agrupamento == "Loja" else "cidades"
        if not tabela.empty:
            pior = tabela.iloc[0]
            st.markdown(painel(f"Pior {agrupamento.lower()}", str(pior[coluna]),
                               f"{int(pior['casos'])} casos em {int(pior['analisadas'])} reviews ({pct(pior['percentual'])})",
                               cor_rank), unsafe_allow_html=True)
        if not completa.empty:
            st.markdown(painel(f"Média entre as {rotulo}", pct(completa["percentual"].mean()),
                               f"das reviews com {tema_escolhido.lower()}"), unsafe_allow_html=True)
            sem_casos = int((completa["casos"] == 0).sum())
            st.markdown(painel(f"{rotulo.capitalize()} sem nenhum caso", f"{sem_casos} de {len(completa)}",
                               f"entre as com pelo menos {minimo} reviews analisadas", "#8f1810"),
                        unsafe_allow_html=True)
    if not tabela.empty:
        with st.expander("Ver tabela"):
            st.dataframe(tabela.rename(columns={coluna: agrupamento}), hide_index=True, width="stretch",
                         column_config={"percentual": st.column_config.NumberColumn(format="percent")})

# ---------------------------------------------------------------- fila de escalados
with aba_fila:
    fila_toda = reviews[reviews["status"].eq("escalado_para_humano")].copy()
    fila_toda["ordem"] = fila_toda["gravidade"].map({"alta": 0, "media": 1, "baixa": 2}).fillna(3)
    q1, q2 = st.columns([1, 2.3], gap="large")
    with q1:
        st.markdown(painel("Aguardando o gerente", f"{len(fila_toda)} casos",
                           "mais graves primeiro; cada um com rascunho de resposta já revisado", "#d03b3b"),
                    unsafe_allow_html=True)
        niveis = [ROTULOS_GRAVIDADE[g] for g in ("alta", "media", "baixa") if g in set(fila_toda["gravidade"])]
        mostrar = st.pills("Mostrar gravidade", niveis, selection_mode="multi", default=niveis)
        if not fila_toda.empty:
            por_tema = fila_toda.groupby(["tema_rotulo"]).size().rename("casos").reset_index()
            st.markdown("**Por tema**")
            desenhar(barras(por_tema, "tema_rotulo", "casos", None,
                                   [alt.Tooltip("tema_rotulo:N", title="Tema"), "casos:Q"],
                                   cor=alt.Color("tema_rotulo:N", legend=None,
                                                 scale=escala_temas(por_tema["tema_rotulo"])), altura_barra=30),
                            width="stretch")
    with q2:
        codigos = {v: k for k, v in ROTULOS_GRAVIDADE.items()}
        fila = fila_toda[fila_toda["gravidade"].isin([codigos[m] for m in (mostrar or [])])]
        fila = fila.sort_values(["ordem", "rating_num"])
        if fila.empty:
            st.info("Nenhum caso com esses filtros.")
        with st.container(height=640, border=False):
            for _, c in fila.iterrows():
                icone = {"alta": "🔴", "media": "🟠", "baixa": "🟡"}.get(c["gravidade"], "⚪")
                with st.expander(f"{icone} {c['tema_rotulo']} · {c['store_label']} · {c['rating_num']}★"):
                    st.markdown(chip_tema(c["tema"]) + chip_gravidade(c["gravidade"])
                                + (chip("🛡️ " + c["categorias_alerta"].replace("_", " "), "#d03b3b")
                                   if c["categorias_alerta"] else ""), unsafe_allow_html=True)
                    st.markdown(f"**Resumo (IA):** {c['resumo'] or 'sem resumo'}  \n**Por que escalou:** {c['motivo']}")
                    if isinstance(c["resposta"], str):
                        st.markdown("**Rascunho de resposta:**")
                        st.info(sem_travessao(c["resposta"]))

# ---------------------------------------------------------------- perguntas
with aba_pergunta:
    st.session_state.setdefault("pergunta", "")

    def usar_exemplo(texto: str) -> None:
        st.session_state["pergunta"] = texto

    p1, p2 = st.columns([1, 2.3], gap="large")
    with p1:
        st.markdown("**Experimente uma pergunta pronta**")
        for i, ex in enumerate(EXEMPLOS):
            st.button(ex, key=f"exemplo_{i}", on_click=usar_exemplo, args=(ex,), width="stretch")
        st.caption("Respostas vêm de consultas nos dados já analisados: **sem chamar modelo, custo zero**.")
    with p2:
        pergunta = st.text_input("Pergunte em português ou inglês", key="pergunta",
                                 placeholder="Ex.: quais cidades têm mais reclamações de demora?")
        if not pergunta:
            st.info("Escreva uma pergunta ou clique num exemplo ao lado.")
        else:
            interp = interpretar(pergunta)
            if interp is None:
                st.warning("Não entendi a pergunta. Cite um tema (demora, atendimento, limpeza, comida, preço, "
                           "pedido errado), 'casos graves' ou 'escalados', e se quer por loja, cidade ou estado.")
            else:
                st.markdown(f"**Interpretação:** {interp.descrever()}")
                resultado = responder(reviews, interp)
                if resultado.empty:
                    st.info("Nenhum caso encontrado.")
                else:
                    grupo = resultado.columns[0]
                    valor = "percentual" if interp.percentual else "casos"
                    desenhar(barras(resultado, grupo, valor, "Casos" if valor == "casos" else "% das reviews",
                                           [alt.Tooltip(f"{grupo}:N"), "casos:Q", "analisadas:Q",
                                            alt.Tooltip("percentual:Q", format=".0%")],
                                           cor=CORES_TEMA.get(interp.filtro, VERMELHO),
                                           formato="d" if valor == "casos" else ".0%"),
                                    width="stretch")
                    with st.expander("Ver tabela"):
                        st.dataframe(resultado, hide_index=True, width="stretch",
                                     column_config={"percentual": st.column_config.NumberColumn(format="percent")})

    # Modo ao vivo (opcional): desligado por padrão para o demo público não gerar custo.
    if configuracao("ENABLE_LIVE_QA", "false").lower() == "true":
        limite = int(configuracao("LIVE_QA_MAX_PER_SESSION", "5"))
        usadas = st.session_state.setdefault("perguntas_ao_vivo", 0)
        st.divider()
        st.markdown(f"**Perguntar à IA (ao vivo)** · {limite - usadas} de {limite} perguntas restantes nesta sessão")
        pergunta_ia = st.text_input("Pergunta livre para o modelo", key="pergunta_ia")
        if st.button("Perguntar", disabled=usadas >= limite or not pergunta_ia):
            try:
                from ifood.llm import chamar_modelo  # só existe no ambiente completo (não no demo)
            except ImportError:
                st.error("Modo ao vivo requer o pacote completo do projeto (litellm).")
            else:
                contexto = (reviews.groupby(["city", "tema_rotulo"]).size().rename("reviews").reset_index()
                            .to_csv(index=False))
                r = chamar_modelo("Você é um analista. Responda em português, em até 5 linhas, usando SOMENTE "
                                  "a tabela fornecida (contagem de reviews por cidade e tema). Se a tabela não "
                                  "responder, diga isso.", f"Tabela:\n{contexto}\n\nPergunta: {pergunta_ia}",
                                  etapa="dashboard_ao_vivo")
                st.session_state["perguntas_ao_vivo"] = usadas + 1
                st.write(r.texto if r.ok else f"Erro: {r.erro}")

# ---------------------------------------------------------------- qualidade da IA
with aba_ia:
    st.markdown("Cada versão do prompt de classificação foi avaliada contra um **gabarito de 50 reviews "
                "rotuladas**. A evolução mostra o efeito de cada mudança no prompt.")
    if not qualidade.empty:
        q = qualidade.copy()
        q["destaque"] = q["versao"].eq(meta["prompt_classificador"]).map({True: "Em uso", False: "Avaliada"})
        metricas = {"acuracia_tema": "Acurácia de tema", "acuracia_gravidade": "Acurácia de gravidade",
                    "taxa_json_invalido": "JSON inválido (menor é melhor)"}
        colunas = st.columns(len(metricas))
        for col, (campo, titulo) in zip(colunas, metricas.items()):
            grafico = alt.Chart(q).mark_bar(cornerRadiusEnd=4, size=34).encode(
                x=alt.X("versao:N", title=None, axis=alt.Axis(labelAngle=0, labelFontSize=13)),
                y=alt.Y(f"{campo}:Q", title=None, axis=alt.Axis(format=".0%", tickCount=4),
                        scale=alt.Scale(domain=[0, 1])),
                color=alt.Color("destaque:N", legend=None,
                                scale=alt.Scale(domain=["Em uso", "Avaliada"], range=[VERMELHO, "#f7c3bd"])),
                tooltip=["versao:N", alt.Tooltip(f"{campo}:Q", format=".0%", title=titulo)],
            )
            rotulo = grafico.mark_text(dy=-8, fontWeight=600).encode(text=alt.Text(f"{campo}:Q", format=".0%"),
                                                                     color=alt.value("#1b1b1a"))
            col.markdown(f"**{titulo}**")
            col.altair_chart((grafico + rotulo).properties(height=220), width="stretch")
        st.caption(f"Vermelho = versão em uso no pipeline ({meta['prompt_classificador']}). Modelo: `{q['modelo'].iloc[0]}`.")
        with st.expander("Ver tabela completa"):
            st.dataframe(qualidade, hide_index=True, width="stretch")

    if not modelos.empty:
        st.subheader("Modelo local × nuvem")
        st.markdown("Mesmo gabarito, mesmos prompts. Para trocar de modelo, bastou mudar uma linha de configuração.")
        m = modelos.copy()
        m["nome"] = m["modelo"].map(lambda x: x.split("/")[-1] + (" (local)" if x.startswith("ollama") else " (API)"))
        m["config"] = m["nome"] + " · " + m["versao"]
        # cada modelo tem uma família de cor (vermelho, laranja...) e cada versão de prompt um tom dela:
        # versão mais antiga = tom claro, mais nova = tom escuro
        familias = [["#f7b3ad", "#e8574a", "#da291c", "#8f1810"],
                    ["#ffe08a", "#ffbc0d", "#f08a1c", "#b45309"]]
        configs, tons = [], []
        for i, (nome, grupo) in enumerate(m.groupby("nome", sort=False)):
            familia = familias[i % len(familias)]
            versoes = sorted(grupo["versao"])
            passo = (len(familia) - 1) / max(1, len(versoes) - 1)
            for j, versao in enumerate(versoes):
                configs.append(f"{nome} · {versao}")
                tons.append(familia[round(j * passo)] if len(versoes) > 1 else familia[2])
        cor_modelo = alt.Color("config:N", legend=None,  # o nome de cada barra já está no eixo
                               scale=alt.Scale(domain=configs, range=tons))
        c1, c2, c3 = st.columns(3)
        for col, (campo, titulo) in zip([c1, c2], [("acuracia_tema", "Acurácia de tema"),
                                                   ("acuracia_gravidade", "Acurácia de gravidade")]):
            base = alt.Chart(m).encode(
                y=alt.Y("config:N", title=None, sort=list(m["config"]), axis=alt.Axis(labelLimit=260)),
                x=alt.X(f"{campo}:Q", title=None, scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format=".0%", tickCount=4)),
                tooltip=[alt.Tooltip("nome:N", title="Modelo"), alt.Tooltip("versao:N", title="Prompt"),
                         alt.Tooltip(f"{campo}:Q", format=".0%", title=titulo)])
            graf = base.mark_bar(cornerRadiusEnd=4, height=20).encode(color=cor_modelo) + \
                base.mark_text(align="left", dx=4, fontWeight=600).encode(text=alt.Text(f"{campo}:Q", format=".0%"))
            col.markdown(f"**{titulo}**")
            col.altair_chart(graf.properties(height=44 * len(m)), width="stretch", height=44 * len(m) + 40)
        c1.caption("Uma cor por modelo (vermelho, amarelo/laranja); tom claro = versão mais antiga do prompt, escuro = mais nova.")
        rapido = m.loc[m["latencia_mediana_s"].idxmin()]
        lento = m.loc[m["latencia_mediana_s"].idxmax()]
        c3.markdown(f"""<div class="card"><div class="tag">Velocidade × custo</div>
            <div class="big">{lento['latencia_mediana_s'] / rapido['latencia_mediana_s']:.0f}× mais rápido</div>
            <div class="sub">{rapido['nome']}: {num_br(rapido['latencia_mediana_s'], 1)} s por review, contra
            {num_br(lento['latencia_mediana_s'])} s do {lento['nome']}. Custo na API: ~US$
            {num_br(m['custo_por_1000_reviews_usd'].max(), 2)} por 1.000 reviews (preço do plano pago).</div></div>""",
                    unsafe_allow_html=True)
        st.markdown('<div class="aviso">📏 <b>Leia com cautela:</b> o gabarito tem 50 reviews. A vantagem da API '
                    'no tema é estatisticamente significativa (teste de McNemar, p = 0,02), mas diferenças de 1 a 2 '
                    'reviews entre versões de prompt são empate. O gabarito tem só 2 casos graves, então "2/2" não '
                    'prova nada: a regra de segurança em código continua valendo para qualquer modelo.</div>',
                    unsafe_allow_html=True)
        with st.expander("Ver tabela completa"):
            st.dataframe(modelos, hide_index=True, width="stretch")

    conc = concordancia
    if not conc.empty:
        n = int(conc["n"].sum())
        st.subheader(f"Nas {n} reviews: onde os dois modelos concordam")
        st.markdown("Fora do gabarito não existe \"resposta certa\". Por isso medimos a **concordância** entre o "
                    "modelo local e a API: onde concordam, a classificação é provavelmente confiável; onde "
                    "discordam, uma pessoa precisa olhar.")
        tema_igual = conc.loc[conc["tema_qwen"] == conc["tema_gemini"], "n"].sum() / n
        grav_igual = conc.loc[conc["gravidade_qwen"] == conc["gravidade_gemini"], "n"].sum() / n
        alta_q = int(conc.loc[conc["gravidade_qwen"] == "alta", "n"].sum())
        alta_g = int(conc.loc[conc["gravidade_gemini"] == "alta", "n"].sum())
        alta_ambos = int(conc.loc[(conc["gravidade_qwen"] == "alta") & (conc["gravidade_gemini"] == "alta"), "n"].sum())
        discordam = int(conc.loc[(conc["tema_qwen"] != conc["tema_gemini"]) |
                                 (conc["gravidade_qwen"] != conc["gravidade_gemini"]), "n"].sum())
        k1, k2, k3 = st.columns(3)
        k1.markdown(f"""<div class="card"><div class="tag">Concordam no tema</div>
            <div class="big">{pct(tema_igual)}</div>
            <div class="sub">das {n} reviews, os dois escolheram o mesmo tema.</div></div>""", unsafe_allow_html=True)
        k2.markdown(f"""<div class="card"><div class="tag">Concordam na gravidade</div>
            <div class="big">{pct(grav_igual)}</div>
            <div class="sub">{discordam} reviews com alguma discordância viraram uma fila de revisão humana.</div></div>""",
                    unsafe_allow_html=True)
        k3.markdown(f"""<div class="card"><div class="tag">Casos graves</div>
            <div class="big">{alta_q} × {alta_g}</div>
            <div class="sub">modelo local × API marcando gravidade alta; os dois juntos em {alta_ambos}.
            A API manda o dobro para o gerente.</div></div>""", unsafe_allow_html=True)

        cruz = conc.groupby(["tema_qwen", "tema_gemini"], as_index=False)["n"].sum()
        cruz["Modelo local"] = cruz["tema_qwen"].map(ROTULOS_TEMAS)
        cruz["API"] = cruz["tema_gemini"].map(ROTULOS_TEMAS)
        cruz["igual"] = cruz["tema_qwen"] == cruz["tema_gemini"]
        ordem = [ROTULOS_TEMAS[t] for t in CORES_TEMA if t in set(cruz["tema_qwen"]) | set(cruz["tema_gemini"])]
        base = alt.Chart(cruz).encode(
            x=alt.X("API:N", sort=ordem, title="Tema segundo a API (Gemini)",
                    axis=alt.Axis(labelAngle=-30, labelLimit=160, orient="top")),
            y=alt.Y("Modelo local:N", sort=ordem, title="Tema segundo o modelo local (Qwen)",
                    axis=alt.Axis(labelLimit=200)),
            tooltip=["Modelo local:N", "API:N", alt.Tooltip("n:Q", title="Reviews")])
        celulas = base.mark_rect(cornerRadius=3, stroke="white", strokeWidth=2).encode(
            color=alt.Color("n:Q", title="Reviews", scale=alt.Scale(range=RAMPA_QUENTE), legend=None))
        numeros = base.mark_text(fontWeight=600).encode(
            text="n:Q", color=alt.condition("datum.n > 50", alt.value("white"), alt.value("#1b1b1a")))
        st.markdown("**Tema: quem disse o quê** · a diagonal é onde concordam")
        st.altair_chart((celulas + numeros).properties(height=36 * len(ordem) + 40), width="stretch")
        st.markdown('<div class="aviso">⚖️ <b>Decisão de negócio:</b> os dois modelos nem sempre concordam sobre '
                    'o que é grave. A API marca mais casos como graves; o modelo local, menos. Marcar grave demais '
                    'gasta tempo do gerente; marcar de menos pode deixar um cliente com problema sério sem resposta, '
                    'e esse é o erro mais caro. Por isso, as 38 reviews em que só um dos modelos viu gravidade alta '
                    'vão primeiro para uma pessoa revisar.</div>',
                    unsafe_allow_html=True)

    st.subheader("Como cada review passa pelos agentes")
    etapas = [
        ("#c2410c", "🤖 IA", "1. Classificador", "Lê a review e define o tema, o sentimento e a gravidade."),
        ("#da291c", "⚙️ Código", "2. Regra de segurança", "Procura termos de saúde, segurança ou ameaça. "
                                                         "Se achar, o caso vira grave, diga a IA o que disser."),
        ("#8f1810", "🤖 IA + ⚙️ código", "3. Decisor", "Escolhe entre ignorar, responder ou escalar. "
                                                      "Caso grave sempre vai para o gerente."),
        ("#b45309", "🤖 IA", "4. Redator", "Escreve a resposta ao cliente, sem prometer reembolso nem cupom."),
        ("#6b3a32", "🤖 IA + ⚙️ código", "5. Revisor", "Confere tom, tamanho e promessas. Reprovou? Reescreve 1 vez; "
                                                      "reprovou de novo, vai para uma pessoa."),
    ]
    blocos = '<div class="seta">→</div>'.join(
        f'<div class="etapa" style="--c:{cor}"><div class="quem">{quem}</div><b>{nome}</b><span>{texto}</span></div>'
        for cor, quem, nome, texto in etapas)
    st.markdown(f'<div class="fluxo">{blocos}</div>'
                '<div class="saidas"><div>✅ <b>Resposta enviada</b>: tudo aprovado</div>'
                '<div>🚨 <b>Escalado ao gerente</b>: com rascunho de resposta pronto</div>'
                '<div>➖ <b>Ignorado</b>: elogio sem nada a responder</div></div>',
                unsafe_allow_html=True)
    gerado = pd.to_datetime(meta["gerado_em"]).strftime("%d/%m/%Y %H:%M")
    st.markdown(f'<div class="rodape">Modelo desta análise: <code>{meta["modelo"]}</code> · '
                f'{meta["chamadas_llm"]} chamadas · custo {"n/d" if custo is None else f"US$ {custo:.2f}"} · '
                f'dados gerados em {gerado} a partir de <code>{meta["fonte"]}</code>. '
                'Projeto de portfólio, dados públicos, sem vínculo com a marca.</div>', unsafe_allow_html=True)
