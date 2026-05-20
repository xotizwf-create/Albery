from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


CHAT = "chat62921"
CALL = "36b344ae-4f94-4644-81c6-1334b2794a8a"
MODEL = "local_manual_correction_no_external_ai_v2"


def main() -> None:
    previous_tasks = [
        {
            "task_id": "c0e250cbacff123e",
            "text": "подтвердить ознакомление с правилом проведения рабочих встреч в Zoom",
            "person_name": "Все руководители",
            "deadline": "2026-05-13",
            "current_status": "partial",
            "status_reason": "В чате обсуждались место хранения транскрибаций и ссылка на папку, но прямого подтверждения ознакомления всех руководителей нет.",
            "source_type": "mixed",
            "confidence": "direct",
            "evidence_message_ids": [2309239, 2309241, 2309243, 2309245, 2309247, 2309303],
            "evidence_zoom_call_ids": [CALL],
        },
        {
            "task_id": "d15923c581019381",
            "text": "назначить ответственного и срок по согласованию документа «Фондовая политика»",
            "person_name": "Наталья Горюнова",
            "deadline": "2026-05-14",
            "current_status": "no_info",
            "status_reason": "В переписке 14.05 нет фактического движения по этому хвосту.",
            "source_type": "previous_report",
            "confidence": "direct",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [],
        },
        {
            "task_id": "5a61a75ea90d62f5",
            "text": "определить и зафиксировать единый маршрут юридического согласования документов",
            "person_name": "Наталья Горюнова",
            "deadline": "2026-05-15",
            "current_status": "partial",
            "status_reason": "В Zoom 14.05 были общие обсуждения оргдокументов/юридического согласования, но маршрут не зафиксирован как закрытый.",
            "source_type": "zoom",
            "confidence": "inferred",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [CALL],
        },
        {
            "task_id": "2dfab3c78c941a1f",
            "text": "инициировать обсуждение стратегических документов «Альбери 2.0», единая цель, финансовый контур, Bitrix-долг, Ларетто",
            "person_name": "Евгений Палей",
            "deadline": "2026-05-15",
            "current_status": "partial",
            "status_reason": "В Zoom 14.05 обсуждались платежный календарь, Laretto, Bitrix/задачи и оргконтур, но хвост не закрыт полностью.",
            "source_type": "zoom",
            "confidence": "inferred",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [CALL],
        },
        {
            "task_id": "0c837f380daa639f",
            "text": "установить срок выполнения задачи «Оплатить удаленные рабочие столы» и подтвердить статус",
            "person_name": "Анастасия Андрусяк",
            "deadline": "2026-05-13",
            "current_status": "no_info",
            "status_reason": "В переписке 14.05 нет подтверждения статуса этой задачи.",
            "source_type": "previous_report",
            "confidence": "direct",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [],
        },
        {
            "task_id": "cbe1d9b798f4f815",
            "text": "Еще раз проверить два документа, которые Сергей отправил, и отправить Евгению Палею на подпись",
            "person_name": "Наталья Горюнова",
            "deadline": "2026-05-12",
            "current_status": "partial",
            "status_reason": "По Zoom есть движение по оргдокументам, но нет прямого подтверждения отправки конкретных двух документов на подпись.",
            "source_type": "zoom",
            "confidence": "inferred",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [CALL],
        },
        {
            "task_id": "20b97af35ec8fcee",
            "text": "Переделать блок документов по оргизменениям, который стоит на 12.05, и закинуть его в рабочий контур",
            "person_name": "Наталья Горюнова",
            "deadline": "2026-05-12",
            "current_status": "partial",
            "status_reason": "Есть движение по оргдокументам в Zoom, но нет прямого подтверждения завершения блока.",
            "source_type": "zoom",
            "confidence": "inferred",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [CALL],
        },
        {
            "task_id": "8277f0ca38683953",
            "text": "Отправить юристу документы по обоснованию организационных изменений и схеме было/стало после повторной вычитки",
            "person_name": "Наталья Горюнова",
            "deadline": "2026-05-12",
            "current_status": "partial",
            "status_reason": "Есть обсуждение юридического согласования в Zoom, но нет прямой отправки юристу в переписке 14.05.",
            "source_type": "zoom",
            "confidence": "inferred",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [CALL],
        },
        {
            "task_id": "e5924f4d76acfc0d",
            "text": "Прогнать последние документы по обоснованию оргизменений через промпт Елены; новые документы тоже прогнать через этот промпт",
            "person_name": "Наталья Горюнова",
            "deadline": None,
            "current_status": "no_info",
            "status_reason": "В переписке 14.05 нет подтверждения прогона документов через промпт.",
            "source_type": "previous_report",
            "confidence": "direct",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [],
        },
        {
            "task_id": "780a10c0b8fb6788",
            "text": "Остановить подготовку шаблонных договоров с ИП и подрядчиками до формирования штатного контура и отдельного чата по договорам/должностным инструкциям",
            "person_name": "Наталья Горюнова",
            "deadline": "2026-05-12",
            "current_status": "no_info",
            "status_reason": "В переписке 14.05 нет подтверждения остановки или возобновления подготовки договоров.",
            "source_type": "previous_report",
            "confidence": "direct",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [],
        },
        {
            "task_id": "a07bb0aa36b5e74c",
            "text": "Создать или использовать отдельный чат по договорам и должностным инструкциям и подгрузить туда документы по штатникам, подрядчикам и аутсорсерам",
            "person_name": "Наталья Горюнова",
            "deadline": None,
            "current_status": "no_info",
            "status_reason": "В переписке 14.05 нет данных о создании/использовании такого чата.",
            "source_type": "previous_report",
            "confidence": "direct",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [],
        },
        {
            "task_id": "2ac14e57474a4ad0",
            "text": "Посмотреть документы 8 и 9 по оплате труда/премированию и привязать их к мотивации Натальи и Артура",
            "person_name": "Сергей Виноградов",
            "deadline": None,
            "current_status": "no_info",
            "status_reason": "В переписке 14.05 нет движения по документам 8 и 9.",
            "source_type": "previous_report",
            "confidence": "direct",
            "evidence_message_ids": [],
            "evidence_zoom_call_ids": [],
        },
    ]

    analysis = {
        "results": [
            {
                "text": "Евгений Палей дал ссылку на папку Google Drive, где можно получить транскрибации встреч.",
                "person_name": "Евгений Палей",
                "completed_at": "2026-05-14",
                "source_type": "chat",
                "confidence": "direct",
                "evidence_message_ids": [2309241, 2309303],
                "evidence_zoom_call_ids": [],
                "links_to_previous_task_text": "закрывает вопрос Натальи о месте получения транскрибаций",
            },
            {
                "text": "Евгений Палей уточнил, что после 11 мая транскрибации обрабатываются и будут подгружены в ту же папку по готовности.",
                "person_name": "Евгений Палей",
                "completed_at": "2026-05-14",
                "source_type": "chat",
                "confidence": "direct",
                "evidence_message_ids": [2309245, 2309247],
                "evidence_zoom_call_ids": [],
                "links_to_previous_task_text": "частично закрывает вопрос Натальи о транскрибациях после 11 мая",
            },
        ],
        "mentions": [
            {
                "mentioned_person_name": "Евгений Палей",
                "mentioned_by": "Артур Игоревич Степанян",
                "message_id": 2308979,
                "reason": "обращение в чате",
            },
            {
                "mentioned_person_name": "Наталья Горюнова",
                "mentioned_by": "системное сообщение",
                "message_id": 2309303,
                "reason": "закрепила сообщение со ссылкой",
            },
        ],
        "decisions": [],
        "questions": [
            {
                "question_id": "chat-2308979-q",
                "text": "Евгений Палей подключаетесь?",
                "asked_by": "Артур Игоревич Степанян",
                "addressed_to": "Евгений Палей",
                "answer_status": "unclear",
                "question_type": "coordination",
                "source_type": "chat",
                "confidence": "direct",
                "asked_at_message_id": 2308979,
                "answer_message_ids": [],
                "evidence_message_ids": [2308979],
                "evidence_zoom_call_ids": [],
            },
            {
                "question_id": "chat-2309239-q",
                "text": "а где можно получить по нашим встречам?",
                "asked_by": "Наталья Викторовна Горюнова",
                "addressed_to": None,
                "answer_status": "answered",
                "question_type": "info_needed",
                "source_type": "chat",
                "confidence": "direct",
                "asked_at_message_id": 2309239,
                "answer_message_ids": [2309241, 2309303],
                "evidence_message_ids": [2309239, 2309241, 2309303],
                "evidence_zoom_call_ids": [],
            },
            {
                "question_id": "chat-2309245-q",
                "text": "а после 11 мая где можно посмотреть?",
                "asked_by": "Наталья Викторовна Горюнова",
                "addressed_to": None,
                "answer_status": "partially_answered",
                "question_type": "info_needed",
                "source_type": "chat",
                "confidence": "direct",
                "asked_at_message_id": 2309245,
                "answer_message_ids": [2309247],
                "evidence_message_ids": [2309245, 2309247],
                "evidence_zoom_call_ids": [],
            },
        ],
        "unanswered_questions": [
            {
                "question_id": "chat-2308979-q",
                "text": "Евгений Палей подключаетесь?",
                "asked_by": "Артур Игоревич Степанян",
                "addressed_to": "Евгений Палей",
                "answer_status": "unclear",
                "question_type": "coordination",
                "source_type": "chat",
                "confidence": "direct",
                "asked_at_message_id": 2308979,
                "answer_message_ids": [],
                "evidence_message_ids": [2308979],
                "evidence_zoom_call_ids": [],
            }
        ],
        "commitments": [],
        "explicit_risks": [
            {
                "risk_id": "ocr-2309235-missing",
                "text": "Вложение photo_2026-05-14 11.22.39.jpeg не было OCR-распознано: в БД нет local_path и ocr_text, поэтому содержимое изображения не попало в отчет.",
                "source_type": "chat",
                "confidence": "direct",
                "evidence_message_ids": [2309235, 2309237],
                "evidence_zoom_call_ids": [],
            }
        ],
        "previous_day_tasks": previous_tasks,
        "topics_discussed": [
            {
                "topic": "транскрибации встреч и место хранения",
                "outcome": "answered_with_followup_pending",
                "message_ids": [2309235, 2309237, 2309239, 2309241, 2309243, 2309245, 2309247, 2309303],
                "counters": {"results": 2, "decisions": 0, "questions": 2, "commitments": 0, "unanswered_questions": 0},
            },
            {
                "topic": "подключение Евгения к встрече",
                "outcome": "unclear",
                "message_ids": [2308979],
                "counters": {"results": 0, "decisions": 0, "questions": 1, "commitments": 0, "unanswered_questions": 1},
            },
        ],
        "report_brief": "14.05.2026: обсуждали, где смотреть транскрибации встреч. Ссылка на Google Drive закрыла вопрос о месте хранения; транскрибации после 11 мая еще обрабатываются и будут подгружены. Вложение с примером транскрибации не OCR-распознано.",
    }

    text = """Ежедневный отчет по чату

14.05.2026, чат "Allberi + Laretto. ИУ. Чат Руководителей". Активность: 9 сообщений, 1 вложение без OCR, 2 фактических ответа, 0 новых задач, 0 принятых решений. Вопросы без ответа: 1 координационный вопрос по подключению.

Источник:
- чат: chat62921;
- сообщения: 9;
- вложение: photo_2026-05-14 11.22.39.jpeg, OCR не выполнен, текста изображения в отчете нет;
- Zoom-отчет дня: 36b344ae-4f94-4644-81c6-1334b2794a8a использован как готовый analytical_note; сырой Zoom-транскрипт в анализ чата не передавался.

1. Хвосты с прошлого дня:
- Все руководители — подтвердить ознакомление с правилом проведения рабочих встреч в Zoom → частично: обсуждались место хранения транскрибаций и ссылка на папку, но прямого подтверждения ознакомления всех руководителей нет.
- Наталья Горюнова — назначить ответственного и срок по согласованию документа «Фондовая политика» → no_info: в переписке 14.05 нет фактического движения по этому хвосту.
- Наталья Горюнова — определить и зафиксировать единый маршрут юридического согласования документов → частично: в Zoom есть общая тема юридического согласования, но маршрут не закрыт.
- Евгений Палей — инициировать обсуждение стратегических документов «Альбери 2.0», единая цель, финансовый контур, Bitrix-долг, Ларетто → частично: в Zoom есть движение по платежному календарю, Laretto, Bitrix/задачам и оргконтуру, но хвост не закрыт полностью.
- Остальные хвосты по оргдокументам, удаленным рабочим столам, отдельному чату договоров и документам 8/9 остаются без прямого закрытия за 14.05.

2. Выполнено сегодня / ответы:
- Евгений Палей дал ссылку на папку Google Drive, где можно получить транскрибации встреч: https://drive.google.com/drive/folders/17m7xReEap0vIvJfsFH-tw463fPmKEr7_. Это закрывает вопрос Натальи «а где можно получить по нашим встречам?».
- Евгений Палей уточнил по транскрибациям после 11 мая: «обрабатывается и туда подгрузится по готовности». Это частичный ответ: место то же, но готовность еще не наступила.

3. Рабочие вопросы дня:
- Артур Игоревич Степанян: «Евгений Палей подключаетесь?» → статус unclear: в чате нет прямого ответа именно на этот координационный вопрос.
- Наталья Викторовна Горюнова: «а где можно получить по нашим встречам?» → answered: Евгений дал ссылку на Google Drive, сообщение закреплено.
- Наталья Викторовна Горюнова: «а после 11 мая где можно посмотреть?» → partially_answered: Евгений ответил, что обрабатывается и будет подгружено туда по готовности.

4. Вопросы без ответа:
- Только координационный вопрос Артура о подключении Евгения остается без прямого текстового закрытия.

5. Принятые решения:
- Принятых решений в переписке 14.05 не зафиксировано. Ссылка и фраза про подгрузку являются ответами/статусом обработки, а не управленческим решением.

6. Риски / пробелы данных:
- Вложение с примером транскрибации не распознано OCR: в базе нет локального файла и OCR-текста, поэтому содержание изображения не анализировалось.
- По транскрибациям после 11 мая есть только статус «обрабатывается», без срока готовности.

7. Что контролировать на следующем отчете:
- Подгружены ли транскрибации после 11 мая в указанную папку.
- Появился ли OCR/текст вложения photo_2026-05-14 11.22.39.jpeg.
- Закрыты ли хвосты по оргдокументам и юридическому маршруту."""

    raw = {
        "model": MODEL,
        "schema": "chat_daily_minimal_extraction_v1",
        "source": "local_manual_correction_2026_05_14_utf8_no_external_ai",
        "external_ai_used": False,
        "analysis": analysis,
        "raw_input": {
            "dialog_id": CHAT,
            "report_date": "2026-05-14",
            "messages_count": 9,
            "files_count": 1,
            "ocr_files_count": 0,
            "zoom_call_ids": [CALL],
            "raw_zoom_transcript_passed_to_chat_analysis": False,
            "correction_reason": "previous local bulk report misclassified answers as decisions and missed missing OCR",
        },
    }
    app.save_chat_daily_report(CHAT, date(2026, 5, 14), text, MODEL, raw)
    print("saved utf8 corrected report for 2026-05-14")


if __name__ == "__main__":
    main()
