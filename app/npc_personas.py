"""Validated, file-backed NPC persona inventory.

Personas supply voice and display identity only. Concrete game plugins remain
the sole source of legal actions; callers must never treat persona text as an
action or rules definition.
"""

from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote


DEFAULT_PERSONA_DIR = Path(__file__).resolve().parent / "config" / "npc_personas"
DEFAULT_AVATAR_DIR = Path(__file__).resolve().parent / "config" / "npc_avatars"
PERSONA_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
AVATAR_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}\.(?:png|jpe?g|webp|gif)$",
    re.IGNORECASE,
)
MAX_DISPLAY_NAME_LENGTH = 80
MAX_PERSONA_LENGTH = 4000
ALLOWED_SUPPORT_FILES = {"README.md", "_schema.json", "_example.json"}


class PersonaConfigError(ValueError):
    pass


@dataclass(frozen=True)
class NpcPersona:
    id: str
    display_name: str
    persona: str
    avatar: str | None = None

    def public_identity(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "avatar_url": (
                f"/api/npc-avatars/{quote(self.avatar, safe='')}"
                if self.avatar else None
            ),
        }

    def model_context(self) -> dict[str, str]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "persona": self.persona,
        }


def persona_directory(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.getenv("DUEL_NPC_PERSONAS_DIR", DEFAULT_PERSONA_DIR))


def avatar_directory(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.getenv("DUEL_NPC_AVATARS_DIR", DEFAULT_AVATAR_DIR))


def resolve_avatar_file(
    filename: str, *, path: str | Path | None = None
) -> Path:
    if not isinstance(filename, str) or not AVATAR_FILENAME_RE.fullmatch(filename):
        raise PersonaConfigError("NPC 头像文件名非法")
    directory = avatar_directory(path).resolve()
    candidate = (directory / filename).resolve()
    if candidate.parent != directory:
        raise PersonaConfigError("NPC 头像路径越界")
    if not candidate.is_file():
        raise PersonaConfigError(f"NPC 头像文件不存在：{filename}")
    return candidate


def _parse_persona(path: Path) -> NpcPersona:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PersonaConfigError(f"NPC 人设文件无法读取：{path.name}") from exc
    if (
        not isinstance(raw, dict)
        or not {"id", "display_name", "persona"} <= set(raw)
        or set(raw) - {"id", "display_name", "persona", "avatar"}
    ):
        raise PersonaConfigError(
            f"NPC 人设 {path.name} 只能包含 id/display_name/persona/avatar"
        )
    persona_id = raw.get("id")
    display_name = raw.get("display_name")
    persona = raw.get("persona")
    avatar = raw.get("avatar")
    if not isinstance(persona_id, str) or not PERSONA_ID_RE.fullmatch(persona_id):
        raise PersonaConfigError(f"NPC 人设 {path.name} 的 id 非法")
    if not isinstance(display_name, str) or not display_name.strip():
        raise PersonaConfigError(f"NPC 人设 {path.name} 的 display_name 不能为空")
    display_name = display_name.strip()
    if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
        raise PersonaConfigError(f"NPC 人设 {path.name} 的 display_name 过长")
    if not isinstance(persona, str) or not persona.strip():
        raise PersonaConfigError(f"NPC 人设 {path.name} 的 persona 不能为空")
    persona = persona.strip()
    if len(persona) > MAX_PERSONA_LENGTH:
        raise PersonaConfigError(f"NPC 人设 {path.name} 的 persona 过长")
    if avatar is not None:
        if not isinstance(avatar, str) or not AVATAR_FILENAME_RE.fullmatch(avatar):
            raise PersonaConfigError(f"NPC 人设 {path.name} 的 avatar 非法")
        resolve_avatar_file(avatar)
    return NpcPersona(persona_id, display_name, persona, avatar)


def load_personas(path: str | Path | None = None) -> list[NpcPersona]:
    directory = persona_directory(path)
    if not directory.exists() or not directory.is_dir():
        raise PersonaConfigError(f"NPC 人设目录不存在：{directory}")
    personas: list[NpcPersona] = []
    seen_ids: set[str] = set()
    for item in sorted(directory.iterdir(), key=lambda entry: entry.name):
        if item.name in ALLOWED_SUPPORT_FILES:
            continue
        if not item.is_file() or item.suffix.lower() != ".json" or item.name.startswith("_"):
            raise PersonaConfigError(f"NPC 人设目录包含非法文件：{item.name}")
        parsed = _parse_persona(item)
        if parsed.id in seen_ids:
            raise PersonaConfigError(f"NPC 人设 id 重复：{parsed.id}")
        seen_ids.add(parsed.id)
        personas.append(parsed)
    return personas


def select_personas(
    count: int,
    *,
    path: str | Path | None = None,
    rng: random.Random | None = None,
) -> list[NpcPersona]:
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise PersonaConfigError("NPC 抽取数量必须是非负整数")
    if count > 4:
        raise PersonaConfigError("每局最多补入 4 名 NPC")
    inventory = load_personas(path)
    if len(inventory) < count:
        raise PersonaConfigError(
            f"NPC 人设库存不足：需要 {count} 份，当前只有 {len(inventory)} 份"
        )
    chooser = rng or random.SystemRandom()
    return chooser.sample(inventory, count)


def get_persona(
    persona_id: str, *, path: str | Path | None = None
) -> NpcPersona:
    for persona in load_personas(path):
        if persona.id == persona_id:
            return persona
    raise PersonaConfigError(f"NPC 人设不存在：{persona_id}")


def public_avatar_url(persona_id: str) -> str | None:
    return get_persona(persona_id).public_identity()["avatar_url"]
