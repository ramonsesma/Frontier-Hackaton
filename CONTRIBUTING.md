# Contributing

Thanks for contributing to Frontier Hackathon.

## Local setup

- Python 3.12
- Docker (optional)

```bash
pip install -r requirements.txt
```

## Run the test suite

```bash
python test_rfb.py
python test_server.py
python test_agent.py
```

## Pull requests

1. Keep changes focused and document any behavior change.
2. Do not commit credentials, tokens, desktop passwords, recordings, or screenshots containing sensitive data.
3. Add or update tests when changing the agent, RFB client, or HTTP service.
4. Ensure the full test suite passes before opening a pull request.
