# Shop ledger agent: dataset

- `prices.json`   price list with units and aliases
- `rules.md`      the rules the agent must follow
- `chats/`        20 customer WhatsApp threads (the agent's INPUT)
- `labels/`       expected events + expected ledger per customer (the answer key)
- `ledger.py`     deterministic engine: events -> balances (no LLM)

Eval idea: LLM(chat) -> events -> ledger.py -> compare with labels/*.json
Deliberate hard cases: Sharma ji (ambiguous payment), Mahesh (stated 240 vs bill 205),
Gupta ji (vague size), Sunil (partial + dispute), Seema (repeat order),
Pooja (overpay + credit), Deepak (two partial payments), Vikram (substitution).
