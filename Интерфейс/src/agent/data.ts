import { Chat, Message, AgentConfig, KnowledgeItem } from './types';

export const mockChats: Chat[] = [
  { id: '1', userName: 'Дмитрий Строгонов', userRole: 'Руководитель производства', avatarInitials: 'ДС', avatarColor: 'bg-blue-500', time: '15:05', lastMessage: 'Поставь задачу Евгению: пров...', tag: 'ops', channel: 'Bitrix' },
  { id: '2', userName: 'Наталья Ким', userRole: 'Менеджер', avatarInitials: 'НК', avatarColor: 'bg-amber-500', time: '14:38', lastMessage: 'Что у нас по регламенту возв...', tag: 'faq', channel: 'Bitrix' },
  { id: '3', userName: 'Евгений Петров', userRole: 'Кладовщик', avatarInitials: 'ЕП', avatarColor: 'bg-emerald-500', time: '13:12', lastMessage: 'Собери таблицу по остаткам с...', tag: 'ops', channel: 'Telegram' },
  { id: '4', userName: 'Мария Алексеева', userRole: 'Бухгалтер', avatarInitials: 'МА', avatarColor: 'bg-purple-500', time: '11:03', lastMessage: 'Операция превысила лимит...', tag: 'ошибка', channel: 'Bitrix' },
  { id: '5', userName: 'Артём Гусев', userRole: 'Логист', avatarInitials: 'АГ', avatarColor: 'bg-slate-400', time: 'вчера', lastMessage: 'Кто отвечает за подписание д...', tag: 'faq', channel: 'Telegram' },
  { id: '6', userName: 'Ольга Смирнова', userRole: 'HR', avatarInitials: 'ОС', avatarColor: 'bg-pink-500', time: 'вчера', lastMessage: 'Сделай отчёт по созвону за по...', tag: 'ops', channel: 'Bitrix' },
];

export const mockMessages: Message[] = [
  { id: '1', sender: 'user', text: 'Поставь задачу Евгению: проверить выпуск партии №348 до завтра. Результат — фото упаковки в чат.', time: '15:00' },
  { 
    id: '2', 
    sender: 'agent', 
    card: {
      title: 'Ставлю задачу...',
      lines: [],
      meta: 'статус • изменялся 3 раза'
    }
  },
  {
    id: '3',
    sender: 'agent',
    card: {
      title: 'Задача №863 поставлена',
      status: 'success',
      lines: [
        '— Исполнитель: Евгений Петров',
        '— Срок: 03.07.2026 19:00',
        '— Результат: фото упаковки в чат',
        '— Постановщик: Дмитрий Строгонов'
      ],
      meta: '⏱ 37 сек • 🔧 create_bitrix_task, search_tasks • сессия e4'
    }
  },
  { id: '4', sender: 'user', text: 'Спасибо! И покажи все открытые задачи Евгения', time: '15:05' },
  {
    id: '5',
    sender: 'agent',
    card: {
      title: 'У Евгения 4 открытые задачи:',
      lines: [
        '- №863 Проверить выпуск партии №348 — до 03.07',
        '- №858 Сбор документов за июнь — до 04.07',
        '- №640 Материалы к еженедельной встрече — в работе',
        '- №612 Инвентаризация стеллажей — до 08.07'
      ],
      meta: '⏱ 22 сек • 🔧 search_tasks'
    }
  }
];

export const mockAgents: AgentConfig[] = [
  { id: '1', name: 'Основной агент', type: 'системный • все функции', isActive: true, channels: ['Bitrix'], toolsCount: 26, skillsCount: 6, usersCount: 9, usersInfo: 'ДО ЕП ОС +6', stats: { movesToday: 31, avgSpeed: '38 сек' }, iconBg: 'bg-orange-100 text-orange-500', iconType: 'zap' },
  { id: '2', name: 'FAQ-агент', type: 'системный • только знания', isActive: true, channels: ['Bitrix'], toolsCount: 15, skillsCount: 4, usersCount: 0, usersInfo: 'Все новые сотрудники', stats: { movesToday: 12, avgSpeed: '19 сек' }, iconBg: 'bg-emerald-100 text-emerald-500', iconType: 'book' },
  { id: '3', name: 'Админ', type: 'системный • полный доступ', isActive: true, channels: ['Bitrix', 'Telegram'], toolsCount: 72, skillsCount: 0, usersCount: 1, usersInfo: 'Только владелец', stats: { movesToday: 4, avgSpeed: '51 сек' }, iconBg: 'bg-amber-100 text-amber-500', iconType: 'crown' },
  { id: '4', name: 'Агент склада', type: 'субагент • создан вами', isActive: true, channels: ['Bitrix'], toolsCount: 11, skillsCount: 2, usersCount: 3, usersInfo: 'ЕП МА ИК', stats: { movesToday: 7, avgSpeed: '26 сек' }, iconBg: 'bg-blue-100 text-blue-500', iconType: 'box' },
];

export const mockKnowledge: KnowledgeItem[] = [
  { id: '1', title: 'Постановка задач', description: 'Срок и результат обязательны; проверка «срок в прошлом»; постановщик — собеседник; всё за один...', type: 'Инструкция', updatedAt: 'обновлено сегодня' },
  { id: '2', title: 'Стиль общения', description: 'Кратко и красиво: жирный через [b], списки, 1-2 эмодзи, без воды и вводных, без Markdown — Bitrix его не...', type: 'Инструкция', updatedAt: 'обновлено вчера' },
  { id: '3', title: 'Google-таблицы', description: 'Создание через create_google_sheet, доступ «всем по ссылке» обязателен, веб-приложения только на applet-...', type: 'Скилл', updatedAt: 'обновлено 28.06' },
  { id: '4', title: 'Оргструктура и роли', description: 'Кто за что отвечает, матрица решений, карта процессов; сверяться перед ответами о зонах ответственности...', type: 'Регламент', updatedAt: 'обновлено 25.06' },
  { id: '5', title: 'Анти-поддакивание', description: 'Не соглашаться автоматически: проверять источники, оргструктуру и матрицу решений, всегда показывать...', type: 'Инструкция', updatedAt: 'обновлено 24.06' },
  { id: '6', title: 'Отчёты по созвонам', description: 'Формат отчёта по Zoom: участники из транскрибации (не из метаданных), задачи по матрице решений, контракт...', type: 'Скилл', updatedAt: 'обновлено 20.06' },
  { id: '7', title: 'Правило про доступ', description: 'Если действия нет в уровне доступа — честно сказать и предложить передать запрос Александру...', type: 'Инструкция', updatedAt: 'обновлено 18.06', isLocked: true },
];
