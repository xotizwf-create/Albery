"""iu_client_bot.py — клиентский вход в воронку ИУ через бота.

Раньше попасть в воронку можно было единственным способом: написать в Telegram-аккаунт
менеджера. Здесь появляется вход по ссылке на бота — с /start, четырьмя кнопками и понятным
следующим шагом на каждой.

Что важно в устройстве. Сценарий не хранит собственного состояния: «на каком шаге клиент»
не записывается никуда отдельно. Признак шага — состояние данных диалога (сколько раз ИИ
уже ответил, забрал ли диалог человек), и по нему же решается, показывать ли кнопку
«Позвать оператора». Отдельный счётчик рассинхронизировался бы с журналом при первой же
ошибке доставки.

Ответы клиенту не отправляются отсюда напрямую: они ставятся в ту же durable-очередь, что
и ответы оператора из рабочего окна. Поэтому команда видит их в ленте обращения, а не в
параллельной реальности бота.
"""
from __future__ import annotations

import os
import re

#: Подписи пунктов меню. Состав согласован владельцем 28.07.2026; смайлики — его же
#: просьба, они помогают отличить пункты друг от друга беглым взглядом.
BUTTON_TERMS = "📄 Условия присоединения к ИУ"
BUTTON_JOIN = "🤝 Присоединиться к ИУ"
BUTTON_CALCULATOR = "🧮 Калькулятор расчёта ИУ"
BUTTON_ASK = "💬 Задать вопрос"
BUTTON_OPERATOR = "🙋 Позвать оператора"
BUTTON_EXIT_SUPPORT = "↩️ Выйти из диалога поддержки"
BUTTON_CONFIRM_YES = "✅ Да"
BUTTON_CONFIRM_NO = "❌ Нет"

CB_TERMS = "iu:terms"
CB_JOIN = "iu:join"
CB_CALCULATOR = "iu:calculator"
CB_ASK = "iu:ask"
CB_OPERATOR = "iu:operator"
CB_EXIT_SUPPORT = "iu:exit-support"
CB_CONFIRM_YES = "iu:exit-yes"
CB_CONFIRM_NO = "iu:exit-no"

#: После двух ответов ИИ кнопка показывается вместе с ответом на третий вопрос.
OPERATOR_OFFER_AFTER_REPLIES = int(os.getenv("IU_CLIENT_BOT_OPERATOR_AFTER", "2") or "2")

WELCOME = (
    "Здравствуйте! Это бот компании по индивидуальным условиям (ИУ) работы с Wildberries.\n\n"
    "Выберите, с чего начать:"
)
WELCOME_BACK = "С возвращением! Выберите нужный раздел:"
MENU_PROMPT = "Главное меню:"
STOPPED = (
    "Поддержка и напоминания остановлены. Чтобы начать снова, используйте /start "
    "или вернитесь в меню командой /menu."
)

#: Публичный калькулятор не требует входа и не отправляет введённые значения на сервер.
CALCULATOR_URL = os.getenv(
    "IU_CALCULATOR_URL", "https://www.m4s.ru/Калькулятор/"
).strip()
#: Название калькулятора — подписью ссылки. Владелец 29.07.2026: «чтоб Калькулятор ИУ был как
#: гиперссылка». Разметка markdown-стилем: в HTML её собирает отправка.
CALCULATOR_REPLY = (
    f"Посчитать свою выгоду вы можете в [Калькуляторе ИУ]({CALCULATOR_URL}).\n\n"
    "Он открывается без регистрации — подставьте свои цифры и увидите, что останется на руках."
)
CALCULATOR_DISCUSSION_TEXT = (
    "Здравствуйте! Я рассчитал экономику ИУ и хочу обсудить условия подключения."
)
CALCULATOR_DISCUSSION_LEGACY_TEXT = (
    "Здравствуйте! Я рассчитал экономику ИУ и хочу обсудить сотрудничество."
)
CALCULATOR_MANAGER_READY = (
    "Ваша анкета уже получена. Сейчас менеджер подключится к диалогу."
)
CALCULATOR_FORM_RECEIVED = (
    "Анкету получили. Сейчас менеджер подключится к диалогу!"
)

#: Анкета выдаётся ПЕРСОНАЛЬНОЙ ссылкой: по ней заявка приклеивается к этому же человеку, а не
#: заводит вторую карточку в воронке. Текст собирается в `join_reply`.
JOIN_INTRO = (
    "Заполните короткую анкету, менеджер с Вами свяжется!\n\n"
    "[Заполнить анкету]({url})"
)
JOIN_REPEAT = (
    "Вижу, анкета ещё не заполнена. Вот ваша ссылка — можно продолжить с неё.\n\n"
    "Заполните короткую анкету, менеджер с Вами свяжется!\n\n"
    "[Заполнить анкету]({url})"
)

#: Ссылку выдать не удалось (база недоступна). Молчать нельзя, но и врать про анкету тоже:
#: честно зовём менеджера.
JOIN_STUB = (
    "Хорошо, оформим присоединение.\n\n"
    "Анкету пришлёт менеджер — он уже видит ваше обращение и свяжется с вами. "
    "Если удобнее, напишите здесь, каким товаром торгуете и какой у вас объём продаж: "
    "это ускорит разбор."
)

#: Анкета уже заполнена: второй раз её давать нельзя (владелец 29.07.2026). Данные
#: показываем те, что РЕАЛЬНО лежат в сделке, — «вот ваши данные» из его же формулировки.
JOIN_FILLED_FOLLOWUP = (
    "Если всё верно - то пожалуйста, подождите, менеджер с Вами скоро свяжется\n\n"
    "Если у Вас есть срочный вопрос или нужно изменить данные в анкете - "
    "напишите прямо сюда, я передам это менеджеру"
)
FORM_RECEIVED = (
    "Увидел Вашу анкету, менеджер свяжется с Вами в ближайшее время!"
)


def join_reply(url: str, *, repeated: bool = False) -> str:
    """Ответ на «Присоединиться к ИУ» с персональной ссылкой."""

    template = JOIN_REPEAT if repeated else JOIN_INTRO
    return template.format(url=url)


def join_answer(anketa: str, url: str, *, repeated: bool = False) -> tuple[str, bool]:
    """Что ответить на «Присоединиться к ИУ»: `(текст, анкета уже есть)`.

    Решает НАЛИЧИЕ анкеты в сделке, а не наша отметка о выдаче ссылки. Владелец 29.07.2026
    удалил анкету в Битриксе, а бот продолжал говорить «вы уже заполнили»: отметка живёт у
    нас, а правда — в CRM. Пустой блок анкеты означает, что заполнять её нужно заново, и
    клиент снова получает ссылку."""

    body = str(anketa or "").strip()
    if body:
        confirmation = "Всё верно?"
        if body.endswith(confirmation):
            body = body[: -len(confirmation)].rstrip()
        return f"{body}\n\n{JOIN_FILLED_FOLLOWUP}", True
    return join_reply(url, repeated=repeated), False

ASK_PROMPT = (
    "Спрашивайте — помогу разобраться в условиях работы, комиссии, сроках и документах.\n\n"
    "Также прикрепил ответы на частые вопросы — там вы сможете найти ответ на свой вопрос."
)

STRICT_QUESTION_HINT = (
    "Если у вас есть вопрос, нажмите «Задать вопрос» — я постараюсь помочь."
)
FILE_NEEDS_CONTEXT = (
    "Файл получил. Напишите, пожалуйста, что именно нужно проверить."
)
FILE_SENT_TO_MANAGER = (
    "Передал файл менеджеру — он посмотрит и ответит здесь."
)
EXIT_CONFIRM = "Вы уверены, что хотите выйти из окна поддержки?"
EXITED_SUPPORT = "Если появятся новые вопросы, снова нажмите «Задать вопрос»!"
CONTINUE_SUPPORT = "Хорошо, остаёмся в поддержке. Напишите ваш вопрос."
FORM_EDIT_SENT = "Передал менеджеру, что нужно изменить в анкете. Он ответит здесь."
REMINDER_WAITING_QUESTION = (
    "Мы готовы помочь. Напишите ваш вопрос, когда будет удобно."
)
REMINDER_AFTER_ANSWER = (
    "Остались ли у вас вопросы? Если что-то понадобится — напишите, я на связи."
)

TERMS_REPLY = (
    "Условия присоединения к ИУ вы можете прочитать в ПДФ файле выше.\n\n"
    f"Вы можете посчитать свою экономию в нашем [калькуляторе ИУ]({CALCULATOR_URL}), "
    "а также ознакомиться с примерным договором, прикрепленным ниже."
)

OPERATOR_CALLED = (
    "Передал ваш вопрос менеджеру — он ответит здесь же, в этом чате."
)

TERMS_FALLBACK = (
    "Условия сейчас уточняет менеджер — он пришлёт их вам в этот чат."
)


def enabled() -> bool:
    """Открыт ли клиентский вход. Пока выключен, бот ведёт себя как раньше."""

    return os.getenv("IU_CLIENT_BOT_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def ai_answers_enabled() -> bool:
    """Отвечает ли клиенту ИИ. Владелец 28.07.2026: «сначала отвечает ИИ»."""

    return os.getenv("IU_CLIENT_BOT_AI", "1").strip().lower() in {"1", "true", "yes", "on"}


def main_menu(*, offer_operator: bool = False) -> dict:
    """Постоянное меню под полем ввода.

    Владелец 28.07.2026: кнопки должны быть меню, а не «висеть где-то наверху». Кнопки
    внутри сообщения уезжают вместе с историей, и через десяток реплик до них не добраться;
    меню под полем ввода остаётся на месте весь разговор. Нажатие такого пункта приходит
    обычным текстовым сообщением — сценарий узнаёт его по подписи.
    """

    # Пункт «Позвать оператора» не живёт в главном меню: он появляется только внутри
    # поддержки после двух содержательных ответов ИИ.
    del offer_operator
    rows = [[BUTTON_TERMS], [BUTTON_JOIN], [BUTTON_CALCULATOR], [BUTTON_ASK]]
    return {
        "keyboard": [[{"text": title} for title in row] for row in rows],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def support_menu(*, offer_operator: bool = False) -> dict:
    """Клавиатура отдельного режима поддержки."""

    rows: list[list[str]] = [[BUTTON_EXIT_SUPPORT]]
    if offer_operator:
        rows.insert(0, [BUTTON_OPERATOR])
    return {
        "keyboard": [[{"text": title} for title in row] for row in rows],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def exit_confirmation_menu() -> dict:
    return {
        "keyboard": [[{"text": BUTTON_CONFIRM_YES}, {"text": BUTTON_CONFIRM_NO}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def remove_keyboard() -> dict:
    return {"remove_keyboard": True}


#: Подписи без смайликов — у клиентов, начавших разговор до их появления, меню закреплено
#: на стороне Telegram со старым текстом. Нажатие такой кнопки обязано остаться выбором
#: пункта, а не превратиться в вопрос к модели.
_MENU_BY_TITLE = {
    "условия присоединения к иу": CB_TERMS,
    "присоединиться к иу": CB_JOIN,
    "калькулятор расчёта иу": CB_CALCULATOR,
    "калькулятор расчета иу": CB_CALCULATOR,
    "задать вопрос": CB_ASK,
    "позвать оператора": CB_OPERATOR,
    "оператор": CB_OPERATOR,
    "менеджер": CB_OPERATOR,
    "человек": CB_OPERATOR,
    "выйти из диалога поддержки": CB_EXIT_SUPPORT,
    "да": CB_CONFIRM_YES,
    "нет": CB_CONFIRM_NO,
}


def _title_key(text: str) -> str:
    """Подпись без смайликов и лишних пробелов — по ней и узнаётся пункт меню."""

    letters = [
        character
        for character in str(text or "")
        if character.isalpha() or character.isspace() or character == "-"
    ]
    return re.sub(r"\s+", " ", "".join(letters)).strip().lower()


def menu_action(text: str) -> str:
    """Пункт меню, который выбрал клиент, или пустая строка для обычного сообщения."""

    return _MENU_BY_TITLE.get(_title_key(text), "")


def is_calculator_discussion(text: str) -> bool:
    """Сообщение, подготовленное кнопкой публичного калькулятора.

    Поддерживаем и прежний текст: открытая до выкладки вкладка калькулятора не должна
    отправить клиента в строгую подсказку вместо сценария подключения.
    """

    normalized = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    return normalized in {
        CALCULATOR_DISCUSSION_TEXT.casefold(),
        CALCULATOR_DISCUSSION_LEGACY_TEXT.casefold(),
    }


def should_offer_operator(agent_replies: int, *, control_mode: str = "ai") -> bool:
    """Пора ли предлагать человека.

    Кнопка появляется, когда ИИ ответил уже несколько раз подряд: столько вопросов подряд
    обычно значит, что автоответы не закрывают задачу. Если диалог уже у человека, звать
    его второй раз незачем.
    """

    if str(control_mode or "").lower() != "ai":
        return False
    return int(agent_replies or 0) >= OPERATOR_OFFER_AFTER_REPLIES


def button_label(callback_data: str) -> str:
    """Подпись нажатой кнопки — она попадает в ленту как реплика клиента."""

    return {
        CB_TERMS: BUTTON_TERMS,
        CB_JOIN: BUTTON_JOIN,
        CB_CALCULATOR: BUTTON_CALCULATOR,
        CB_ASK: BUTTON_ASK,
        CB_OPERATOR: BUTTON_OPERATOR,
        CB_EXIT_SUPPORT: BUTTON_EXIT_SUPPORT,
        CB_CONFIRM_YES: BUTTON_CONFIRM_YES,
        CB_CONFIRM_NO: BUTTON_CONFIRM_NO,
    }.get(str(callback_data or ""), "")
