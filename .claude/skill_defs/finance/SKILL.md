---
name: Financial Tools (Theo Port)
description: Access financial data (balances, transactions, spending analysis) via Plaid.
---

# Financial Tools

This skill provides access to banking information using the code ported from "Project Theo".
It utilizes the Plaid API to fetch real-time financial data.

## Prerequisites
1.  **Plaid API Keys:** Ensure `PLAID_CLIENT_ID` and `PLAID_SECRET` are set in the environment or `.env` file.
2.  **Dependencies:** Ensure `plaid-python` and `python-dotenv` are installed.
    ```bash
    pip install plaid-python python-dotenv
    ```

## CLI Usage

A command-line interface `finance_cli.py` is available for easy interaction.

**Command:**
```bash
# Run from .agent/scripts directory
python -m theo_ports.finance_cli [COMMAND] [ARGS]
```

**Commands:**
- `accounts`: List all connected accounts and their current balances.
- `transactions --days N`: Show transactions from the last N days (default 30).
- `analysis --days N`: Show spending breakdown by category for the last N days.
- `connect`: Launches a local server (port 3000) for secure bank authentication.

## Data Storage
- All financial data is cached locally in `.agent/scripts/theo_ports/vault/financial/`.
- Credentials (Tokens) are stored securely in the same vault.

## Example

To check spending analysis:
```bash
python -m theo_ports.finance_cli analysis --days 60
```
