import os
import sys
import uvicorn
from dotenv import load_dotenv

# Load .env into the environment BEFORE we check for keys.
load_dotenv()

if __name__ == "__main__":
    # Fail fast with a clear message if the one key we actually need is missing.
    # (Apify / Hunter keys get added to this check when those features land.)
    missing = [v for v in ["ANTHROPIC_API_KEY"] if not os.getenv(v)]
    if missing:
        print(f"❌  Missing required env vars: {missing}")
        print("    Copy .env.example to .env and add your key(s).")
        sys.exit(1)

    print("🚀  Job Search Agent API starting...")
    print("📖  Interactive docs → http://localhost:8000/docs")
    print("     Press Ctrl+C to stop.\n")

    uvicorn.run(
        "api.main:app",   # import path to the app object: api/main.py → app
        host="0.0.0.0",   # reachable by n8n and other local processes, not just Python
        port=8000,
        reload=True,      # auto-restart on code changes (dev convenience)
        log_level="info",
    )