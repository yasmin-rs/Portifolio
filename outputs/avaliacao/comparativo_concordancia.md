# Qwen 2.5 7B (local) × Gemini 3.5 Flash-Lite (API) — 500 reviews da amostra

| Medida | Valor |
|---|---|
| Concordam no tema | 73% (kappa 0.681) |
| Concordam na gravidade | 78% (kappa 0.627) |
| Concordam nos dois | 56% |
| Gravidade alta — Qwen / Gemini / ambos | 30 / 62 / 27 |
| Alta só para um dos dois (revisar primeiro) | 38 |

Gravidade já inclui a trava em código (regra de segurança), igual nos dois.
Kappa desconta a concordância por acaso: >0,6 é boa, >0,8 muito boa.

## Nas 50 reviews do gabarito (rótulo humano)

| | Qwen | Gemini |
|---|---|---|
| Tema | 80% | 94% |
| Gravidade | 92% | 84% |

## Tema: Qwen (linhas) × Gemini (colunas)

| Qwen \ Gemini | atendimento | comida_errada_ou_fria | demora | elogio_sem_problema | limpeza | outro | pedido_incorreto | preco |
|---|---|---|---|---|---|---|---|---|
| atendimento | 66 | 1 | 7 | 4 | 1 | 12 | 0 | 0 |
| comida_errada_ou_fria | 2 | 41 | 6 | 2 | 2 | 10 | 4 | 0 |
| demora | 1 | 1 | 56 | 2 | 1 | 4 | 1 | 0 |
| elogio_sem_problema | 0 | 0 | 0 | 87 | 0 | 7 | 0 | 0 |
| limpeza | 0 | 0 | 1 | 8 | 20 | 6 | 0 | 0 |
| outro | 3 | 1 | 4 | 11 | 1 | 47 | 0 | 0 |
| pedido_incorreto | 9 | 2 | 2 | 0 | 0 | 3 | 38 | 0 |
| preco | 1 | 1 | 1 | 3 | 1 | 10 | 0 | 9 |