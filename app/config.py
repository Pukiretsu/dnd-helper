import os

# JWT Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "Llave de fallback")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 día

# Database Configuration
DB_FILE = "data/sessions.db" # La base de datos se guardará en la carpeta 'data'

# Console Tags for logging
TAGS = {
    "server":        "    -->> [\033[96mSERVER\033[0m]   ",
    "db":            "    -->> [\033[93mDATABASE\033[0m] ",
    "websocket":     "    -->> [\033[92mWEBSOCKET\033[0m]",
    "app_error":     "    -->> [\033[91mAPP\033[0m]      ",
    "app_log":       "    -->> [\033[95mAPP\033[0m]      ",
}
