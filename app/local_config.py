"""Fixed identities and paths for the standalone loopback runtime only."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_HUMAN_ID = "local-human"
LOCAL_AI_ID = "local-ai"
LOCAL_HUMAN_NAME = os.getenv("DUEL_LOCAL_HUMAN_NAME", "本地玩家").strip() or "本地玩家"
LOCAL_AI_NAME = os.getenv("DUEL_LOCAL_AI_NAME", "本地小机").strip() or "本地小机"
LOCAL_DB_PATH = PROJECT_ROOT / "data" / "local-duel.db"
LOCAL_PERSONA_DIR = PROJECT_ROOT / "local" / "npc_personas"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8772"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def configure_local_environment() -> None:
    """Set standalone defaults before importing modules with import-time config."""
    os.environ["DUEL_DB_PATH"] = os.getenv(
        "DUEL_LOCAL_DB_PATH", str(LOCAL_DB_PATH)
    )
    os.environ["DUEL_NPC_PERSONAS_DIR"] = os.getenv(
        "DUEL_LOCAL_PERSONAS_DIR", str(LOCAL_PERSONA_DIR)
    )


def local_base_url() -> str:
    value = os.getenv("DUEL_LOCAL_BASE_URL", DEFAULT_LOCAL_BASE_URL).strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("DUEL_LOCAL_BASE_URL 必须是 localhost/127.0.0.1/::1 的 HTTP 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("DUEL_LOCAL_BASE_URL 不能包含认证信息、query 或 fragment")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("DUEL_LOCAL_BASE_URL 端口无效") from exc
    return value


def local_identity_headers() -> list[tuple[bytes, bytes]]:
    machines = json.dumps(
        [{"id": LOCAL_AI_ID, "name": LOCAL_AI_NAME}],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded_machines = base64.urlsafe_b64encode(machines).decode("ascii").rstrip("=")
    return [
        (b"x-duel-human-player", LOCAL_HUMAN_ID.encode("ascii")),
        (b"x-duel-human-name", quote(LOCAL_HUMAN_NAME, safe="").encode("ascii")),
        (b"x-duel-bound-ais", encoded_machines.encode("ascii")),
    ]
