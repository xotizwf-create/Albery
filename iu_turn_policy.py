"""Детерминированная политика одного клиентского хода по ИУ.

LLM формулирует текст, но не может сама разрешить CRM-сделку, документ условий или анкету.
Тема ИУ, вопрос об условиях и готовность заполнить анкету — разные намерения. Смешивать их
опасно: именно поэтому обычные «Здравствуйте» и «Сколько стоит доставка?» раньше получали
принудительную форму.

Слой чистый: только текст и уже известный контекст на входе, без CRM/Telegram/БД.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Слитное «ИУ»/«И.У.» — да; обычное сочетание «и у бухгалтера» — нет. Намеренно не разрешаем
# пробел между буквами: false positive на живой русской речи опаснее редкой необычной записи.
_IU_ACRONYM_RE = re.compile(r"(?<!\w)(?:ИУ|И\.У\.?|иу|и\.у\.?)(?!\w)")
_IU_PHRASE_RE = re.compile(
    r"(?:wb|wildberries|вайлдберриз).{0,35}(?:услови\w*|комисси\w*|партн[её]рск\w*)|"
    r"(?:услови\w*|комисси\w*|партн[её]рск\w*).{0,35}(?:wb|wildberries|вайлдберриз)|"
    r"(?:albery|албери).{0,35}(?:услови\w*|программ\w*|подключ\w*)|"
    r"(?:услови\w*|программ\w*|подключ\w*).{0,35}(?:albery|албери)|"
    r"индивидуальн\w*\s+услови\w*"
    r".{0,35}(?:подключ\w*|продав\w*|селлер\w*|маркетплейс\w*)|"
    r"(?:подключ\w*|продав\w*|селлер\w*|маркетплейс\w*).{0,35}"
    r"индивидуальн\w*\s+услови\w*",
    re.I,
)
_PARTNER_PROGRAM_RE = re.compile(
    r"партн[её]рск\w*(?:\s+эт\w+)?\s+программ\w*",
    re.I,
)
_PARTNER_ALLOWED_TAIL_RE = re.compile(
    r"\s*(?:[,—:;-]\s*)?(?:"
    r"(?:(?:для|на|по|от)\s+)?(?:селлер\w*|продавц\w*|маркетплейс\w*)|"
    r"(?:(?:какие|какая|какой|каков\w*|сколько)\s+)?"
    r"(?:услови\w*|комисси\w*|тариф\w*|стоимост\w*|цен[аыу]\w*)"
    r")?\s*[?!.]*\s*",
    re.I,
)
_PARTNER_ALLOWED_PREFIX_WORD_RE = re.compile(
    r"(?:в|во|по|о|об|про|для|на|за|"
    r"услови\w*|комисси\w*|тариф\w*|стоимост\w*|цен[аыу]\w*|стоит|"
    r"такое|работает|эта|этой|эту|такой|такую)",
    re.I,
)
_FIRST_PERSON_PARTNER_INTEREST_RE = re.compile(
    r"\s*(?:"
    r"(?:меня|мне)\s+(?:за)?интерес\w*|"
    r"я(?:\s+[а-яё-]+){0,3}\s*[,—:;-]?\s*(?:за)?интерес\w*|"
    r"(?:за)?интерес\w*"
    r")\s+партн[её]рск\w*(?:\s+эт\w+)?\s+программ\w*\s*[?!.]*\s*",
    re.I,
)
_BARE_PRODUCT_ALIAS_RE = re.compile(
    r"\s*(?:(?:какие|что\s+такое|как\s+работает|интересуют|"
    r"расскажите\s+(?:про|о)|хочу\s+узнать\s+про)\s+)?"
    r"(?:индивидуальн\w*\s+услови\w*|партн[её]рск\w*\s+программ\w*)"
    r"\s*[?!.]*\s*",
    re.I,
)
_BARE_CONNECTION_TERMS_RE = re.compile(
    r"\s*(?:"
    r"(?:(?:какие|каков\w*)\s+)?услови\w*\s+подключ\w*|"
    r"(?:какая\s+)?(?:стоимост\w*|цен[аыу]\w*|тариф\w*|комисси\w*)\s+подключ\w*|"
    r"сколько\s+стоит\s+подключ\w*|"
    r"(?:расскажите\s+про\s+)?(?:услови\w*|тариф\w*)\s+подключ\w*|"
    r"подключени\w*\s+(?:сколько\s+стоит|какая\s+цен[аыу]\w*|"
    r"какие\s+услови\w*|какой\s+тариф\w*)"
    r")\s*[?!.]*\s*",
    re.I,
)
_TERMS_TOPIC_RE = re.compile(
    r"услови\w*|комисси\w*|тариф\w*|стоим\w*|цен[аыу]\w*|сколько\s+стоит|"
    r"как\s+работа\w*|что\s+такое|расскаж\w*|оплат\w*",
    re.I,
)
_TERMS_DOCUMENT_QUESTION_RE = re.compile(
    r"услови\w*|комисси\w*|тариф\w*|стоим\w*|цен[аыу]\w*|сколько\s+стоит|"
    r"как\s+работа\w*|что\s+такое|оплат\w*",
    re.I,
)
_BARE_IU_OVERVIEW_RE = re.compile(
    r"\s*расскаж\w*(?:\s+(?:подробнее\s+)?(?:про|об?|о))?\s+"
    r"(?:иу|и\.у\.?)\s*[?!.]*\s*",
    re.I,
)
_CONTRACT_ACTION_RE = re.compile(
    r"(?:пришл\w*|отправ\w*|скин\w*|подпис\w*|состав\w*|получ\w*).{0,25}договор\w*|"
    r"договор\w*.{0,25}(?:пришл\w*|отправ\w*|скин\w*|подпис\w*|состав\w*)",
    re.I,
)
_OTHER_TOPIC_RE = re.compile(
    r"доставк\w*|логистик\w*|поставк\w*|накладн\w*|ткан\w*|"
    r"бухгалтер\w*|груз\w*|курьер\w*|интернет\w*|онлайн[-\s]?касс\w*|"
    r"реклам\w*|ремонт\w*|офис\w*|банк\w*|банковск\w*|возврат\w*|эквайринг\w*|"
    r"лояльн\w*|авиакомпан\w*|университет\w*|услови\w*\s+труд\w*|"
    r"прожив\w*|обучен\w*|кредит\w*",
    re.I,
)
_FOREIGN_TERMS_OBJECT_RE = re.compile(
    r"(?:услови\w*|стоим\w*|цен[аыу]\w*|тариф\w*|комисси\w*|сколько\s+стоит)\s+"
    r"(?:(?:на|для|по|за|в|у)\s+)?"
    r"(?!иу\b|и\.у|индивидуальн\w*|подключ\w*|сотруднич\w*|партн[её]р\w*|"
    r"программ\w*|wb\b|wildberries\b|вайлдберриз\w*)[а-яёa-z][\w-]{2,}",
    re.I,
)
_BARE_TERMS_RE = re.compile(
    r"\s*(?:(?:а|и)\s+)?(?:подскажите\s*[,.:]?\s*)?"
    r"(?:"
    r"(?:каков\w*|как(?:ая|ие|ой))\s+(?:у\s+вас\s+)?"
    r"(?:услови\w*|комисси\w*|тариф\w*|цен[аыу]\w*|стоимост\w*)|"
    r"(?:услови\w*|комисси\w*|тариф\w*|цен[аыу]\w*|стоимост\w*)"
    r"(?:\s+у\s+вас)?|"
    r"сколько\s+(?:это\s+)?стоит|"
    r"(?:расскажите|хочу\s+узнать|можно\s+узнать)\s+(?:про\s+)?"
    r"(?:услови\w*|комисси\w*|тариф\w*|цен[аыу]\w*|стоимост\w*)"
    r")\s*[?!.]*\s*",
    re.I,
)
_TERMS_RESEND_RE = re.compile(
    r"(?:ещ[её]\s+раз|повтор\w*|продублир\w*|снова|заново|не\s+приш\w*|"
    r"не\s+получ\w*).{0,45}(?:услови\w*|документ\w*|файл\w*)|"
    r"(?:услови\w*|документ\w*|файл\w*).{0,45}"
    r"(?:ещ[её]\s+раз|повтор\w*|продублир\w*|снова|заново|не\s+приш\w*|"
    r"не\s+получ\w*)",
    re.I,
)
_BARE_TERMS_REQUEST_RE = re.compile(
    r"\s*(?:(?:пожалуйста|да)\s*[,.:]?\s*)?"
    r"(?:"
    r"(?:пришл\w*|отправ\w*|скин\w*|дай\w*|дайте|покаж\w*)\s*"
    r"(?:,\s*пожалуйста\s*,?\s*)?(?:документ\w*\s+с\s+)?"
    r"(?:услови\w*|тариф\w*)|"
    r"можно\s+(?:услови\w*|тариф\w*)|"
    r"(?:услови\w*|тариф\w*)\s*,?\s*пожалуйста|"
    r"хоч\w*\s+посмотр\w*\s+(?:услови\w*|тариф\w*)|"
    r"где\s+(?:посмотр\w*\s+)?(?:услови\w*|тариф\w*)"
    r")\s*[?!.]*\s*",
    re.I,
)

_ANKETA_RE = re.compile(r"анкет\w*", re.I)
_APPLICATION_RE = re.compile(r"заявк\w*", re.I)
_GENERIC_FORM_RE = re.compile(r"\bформ\w*\b", re.I)
_FORM_REQUEST_RE = re.compile(
    r"\b(?:пришл\w*|отправ\w*|скин\w*|дай\w*|дайте|где|покаж\w*|"
    r"можно\s+получ\w*|хоч\w*\s+заполн\w*|готов\w*\s+заполн\w*)\b",
    re.I,
)
_BARE_FORM_REQUEST_RE = re.compile(
    r"\s*(?:(?:да|конечно)\s*,?\s*)?(?:пожалуйста\s*[,.:]?\s*)?"
    r"(?:"
    r"(?:пришл\w*|отправ\w*|скин\w*|дай\w*|дайте)\s*"
    r"(?:,\s*пожалуйста\s*,?\s*)?"
    r"(?:мне\s+)?(?:ссылк\w*\s+на\s+)?(?:анкет\w*|заявк\w*|форм\w*)|"
    r"где\s+(?:ссылк\w*\s+на\s+)?(?:анкет\w*|заявк\w*|форм\w*)|"
    r"(?:хоч\w*|готов\w*)\s+заполн\w*\s+(?:анкет\w*|заявк\w*|форм\w*)|"
    r"можно\s+получ\w*\s+(?:анкет\w*|заявк\w*|форм\w*)"
    r")\s*(?:,\s*пожалуйста)?\s*[?!.]*\s*",
    re.I,
)
_DIRECT_NAMED_FORM_INTENT_RE = re.compile(
    r"\s*(?:"
    r"можно\s+(?:анкет\w*|заявк\w*|форм\w*)|"
    r"(?:анкет\w*|заявк\w*|форм\w*)\s*,?\s*пожалуйста|"
    r"(?:нужн\w*|хоч\w*|давай(?:те)?)\s+(?:анкет\w*|заявк\w*|форм\w*)|"
    r"(?:(?:я|могу|давай(?:те)?)\s+)?заполн\w*\s+"
    r"(?:анкет\w*|заявк\w*|форм\w*)"
    r")\s*[?!.]*\s*",
    re.I,
)
_FORM_RESEND_RE = re.compile(
    r"(?:анкет\w*|форм\w*|ссылк\w*.{0,20}(?:анкет\w*|форм\w*)).{0,45}"
    r"(?:ещ[её]\s+раз|повтор\w*|продублир\w*|снова|заново|не\s+приш\w*|"
    r"не\s+получ\w*)|"
    r"(?:ещ[её]\s+раз|повтор\w*|продублир\w*|снова|заново|не\s+приш\w*|"
    r"не\s+получ\w*).{0,45}(?:анкет\w*|форм\w*|ссылк\w*)",
    re.I,
)
_FORM_ASSET_OBJECT_RE = re.compile(r"\b(?:анкет\w*|форм\w*|заявк\w*)\b", re.I)
_FORM_ALLOWED_TAIL_RE = re.compile(
    r"\s*(?:[,—:;-]\s*)?(?:"
    r"(?:ещ[её]\s+раз|повтор\w*|снова|заново)|"
    r"(?:не\s+(?:приш\w*|получ\w*)"
    r"(?:\s*,\s*(?:пришл\w*|отправ\w*|скин\w*|продублир\w*|повтор\w*)"
    r"(?:\s+ещ[её]\s+раз|\s+повтор\w*)?)?)|"
    r"(?:пожалуйста)|"
    r"(?:"
    r"для\s+(?:подключ\w*(?:\s+к\s+(?:иу|и\.у\.?))?|иу|и\.у\.?)|"
    r"(?:на|по)\s+(?:иу|и\.у\.?)|"
    r"подключ\w*(?:\s+к\s+(?:иу|и\.у\.?))?|"
    r"иу|и\.у\.?|albery|албери|wb|wildberries|вайлдберриз"
    r")"
    r")?(?:\s*,?\s*(?:пожалуйста|ещ[её]\s+раз|повтор\w*|снова|заново))*"
    r"\s*[?!.]*\s*",
    re.I,
)
_FORM_REJECTION_RE = re.compile(
    r"\bне\s+(?:хоч\w*|готов\w*|буд\w*|собира\w*|планир\w*|намерен\w*|"
    r"соглас\w*|нужн\w*|надо|интерес\w*)\b.{0,60}"
    r"(?:подключ\w*|присоедин\w*|сотруднич\w*|анкет\w*|заявк\w*|форм\w*|\bиу\b)|"
    r"\b(?:подключ\w*|присоедин\w*|сотруднич\w*|анкет\w*|заявк\w*|форм\w*|\bиу\b)"
    r".{0,60}\bне\s+(?:хоч\w*|готов\w*|буд\w*|собира\w*|планир\w*|намерен\w*|"
    r"соглас\w*|нужн\w*|надо|интерес\w*)\b|"
    r"\bне\s+(?:подключа(?:й|йте)\w*|присоединя(?:й|йте)\w*)\b|"
    r"\bне\s+(?:присыла\w*|отправля\w*|скидыва\w*)\b.{0,25}"
    r"(?:анкет\w*|заявк\w*|форм\w*)|"
    r"\b(?:анкет\w*|заявк\w*|форм\w*)\b.{0,25}"
    r"\bне\s+(?:присыла\w*|отправля\w*|скидыва\w*)\b",
    re.I,
)
_TERMS_REJECTION_RE = re.compile(
    r"\bне\s+(?:хоч\w*|буд\w*|собира\w*|планир\w*)\b.{0,35}"
    r"(?:зна\w*|узна\w*|получ\w*|вид\w*|чита\w*)\b.{0,30}"
    r"(?:услови\w*|документ\w*)|"
    r"\bне\s+(?:хоч\w*|интерес\w*|нужн\w*|надо|рассказыва\w*)\b.{0,35}"
    r"(?:услови\w*|комисси\w*|тариф\w*|документ\w*)|"
    r"\bне\s+(?:подход\w*|устраива\w*|принима\w*|соглас\w*)\b.{0,35}"
    r"(?:услови\w*|комисси\w*|тариф\w*|документ\w*)|"
    r"\b(?:отказыва\w*|отверга\w*)\b.{0,35}"
    r"(?:услови\w*|комисси\w*|тариф\w*|документ\w*)|"
    r"\bне\s+спрашива\w*\b.{0,35}(?:услови\w*|комисси\w*|тариф\w*)|"
    r"\bне\s+(?:присыла\w*|отправля\w*|показыва\w*)\b.{0,35}"
    r"(?:услови\w*|документ\w*)|"
    r"\b(?:услови\w*|документ\w*)\b.{0,35}"
    r"\bне\s+(?:интерес\w*|нужн\w*|надо|присыла\w*|отправля\w*|показыва\w*|"
    r"рассказыва\w*|подход\w*|устраива\w*|принима\w*|соглас\w*)\b|"
    r"\b(?:услови\w*|комисси\w*|тариф\w*|документ\w*)\b.{0,35}"
    r"\b(?:отказыва\w*|отверга\w*)\b",
    re.I,
)
_IU_REJECTION_RE = re.compile(
    r"\b(?:иу|подключени\w*)\b.{0,45}"
    r"\bне\s+(?:нуж\w*|подход\w*|требу\w*|интерес\w*)\b|"
    r"\bне\s+(?:нуж\w*|подход\w*|требу\w*|интерес\w*)\b.{0,45}"
    r"(?:\bиу\b|подключени\w*)|"
    r"\b(?:отказыва\w*|против)\b.{0,45}"
    r"(?:подключ\w*|присоедин\w*|\bиу\b)|"
    r"\bне\s+(?:предлага\w*|разреша\w*)\b.{0,45}"
    r"(?:подключ\w*|присоедин\w*|\bиу\b)|"
    r"(?:подключ\w*|присоедин\w*|\bиу\b).{0,45}"
    r"\bне\s+(?:предлага\w*|разреша\w*)\b|"
    r"\bне\s+(?:стан\w+\s+)?(?:подключ\w*|присоедин\w*)\b.{0,45}"
    r"(?:\bиу\b|и\.у\.?)|"
    r"(?:\bиу\b|и\.у\.?).{0,45}\bне\s+(?:стан\w+\s+)?"
    r"(?:подключ\w*|присоедин\w*)\b|"
    r"\b(?:подключ\w*|присоедин\w*)\b.{0,45}(?:\bиу\b|и\.у\.?)"
    r".{0,30}\bне\s+стан\w*\b|"
    r"\bотказ\w*\b.{0,30}(?:от\s+)?(?:\bиу\b|и\.у\.?)|"
    r"\bне\s+заинтересова\w*\b.{0,35}(?:\bиу\b|и\.у\.?)|"
    r"(?:\bиу\b|и\.у\.?).{0,35}\bне\s+заинтересова\w*\b|"
    r"\bпока\s+без\s+иу\b",
    re.I,
)
_FUSED_DISINTEREST_RE = re.compile(
    r"\bнеинтерес\w*\b.{0,45}"
    r"(?:услови\w*|комисси\w*|тариф\w*|документ\w*|\bиу\b|подключ\w*)|"
    r"\b(?:услови\w*|комисси\w*|тариф\w*|документ\w*|\bиу\b|подключ\w*)"
    r".{0,45}\bнеинтерес\w*\b",
    re.I,
)
_CONNECT_ACTION_RE = re.compile(
    r"подключ\w*|присоедин\w*|сотруднича\w*|нач(?:ать|инаем|инаю)\s+работ\w*|"
    r"вступ\w*|участв\w*|"
    r"перейти\s+на\s+(?:иу|индивидуальн\w*\s+услови\w*)",
    re.I,
)
_COMMITMENT_RE = re.compile(
    r"\bхоч\w*|\bготов\w*|\bдавай(?:те)?\b|\bрешил\w*|\bсоглас\w*|"
    r"\bпланир\w*|\bнамерен\w*|\bподключайте\b|\bначинаем\b|\bнужн\w*",
    re.I,
)
_INFO_ONLY_RE = re.compile(
    r"\bхоч\w*\s+(?:узнать|понять|уточнить|посмотреть|почитать|спросить)|"
    r"\bинтересн\w*\s+(?:узнать|понять)|\bкак\s+подключ\w*\??$",
    re.I,
)
_EXPLORATORY_RE = re.compile(
    r"\bне\s+уверен\w*|\bне\s+зна\w*.{0,35}(?:хоч\w*|подключ\w*)|"
    r"\b(?:ещ[её]\s+|пока\s+)?не\s+решил\w*|\bсомнева\w*|"
    r"\bстоит\s+ли\s+подключ\w*|\bподключ\w*\s+ли\b|"
    r"\b(?:готов\w*|хоч\w*|давай(?:те)?)\s+(?:сначала\s+)?"
    r"(?:обсуд\w*|рассмотр\w*)|"
    r"\b(?:нужн\w*|планир\w*)\s+(?:сначала\s+)?"
    r"(?:узна\w*|изуч\w*|обсуд\w*|рассмотр\w*)",
    re.I,
)
_DIRECT_JOIN_RE = re.compile(
    r"\b(?:подключ(?:и|ите)|можете\s+подключить|прошу\s+подключить)\b|"
    r"\b(?:начните|оформите|запускайте)\s+подключени\w*|"
    r"\b(?:я\s+)?(?:выбираю|согласен\w*\s+на)\s+(?:иу|"
    r"индивидуальн\w*\s+услови\w*|партн[её]рск\w*\s+программ\w*)|"
    r"\bхоч\w*\s+(?:вступить|участвовать)\b",
    re.I,
)
_BARE_JOIN_RE = re.compile(
    r"\s*(?:я\s+)?(?:да\s*,?\s*)?"
    r"(?:хоч\w*|готов\w*|давай(?:те)?|решил\w*|соглас\w*|начинаем|нужн\w*)\s+"
    r"(?:уже\s+)?(?:подключиться|подключаться)"
    r"(?:\s+с\s+(?:вами|albery|албери))?\s*[.!]*\s*",
    re.I,
)
_DATA_ACTION_RE = re.compile(
    r"(?:переда\w*|предостав\w*|отправ\w*|остав\w*|заполн\w*).{0,25}"
    r"(?:данн\w*|информац\w*|анкет\w*|заявк\w*)",
    re.I,
)
_DIRECT_DATA_TRANSFER_RE = re.compile(
    r"(?:могу\s+)?(?:переда\w*|предостав\w*|отправ\w*|остав\w*|заполн\w*)"
    r".{0,25}(?:данн\w*|информац\w*|анкет\w*|заявк\w*)",
    re.I,
)
_DATA_TRANSFER_NEGATION_RE = re.compile(
    r"\bне\s+(?:могу\s+)?(?:переда\w*|предостав\w*|отправ\w*|остав\w*|заполн\w*)",
    re.I,
)
_SHORT_CONFIRM_RE = re.compile(
    r"\s*(?:да(?:\s*,?\s*(?:давайте|конечно|присылайте))?|давайте|согласен|согласна|"
    r"готов|готова|хорошо|конечно|ок|присылайте)\s*[.!👍👌]*\s*",
    re.I,
)
_FORM_CONTEXT_RE = re.compile(
    r"(?:готов\w*|хот\w*|соглас\w*).{0,30}заполн\w*.{0,25}"
    r"(?:анкет\w*|заявк\w*|форм\w*)|"
    r"(?:могу|можем|давай(?:те)?|предлагаю).{0,30}"
    r"(?:присла\w*|отправ\w*|дать|перейти).{0,35}"
    r"(?:ссылк\w*|анкет\w*|заявк\w*|форм\w*)|"
    r"(?:хотите.{0,20})?(?:я\s+)?(?:пришл\w*|отправ\w*|скин\w*)"
    r".{0,35}(?:ссылк\w*|анкет\w*|заявк\w*|форм\w*)|"
    r"(?:прислать|отправить|скинуть|дать).{0,35}"
    r"(?:ссылк\w*|анкет\w*|заявк\w*|форм\w*)|"
    r"(?:предлагаю|давай(?:те)?).{0,25}заполнить.{0,25}"
    r"(?:анкет\w*|заявк\w*|форм\w*)",
    re.I,
)
_FORM_OFFER_NEGATION_RE = re.compile(
    r"\bне\s+(?:могу|можем|готов\w*|хоч\w*|буд\w*|получ\w*)\b.{0,60}"
    r"(?:присла\w*|отправ\w*|скин\w*|дать|заполн\w*|"
    r"ссылк\w*|анкет\w*|заявк\w*|форм\w*)",
    re.I,
)
_CONTEXT_FORM_ACCEPT_RE = re.compile(
    r"\s*(?:"
    r"(?:можно|нужн\w*|хоч\w*|давай(?:те)?)\s+(?:анкет\w*|форм\w*|заявк\w*)|"
    r"(?:анкет\w*|форм\w*|заявк\w*|ссылк\w*)\s*,?\s*пожалуйста|"
    r"(?:дай\w*|дайте)\s+ссылк\w*|можно\s+ссылк\w*|"
    r"(?:да|хорошо|готов\w*)\s*,?\s*(?:пришл\w*|присыла\w*|заполн\w*)|"
    r"(?:я\s+|могу\s+|хоч\w*\s+)?заполн\w*(?:\s+(?:анкет\w*|форм\w*|заявк\w*))?"
    r")\s*[?!.]*\s*",
    re.I,
)
_EXPLICIT_IU_TERMS_RE = re.compile(
    r"(?:услови\w*|комисси\w*|тариф\w*|стоим\w*|цен[аыу]\w*|сколько\s+стоит)"
    r"\s+(?:(?:по|для|на)\s+)?(?:иу|и\.у\.?)\b|"
    r"\b(?:иу|и\.у\.?)\s*[-—:]?\s*"
    r"(?:услови\w*|комисси\w*|тариф\w*|стоим\w*|цен[аыу]\w*|сколько\s+стоит)",
    re.I,
)
_EXPLICIT_IU_FORM_RE = re.compile(
    r"(?:анкет\w*|форм\w*|заявк\w*)\s+(?:(?:по|для|на)\s+)?(?:иу|и\.у\.?)\b|"
    r"\b(?:иу|и\.у\.?)\s*[-—:]?\s*(?:анкет\w*|форм\w*|заявк\w*)",
    re.I,
)
_EXPLICIT_IU_JOIN_RE = re.compile(
    r"(?:подключ\w*|присоедин\w*|вступ\w*)\s+(?:к\s+|на\s+)?(?:иу|и\.у\.?)\b|"
    r"\b(?:иу|и\.у\.?)\s*[-—:]?\s*(?:подключ\w*|присоедин\w*|вступ\w*)",
    re.I,
)
_GENERAL_IU_INTEREST_RE = re.compile(
    r"интерес\w*|расскаж\w*|подробн\w*|как\s+подключ\w*|что\s+за\s+программ\w*",
    re.I,
)

_URL_RE = re.compile(r"https?://\S+", re.I)
_NEXT_STEP_RE = re.compile(
    r"\b(?:напиш(?:и|ите)|пришл(?:и|ите)|отправ(?:ь|ьте)|уточн(?:и|ите)|"
    r"расскаж(?:и|ите)|укаж(?:и|ите)|заполн(?:и|ите)|остав(?:ь|ьте)|"
    r"перейд(?:и|ите)|выбер(?:и|ите)|подтверд(?:и|ите)|сообщ(?:и|ите)|давай(?:те)?|"
    r"да(?:й|йте)|скаж(?:и|ите)|направ(?:ь|ьте)|покаж(?:и|ите)|ответ(?:ь|ьте)|"
    r"позвон(?:и|ите)|предостав(?:ь|ьте)|перешл(?:и|ите)|загруз(?:и|ите)|"
    r"добав(?:ь|ьте)|прикреп(?:и|ите)|свяж(?:ись|итесь)|оплат(?:и|ите)|"
    r"подпиш(?:и|ите)|нажм(?:и|ите)|открой(?:те)?)\b|"
    r"\b(?:ознаком(?:ься|ьтесь)|обрат(?:ись|итесь)|оформ(?:и|ите)|"
    r"провер(?:ь|ьте)|посмотр(?:и|ите)|дожд(?:ись|итесь)|жд(?:и|ите))\b|"
    r"\b(?!здравствуйте\b)[а-яё]{3,}(?:йте|ьте|дите)\b|"
    r"\b(?:можно|можете)\b.{0,45}\b(?:заполнить|использовать)\b.{0,20}"
    r"\b(?:анкет\w*|форм\w*|заявк\w*)\b|"
    r"\bначать\b.{0,20}\bможно\b.{0,20}\b(?:анкет\w*|форм\w*|заявк\w*)\b|"
    r"\bдоступн\w*\b.{0,20}\b(?:анкет\w*|форм\w*|заявк\w*)\b|"
    r"\b(?:нужно|необходимо|можете|предлагаю|лучше|стоит|следует|важно|"
    r"рекомендую|советую)\s+"
    r"(?:написать|прислать|отправить|уточнить|рассказать|указать|заполнить|"
    r"оставить|перейти|выбрать|подтвердить|сообщить|дать|сказать|направить|"
    r"показать|ответить|позвонить|предоставить|переслать|загрузить|добавить|"
    r"прикрепить|связаться|оплатить|подписать|нажать|открыть|ознакомиться|"
    r"обратиться|оформить|проверить|посмотреть|дождаться|ждать)\b|"
    r"^\s*(?:написать|прислать|отправить|уточнить|рассказать|указать|заполнить|"
    r"оставить|перейти|выбрать|подтвердить|сообщить|дать|направить|показать|"
    r"ответить|позвонить|предоставить|переслать|загрузить|добавить|прикрепить|"
    r"связаться|оплатить|подписать|нажать|открыть|ознакомиться|обратиться|"
    r"оформить|проверить|посмотреть|дождаться|ждать)\b",
    re.I,
)
_QUESTION_WORD_RE = re.compile(
    r"\b(?:что|кто|где|когда|зачем|почему|как|каков\w*|какая|какие|какой|сколько)\b",
    re.I,
)
_QUESTION_CLAUSE_SPLIT_RE = re.compile(
    r"(?:\s+(?:и|а|плюс)\s+|\s*[,;:—–]\s*)(?="
    r"(?:что|кто|где|когда|зачем|почему|как|каков\w*|какая|какие|какой|сколько|"
    r"расскаж\w*|объясн\w*|уточн\w*|подскаж\w*)\b)",
    re.I,
)
_PUNCTUATED_QUESTION_SPLIT_RE = re.compile(
    r"\s*[,;:—–]\s*(?=[^,;:—–]*(?:[?？]|"
    r"\b(?:что|кто|где|когда|зачем|почему|как|каков\w*|какая|какие|какой|"
    r"сколько|расскаж\w*|объясн\w*|уточн\w*|подскаж\w*)\b))",
    re.I,
)
_BARE_QUESTION_LEAD_IN_RE = re.compile(
    r"\s*(?:скаж\w*|расскаж\w*|объясн\w*|уточн\w*|подскаж\w*)"
    r"\s*[,.:;-]?\s*(?:пожалуйста)?\s*[,.:;-]?\s*",
    re.I,
)
_FORM_FIELD_STEP_RE = re.compile(
    r"\b(?:оборот\w*|категори\w*|магазин\w*|ссылк\w*.{0,20}магазин\w*|"
    r"что\s+прода[её]т\w*|какие\s+товар\w*)\b",
    re.I,
)
_FORM_ASSET_RE = re.compile(r"\b(?:анкет\w*|форм\w*|заявк\w*)\b", re.I)
_FORM_OFFER_WORDING_RE = re.compile(
    r"\bвот\b.{0,20}(?:анкет\w*|форм\w*|заявк\w*)|"
    r"\bссылк\w*.{0,20}(?:анкет\w*|форм\w*|заявк\w*)|"
    r"\b(?:анкет\w*|форм\w*|заявк\w*)\b.{0,20}(?:здесь|ниже|по\s+ссылк\w*)|"
    r"\b(?:могу|можем)\b.{0,25}(?:присла\w*|отправ\w*|перейти\w*)"
    r".{0,25}(?:анкет\w*|форм\w*|заявк\w*)|"
    r"\b(?:хотите|предлагаю|советую|рекомендую)\b.{0,25}"
    r"(?:анкет\w*|форм\w*|заявк\w*)|"
    r"\b(?:я\s+)?пришл\w*.{0,25}(?:анкет\w*|форм\w*|заявк\w*)|"
    r"\b(?:можно|можете|нужно|следует|стоит)\b.{0,50}"
    r"(?:заполн\w*|использ\w*|нача\w*|перейти\w*).{0,30}"
    r"(?:анкет\w*|форм\w*|заявк\w*)|"
    r"\bнача\w*\b.{0,20}\bможно\b.{0,20}(?:анкет\w*|форм\w*|заявк\w*)|"
    r"\bдоступн\w*\b.{0,20}(?:анкет\w*|форм\w*|заявк\w*)|"
    r"\b(?:анкет\w*|форм\w*|заявк\w*)\b.{0,20}\bдоступн\w*",
    re.I,
)


@dataclass(frozen=True)
class CustomerIntent:
    """Разные разрешения одного хода; одно не подразумевает другое."""

    iu_topic: bool = False
    asks_terms: bool = False
    asks_form: bool = False
    wants_to_join: bool = False
    confirms_form: bool = False
    resend_form: bool = False
    rejects_form: bool = False
    rejects_terms: bool = False
    rejects_iu: bool = False
    rejects: bool = False

    @property
    def offer_form(self) -> bool:
        return not self.rejects_form and not self.rejects_iu and (
            self.asks_form or self.wants_to_join or self.confirms_form or self.resend_form
        )

    @property
    def open_deal(self) -> bool:
        return not self.rejects and (
            self.iu_topic or self.asks_terms or self.asks_form or self.wants_to_join
            or self.resend_form
        )


def has_iu_context(text: str) -> bool:
    """Есть ли в тексте именно тема ИУ, а не отдельное слово «условия»."""
    value = str(text or "")
    partner_match = _PARTNER_PROGRAM_RE.search(value)
    partner_scoped = False
    if partner_match:
        # «Партнёрская программа» без уточнения — рабочее название ИУ, но произвольный
        # хвост («школы», «клуба», «магазина») уже называет чужой продукт. Белый список
        # контекста намеренно узкий: неизвестный объект безопаснее уточнить, чем слать asset.
        prefix = value[:partner_match.start()]
        tail = value[partner_match.end():]
        previous = re.search(r"([а-яёa-z]+)\s*$", prefix, re.I)
        prefix_scoped = bool(
            previous is None
            or _PARTNER_ALLOWED_PREFIX_WORD_RE.fullmatch(previous.group(1))
        )
        explicit_product = bool(
            _IU_ACRONYM_RE.search(value)
            or re.search(r"\b(?:albery|албери|wb|wildberries|вайлдберриз)\b", value, re.I)
        )
        partner_scoped = bool(
            explicit_product
            or _FIRST_PERSON_PARTNER_INTEREST_RE.fullmatch(value)
            or (prefix_scoped and _PARTNER_ALLOWED_TAIL_RE.fullmatch(tail))
        )
    return bool(
        _IU_ACRONYM_RE.search(value)
        or _IU_PHRASE_RE.search(value)
        or _BARE_PRODUCT_ALIAS_RE.fullmatch(value)
        or partner_scoped
    )


def _form_asset_has_foreign_tail(text: str) -> bool:
    """Назван ли после анкеты/формы посторонний объект.

    CRM-этап не превращает «форму оплаты» или «анкету кандидата» в анкету ИУ. Для
    неизвестного хвоста действует fail-closed; явно разрешены только ИУ/подключение и слова
    повторной отправки.
    """
    value = str(text or "")
    for match in _FORM_ASSET_OBJECT_RE.finditer(value):
        tail = value[match.end():]
        # Смотрим до следующей самостоятельной фразы: объект формы находится рядом с asset.
        tail = re.split(
            r"[;\n]|[.!?？]\s+|"
            r"\s+(?=(?:доступн\w*|наход\w*|есть)\b)|"
            r"\s+(?:и|а|но)\s+(?=(?:скаж\w*|расскаж\w*|подскаж\w*|уточн\w*|"
            r"пришл\w*|отправ\w*|покаж\w*)\b)|"
            r"\s*,?\s*(?:(?:и|а|но)\s+)?(?="
            r"(?:что|кто|где|когда|зачем|почему|как|каков\w*|какая|какие|"
            r"какой|сколько|хоч\w*\s+узна\w*)\b)",
            tail,
            maxsplit=1,
            flags=re.I,
        )[0]
        if tail.strip() and not _FORM_ALLOWED_TAIL_RE.fullmatch(tail):
            return True
    return False


def _last_agent_message(history: str) -> str:
    """Последняя видимая реплика агента из текстового журнала `Клиент:` / `Ты:`."""
    messages = re.findall(
        r"(?:^|\n)Ты:\s*(.*?)(?=\n(?:Клиент|Ты):|\Z)",
        str(history or ""),
        flags=re.S,
    )
    return messages[-1].strip() if messages else ""


def _intent_clauses(text: str) -> list[str]:
    """Не переносить отрицание одного asset через «но» на отдельный запрос другого."""
    return [
        clause.strip()
        for clause in re.split(
            r"[.!?？;\n]+|,\s*(?:но|а|зато)\s+|\s+(?:но|зато)\s+|"
            r",\s*(?=(?:расскажите|пришлите|отправьте|скиньте|дайте|покажите|"
            r"хочу|готов\w*|подключите|"
            r"оформите)\b)",
            str(text or ""),
            flags=re.I,
        )
        if clause.strip()
    ]


def classify(text: str, history: str = "", *, known_lead: bool = False) -> CustomerIntent:
    """Классифицировать ход один раз для CRM, документа, формы и промпта."""
    value = " ".join(str(text or "").split())
    prior = str(history or "")
    last_agent = _last_agent_message(prior)
    explicit_acronym = bool(_IU_ACRONYM_RE.search(value))
    current_context = has_iu_context(value)
    history_context = has_iu_context(prior)
    context = current_context or history_context
    strong_terms = bool(_BARE_CONNECTION_TERMS_RE.fullmatch(value))
    terms_topic = bool(_TERMS_TOPIC_RE.search(value))
    clauses = _intent_clauses(value)
    rejects_terms = any(
        _TERMS_REJECTION_RE.search(clause)
        or (
            _FUSED_DISINTEREST_RE.search(clause)
            and _TERMS_TOPIC_RE.search(clause)
        )
        for clause in clauses
    )
    rejects_form = any(_FORM_REJECTION_RE.search(clause) for clause in clauses)
    rejects_iu_raw = any(
        _IU_REJECTION_RE.search(clause)
        or (
            _FUSED_DISINTEREST_RE.search(clause)
            and not _TERMS_TOPIC_RE.search(clause)
        )
        for clause in clauses
    )
    positive_iu_clause = any(
        not _IU_REJECTION_RE.search(clause)
        and not _FUSED_DISINTEREST_RE.search(clause)
        and (
            (
                _TERMS_TOPIC_RE.search(clause)
                and has_iu_context(clause)
                and not _TERMS_REJECTION_RE.search(clause)
            )
            or (
                (_ANKETA_RE.search(clause)
                 or _APPLICATION_RE.search(clause)
                 or _GENERIC_FORM_RE.search(clause))
                and _FORM_REQUEST_RE.search(clause)
                and not _FORM_REJECTION_RE.search(clause)
            )
            or (
                (_COMMITMENT_RE.search(clause) or _DIRECT_JOIN_RE.search(clause))
                and _CONNECT_ACTION_RE.search(clause)
                and not _EXPLORATORY_RE.search(clause)
            )
        )
        for clause in clauses
    )
    rejects_iu = bool(rejects_iu_raw and not positive_iu_clause)
    other_topic = bool(_OTHER_TOPIC_RE.search(value))
    explicit_terms = bool(_EXPLICIT_IU_TERMS_RE.search(value))
    explicit_form = bool(_EXPLICIT_IU_FORM_RE.search(value))
    explicit_join = bool(_EXPLICIT_IU_JOIN_RE.search(value))
    foreign_terms_object = bool(
        not current_context and _FOREIGN_TERMS_OBJECT_RE.search(value)
    )
    foreign_named_form_request = bool(
        _FORM_ASSET_OBJECT_RE.search(value)
        and _FORM_REQUEST_RE.search(value)
        and _form_asset_has_foreign_tail(value)
    )

    asks_terms_now = (
        terms_topic
        and (
            current_context
            or strong_terms
            or (
                (known_lead or history_context)
                and (
                    _BARE_TERMS_RE.fullmatch(value)
                    or _TERMS_RESEND_RE.search(value)
                    or _BARE_TERMS_REQUEST_RE.fullmatch(value)
                )
            )
        )
        and (not other_topic or explicit_terms)
        and not foreign_terms_object
        and not foreign_named_form_request
        and not _CONTRACT_ACTION_RE.search(value)
        and not rejects_terms
        and not rejects_iu
    )

    form_context_now = bool(
        current_context
        or re.search(
            r"(?:анкет\w*|заявк\w*|форм\w*).{0,35}подключ\w*|"
            r"подключ\w*.{0,35}(?:анкет\w*|заявк\w*|форм\w*)",
            value,
            re.I,
        )
    )
    bare_form_request = bool(_BARE_FORM_REQUEST_RE.fullmatch(value))
    asks_named_form = bool(
        _ANKETA_RE.search(value) and _FORM_REQUEST_RE.search(value)
        and (not other_topic or explicit_form) and (form_context_now or bare_form_request)
    )
    asks_application = bool(
        _APPLICATION_RE.search(value) and _FORM_REQUEST_RE.search(value)
        and (not other_topic or explicit_form) and (form_context_now or bare_form_request)
    )
    asks_generic_form = bool(
        _GENERIC_FORM_RE.search(value) and _FORM_REQUEST_RE.search(value)
        and (not other_topic or explicit_form) and (form_context_now or bare_form_request)
    )
    direct_named_form = bool(_DIRECT_NAMED_FORM_INTENT_RE.fullmatch(value))
    asks_form_now = not rejects_form and not rejects_iu and (
        asks_named_form or asks_application or asks_generic_form or direct_named_form
    ) and not _form_asset_has_foreign_tail(value)
    resend_form_now = bool(
        not rejects_form
        and not rejects_iu
        and (not other_topic or explicit_form)
        and not _form_asset_has_foreign_tail(value)
        and _FORM_RESEND_RE.search(value)
        and (
            _ANKETA_RE.search(value)
            or _APPLICATION_RE.search(value)
            or _GENERIC_FORM_RE.search(value)
            or (
                re.search(r"\bссылк\w*\b", value, re.I)
                and _FORM_CONTEXT_RE.search(last_agent)
                and not _FORM_OFFER_NEGATION_RE.search(last_agent)
                and not _FORM_REJECTION_RE.search(last_agent)
            )
        )
    )

    committed = bool(_COMMITMENT_RE.search(value))
    joins = bool(
        committed
        and _CONNECT_ACTION_RE.search(value)
        and not _INFO_ONLY_RE.search(value)
        and not _EXPLORATORY_RE.search(value)
        and (not other_topic or explicit_join)
        and (current_context or _BARE_JOIN_RE.fullmatch(value))
    )
    direct_join = bool(
        current_context
        and _DIRECT_JOIN_RE.search(value)
        and not _EXPLORATORY_RE.search(value)
        and (not other_topic or explicit_join)
    )
    transfers = bool(
        current_context
        and (
            (committed and _DATA_ACTION_RE.search(value))
            or _DIRECT_DATA_TRANSFER_RE.search(value)
        )
        and not _DATA_TRANSFER_NEGATION_RE.search(value)
        and not _EXPLORATORY_RE.search(value)
        and (not other_topic or explicit_form or explicit_join)
    )
    wants_to_join_now = not rejects_form and not rejects_iu and (
        joins or direct_join or transfers
    )

    confirms = bool(
        not rejects_form
        and not rejects_iu
        and (
            _SHORT_CONFIRM_RE.fullmatch(value)
            or _CONTEXT_FORM_ACCEPT_RE.fullmatch(value)
        )
        and _FORM_CONTEXT_RE.search(last_agent)
        and not _FORM_OFFER_NEGATION_RE.search(last_agent)
        and not _FORM_REJECTION_RE.search(last_agent)
        and not _IU_REJECTION_RE.search(last_agent)
    )

    positive_asset = bool(
        asks_terms_now or asks_form_now or resend_form_now or wants_to_join_now or confirms
    )
    rejects = bool((rejects_form or rejects_terms or rejects_iu) and not positive_asset)
    iu_topic = bool(
        (not other_topic or explicit_acronym)
        and (
            context or strong_terms or asks_terms_now or asks_form_now or wants_to_join_now
            or resend_form_now
            or (context and _GENERAL_IU_INTEREST_RE.search(value))
        )
    )
    # Чистый отказ — не лид и не разрешение на какой-либо asset.
    if rejects:
        iu_topic = False

    return CustomerIntent(
        iu_topic=iu_topic,
        asks_terms=bool(asks_terms_now),
        asks_form=bool(asks_form_now),
        wants_to_join=bool(wants_to_join_now),
        confirms_form=bool(confirms),
        resend_form=bool(resend_form_now),
        rejects_form=bool(rejects_form),
        rejects_terms=bool(rejects_terms),
        rejects_iu=bool(rejects_iu),
        rejects=bool(rejects),
    )


def asks_terms(text: str, history: str = "", *, known_lead: bool = False) -> bool:
    return classify(text, history, known_lead=known_lead).asks_terms


def wants_form(text: str, history: str = "", *, known_lead: bool = False) -> bool:
    return classify(text, history, known_lead=known_lead).offer_form


def has_iu_interest(text: str, history: str = "") -> bool:
    return classify(text, history).open_deal


def has_other_topic(text: str) -> bool:
    """Есть ли в ходе отдельная тема вне продукта ИУ, требующая своего ответа/handoff."""
    return bool(_OTHER_TOPIC_RE.search(str(text or "")))


def question_clauses(text: str) -> list[str]:
    """Разбить один ход на самостоятельные вопросы, включая «... и как ...»."""
    primary = [
        part.strip(" \t-–—•")
        for part in re.split(r"[\n\r]+|(?<=[?？])\s*|\s+\+(?=\S)", str(text or ""))
        if part and part.strip()
    ]
    out: list[str] = []
    for part in primary:
        for item in _QUESTION_CLAUSE_SPLIT_RE.split(part):
            if not item or not item.strip(" \t-–—•"):
                continue
            out.extend(
                nested.strip(" \t-–—•")
                for nested in _PUNCTUATED_QUESTION_SPLIT_RE.split(item)
                if nested and nested.strip(" \t-–—•")
            )
    return [
        part for part in out
        if len(part) > 2 and not _BARE_QUESTION_LEAD_IN_RE.fullmatch(part)
    ]


def has_additional_question(text: str) -> bool:
    """Есть ли кроме коммерческого вопроса, закрываемого документом, ещё один запрос."""
    value = str(text or "")
    if has_other_topic(value):
        return True
    for clause in question_clauses(value):
        question_like = bool(
            "?" in clause or "？" in clause or _QUESTION_WORD_RE.search(clause)
            or re.search(r"\b(?:расскаж\w*|объясн\w*|уточн\w*|подскаж\w*)\b", clause, re.I)
        )
        terms_covered = bool(
            _TERMS_DOCUMENT_QUESTION_RE.search(clause)
            or _BARE_PRODUCT_ALIAS_RE.fullmatch(clause)
            or _BARE_IU_OVERVIEW_RE.fullmatch(clause)
        )
        if question_like and not terms_covered:
            return True
    return False


def has_next_step(answer: str) -> bool:
    """Уже выбрала ли модель вопрос/CTA, рядом с которым нельзя клеить системную форму."""
    value = str(answer or "").strip()
    return bool(
        "?" in value
        or "？" in value
        or _URL_RE.search(value)
        or _NEXT_STEP_RE.search(value)
        or (
            _FORM_ASSET_RE.search(value)
            and _FORM_OFFER_WORDING_RE.search(value)
        )
    )


def _sentence_parts(value: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"(?<=[!?？])\s*|(?<=\.)\s+|\n+", value)
        if part and part.strip()
    ]


def without_next_steps(answer: str) -> str:
    """Оставить только содержательную часть перед системным CTA."""
    value = str(answer or "").strip()
    return " ".join(part for part in _sentence_parts(value) if not has_next_step(part)).strip()


def without_form_steps(answer: str) -> str:
    """Убрать модельное приглашение в форму, если policy его не разрешила."""
    kept: list[str] = []
    for part in _sentence_parts(str(answer or "").strip()):
        if (
            _FORM_ASSET_RE.search(part)
            and _FORM_OFFER_WORDING_RE.search(part)
            and _URL_RE.search(part)
        ):
            continue
        clauses = [
            item.strip()
            for item in re.split(r"(?<=[,;:—–])\s+", part)
            if item.strip()
        ]
        for clause in clauses:
            if _FORM_ASSET_RE.search(clause) and _form_asset_has_foreign_tail(clause):
                kept.append(clause)
                continue
            if _FORM_ASSET_RE.search(clause) and (
                has_next_step(clause) or _FORM_OFFER_WORDING_RE.search(clause)
            ):
                continue
            kept.append(clause)
    return " ".join(kept).strip()


def _finish_step(prefix: str, *, question: bool = False) -> str:
    prefix = re.sub(
        r"(?:[,;]\s*|\s+(?:и|а|затем|потом)\s*)$", "", prefix.rstrip(), flags=re.I,
    )
    if prefix and prefix[-1] not in ".!?？" and not _URL_RE.fullmatch(prefix):
        prefix += "?" if question else "."
    return prefix


def _keep_first_action_in_part(part: str) -> str:
    actions = []
    seen_spans: set[tuple[int, int]] = set()
    for match in _NEXT_STEP_RE.finditer(part):
        if match.span() not in seen_spans:
            actions.append(match)
            seen_spans.add(match.span())
    urls = list(_URL_RE.finditer(part))
    questions = list(_QUESTION_WORD_RE.finditer(part)) if ("?" in part or "？" in part) else []
    # Несколько вопросительных слов внутри одного грамматического вопроса — всё ещё один шаг.
    questions = questions[:1]
    candidates = sorted(
        [(match.start(), "action") for match in actions]
        + [(match.start(), "url") for match in urls]
        + [(match.start(), "question") for match in questions],
        key=lambda item: item[0],
    )
    if not candidates:
        return part
    first_pos, first_kind = candidates[0]
    for position, kind in candidates[1:]:
        # «Пришлите ссылку?» — один вопрос-призыв, а не два разных шага.
        if {kind, first_kind} == {"action", "question"} and abs(position - first_pos) < 12:
            continue
        return _finish_step(part[:position], question=first_kind == "question")
    if len(actions) > 1:
        return _finish_step(part[:actions[1].start()])
    return part


def _mixed_clauses(part: str) -> list[str]:
    """Разделить «CTA, затем факт», чтобы факт можно было поставить первым."""
    clauses = [
        item.strip()
        for item in re.split(r"(?<=[,;:—–])\s+", part)
        if item.strip()
    ]
    if len(clauses) < 2:
        clauses = [
            item.strip()
            for item in re.split(
                r"\s+и\s+(?=(?:после|тогда|при\s+этом|далее|"
                r"услови\w*|комисси\w*|иу\b|подключени\w*|систем\w*|"
                r"документ\w*|анкет\w*))",
                part,
                flags=re.I,
            )
            if item.strip()
        ]
    if len(clauses) < 2:
        return [part]
    if clauses[0].rstrip(" ,;").casefold() in {
        "да", "конечно", "хорошо", "понял", "поняла",
    }:
        return [part]
    flags = [has_next_step(item) for item in clauses]
    if all(flags) or not any(flags):
        return [part]
    out: list[str] = []
    for item in clauses:
        clean = item.rstrip(" ,;:—–-")
        starts_with_url = bool(_URL_RE.match(clean))
        is_bare_url = bool(_URL_RE.fullmatch(clean))
        if clean[:1].isalpha() and not starts_with_url:
            clean = clean[:1].upper() + clean[1:]
        if clean and clean[-1] not in ".!?？" and not is_bare_url:
            clean += "."
        out.append(clean)
    return out


def keep_one_next_step(answer: str) -> str:
    """Оставить содержательный ответ и не больше одного вопроса/CTA модели.

    Промпт снижает вероятность лишних шагов, но runtime не полагается только на послушание LLM.
    Предложения без CTA сохраняются все; из предложений-вопросов/призывов остаётся первое."""
    value = str(answer or "").strip()
    if not value:
        return ""
    informational: list[str] = []
    next_steps: list[str] = []
    for raw_part in _sentence_parts(value):
        for raw_clause in _mixed_clauses(raw_part):
            part = _keep_first_action_in_part(raw_clause)
            if has_next_step(part):
                # Эти данные живут в анкете; повторный допрос в чате и выглядит как скрипт.
                if _FORM_FIELD_STEP_RE.search(part):
                    continue
                next_steps.append(part)
            else:
                informational.append(part)
    # Даже если модель начала с просьбы, клиент сначала получает содержательный ответ.
    return " ".join(informational + next_steps[:1]).strip()
