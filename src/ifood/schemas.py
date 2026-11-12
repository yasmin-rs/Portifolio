"""Formatos de saída esperados dos modelos (validados com Pydantic).

Toda resposta JSON de um modelo passa por um destes schemas. Se não validar,
llm.chamar_modelo() pede de novo (até JSON_MAX_RETRIES vezes).
"""
from typing import Literal

from pydantic import BaseModel, Field

Tema = Literal[
    "atendimento", "demora", "limpeza", "comida_errada_ou_fria",
    "preco", "pedido_incorreto", "elogio_sem_problema", "outro",
]

# Definição de cada tema: usada na planilha de gabarito e nos prompts (mesmo critério para humano e modelo).
DEFINICOES_TEMAS = {
    "atendimento": "Atitude dos funcionários/gerente: grosseria, descaso, má vontade, falta de educação.",
    "demora": "Espera longa: fila, drive-thru parado, pedido que demorou para sair.",
    "limpeza": "Higiene: loja/banheiro/mesas sujos, cabelo ou objeto estranho na comida, insetos.",
    "comida_errada_ou_fria": "Qualidade da comida: fria, crua, velha, mal preparada, sabor ruim.",
    "preco": "Valor: caro, cobrança errada ou cobrada a mais, promoção não aplicada.",
    "pedido_incorreto": "Pedido diferente do pedido: item faltando, trocado, ingrediente errado.",
    "elogio_sem_problema": "Review positiva ou neutra que não relata nenhum problema.",
    "outro": "Problema fora da lista (máquina de sorvete quebrada, horário, app, estacionamento, "
             "segurança) ou texto sem informação suficiente.",
}
Sentimento = Literal["positivo", "neutro", "negativo"]
Gravidade = Literal["baixa", "media", "alta"]

TEMAS: tuple[str, ...] = Tema.__args__  # lista usada em prompts, dashboard e avaliação


Acao = Literal["ignorar", "responder", "escalar"]
ACOES: tuple[str, ...] = Acao.__args__


class Decisao(BaseModel):
    """Saída do agente decisor."""
    acao: Acao
    motivo: str = Field(min_length=3, max_length=300)


class RespostaCliente(BaseModel):
    """Saída do agente redator (texto que iria para o cliente)."""
    resposta: str = Field(min_length=20, max_length=900)


class Revisao(BaseModel):
    """Saída do agente revisor."""
    aprovado: bool
    problemas: list[str] = Field(default_factory=list)


class Classificacao(BaseModel):
    """Saída do agente classificador para uma review."""
    tema: Tema
    sentimento: Sentimento
    gravidade: Gravidade
    resumo: str = Field(min_length=3, max_length=200, description="Resumo de uma linha, em português")
