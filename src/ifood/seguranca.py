"""Regra de segurança determinística (sem modelo): rede de proteção para casos graves.

Motivo: na avaliação, o modelo local classificou "Fruit Fly in the OJ" como gravidade
média em 3 de 4 versões do prompt. Casos de saúde/segurança não podem depender só do LLM.

Se algum padrão casar, o pipeline força gravidade 'alta' e ação 'escalar'.
Preferimos falso positivo (escalar à toa) a falso negativo (ignorar intoxicação).
"""
import re

# categoria -> padrões (regex, inglês, sem diferenciar maiúsculas)
PADROES_GRAVES: dict[str, list[str]] = {
    "saude": [
        r"food poisoning", r"\bpoison(ed|ing)?\b", r"\b(got|get|getting|made (me|us|my \w+)) sick\b",
        r"\bthrew up\b", r"\bvomit", r"\bdiarrh", r"\bhospital(s|ized)?\b", r"\ballergic reaction\b",
        r"\braw (chicken|meat)\b", r"\bundercooked (chicken|meat)\b",
    ],
    "corpo_estranho": [
        r"\bhair (in|on)\b", r"\bbugs?\b", r"\binsects?\b", r"\b(cock)?roach", r"\bfruit fl(y|ies)\b",
        r"\bflies\b", r"\bfly (in|on)\b", r"\bmaggots?\b", r"\bworms?\b", r"\bmou?ld(y)?\b",
        r"\bglass (in|on)\b", r"\bplastic (in|on)\b", r"\bband-?aid\b", r"\brats?\b", r"\bmice\b",
    ],
    "discriminacao": [
        r"\bracis[tm]", r"\bdiscriminat", r"\bhomophob", r"\bsexis[tm]", r"\bslur", r"\bn-word\b",
    ],
    "violencia_ameaca": [
        r"\bthreat(en|ened|ening|s)?\b", r"\bassault", r"\battack(ed|ing)\b", r"\bpunch(ed)?\b",
        r"\bhit me\b", r"\bgun\b", r"\bknife\b", r"\bstabb", r"\bharass",
    ],
    "seguranca_local": [
        r"\bdrugs?\b", r"\bneedles?\b", r"\bnot (a )?safe\b", r"\bunsafe\b",
    ],
}

_COMPILADOS = {cat: [re.compile(p, re.IGNORECASE) for p in pads] for cat, pads in PADROES_GRAVES.items()}


def verificar_seguranca(texto: str) -> list[dict]:
    """Devolve os alertas encontrados: [{'categoria': 'corpo_estranho', 'trecho': 'Fruit Fly'}, ...]."""
    alertas = []
    for categoria, padroes in _COMPILADOS.items():
        for padrao in padroes:
            achado = padrao.search(texto or "")
            if achado:
                alertas.append({"categoria": categoria, "trecho": achado.group(0)})
                break  # um alerta por categoria basta
    return alertas
