"""Perguntas em linguagem natural respondidas SEM modelo (custo zero).

Estratégia: identificar na pergunta (português ou inglês)
  - o assunto: um tema, 'casos graves', 'escalados' ou 'negativas';
  - o agrupamento: loja, cidade ou estado;
  - a métrica: contagem ou percentual;
  - quantos itens mostrar (top N).
e executar a consulta pandas correspondente. A interpretação é mostrada ao usuário,
para ficar claro o que foi entendido.
"""
import re
import unicodedata
from dataclasses import dataclass

import pandas as pd

ROTULOS_TEMAS = {
    "atendimento": "Atendimento", "demora": "Demora", "limpeza": "Limpeza",
    "comida_errada_ou_fria": "Comida errada ou fria", "preco": "Preço",
    "pedido_incorreto": "Pedido incorreto", "elogio_sem_problema": "Elogio (sem problema)", "outro": "Outro",
}

# palavra-chave (sem acento, minúscula) -> filtro
PALAVRAS_TEMA = {
    "demora": ["demora", "demorad", "espera", "fila", "lent", "wait", "slow", "line"],
    "atendimento": ["atendimento", "atendente", "funcionari", "grosse", "rude", "staff", "employee", "gerente"],
    "limpeza": ["limpeza", "suj", "banheiro", "higiene", "dirty", "clean", "bathroom"],
    "comida_errada_ou_fria": ["comida", "fria", "frio", "qualidade", "sabor", "cold", "food"],
    "preco": ["preco", "caro", "cobranc", "price", "expensive", "charge"],
    "pedido_incorreto": ["pedido errado", "pedido incorreto", "faltando", "faltou", "wrong order", "missing",
                         "pedido"],
    "elogio_sem_problema": ["elogio", "positiv", "praise"],
}
PALAVRAS_GRAVE = ["grave", "graves", "gravidade alta", "serio", "urgente", "seguranca", "intoxica", "serious"]
PALAVRAS_ESCALADO = ["escalad", "gerente precisa", "humano", "escalat"]
PALAVRAS_NEGATIVO = ["negativ", "reclama", "problema", "complain", "insatisf"]
PALAVRAS_PERCENTUAL = ["percent", "%", "proporc", "taxa", "rate", "share"]
AGRUPAMENTOS = {
    "city": ["cidade", "cidades", "city", "cities"],
    "state": ["estado", "estados", "state", "uf"],
    "store_label": ["loja", "lojas", "unidade", "restaurante", "store"],
}
NOMES_AGRUPAMENTO = {"store_label": "loja", "city": "cidade", "state": "estado"}


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def contem(texto: str, palavras: list[str]) -> bool:
    return any(p in texto for p in palavras)


@dataclass
class Interpretacao:
    filtro: str            # tema, "grave", "escalado", "negativo" ou "problema"
    agrupar_por: str       # coluna
    percentual: bool
    top: int

    def descrever(self) -> str:
        assunto = {"grave": "casos de gravidade alta", "escalado": "casos escalados para o gerente",
                   "negativo": "reviews com sentimento negativo",
                   "problema": "reviews com algum problema (qualquer tema exceto elogio)"}.get(
            self.filtro, f"reclamações de **{ROTULOS_TEMAS.get(self.filtro, self.filtro).lower()}**")
        metrica = "percentual das reviews analisadas de cada" if self.percentual else "quantidade por"
        return f"Top {self.top}: {assunto}, {metrica} {NOMES_AGRUPAMENTO[self.agrupar_por]}."


def interpretar(pergunta: str) -> Interpretacao | None:
    texto = normalizar(pergunta)
    if not texto.strip():
        return None

    filtro = None
    if contem(texto, PALAVRAS_GRAVE):
        filtro = "grave"
    elif contem(texto, PALAVRAS_ESCALADO):
        filtro = "escalado"
    else:
        # se várias palavras casarem, vence a mais longa (mais específica): "pedido errado" > "pedido"
        achados = [(len(p), tema) for tema, palavras in PALAVRAS_TEMA.items() for p in palavras if p in texto]
        if achados:
            filtro = max(achados)[1]
        elif "negativ" in texto:
            filtro = "negativo"
        elif contem(texto, PALAVRAS_NEGATIVO):
            filtro = "problema"
    if filtro is None:
        return None

    agrupar_por = "store_label"
    for coluna, palavras in AGRUPAMENTOS.items():
        if any(re.search(rf"\b{p}\b", texto) for p in palavras):
            agrupar_por = coluna
            break

    numero = re.search(r"\b(\d{1,2})\b", texto)
    top = min(int(numero.group(1)), 20) if numero else 5
    return Interpretacao(filtro, agrupar_por, contem(texto, PALAVRAS_PERCENTUAL), top)


def mascara(reviews: pd.DataFrame, filtro: str) -> pd.Series:
    if filtro == "grave":
        return reviews["gravidade"].eq("alta")
    if filtro == "escalado":
        return reviews["status"].eq("escalado_para_humano")
    if filtro == "negativo":
        return reviews["sentimento"].eq("negativo")
    if filtro == "problema":
        return reviews["tema"].notna() & reviews["tema"].ne("elogio_sem_problema")
    return reviews["tema"].eq(filtro)


def responder(reviews: pd.DataFrame, interp: Interpretacao) -> pd.DataFrame:
    """Tabela com: grupo, casos, analisadas, percentual — ordenada pela métrica pedida."""
    col = interp.agrupar_por
    total = reviews.groupby(col).size().rename("analisadas")
    casos = reviews[mascara(reviews, interp.filtro)].groupby(col).size().rename("casos")
    tabela = pd.concat([casos, total], axis=1).fillna(0).astype(int)
    tabela = tabela[tabela["casos"] > 0]
    tabela["percentual"] = (tabela["casos"] / tabela["analisadas"]).round(3)
    ordem = ["percentual", "casos"] if interp.percentual else ["casos", "percentual"]
    tabela = tabela.sort_values(ordem, ascending=False).head(interp.top)
    return tabela.reset_index().rename(columns={col: NOMES_AGRUPAMENTO[col]})


EXEMPLOS = [
    "Quais lojas têm mais reclamação de demora?",
    "Quais cidades têm mais casos graves?",
    "Top 10 lojas com maior percentual de problemas de limpeza",
    "Em quais estados há mais pedidos incorretos?",
    "Quais lojas tiveram mais casos escalados?",
]
