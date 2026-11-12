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
# Cada tema tem UMA cor fixa em todo o dashboard (paleta categórica validada para daltonismo,
# na ordem validada). "Elogio" é ausência de problema -> cinza neutro.
CORES_TEMA = {
    "atendimento": "#2a78d6", "demora": "#eb6834", "limpeza": "#1baf7a", "comida_errada_ou_fria": "#eda100",
    "preco": "#e87ba4", "pedido_incorreto": "#008300", "outro": "#6250d6", "elogio_sem_problema": "#a3a29c",
}
# Gravidade é um estado -> paleta de status, sempre com rótulo em texto.
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
h1, h2, h3, .hero h1, .card .big { font-family: 'Space Grotesk', sans-serif !important; letter-spacing: -0.01em; }
.block-container { padding-top: 1.6rem; }
.hero { background: linear-gradient(120deg, #0d1b2a 0%, #16304f 55%, #3a1f4f 100%); color: #f4f3ef;
        border-radius: 18px; padding: 28px 32px 22px; margin-bottom: 1rem; }
.hero h1 { color: #ffffff; font-size: 2.1rem; margin: 0 0 4px; }
.hero p { color: #c9c8c2; margin: 0 0 18px; font-size: .95rem; }
.hero .nums { display: flex; flex-wrap: wrap; gap: 34px; }
.hero .num { font-family: 'Space Grotesk', sans-serif; font-size: 2.3rem; font-weight: 700; color: #fff; line-height: 1; }
.hero .lbl { font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; color: #a9b4c4; margin-top: 6px; }
.hero .accent { color: #ff9a6b; }
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
    reviews["tema_rotulo"] = reviews["tema"].map(ROTULOS_TEMAS).fillna("Sem classificação")
    reviews["gravidade_rotulo"] = reviews["gravidade"].map(ROTULOS_GRAVIDADE)
    reviews["status_rotulo"] = reviews["status"].map(ROTULOS_STATUS)
    reviews["tem_problema"] = reviews["tema"].notna() & reviews["tema"].ne("elogio_sem_problema")
    return reviews, lojas, meta, qualidade


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


def chip(texto: str, cor: str) -> str:
    return f'<span class="chip"><span class="dot" style="background:{cor}"></span>{texto}</span>'


def chip_tema(tema) -> str:
    return chip(ROTULOS_TEMAS.get(tema, "—"), CORES_TEMA.get(tema, "#a3a29c"))


def chip_gravidade(grav) -> str:
    return chip(f"Gravidade {ROTULOS_GRAVIDADE.get(grav, '—').lower()}", CORES_GRAVIDADE.get(grav, "#a3a29c"))


def escala_temas(temas_presentes) -> alt.Scale:
    dominio = [ROTULOS_TEMAS[t] for t in CORES_TEMA if ROTULOS_TEMAS[t] in set(temas_presentes)]
    return alt.Scale(domain=dominio, range=[CORES_TEMA[t] for t in CORES_TEMA if ROTULOS_TEMAS[t] in dominio])


# ---------------------------------------------------------------- gráficos
def barras(df, categoria, valor, titulo_valor, tooltip, cor="#2a78d6", formato="d", altura_barra=28):
    """Barras horizontais ordenadas, com valor na ponta e tooltip. `cor` = hex ou alt.Color."""
    base = alt.Chart(df).encode(
        y=alt.Y(f"{categoria}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=320, labelFontSize=12)),
        x=alt.X(f"{valor}:Q", title=titulo_valor, axis=alt.Axis(format=formato, grid=True, tickCount=5)),
        tooltip=tooltip,
    )
    if isinstance(cor, str):
        marca = base.mark_bar(cornerRadiusEnd=4, height=altura_barra - 9, color=cor)
    else:
        marca = base.mark_bar(cornerRadiusEnd=4, height=altura_barra - 9).encode(color=cor)
    rotulo = base.mark_text(align="left", dx=5, fontWeight=600).encode(text=alt.Text(f"{valor}:Q", format=formato))
    return (marca + rotulo).properties(height=max(120, altura_barra * len(df)))


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
    celulas = base.mark_rect(cornerRadius=4, stroke="#f7f6f3", strokeWidth=2).encode(
        color=alt.Color("casos:Q", title="Reclamações",
                        scale=alt.Scale(range=["#cde2fb", "#104281"]), legend=alt.Legend(orient="bottom")))
    texto = base.mark_text(fontWeight=600).encode(
        text="casos:Q",
        color=alt.condition(alt.datum.casos >= dados["casos"].max() * 0.55, alt.value("white"), alt.value("#1b1b1a")))
    return (celulas + texto).properties(height=max(160, 30 * len(principais)))


# ---------------------------------------------------------------- dados + filtros
reviews_todas, lojas, meta, qualidade = carregar()

f1, f2, f3 = st.columns([1, 2, 1])
estados = f1.multiselect("Estado", sorted(reviews_todas["state"].dropna().unique()))
cidades_opcoes = reviews_todas[reviews_todas["state"].isin(estados)] if estados else reviews_todas
cidades = f2.multiselect("Cidade", sorted(cidades_opcoes["city"].dropna().unique()))
notas = f3.select_slider("Nota (estrelas)", options=[1, 2, 3, 4, 5], value=(1, 5))

reviews = reviews_todas[reviews_todas["rating_num"].between(*notas)]
if estados:
    reviews = reviews[reviews["state"].isin(estados)]
if cidades:
    reviews = reviews[reviews["city"].isin(cidades)]
if reviews.empty:
    st.warning("Nenhuma review com esses filtros.")
    st.stop()

# ---------------------------------------------------------------- hero
n = len(reviews)
graves = int(reviews["gravidade"].eq("alta").sum())
escaladas = int(reviews["status"].eq("escalado_para_humano").sum())
automatico = reviews["status"].isin(["resposta_enviada", "ignorado"]).mean()
st.markdown(f"""
<div class="hero">
  <h1>Central de operações de reviews <span class="accent">com IA</span></h1>
  <p>Agentes de IA leem avaliações de clientes, classificam o problema, decidem a ação e escrevem a resposta —
     e casos graves sempre chegam a um humano.</p>
  <div class="nums">
    <div><div class="num">{n:,}</div><div class="lbl">reviews analisadas</div></div>
    <div><div class="num">{pct(reviews['tem_problema'].mean())}</div><div class="lbl">relatam um problema</div></div>
    <div><div class="num">{pct(automatico)}</div><div class="lbl">resolvidas sem humano</div></div>
    <div><div class="num accent">{escaladas}</div><div class="lbl">escaladas ao gerente</div></div>
    <div><div class="num">{graves}</div><div class="lbl">casos graves</div></div>
  </div>
</div>
""".replace(",", "."), unsafe_allow_html=True)

if n < 200:
    st.markdown(f'<div class="aviso">⚠️ Prévia com apenas {n} reviews analisadas (≈{n / reviews["store_id"].nunique():.0f} '
                'por loja). Rankings e percentuais por loja ficam instáveis até a análise completa da amostra.</div>',
                unsafe_allow_html=True)

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
travas = int(reviews["trava_aplicada"].sum())
d3.markdown(f"""<div class="card"><div class="tag">Rede de segurança</div>
    <div class="big">{graves} casos graves</div>
    <div class="sub">{travas} detectado(s) só pela regra em código — o modelo tinha classificado como não grave</div></div>""",
            unsafe_allow_html=True)
custo = meta.get("custo_total_usd")
d4.markdown(f"""<div class="card"><div class="tag">Custo da análise</div>
    <div class="big">{'—' if custo is None else f'US$ {custo:.2f}'}</div>
    <div class="sub">{meta['chamadas_llm']} chamadas ao modelo <code>{meta['modelo']}</code></div></div>""",
            unsafe_allow_html=True)
st.write("")

# ---------------------------------------------------------------- impacto: horas economizadas
# Estimativa, não medição: a premissa (minutos por review) fica visível e ajustável.
# Sem IA, uma pessoa trata TODAS as reviews; com IA, só as escaladas continuam com humano.
h1, h2 = st.columns([3, 1], gap="large")
minutos = h2.slider("Premissa: minutos de trabalho manual por review", 1, 10, 3,
                    help="Ler, classificar, decidir o que fazer e escrever a resposta.")
horas_sem_ia = n * minutos / 60
horas_com_ia = escaladas * minutos / 60
economia = horas_sem_ia - horas_com_ia


def num_br(x: float, casas: int = 0) -> str:
    """1234.5 -> '1.234,5' (formato brasileiro)."""
    return f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


h1.markdown(f"""<div class="card" style="border-left: 5px solid #1baf7a">
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

# ---------------------------------------------------------------- visão geral
with aba_geral:
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        st.subheader("Do que os clientes reclamam")
        temas = reviews.groupby(["tema", "tema_rotulo"]).size().rename("reviews").reset_index()
        temas["percentual"] = temas["reviews"] / n
        cor = alt.Color("tema_rotulo:N", legend=None, scale=escala_temas(temas["tema_rotulo"]))
        st.altair_chart(barras(temas, "tema_rotulo", "reviews", "Reviews",
                               [alt.Tooltip("tema_rotulo:N", title="Tema"), alt.Tooltip("reviews:Q", title="Reviews"),
                                alt.Tooltip("percentual:Q", format=".0%", title="% das reviews")], cor=cor),
                        width="stretch")
    with c2:
        st.subheader("Gravidade")
        grav = reviews.groupby(["gravidade", "gravidade_rotulo"]).size().rename("reviews").reset_index()
        cor_g = alt.Color("gravidade:N", legend=None,
                          scale=alt.Scale(domain=list(CORES_GRAVIDADE), range=list(CORES_GRAVIDADE.values())))
        st.altair_chart(barras(grav, "gravidade_rotulo", "reviews", "Reviews",
                               [alt.Tooltip("gravidade_rotulo:N", title="Gravidade"), "reviews:Q"], cor=cor_g),
                        width="stretch")
        st.subheader("O que os agentes fizeram")
        status = reviews.groupby("status_rotulo").size().rename("reviews").reset_index()
        st.altair_chart(barras(status, "status_rotulo", "reviews", "Reviews",
                               [alt.Tooltip("status_rotulo:N", title="Status"), "reviews:Q"], cor="#16304f"),
                        width="stretch")
    st.subheader("Onde cada problema se concentra")
    st.caption("Cidades com mais reclamações × tipo de problema. Passe o mouse para ver os números.")
    if problemas.empty:
        st.info("Sem reclamações com esses filtros.")
    else:
        st.altair_chart(mapa_de_calor(reviews), width="stretch")

# ---------------------------------------------------------------- por dentro dos agentes
with aba_agentes:
    st.markdown("Escolha uma review e veja **cada decisão da IA**, passo a passo. O texto original não é "
                "exibido (licença do dataset); o resumo foi escrito pelo próprio agente classificador.")
    candidatos = reviews.copy()
    candidatos["prioridade"] = (candidatos["trava_aplicada"].astype(int) * 4
                                + candidatos["gravidade"].eq("alta").astype(int) * 2
                                + candidatos["tentativas_redacao"].fillna(0).gt(1).astype(int))
    candidatos = candidatos.sort_values("prioridade", ascending=False)
    opcoes = {f"#{r.review_id} · {r.tema_rotulo} · {r.rating_num}★ · {r.store_label}": r.review_id
              for r in candidatos.itertuples()}
    escolha = st.selectbox("Review", list(opcoes), help="As primeiras da lista são os casos mais interessantes.")
    caso = candidatos[candidatos["review_id"] == opcoes[escolha]].iloc[0]

    s1, s2, s3, s4 = st.columns(4, gap="small")
    s1.markdown(f"""<div class="step" style="--c:#2a78d6"><div class="n">① Classificador</div>
        <div class="t">O que o cliente relatou</div><div class="body">
        {chip_tema(caso['tema'])}{chip_gravidade(caso['gravidade'])}
        {chip('Sentimento ' + str(caso['sentimento'] or '—'), '#6d6c66')}
        <p style="margin-top:8px">“{caso['resumo'] or '—'}”</p></div></div>""", unsafe_allow_html=True)
    alerta = caso["categorias_alerta"] or ""
    s2.markdown(f"""<div class="step" style="--c:#d03b3b"><div class="n">② Regra de segurança</div>
        <div class="t">Verificação em código (sem IA)</div><div class="body">
        {('🛡️ <b>Alerta:</b> ' + alerta.replace('_', ' ') + '<br/>Gravidade forçada para alta.') if alerta
         else '✅ Nenhum termo de saúde, segurança, discriminação ou ameaça.'}
        </div></div>""", unsafe_allow_html=True)
    acao_ia = ROTULOS_ACAO.get(caso["acao_decisor"], "—")
    acao_final = ROTULOS_ACAO.get(caso["acao_final"], "—")
    trava = (f"<br/><b>🔒 Trava aplicada:</b> a IA sugeriu <i>{acao_ia}</i>, o código forçou <i>{acao_final}</i>."
             if caso["trava_aplicada"] and acao_ia != acao_final else "")
    s3.markdown(f"""<div class="step" style="--c:#6250d6"><div class="n">③ Decisor</div>
        <div class="t">Ação: {acao_final}</div><div class="body">{caso['motivo']}{trava}</div></div>""",
                unsafe_allow_html=True)
    try:
        problemas_rev = ast.literal_eval(caso["problemas_revisao"]) if isinstance(caso["problemas_revisao"], str) else []
    except (ValueError, SyntaxError):
        problemas_rev = []
    if isinstance(caso["resposta"], str):
        aprov = "✅ Aprovada pelo revisor" if caso["revisao_aprovada"] else "❌ Reprovada pelo revisor"
        tent = int(caso["tentativas_redacao"] or 0)
        corpo = (f"<b>{aprov}</b> · {tent} tentativa(s)"
                 + (f"<br/><small>Apontamentos: {'; '.join(problemas_rev)}</small>" if problemas_rev else ""))
    else:
        corpo = "Nenhuma resposta: review ignorada (elogio genérico ou sem conteúdo)."
    s4.markdown(f"""<div class="step" style="--c:#1baf7a"><div class="n">④ Redator + revisor</div>
        <div class="t">Resposta ao cliente</div><div class="body">{corpo}</div></div>""", unsafe_allow_html=True)
    if isinstance(caso["resposta"], str):
        st.markdown("**Resposta gerada**" + (" (rascunho para o gerente)" if caso["status"] == "escalado_para_humano"
                                              else " (enviada automaticamente — simulado)"))
        st.info(caso["resposta"])

# ---------------------------------------------------------------- mapa
with aba_mapa:
    m1, m2 = st.columns([1, 1])
    modo = m1.radio("Visualização", ["Colunas 3D", "Círculos"], horizontal=True)
    minimo_mapa = m2.slider("Colorir só lojas com pelo menos N reviews analisadas", 1, 10, MIN_REVIEWS_PADRAO)

    por_loja = reviews.groupby("store_id").agg(analisadas=("review_id", "size"),
                                               com_problema=("tem_problema", "sum"),
                                               graves=("gravidade", lambda s: int((s == "alta").sum()))).reset_index()
    mapa = lojas.merge(por_loja, on="store_id", how="inner").dropna(subset=["latitude", "longitude"])
    mapa["pct_problema"] = mapa["com_problema"] / mapa["analisadas"]
    confiavel = mapa["analisadas"] >= minimo_mapa
    # cor sequencial (um laranja, apagado -> intenso) pelo % com problema; amostra pequena = cinza
    apagado, intenso = (255, 214, 190), (235, 104, 52)
    mapa["cor"] = [
        [round(a + (b - a) * p) for a, b in zip(apagado, intenso)] + [235] if ok else [150, 150, 150, 160]
        for p, ok in zip(mapa["pct_problema"], confiavel)]
    mapa["pct_txt"] = [pct(p) if ok else f"amostra pequena ({a})" for p, ok, a
                       in zip(mapa["pct_problema"], confiavel, mapa["analisadas"])]
    mapa["nota_txt"] = mapa["nota_media_base"].map(lambda x: f"{x:.1f}")

    if modo == "Colunas 3D":
        camada = pdk.Layer("ColumnLayer", data=mapa, get_position="[longitude, latitude]",
                           get_elevation="com_problema", elevation_scale=60000, radius=28000,
                           get_fill_color="cor", pickable=True, auto_highlight=True, extruded=True)
        vista = pdk.ViewState(latitude=35.5, longitude=-95, zoom=3.3, pitch=50, bearing=-12)
    else:
        mapa["raio"] = 9000 + mapa["analisadas"] * 2500
        camada = pdk.Layer("ScatterplotLayer", data=mapa, get_position="[longitude, latitude]",
                           get_fill_color="cor", get_radius="raio", radius_min_pixels=6, radius_max_pixels=45,
                           stroked=True, get_line_color=[90, 90, 90], line_width_min_pixels=1,
                           pickable=True, auto_highlight=True)
        vista = pdk.ViewState(latitude=37.5, longitude=-96, zoom=3.2)
    st.pydeck_chart(pdk.Deck(
        layers=[camada], initial_view_state=vista, map_provider="carto", map_style=pdk.map_styles.CARTO_LIGHT,
        tooltip={"html": "<b>{store_label}</b><br/>Reviews analisadas: {analisadas}<br/>"
                         "Com problema: {com_problema} · {pct_txt}<br/>Gravidade alta: {graves}<br/>"
                         "Nota média (base completa): {nota_txt}",
                 "style": {"backgroundColor": "#0d1b2a", "color": "white", "fontSize": "12px"}},
    ), width="stretch", height=520)
    st.caption(("Altura = reclamações" if modo == "Colunas 3D" else "Tamanho = reviews analisadas")
               + " · Cor = % das reviews com problema (laranja mais intenso = pior) · Cinza = amostra pequena "
                 "demais para calcular %. Arraste com o botão direito para girar.")
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
    agrupamento = r2.radio("Agrupar por", ["Loja", "Cidade"], horizontal=True)
    metrica = r3.radio("Métrica", ["Quantidade", "% das reviews"], horizontal=True)
    minimo = r4.number_input("Mínimo de reviews analisadas", min_value=1, value=MIN_REVIEWS_PADRAO,
                             help="Com 1 review, uma loja só pode ter 0% ou 100%.")

    coluna = "store_label" if agrupamento == "Loja" else "city"
    filtro = opcoes_tema[tema_escolhido]
    alvo = reviews["tem_problema"] if filtro == "problema" else reviews["tema"].eq(filtro)
    tabela = (reviews.assign(alvo=alvo).groupby(coluna)
              .agg(casos=("alvo", "sum"), analisadas=("alvo", "size")).reset_index())
    tabela = tabela[(tabela["analisadas"] >= minimo) & (tabela["casos"] > 0)]
    tabela["percentual"] = tabela["casos"] / tabela["analisadas"]
    valor = "casos" if metrica == "Quantidade" else "percentual"
    tabela = tabela.sort_values([valor, "casos"], ascending=False).head(15)
    cor_rank = CORES_TEMA.get(filtro, "#16304f")

    if tabela.empty:
        st.info("Nenhum caso com esses critérios. Experimente diminuir o mínimo de reviews analisadas.")
    else:
        st.altair_chart(barras(tabela, coluna, valor, "Casos" if valor == "casos" else "% das reviews analisadas",
                               [alt.Tooltip(f"{coluna}:N", title=agrupamento), "casos:Q", "analisadas:Q",
                                alt.Tooltip("percentual:Q", format=".0%", title="%")],
                               cor=cor_rank, formato="d" if valor == "casos" else ".0%"),
                        width="stretch")
        with st.expander("Ver tabela"):
            st.dataframe(tabela.rename(columns={coluna: agrupamento}), hide_index=True, width="stretch",
                         column_config={"percentual": st.column_config.NumberColumn(format="percent")})

# ---------------------------------------------------------------- fila de escalados
with aba_fila:
    fila = reviews[reviews["status"].eq("escalado_para_humano")].copy()
    fila["ordem"] = fila["gravidade"].map({"alta": 0, "media": 1, "baixa": 2}).fillna(3)
    fila = fila.sort_values(["ordem", "rating_num"])
    st.markdown(f"**{len(fila)} casos** aguardando o gerente da loja, mais graves primeiro. "
                "Cada um traz um rascunho de resposta já revisado.")
    for _, c in fila.iterrows():
        icone = {"alta": "🔴", "media": "🟠", "baixa": "🟡"}.get(c["gravidade"], "⚪")
        with st.expander(f"{icone} {c['tema_rotulo']} · {c['store_label']} · {c['rating_num']}★"):
            st.markdown(chip_tema(c["tema"]) + chip_gravidade(c["gravidade"])
                        + (chip("🛡️ " + c["categorias_alerta"].replace("_", " "), "#d03b3b")
                           if c["categorias_alerta"] else ""), unsafe_allow_html=True)
            st.markdown(f"**Resumo (IA):** {c['resumo'] or '—'}  \n**Por que escalou:** {c['motivo']}")
            if isinstance(c["resposta"], str):
                st.markdown("**Rascunho de resposta:**")
                st.info(c["resposta"])

# ---------------------------------------------------------------- perguntas
with aba_pergunta:
    st.markdown("Pergunte em português ou inglês. As respostas vêm de consultas nos dados já analisados "
                "(**sem chamar modelo, custo zero**).")
    exemplo = st.selectbox("Exemplos", ["(escreva a sua)"] + EXEMPLOS)
    pergunta = st.text_input("Sua pergunta", value="" if exemplo == "(escreva a sua)" else exemplo)
    if pergunta:
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
                st.altair_chart(barras(resultado, grupo, valor, "Casos" if valor == "casos" else "% das reviews",
                                       [alt.Tooltip(f"{grupo}:N"), "casos:Q", "analisadas:Q",
                                        alt.Tooltip("percentual:Q", format=".0%")],
                                       cor=CORES_TEMA.get(interp.filtro, "#16304f"),
                                       formato="d" if valor == "casos" else ".0%"),
                                width="stretch")
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
                                scale=alt.Scale(domain=["Em uso", "Avaliada"], range=["#eb6834", "#b7c3d3"])),
                tooltip=["versao:N", alt.Tooltip(f"{campo}:Q", format=".0%", title=titulo)],
            )
            rotulo = grafico.mark_text(dy=-8, fontWeight=600).encode(text=alt.Text(f"{campo}:Q", format=".0%"),
                                                                     color=alt.value("#1b1b1a"))
            col.markdown(f"**{titulo}**")
            col.altair_chart((grafico + rotulo).properties(height=220), width="stretch")
        st.caption(f"Laranja = versão em uso no pipeline ({meta['prompt_classificador']}). Modelo: `{q['modelo'].iloc[0]}`.")
        with st.expander("Ver tabela completa"):
            st.dataframe(qualidade, hide_index=True, width="stretch")
    st.markdown(f"""
- **Agentes:** classificador → regra de segurança (código) → decisor → redator → revisor.
- **Modelo desta análise:** `{meta['modelo']}` · **chamadas:** {meta['chamadas_llm']} ·
  **custo:** {'—' if custo is None else f'US$ {custo:.4f}'}
- Dados gerados em {meta['gerado_em']} a partir de `{meta['fonte']}`. Projeto de portfólio, dados públicos,
  sem vínculo com a marca.
""")
