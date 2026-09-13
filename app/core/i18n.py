"""Fixed bot strings in the three languages.

Only what the bot says on its own goes here: command help, button labels,
reminders, error lines. Everything conversational comes from the model in the
active language. Keys are plain text; adapters send them without markup, so a
task name with `*` or `_` in it never breaks a message.
"""

LANGUAGES = ("en", "ru", "fr")
DEFAULT_LANGUAGE = "en"

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
    "no_llm_yet": {
        "en": "I can't hold a conversation yet — that comes in a later version. Try /help.",
        "ru": "Разговаривать я пока не умею — это появится в следующей версии. Попробуй /help.",
        "fr": "Je ne sais pas encore discuter — ça viendra dans une prochaine version. "
        "Essaie /help.",
    },
    "voice_not_yet": {
        "en": "Voice messages come in a later version. Please type for now.",
        "ru": "Голосовые сообщения появятся в следующей версии. Пока напиши текстом.",
        "fr": "Les messages vocaux arrivent dans une prochaine version. Écris-moi pour l'instant.",
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
    "confirm_do_it": {
        "en": "✅ Do it",
        "ru": "✅ Давай",
        "fr": "✅ Vas-y",
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
