import os

# O servidor lê a configuração na importação: ambiente de teste sem OAuth real.
os.environ.setdefault("MCP_AUTH_DISABLED", "true")
os.environ.setdefault("COLIBRI_CLIENT_ID", "chave-de-teste")
os.environ.setdefault("COLIBRI_BASE_URL", "https://colibri.test")
os.environ.setdefault("PUBLIC_URL", "http://localhost:8000")
