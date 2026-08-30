"""One-command standalone launcher for local human + AI play."""

from __future__ import annotations

import os


def main() -> None:
    # Set before uvicorn imports app.main so the web identity layer can expose
    # the built-in local human/AI pair. Production deployments do not use this.
    os.environ["DUEL_STANDALONE_LOCAL"] = "1"
    os.environ.setdefault("DUEL_NPC_PROVIDER", "disabled")

    import uvicorn

    host = "127.0.0.1"
    port = int(os.getenv("DUEL_LOCAL_PORT", "8772"))
    human_id = os.getenv("DUEL_LOCAL_HUMAN_ID", "local-human")
    ai_id = os.getenv("DUEL_LOCAL_AI_ID", "local-ai")
    print(f"CedarDuet local web: http://{host}:{port}/")
    print(f"Local pair: human={human_id} ai={ai_id}")
    print(f"AI endpoint: http://{host}:{port}/mcp/play")
    uvicorn.run("app.main:app", host=host, port=port, workers=1)


if __name__ == "__main__":
    main()
