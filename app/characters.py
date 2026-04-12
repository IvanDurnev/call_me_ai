from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import OperationalError, ProgrammingError

from .extensions import db
from .models import Hero


OPENAI_VOICE_OPTIONS = [
    {"value": "alloy", "label": "Alloy"},
    {"value": "ash", "label": "Ash"},
    {"value": "ballad", "label": "Ballad"},
    {"value": "cedar", "label": "Cedar", "recommended": True},
    {"value": "coral", "label": "Coral"},
    {"value": "echo", "label": "Echo"},
    {"value": "marin", "label": "Marin", "recommended": True},
    {"value": "sage", "label": "Sage"},
    {"value": "shimmer", "label": "Shimmer"},
    {"value": "verse", "label": "Verse"},
]

SUPPORTED_REALTIME_VOICES = {option["value"] for option in OPENAI_VOICE_OPTIONS}
VOICE_FALLBACKS = {
    "fable": "sage",
    "nova": "marin",
    "onyx": "ash",
}

REALTIME_MODEL_OPTIONS = [
    "gpt-realtime",
    "gpt-realtime-2025-08-28",
    "gpt-4o-realtime-preview",
    "gpt-4o-realtime-preview-2024-10-01",
    "gpt-4o-realtime-preview-2024-12-17",
    "gpt-4o-realtime-preview-2025-06-03",
    "gpt-4o-mini-realtime-preview",
    "gpt-4o-mini-realtime-preview-2024-12-17",
]

TRANSCRIPTION_MODEL_OPTIONS = [
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
    "gpt-4o-transcribe-latest",
    "whisper-1",
]

NOISE_REDUCTION_OPTIONS = ["none", "near_field", "far_field"]

ELEVENLABS_DEFAULT_LLM = "gpt-4o-mini"
ELEVENLABS_TURN_EAGERNESS_ALIASES = {
    "low": "patient",
    "normal": "normal",
    "high": "eager",
    "patient": "patient",
    "eager": "eager",
}

DEFAULT_GREETING_PROMPT = (
    "Начни разговор первым. Коротко поздоровайся по-русски, как будто это живой "
    "телефонный звонок ребёнка любимому сказочному персонажу. Сразу держи роль героя "
    "и ни при каких обстоятельствах не называй себя ИИ, ассистентом, ботом, программой или моделью."
)


def build_runtime_instructions(character: dict[str, Any], *, end_call_mode: str = "function") -> str:
    settings = normalize_realtime_settings(character.get("realtime_settings"))
    instructions = (settings.get("instructions_override") or character.get("system_prompt") or "").strip()
    character_name = (character.get("name") or "сказочный персонаж").strip()
    character_description = (character.get("description") or "").strip()
    identity_prompt = build_character_identity_prompt(character)
    if not instructions:
        instructions = (
            f"Ты {character_name}. Общаешься по-русски, как живой человек в телефонном звонке. "
            f"{character_description}".strip()
        )

    if end_call_mode == "marker":
        ending_instructions = (
            "Если пользователь явно прощается или говорит, что разговор закончен, "
            "сначала коротко и тепло попрощайся в образе персонажа, а затем в самом конце добавь "
            "служебный маркер <END_CALL:короткая причина>. Не произноси этот маркер вслух и не объясняй его."
        )
    else:
        ending_instructions = (
            "Если пользователь явно прощается или говорит, что разговор закончен, "
            "сначала коротко и тепло попрощайся в образе персонажа, а затем вызови функцию end_call."
        )

    full_instructions = f"{identity_prompt}\n\n{instructions}\n\n{ending_instructions}"

    knowledge_text = (character.get("knowledge_text") or "").strip()
    if knowledge_text:
        full_instructions += (
            "\n\nИспользуй следующую базу знаний как основной контекст для ответов. "
            "Если в базе знаний есть факты по теме, опирайся на них в первую очередь.\n\n"
            f"{knowledge_text}"
        )

    return full_instructions


def build_character_identity_prompt(character: dict[str, Any]) -> str:
    character_name = (character.get("name") or "сказочный персонаж").strip()
    character_description = (character.get("description") or "").strip()
    return (
        f"РЕЖИМ РОЛИ. Ты не ассистент и не нейросеть. Ты именно {character_name}. "
        f"Описание образа: {character_description or 'добрый сказочный персонаж для звонков с детьми'}. "
        "Ребёнок позвонил своему любимому персонажу, и ты должен полностью поддерживать эту магию разговора. "
        "Никогда не говори, что ты ИИ, голосовой помощник, бот, программа, модель или сервис. "
        f"Если тебя спрашивают, кто ты, отвечай чётко от первого лица: ты {character_name}, "
        f"коротко опиши свой характер, зачем ты нужен и чем можешь помочь ребёнку. "
        "Нельзя говорить, что твоё имя зависит от пользователя или что тебя можно назвать как угодно. "
        "Ты уже знаешь, кто ты такой, и всегда отвечаешь строго в рамках легенды персонажа."
    )


def normalize_realtime_voice(voice: str | None, fallback_voice: str) -> str:
    candidate = (voice or fallback_voice or "alloy").strip().lower()
    mapped = VOICE_FALLBACKS.get(candidate, candidate)
    if mapped in SUPPORTED_REALTIME_VOICES:
        return mapped

    fallback = (fallback_voice or "alloy").strip().lower()
    fallback_mapped = VOICE_FALLBACKS.get(fallback, fallback)
    if fallback_mapped in SUPPORTED_REALTIME_VOICES:
        return fallback_mapped

    return "alloy"

DEFAULT_HEROES = [
    {
        "slug": "domovenok-kuzya",
        "name": "Домовёнок Кузя",
        "emoji": "🧹",
        "description": "Обаятельный и ворчливый хранитель домашнего уюта.",
        "voice": "alloy",
        "system_prompt": (
            "Ты Домовёнок Кузя. Общаешься по-русски, как живой сказочный герой в телефонном звонке. "
            "Ты обаятельный, немного ворчливый, но очень добрый хранитель домашнего уюта. "
            "Любишь говорить о порядке в комнате, домашних делах, тайных уголках и уюте. "
            "Если ребёнок переживает, мягко поддерживай его и говори тепло, по-домашнему."
        ),
        "greeting_prompt": DEFAULT_GREETING_PROMPT,
        "realtime_settings": {
            "model": "gpt-4o-realtime-preview",
            "input_transcription_model": "gpt-4o-mini-transcribe",
            "input_transcription_language": "ru",
            "noise_reduction_type": "near_field",
            "max_output_tokens": "inf",
            "output_audio_format": "pcm16",
            "output_audio_speed": 0.98,
        },
    },
    {
        "slug": "kot-matroskin",
        "name": "Кот Матроскин",
        "emoji": "🐱",
        "description": "Рассудительный и хозяйственный, с советом на каждый случай.",
        "voice": "verse",
        "system_prompt": (
            "Ты Кот Матроскин. Общаешься по-русски, как живой персонаж из мультфильма в телефонном звонке. "
            "Ты рассудительный, хозяйственный, иногда ироничный, но очень заботливый. "
            "Можешь давать понятные житейские советы, говорить о домашних животных, хозяйстве "
            "и простых радостях жизни. Отвечай тепло и с лёгкой кошачьей важностью."
        ),
        "greeting_prompt": DEFAULT_GREETING_PROMPT,
        "realtime_settings": {
            "model": "gpt-4o-realtime-preview",
            "input_transcription_model": "gpt-4o-mini-transcribe",
            "input_transcription_language": "ru",
            "noise_reduction_type": "near_field",
            "max_output_tokens": 800,
            "output_audio_format": "pcm16",
            "output_audio_speed": 1.0,
        },
    },
    {
        "slug": "cheburashka",
        "name": "Чебурашка",
        "emoji": "🧡",
        "description": "Самый добрый и искренний друг для важных разговоров.",
        "voice": "echo",
        "system_prompt": (
            "Ты Чебурашка. Общаешься по-русски, как очень добрый, искренний и немного наивный "
            "сказочный друг в телефонном звонке. Особенно хорошо умеешь говорить о дружбе, "
            "одиночестве, добрых поступках и заботе о других. Слушай внимательно, отвечай мягко "
            "и ободряюще, чтобы ребёнок чувствовал себя рядом с настоящим другом."
        ),
        "greeting_prompt": DEFAULT_GREETING_PROMPT,
        "realtime_settings": {
            "model": "gpt-4o-realtime-preview",
            "input_transcription_model": "gpt-4o-mini-transcribe",
            "input_transcription_language": "ru",
            "noise_reduction_type": "near_field",
            "max_output_tokens": 900,
            "output_audio_format": "pcm16",
            "output_audio_speed": 0.97,
        },
    },
    {
        "slug": "baba-yaga-kind",
        "name": "Баба Яга",
        "emoji": "🪄",
        "description": "Добрая лесная наставница, знающая секреты трав и зверей.",
        "voice": "sage",
        "system_prompt": (
            "Ты добрая версия Бабы Яги. Общаешься по-русски, как харизматичная лесная бабушка-наставница "
            "в телефонном звонке. Ты знаешь много о травах, лесных зверях, маленьких хитростях и сказочных "
            "секретах. Можешь немного поворчать для образа, но внутри ты заботливая, мудрая и очень тёплая."
        ),
        "greeting_prompt": DEFAULT_GREETING_PROMPT,
        "realtime_settings": {
            "model": "gpt-4o-realtime-preview",
            "input_transcription_model": "gpt-4o-mini-transcribe",
            "input_transcription_language": "ru",
            "noise_reduction_type": "near_field",
            "max_output_tokens": 850,
            "output_audio_format": "pcm16",
            "output_audio_speed": 0.95,
        },
    },
    {
        "slug": "ivan-tsarevich",
        "name": "Иван Царевич",
        "emoji": "🛡️",
        "description": "Герой-защитник, который знает, как не бояться трудностей.",
        "voice": "cedar",
        "system_prompt": (
            "Ты Иван Царевич. Общаешься по-русски, как смелый, благородный и добрый герой в телефонном звонке. "
            "Любишь рассказывать о подвигах, драконах, дороге, испытаниях и победе над страхом. "
            "Поддерживай ребёнка уверенно и спокойно, помогай чувствовать храбрость и веру в себя."
        ),
        "greeting_prompt": DEFAULT_GREETING_PROMPT,
        "realtime_settings": {
            "model": "gpt-4o-realtime-preview",
            "input_transcription_model": "gpt-4o-mini-transcribe",
            "input_transcription_language": "ru",
            "noise_reduction_type": "near_field",
            "max_output_tokens": 850,
            "output_audio_format": "pcm16",
            "output_audio_speed": 1.0,
        },
    },
]


def list_characters(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    try:
        query = Hero.query.order_by(Hero.sort_order.asc(), Hero.id.asc())
        if not include_inactive:
            query = query.filter(Hero.is_active.is_(True))
        heroes = query.all()
        if heroes:
            return [_serialize_hero(hero) for hero in heroes]
    except (OperationalError, ProgrammingError) as exc:
        logging.warning("Heroes table is not ready yet: %s", exc)
        db.session.rollback()

    return [
        _serialize_default_hero(hero, index)
        for index, hero in enumerate(DEFAULT_HEROES)
        if include_inactive or hero.get("is_active", True)
    ]


def get_character(slug: str, *, include_inactive: bool = True) -> dict | None:
    try:
        query = Hero.query.filter_by(slug=slug)
        if not include_inactive:
            query = query.filter(Hero.is_active.is_(True))
        hero = query.first()
        if hero:
            return _serialize_hero(hero)
    except (OperationalError, ProgrammingError) as exc:
        logging.warning("Hero lookup fell back to defaults for %s: %s", slug, exc)
        db.session.rollback()

    for index, hero in enumerate(DEFAULT_HEROES):
        if hero["slug"] == slug and (include_inactive or hero.get("is_active", True)):
            return _serialize_default_hero(hero, index)
    return None


def ensure_default_heroes() -> None:
    try:
        existing = {hero.slug: hero for hero in Hero.query.all()}
    except (OperationalError, ProgrammingError) as exc:
        logging.warning("Skipping hero seeding until migrations are applied: %s", exc)
        db.session.rollback()
        return

    created = False
    for index, payload in enumerate(DEFAULT_HEROES):
        hero = existing.get(payload["slug"])
        if hero:
            continue

        hero = Hero(
            slug=payload["slug"],
            name=payload["name"],
            description=payload["description"],
            emoji=payload.get("emoji") or "AI",
            voice=payload.get("voice") or "alloy",
            system_prompt=payload.get("system_prompt"),
            greeting_prompt=payload.get("greeting_prompt") or DEFAULT_GREETING_PROMPT,
            realtime_settings_json=normalize_realtime_settings(payload.get("realtime_settings")),
            sort_order=index,
            is_active=True,
        )
        db.session.add(hero)
        created = True

    if created:
        db.session.commit()


def normalize_realtime_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(settings or {})
    normalized: dict[str, Any] = {}

    if payload.get("model"):
        normalized["model"] = str(payload["model"]).strip()

    transcription_model = str(payload.get("input_transcription_model") or "").strip()
    if transcription_model:
        normalized["input_transcription_model"] = transcription_model

    transcription_language = str(payload.get("input_transcription_language") or "").strip()
    if transcription_language:
        normalized["input_transcription_language"] = transcription_language

    transcription_prompt = str(payload.get("input_transcription_prompt") or "").strip()
    if transcription_prompt:
        normalized["input_transcription_prompt"] = transcription_prompt

    noise_reduction_type = str(payload.get("noise_reduction_type") or "").strip()
    if noise_reduction_type and noise_reduction_type != "none":
        normalized["noise_reduction_type"] = noise_reduction_type

    max_output_tokens = payload.get("max_output_tokens")
    if max_output_tokens in {"inf", "INF"}:
        normalized["max_output_tokens"] = "inf"
    elif max_output_tokens not in {None, ""}:
        normalized["max_output_tokens"] = int(max_output_tokens)

    output_audio_format = str(payload.get("output_audio_format") or "").strip()
    if output_audio_format:
        normalized["output_audio_format"] = output_audio_format

    output_audio_speed = payload.get("output_audio_speed")
    if output_audio_speed not in {None, ""}:
        normalized["output_audio_speed"] = float(output_audio_speed)

    instructions_override = str(payload.get("instructions_override") or "").strip()
    if instructions_override:
        normalized["instructions_override"] = instructions_override

    elevenlabs_agent_id = str(payload.get("elevenlabs_agent_id") or "").strip()
    if elevenlabs_agent_id:
        normalized["elevenlabs_agent_id"] = elevenlabs_agent_id

    elevenlabs_llm = str(payload.get("elevenlabs_llm") or "").strip()
    if elevenlabs_llm:
        normalized["elevenlabs_llm"] = elevenlabs_llm

    elevenlabs_turn_eagerness = str(payload.get("elevenlabs_turn_eagerness") or "").strip().lower()
    normalized_turn_eagerness = ELEVENLABS_TURN_EAGERNESS_ALIASES.get(elevenlabs_turn_eagerness, "")
    if normalized_turn_eagerness:
        normalized["elevenlabs_turn_eagerness"] = normalized_turn_eagerness

    provider = str(payload.get("provider") or "").strip().lower()
    if provider in {"openai", "elevenlabs"}:
        normalized["provider"] = provider

    return normalized


def build_realtime_session_config(character: dict[str, Any], fallback_model: str, fallback_voice: str) -> dict[str, Any]:
    settings = normalize_realtime_settings(character.get("realtime_settings"))
    instructions = build_runtime_instructions(character, end_call_mode="function")

    model = settings.get("model") or fallback_model
    voice = normalize_realtime_voice(character.get("voice"), fallback_voice)
    transcription_model = settings.get("input_transcription_model") or "gpt-4o-mini-transcribe"

    session: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "voice": voice,
        "input_audio_format": "pcm16",
        "output_audio_format": settings.get("output_audio_format") or "pcm16",
        "input_audio_transcription": {
            "model": transcription_model,
        },
        # We drive push-to-talk manually from the client, so turn detection must stay off.
        "turn_detection": None,
        "tool_choice": "auto",
        "tools": [
            {
                "type": "function",
                "name": "end_call",
                "description": (
                    "Call this only after you have already said a brief goodbye in character "
                    "and only when the user clearly indicates the conversation should end."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Short reason for ending the call in Russian.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        ],
    }

    transcription_language = settings.get("input_transcription_language")
    if transcription_language:
        session["input_audio_transcription"]["language"] = transcription_language

    transcription_prompt = settings.get("input_transcription_prompt")
    if transcription_prompt:
        session["input_audio_transcription"]["prompt"] = transcription_prompt

    noise_reduction_type = settings.get("noise_reduction_type")
    if noise_reduction_type:
        session["input_audio_noise_reduction"] = {"type": noise_reduction_type}

    max_output_tokens = settings.get("max_output_tokens")
    if max_output_tokens:
        session["max_response_output_tokens"] = max_output_tokens

    return session


def _serialize_hero(hero: Hero) -> dict[str, Any]:
    return {
        "slug": hero.slug,
        "name": hero.name,
        "description": hero.description or "",
        "emoji": hero.emoji or "AI",
        "voice": hero.voice or "alloy",
        "elevenlabs_voice_id": hero.elevenlabs_voice_id or "",
        "elevenlabs_first_message": hero.elevenlabs_first_message or "",
        "avatar_path": hero.avatar_path,
        "knowledge_file_name": hero.knowledge_file_name,
        "knowledge_file_path": hero.knowledge_file_path,
        "knowledge_text": hero.knowledge_text or "",
        "system_prompt": hero.system_prompt or "",
        "greeting_prompt": hero.greeting_prompt or DEFAULT_GREETING_PROMPT,
        "realtime_settings": normalize_realtime_settings(hero.realtime_settings_json),
        "sort_order": hero.sort_order,
        "is_active": hero.is_active,
    }


def _serialize_default_hero(payload: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "slug": payload["slug"],
        "name": payload["name"],
        "description": payload.get("description") or "",
        "emoji": payload.get("emoji") or "AI",
        "voice": payload.get("voice") or "alloy",
        "elevenlabs_voice_id": payload.get("elevenlabs_voice_id") or "",
        "elevenlabs_first_message": payload.get("elevenlabs_first_message") or "",
        "avatar_path": payload.get("avatar_path"),
        "knowledge_file_name": payload.get("knowledge_file_name"),
        "knowledge_file_path": payload.get("knowledge_file_path"),
        "knowledge_text": payload.get("knowledge_text") or "",
        "system_prompt": payload.get("system_prompt") or "",
        "greeting_prompt": payload.get("greeting_prompt") or DEFAULT_GREETING_PROMPT,
        "realtime_settings": normalize_realtime_settings(payload.get("realtime_settings")),
        "sort_order": payload.get("sort_order", index),
        "is_active": payload.get("is_active", True),
    }
