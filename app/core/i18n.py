"""Fixed bot strings in the three languages.

Only what the bot says on its own goes here: command help, button labels,
reminders, error lines. Everything conversational comes from the model in the
active language. Keys are plain text; adapters send them without markup, so a
task name with `*` or `_` in it never breaks a message.
"""

from datetime import date, datetime, time

LANGUAGES = ("en", "ru", "fr")
DEFAULT_LANGUAGE = "en"

# Dates are shown the European way in every language: 31.12.2026.
DATE_FORMAT = "%d.%m.%Y"


def format_day(day: date) -> str:
    return day.strftime(DATE_FORMAT)


def format_moment(moment: datetime) -> str:
    """`31.12.2026 14:05`; the caller converts to the user's timezone first."""
    return moment.strftime(f"{DATE_FORMAT} %H:%M")


def format_clock(moment: datetime) -> str:
    """`14:05`; the caller converts to the user's timezone first."""
    return moment.strftime("%H:%M")


def parse_clock(value: str) -> time:
    """`21:00` → time; raises ValueError for anything else."""
    return datetime.strptime(value.strip(), "%H:%M").time()


def parse_day(value: str) -> date:
    """Accepts `31.12.2026` and `2026-12-31`; raises ValueError otherwise."""
    text = value.strip()
    try:
        return datetime.strptime(text, DATE_FORMAT).date()
    except ValueError:
        return date.fromisoformat(text)


STRINGS: dict[str, dict[str, str]] = {
    "welcome": {
        "en": "Hi! I'm your Habitica coach. Here is what I can do so far:",
        "ru": "Привет! Я твой тренер по Habitica. Вот что я уже умею:",
        "fr": "Salut ! Je suis ton coach Habitica. Voici ce que je sais faire pour l'instant :",
    },
    "help_header": {
        "en": "Commands:",
        "ru": "Команды:",
        "fr": "Commandes :",
    },
    "help_help": {
        "en": "this list",
        "ru": "этот список",
        "fr": "cette liste",
    },
    "help_language": {
        "en": "reply language",
        "ru": "язык ответов",
        "fr": "langue des réponses",
    },
    "help_stats": {
        "en": "your Habitica stats",
        "ru": "твои показатели в Habitica",
        "fr": "tes stats Habitica",
    },
    "help_sync": {
        "en": "pull a fresh snapshot from Habitica now",
        "ru": "снять свежий снимок Habitica прямо сейчас",
        "fr": "prendre un instantané Habitica maintenant",
    },
    "help_new": {
        "en": "start a fresh conversation (the current one is summarised)",
        "ru": "начать разговор заново (текущий будет кратко сохранён)",
        "fr": "repartir sur une conversation neuve (l'actuelle est résumée)",
    },
    "help_session": {
        "en": "show the current session, or open a long planning one",
        "ru": "показать текущую сессию или открыть долгую сессию планирования",
        "fr": "voir la session en cours, ou en ouvrir une longue de planification",
    },
    "help_model": {
        "en": "which model answers you, with what it has cost so far",
        "ru": "какая модель тебе отвечает и сколько это стоило",
        "fr": "quel modèle te répond, et ce qu'il a coûté jusqu'ici",
    },
    "model_header": {
        "en": "Models (● is yours):",
        "ru": "Модели (● — твоя):",
        "fr": "Modèles (● = le tien) :",
    },
    "model_footer": {
        "en": "/model strong|cheap to switch. The weekly coach always uses strong.",
        "ru": "/model strong|cheap — переключить. Еженедельный тренер всегда на strong.",
        "fr": "/model strong|cheap pour changer. Le coach hebdomadaire reste sur strong.",
    },
    "model_set": {
        "en": "Switched to {name} ({id}).",
        "ru": "Переключено на {name} ({id}).",
        "fr": "Passé sur {name} ({id}).",
    },
    "model_invalid": {
        "en": "I have two models: strong and cheap. Example: /model cheap",
        "ru": "У меня две модели: strong и cheap. Например: /model cheap",
        "fr": "J'ai deux modèles : strong et cheap. Exemple : /model cheap",
    },
    "help_glossary": {
        "en": "words the voice recogniser gets wrong for you",
        "ru": "слова, которые распознавание речи путает",
        "fr": "les mots que la reconnaissance vocale écorche",
    },
    "help_tts": {
        "en": "answer with a voice note too",
        "ru": "отвечать ещё и голосом",
        "fr": "répondre aussi par message vocal",
    },
    "tts_on": {
        "en": "Voice replies are on: I'll send a voice note with every short answer.",
        "ru": "Голосовые ответы включены: к каждому короткому ответу будет голосовое.",
        "fr": "Réponses vocales activées : chaque réponse courte arrive aussi en vocal.",
    },
    "tts_off": {
        "en": "Voice replies are off. /tts on turns them on.",
        "ru": "Голосовые ответы выключены. /tts on — включить.",
        "fr": "Réponses vocales désactivées. /tts on les active.",
    },
    "tts_usage": {
        "en": "Usage: /tts on, /tts off",
        "ru": "Использование: /tts on, /tts off",
        "fr": "Usage : /tts on, /tts off",
    },
    "tts_not_configured": {
        "en": "Voice replies need OPENROUTER_API_KEY on the server.",
        "ru": "Для голосовых ответов на сервере нужен OPENROUTER_API_KEY.",
        "fr": "Les réponses vocales demandent OPENROUTER_API_KEY sur le serveur.",
    },
    "glossary_empty": {
        "en": "Your glossary is empty. /glossary add <term> teaches me a spelling.",
        "ru": "Глоссарий пуст. /glossary add <слово> — научить меня написанию.",
        "fr": "Ton glossaire est vide. /glossary add <terme> m'apprend une orthographe.",
    },
    "glossary_list": {
        "en": "Glossary:\n{terms}",
        "ru": "Глоссарий:\n{terms}",
        "fr": "Glossaire :\n{terms}",
    },
    "glossary_usage": {
        "en": "Usage: /glossary, /glossary add <term>, /glossary remove <term>",
        "ru": "Использование: /glossary, /glossary add <слово>, /glossary remove <слово>",
        "fr": "Usage : /glossary, /glossary add <terme>, /glossary remove <terme>",
    },
    "glossary_added": {
        "en": "Added “{term}” to your glossary.",
        "ru": "«{term}» добавлено в глоссарий.",
        "fr": "« {term} » ajouté au glossaire.",
    },
    "glossary_exists": {
        "en": "“{term}” is already in your glossary.",
        "ru": "«{term}» уже есть в глоссарии.",
        "fr": "« {term} » est déjà dans le glossaire.",
    },
    "glossary_removed": {
        "en": "Removed “{term}” from your glossary.",
        "ru": "«{term}» удалено из глоссария.",
        "fr": "« {term} » retiré du glossaire.",
    },
    "glossary_missing": {
        "en": "“{term}” is not in your glossary.",
        "ru": "«{term}» нет в глоссарии.",
        "fr": "« {term} » n'est pas dans le glossaire.",
    },
    "glossary_bad_term": {
        "en": "Can't add that: {reason}.",
        "ru": "Не могу добавить: {reason}.",
        "fr": "Impossible d'ajouter ça : {reason}.",
    },
    "session_none": {
        "en": "No open session. Just write to me and one starts.",
        "ru": "Открытой сессии нет. Просто напиши — и она начнётся.",
        "fr": "Pas de session ouverte. Écris-moi et elle commence.",
    },
    "session_closed": {
        "en": "Session closed. Your next message starts a fresh one.",
        "ru": "Сессия закрыта. Следующее сообщение начнёт новую.",
        "fr": "Session fermée. Ton prochain message en ouvre une nouvelle.",
    },
    "session_info": {
        "en": "{kind} session{topic} · started {age} ago · last message {idle} ago · "
        "{count} messages",
        "ru": "{kind} сессия{topic} · началась {age} назад · последнее сообщение {idle} назад · "
        "сообщений: {count}",
        "fr": "session {kind}{topic} · ouverte depuis {age} · dernier message il y a {idle} · "
        "{count} messages",
    },
    "session_kind_quick": {
        "en": "Quick",
        "ru": "Быстрая",
        "fr": "rapide",
    },
    "session_kind_planning": {
        "en": "Planning",
        "ru": "Плановая",
        "fr": "de planification",
    },
    "session_planning_opened": {
        "en": "Planning session opened: {topic}. It survives two hours of silence; "
        "/new closes it earlier.",
        "ru": "Сессия планирования открыта: {topic}. Она живёт два часа тишины; "
        "/new закроет раньше.",
        "fr": "Session de planification ouverte : {topic}. Elle survit à deux heures de "
        "silence ; /new la ferme avant.",
    },
    "session_planning_needs_topic": {
        "en": "Give the session a topic: /session planning learning Latin",
        "ru": "Укажи тему сессии: /session planning выучить латынь",
        "fr": "Donne un sujet à la session : /session planning apprendre le latin",
    },
    "llm_not_configured": {
        "en": "I can't hold a conversation on this install: OPENROUTER_API_KEY is not set. "
        "Commands still work — try /help.",
        "ru": "Разговаривать на этой установке я не могу: не задан OPENROUTER_API_KEY. "
        "Команды работают — попробуй /help.",
        "fr": "Je ne peux pas discuter sur cette installation : OPENROUTER_API_KEY n'est pas "
        "défini. Les commandes marchent — essaie /help.",
    },
    "llm_error": {
        "en": "The model didn't answer: {error}",
        "ru": "Модель не ответила: {error}",
        "fr": "Le modèle n'a pas répondu : {error}",
    },
    "llm_empty_reply": {
        "en": "(The model returned an empty answer. Try rephrasing.)",
        "ru": "(Модель вернула пустой ответ. Попробуй переформулировать.)",
        "fr": "(Le modèle a renvoyé une réponse vide. Essaie de reformuler.)",
    },
    "action_failed": {
        "en": "Couldn't {action}: {error}",
        "ru": "Не удалось {action}: {error}",
        "fr": "Impossible de {action} : {error}",
    },
    "voice_not_configured": {
        "en": "Voice needs OPENROUTER_API_KEY on the server. Please type for now.",
        "ru": "Для голоса на сервере нужен OPENROUTER_API_KEY. Пока напиши текстом.",
        "fr": "La voix demande OPENROUTER_API_KEY sur le serveur. Écris-moi pour l'instant.",
    },
    "asr_error": {
        "en": "I couldn't transcribe that ({error}). Try again, or type it.",
        "ru": "Не удалось распознать ({error}). Попробуй ещё раз или напиши текстом.",
        "fr": "Je n'ai pas pu transcrire ça ({error}). Réessaie, ou écris-le.",
    },
    "voice_heard_nothing": {
        "en": "I heard nothing in that voice note.",
        "ru": "В этом голосовом ничего не слышно.",
        "fr": "Je n'ai rien entendu dans ce vocal.",
    },
    "voice_heard": {
        "en": "I heard:\n{text}",
        "ru": "Я услышал:\n{text}",
        "fr": "J'ai entendu :\n{text}",
    },
    "voice_correct": {
        "en": "✅ Correct",
        "ru": "✅ Верно",
        "fr": "✅ Correct",
    },
    "voice_amend": {
        "en": "✏️ Amend",
        "ru": "✏️ Исправить",
        "fr": "✏️ Corriger",
    },
    "voice_amend_prompt": {
        "en": "What should it say? Type the correction.",
        "ru": "Как должно быть? Напиши исправление.",
        "fr": "Que fallait-il comprendre ? Écris la correction.",
    },
    "glossary_offer": {
        "en": "➕ Add “{term}”",
        "ru": "➕ Добавить «{term}»",
        "fr": "➕ Ajouter « {term} »",
    },
    "voice_dropped": {
        "en": "(The unconfirmed transcript is dropped.)",
        "ru": "(Неподтверждённая расшифровка отброшена.)",
        "fr": "(La transcription non confirmée est abandonnée.)",
    },
    "unsupported_message": {
        "en": "I can only read text for now.",
        "ru": "Пока я понимаю только текст.",
        "fr": "Je ne lis que du texte pour l'instant.",
    },
    "unknown_command": {
        "en": "I don't know /{command}. Try /help.",
        "ru": "Я не знаю команду /{command}. Попробуй /help.",
        "fr": "Je ne connais pas /{command}. Essaie /help.",
    },
    "unknown_button": {
        "en": "That button has expired. Send the request again.",
        "ru": "Эта кнопка уже не действует. Отправь запрос ещё раз.",
        "fr": "Ce bouton a expiré. Renvoie la demande.",
    },
    "language_current": {
        "en": "Replies are in English. /language en|ru|fr to change.",
        "ru": "Отвечаю по-русски. /language en|ru|fr — чтобы сменить.",
        "fr": "Je réponds en français. /language en|ru|fr pour changer.",
    },
    "language_set": {
        "en": "Done, I'll reply in English.",
        "ru": "Готово, буду отвечать по-русски.",
        "fr": "C'est fait, je répondrai en français.",
    },
    "language_invalid": {
        "en": "I speak en, ru and fr. Example: /language ru",
        "ru": "Я говорю на en, ru и fr. Например: /language en",
        "fr": "Je parle en, ru et fr. Exemple : /language en",
    },
    "stats_line": {
        "en": "❤️ {hp}/{max_hp}  🔵 {mp}/{max_mp}  ⭐ {exp}/{to_next_level}  💰 {gp}\n"
        "Level {lvl} {class_name} · as of {time}",
        "ru": "❤️ {hp}/{max_hp}  🔵 {mp}/{max_mp}  ⭐ {exp}/{to_next_level}  💰 {gp}\n"
        "Уровень {lvl}, {class_name} · на {time}",
        "fr": "❤️ {hp}/{max_hp}  🔵 {mp}/{max_mp}  ⭐ {exp}/{to_next_level}  💰 {gp}\n"
        "Niveau {lvl} {class_name} · à {time}",
    },
    "sync_done": {
        "en": "Synced with Habitica: {tasks} tasks, {changed} changed, {events} events.",
        "ru": "Синхронизировано с Habitica: задач — {tasks}, изменилось — {changed}, "
        "событий — {events}.",
        "fr": "Synchronisé avec Habitica : {tasks} tâches, {changed} modifiées, "
        "{events} événements.",
    },
    "habitica_error": {
        "en": "Habitica didn't answer properly: {error}",
        "ru": "Habitica не ответила как следует: {error}",
        "fr": "Habitica n'a pas répondu correctement : {error}",
    },
    # ---- reminders (P06) ----
    "help_remind": {
        "en": "daily reminder of what is still due (default 21:00), or off",
        "ru": "ежедневное напоминание о невыполненном (по умолчанию 21:00) или выключить",
        "fr": "rappel quotidien de ce qui reste à faire (21:00 par défaut), ou off",
    },
    "remind_current": {
        "en": "The daily reminder is at {time}. /remind HH:MM to change it, /remind off to stop "
        "it.",
        "ru": "Ежедневное напоминание в {time}. /remind ЧЧ:ММ — изменить, /remind off — выключить.",
        "fr": "Le rappel quotidien est à {time}. /remind HH:MM pour le changer, /remind off pour "
        "l'arrêter.",
    },
    "remind_none": {
        "en": "The daily reminder is off. /remind HH:MM to set one.",
        "ru": "Ежедневное напоминание выключено. /remind ЧЧ:ММ — включить.",
        "fr": "Le rappel quotidien est désactivé. /remind HH:MM pour en définir un.",
    },
    "remind_set": {
        "en": "Daily reminder set to {time}.",
        "ru": "Ежедневное напоминание установлено на {time}.",
        "fr": "Rappel quotidien réglé à {time}.",
    },
    "remind_off": {
        "en": "Daily reminder off.",
        "ru": "Ежедневное напоминание выключено.",
        "fr": "Rappel quotidien désactivé.",
    },
    "remind_usage": {
        "en": "Usage: /remind HH:MM, or /remind off.",
        "ru": "Использование: /remind ЧЧ:ММ или /remind off.",
        "fr": "Utilisation : /remind HH:MM, ou /remind off.",
    },
    "reminder_dailies": {
        "en": "Still due today:\n{dailies}",
        "ru": "Ещё не выполнено сегодня:\n{dailies}",
        "fr": "Encore à faire aujourd'hui :\n{dailies}",
    },
    # ---- gear (P06) ----
    "help_gear": {
        "en": "the INT / PER / CON gear sets worn around day start",
        "ru": "наборы снаряжения INT / PER / CON на начало дня",
        "fr": "les tenues INT / PER / CON portées autour du début de journée",
    },
    "gear_header": {
        "en": "Gear sets — CON or INT for the cron, PER for the day:",
        "ru": "Наборы снаряжения — CON или INT на начало дня, PER на день:",
        "fr": "Tenues — CON ou INT pour le début de journée, PER pour la journée :",
    },
    "gear_line": {
        "en": "{stat} (+{value}){on}: {items}",
        "ru": "{stat} (+{value}){on}: {items}",
        "fr": "{stat} (+{value}){on} : {items}",
    },
    "gear_on_now": {
        "en": " — on now",
        "ru": " — надето",
        "fr": " — portée",
    },
    "gear_unset": {
        "en": "{stat}: not set — /gear {cmd} auto picks the best owned item per slot",
        "ru": "{stat}: не задан — /gear {cmd} auto выберет лучший предмет на каждый слот",
        "fr": "{stat} : non définie — /gear {cmd} auto choisit le meilleur objet par emplacement",
    },
    "gear_equipped": {
        "en": "{stat} set on: {items}",
        "ru": "Надет набор {stat}: {items}",
        "fr": "Tenue {stat} portée : {items}",
    },
    "gear_already_on": {
        "en": "The {stat} set is already on.",
        "ru": "Набор {stat} уже надет.",
        "fr": "La tenue {stat} est déjà portée.",
    },
    "gear_bad_slot": {
        "en": "Unknown slot {slot}. Slots: {slots}",
        "ru": "Неизвестный слот {slot}. Слоты: {slots}",
        "fr": "Emplacement inconnu {slot}. Emplacements : {slots}",
    },
    "gear_unknown_item": {
        "en": "No item with key {item_key} in the game content.",
        "ru": "В игре нет предмета с ключом {item_key}.",
        "fr": "Aucun objet avec la clé {item_key} dans le jeu.",
    },
    "gear_not_owned": {
        "en": "You don't own {item}.",
        "ru": "У вас нет предмета {item}.",
        "fr": "Vous ne possédez pas {item}.",
    },
    "gear_usage": {
        "en": "Usage: /gear · /gear int|per|con auto · /gear int|per|con <slot> <key> · "
        "/gear now int|per|con",
        "ru": "Использование: /gear · /gear int|per|con auto · /gear int|per|con <слот> <ключ> · "
        "/gear now int|per|con",
        "fr": "Utilisation : /gear · /gear int|per|con auto · /gear int|per|con <slot> <clé> · "
        "/gear now int|per|con",
    },
    # ---- the new day (P06) ----
    "new_day": {
        "en": "New day. HP lost overnight: {hp_lost} (now {hp}/{max_hp}); mana {mp}/{max_mp}.\n"
        "Due today:\n{dailies}",
        "ru": "Новый день. Потеряно здоровья за ночь: {hp_lost} (сейчас {hp}/{max_hp}); мана "
        "{mp}/{max_mp}.\nНа сегодня:\n{dailies}",
        "fr": "Nouvelle journée. Santé perdue cette nuit : {hp_lost} (maintenant {hp}/{max_hp}) ; "
        "mana {mp}/{max_mp}.\nÀ faire aujourd'hui :\n{dailies}",
    },
    "new_day_nothing_due": {
        "en": "• nothing — no daily is due today",
        "ru": "• ничего — сегодня нет ежедневных дел",
        "fr": "• rien — aucune quotidienne aujourd'hui",
    },
    "new_day_gear": {
        "en": "Gear: {for_cron} for the cron, {now} on now.",
        "ru": "Снаряжение: {for_cron} на начало дня, сейчас {now}.",
        "fr": "Équipement : {for_cron} pour le début de journée, {now} maintenant.",
    },
    # ---- the day-turn window (P05c) ----
    "day_turn_ask": {
        "en": "Before the day turns at {cron_time} — still unticked:\n{dailies}\n"
        "Did you do any of them? Tick what you did. If you don't answer, I wait until "
        "{deadline} and then turn the day.",
        "ru": "До начала нового дня в {cron_time} — ещё не отмечены:\n{dailies}\n"
        "Что-то из этого сделано? Отметьте, что выполнили. Без ответа я подожду до "
        "{deadline} и затем начну новый день.",
        "fr": "Avant le début de journée à {cron_time} — pas encore cochées :\n{dailies}\n"
        "En avez-vous fait ? Cochez ce que vous avez fait. Sans réponse, j'attends jusqu'à "
        "{deadline} puis je lance la nouvelle journée.",
    },
    "day_turn_nothing_else": {
        "en": "Nothing else",
        "ru": "Больше ничего",
        "fr": "Rien d'autre",
    },
    "day_turn_ticked": {
        "en": "✅ {task} — ticked.",
        "ru": "✅ {task} — отмечено.",
        "fr": "✅ {task} — coché.",
    },
    "day_turn_remaining": {
        "en": "Still unticked:\n{dailies}",
        "ru": "Ещё не отмечены:\n{dailies}",
        "fr": "Pas encore cochées :\n{dailies}",
    },
    "day_turn_ok": {
        "en": "OK — the day turns at {cron_time}.",
        "ru": "Хорошо — новый день начнётся в {cron_time}.",
        "fr": "D'accord — la nouvelle journée commence à {cron_time}.",
    },
    "day_turn_too_late": {
        "en": "Too late — the day already turned at {time}. Anything ticked now counts for today.",
        "ru": "Поздно — новый день уже начался в {time}. Всё, что отметить сейчас, зачтётся "
        "за сегодня.",
        "fr": "Trop tard — la journée a déjà tourné à {time}. Ce que vous cochez maintenant "
        "compte pour aujourd'hui.",
    },
    "confirm_do_it": {
        "en": "✅ Do it",
        "ru": "✅ Давай",
        "fr": "✅ Vas-y",
    },
    "confirm_do_all": {
        "en": "✅ Do all {count}",
        "ru": "✅ Сделать все {count}",
        "fr": "✅ Faire les {count}",
    },
    "action_not_tried": {
        "en": "Not tried: {action}.",
        "ru": "Не выполнено: {action}.",
        "fr": "Pas tenté : {action}.",
    },
    "confirm_cancel": {
        "en": "❌ Cancel",
        "ru": "❌ Отмена",
        "fr": "❌ Annuler",
    },
    "action_delete_task": {
        "en": "delete “{text}”",
        "ru": "удалить «{text}»",
        "fr": "supprimer « {text} »",
    },
    "action_cast_skill": {
        "en": "cast {skill} ({mana} MP)",
        "ru": "применить {skill} ({mana} MP)",
        "fr": "lancer {skill} ({mana} PM)",
    },
    "action_cast_skill_on": {
        "en": "cast {skill} on “{text}” ({mana} MP)",
        "ru": "применить {skill} на «{text}» ({mana} MP)",
        "fr": "lancer {skill} sur « {text} » ({mana} PM)",
    },
    "help_skills": {
        "en": "the class skills you can cast now, with their mana cost",
        "ru": "умения класса, доступные сейчас, с ценой в мане",
        "fr": "les compétences de classe disponibles, avec leur coût en mana",
    },
    "skills_header": {
        "en": "{class_name}, level {lvl}, {mp}/{max_mp} MP:",
        "ru": "{class_name}, уровень {lvl}, {mp}/{max_mp} MP:",
        "fr": "{class_name}, niveau {lvl}, {mp}/{max_mp} PM :",
    },
    "skills_line": {
        "en": "{mark} {text} — {mana} MP, {where}",
        "ru": "{mark} {text} — {mana} MP, {where}",
        "fr": "{mark} {text} — {mana} PM, {where}",
    },
    "skills_none": {
        "en": "No skills yet — the first one unlocks at level 11.",
        "ru": "Умений пока нет — первое открывается на 11-м уровне.",
        "fr": "Pas encore de compétence — la première se débloque au niveau 11.",
    },
    "skill_target_task": {"en": "on a task", "ru": "на задачу", "fr": "sur une tâche"},
    "skill_target_self": {"en": "on yourself", "ru": "на себя", "fr": "sur vous"},
    "skill_target_party": {"en": "on the party", "ru": "на группу", "fr": "sur le groupe"},
    "skill_target_tasks": {
        "en": "on all tasks",
        "ru": "на все задачи",
        "fr": "sur toutes les tâches",
    },
    "action_done": {
        "en": "Done: {action}.",
        "ru": "Готово: {action}.",
        "fr": "C'est fait : {action}.",
    },
    "action_cancelled": {
        "en": "Cancelled: {action}.",
        "ru": "Отменено: {action}.",
        "fr": "Annulé : {action}.",
    },
    "action_dropped": {
        "en": "(The pending action — {action} — is dropped.)",
        "ru": "(Ожидавшее действие — {action} — отменено.)",
        "fr": "(L'action en attente — {action} — est abandonnée.)",
    },
    "something_went_wrong": {
        "en": "Something went wrong on my side. It's logged; try again in a moment.",
        "ru": "Что-то пошло не так с моей стороны. Записано в лог; попробуй чуть позже.",
        "fr": "Quelque chose a cassé de mon côté. C'est dans le journal ; réessaie dans un moment.",
    },
}


def t(key: str, lang: str, **values: object) -> str:
    """The string `key` in `lang`, with `{placeholders}` filled from `values`.

    A missing key is a programming error and raises. A language we don't have
    falls back to English rather than raising: a bad value in `user_settings`
    should not silence the bot.
    """
    entry = STRINGS[key]
    template = entry.get(lang) or entry[DEFAULT_LANGUAGE]
    return template.format(**values) if values else template
