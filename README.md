# Central de operações de reviews com IA

> 🚧 Projeto em construção — README completo (resultados, demo e decisões de design) em breve.

Pipeline de ponta a ponta que lê avaliações públicas de restaurantes fast-food, **classifica os problemas com LLM**, **decide ações com agentes** (ignorar, responder ou escalar para o gerente), **redige e revisa respostas ao cliente** e apresenta tudo em um dashboard.

## Já implementado
- **Limpeza e amostragem** (pandas): tratamento de encoding, deduplicação, amostra estratificada reprodutível.
- **Camada única de modelos com [litellm](https://github.com/BerriAI/litellm)**: troca entre modelo local (Ollama) e API (Anthropic) só pelo `.env`; validação de JSON com Pydantic + retry, cache em disco, log de tokens/custo/latência e estimativa de custo antes de execuções em lote.
- **Prompt engineering com avaliação**: 4 versões do prompt de classificação comparadas contra um gabarito rotulado (acurácia, gravidade, sentimento × nota, JSON inválido) — ver [`outputs/avaliacao/`](outputs/avaliacao/).
- **Orquestração de 4 agentes** (classificador → decisor → redator → revisor) com **regra de segurança determinística**: casos graves (saúde, segurança, discriminação, ameaça) sempre escalam para humano.

## Em andamento
Dashboard (Streamlit), automação com alertas, comparação modelo local × API.

## Como rodar (resumo)
```bash
uv sync
cp .env.example .env              # escolha o modelo (local ou API)
uv run python -m ifood.data_prep  # baixe o CSV do Kaggle em data/raw/ antes
uv run pytest
```

## Dados e aviso
Usa um dataset público de avaliações do Kaggle (McDonald's Store Reviews). O texto original das reviews **não é redistribuído** neste repositório (a licença do dataset não está especificada); só são publicados resultados agregados e derivados. Projeto de estudo/portfólio, **sem nenhum vínculo com a marca**.
