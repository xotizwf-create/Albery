import React, { useState, useRef, useEffect } from "react";
import {
  BANKS_DATA,
  WB_REVENUE_WEEKS,
  WB_DRR_DAILY,
  WB_RETURNS_DAILY,
  INITIAL_REGISTRY,
  type PaymentItem,
} from "./fixtures/demoData";
import { fetchJsonSafe } from "./api/client";
import {
  Bell,
  X,
  Wallet,
  AlertTriangle,
  ArrowRight,
  Building2,
  RefreshCw,
  Upload,
  LayoutDashboard,
  Package,
  Store,
  BookText,
  Users,
  Settings,
  ChevronLeft,
  ChevronRight,
  Clock,
  Landmark,
  CalendarDays,
  ListTodo,
  Calendar,
  ChevronDown,
  TrendingUp,
  ArrowDownRight,
  ArrowUpRight,
  Plus,
  CheckCircle2,
  CircleDashed,
  FileText,
  FileSpreadsheet,
  FileCheck,
  AlertOctagon,
  PackageX,
  Video,
  Search,
  MessageSquareText,
  SlidersHorizontal,
  History,
  Trash2,
  Target,
  Send,
  Pencil,
  Folder,
  FolderOpen,
  Sparkles,
  Download,
  Bot,
  BookOpen,
  Activity,
  MessageSquare,
  BarChart3,
} from "lucide-react";
import { DialogsView } from "./agent/views/DialogsView";
import { AgentsView } from "./agent/views/AgentsView";
import { KnowledgeBaseView } from "./agent/views/KnowledgeBaseView";
import { MonitoringView } from "./agent/views/MonitoringView";
import { UsageView } from "./agent/views/UsageView";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  AreaChart,
  Area,
  ReferenceLine,
  Cell,
} from "recharts";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const chatDayWorkflowStatusClass = (status?: { status?: string; workflow_status?: string; ocr_errors?: number }) =>
  status?.workflow_status === "report_formed" || status?.status === "report_formed"
    ? "bg-blue-50 text-blue-700 border-blue-100"
    : status?.ocr_errors
    ? "bg-red-50 text-red-700 border-red-100"
    : status?.workflow_status === "ready" || status?.status === "ready"
      ? "bg-emerald-50 text-emerald-700 border-emerald-100"
      : "bg-amber-50 text-amber-700 border-amber-100";

const moscowNow = () => new Date(new Date().toLocaleString("en-US", { timeZone: "Europe/Moscow" }));
const moscowTodayIso = () => moscowNow().toISOString().slice(0, 10);
const renderInlineMarkdown = (text: string) => {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={index} className="font-black text-slate-900">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
};

const stripMarkdown = (text: string) =>
  text
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*/g, "")
    .replace(/\s+/g, " ")
    .trim();

const zoomReportPreview = (text: string) => {
  const cleaned = stripMarkdown(text);
  return cleaned.length > 220 ? `${cleaned.slice(0, 220).trim()}...` : cleaned;
};

const normalizeZoomReportText = (text: string) =>
  text
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+/g, " ")
    .replace(/\s+(\|\s*№\s*\|)/gi, "\n$1")
    .replace(/\s+(\|\s*[-:]{3,}[\s|:-]*\|)/g, "\n$1")
    .replace(/\s+\|\s*(?=\d+\s*\|)/g, "\n| ")
    .replace(/\s+(#{1,4}\s+)/g, "\n\n$1")
    .replace(/\s+(\*\*(?:call_id|zoom_uuid|Дата|Участники|Источник|Статус|Владелец|Срок|Контекст|Уровень|Контрольная точка):\*\*)/gi, "\n$1")
    .trim();

const renderZoomReportText = (text: string) => {
  const lines = normalizeZoomReportText(text).split("\n");
  const blocks: React.ReactNode[] = [];
  let paragraph: string[] = [];
  let listItems: string[] = [];
  let tableLines: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push(
      <p key={`p-${blocks.length}`} className="text-[15px] leading-7 text-slate-700">
        {renderInlineMarkdown(paragraph.join(" "))}
      </p>,
    );
    paragraph = [];
  };

  const flushList = () => {
    if (!listItems.length) return;
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="space-y-2 pl-5 text-[15px] leading-7 text-slate-700 list-disc">
        {listItems.map((item, index) => (
          <li key={index}>{renderInlineMarkdown(item)}</li>
        ))}
      </ul>,
    );
    listItems = [];
  };

  const flushTable = () => {
    if (!tableLines.length) return;
    const rows = tableLines
      .filter((line) => !isReportTableSeparatorLine(line))
      .map(splitReportTableRow)
      .filter((row) => row.length >= 2 && row.some(Boolean));
    const headers = rows[0] || [];
    const bodyRows = rows.slice(1);
    const columnCount = headers.length;
    if (columnCount) {
      blocks.push(
        <div key={`table-${blocks.length}`} className="my-5 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm ring-1 ring-slate-100">
          <table className="w-full min-w-[820px] border-separate border-spacing-0 text-left text-[13px] leading-5">
            <thead className="bg-slate-100/90 text-slate-800">
              <tr>
                {headers.map((header, cellIndex) => (
                  <th key={cellIndex} className="min-w-[120px] border-b border-slate-200 px-4 py-3 font-semibold align-top">
                    {renderInlineMarkdown(header)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bodyRows.map((row, rowIndex) => (
                <tr key={rowIndex} className={rowIndex % 2 ? "bg-white" : "bg-slate-50/60"}>
                  {Array.from({ length: columnCount }).map((_, cellIndex) => (
                    <td key={cellIndex} className="min-w-[120px] max-w-[280px] break-words border-b border-slate-100 px-4 py-3 align-top font-normal text-slate-700">
                      {renderInlineMarkdown(row[cellIndex] || "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    }
    tableLines = [];
  };

  lines.forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      flushTable();
      return;
    }

    if (isReportTableLine(line)) {
      flushParagraph();
      flushList();
      tableLines.push(line);
      return;
    }

    flushTable();

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length;
      const textValue = heading[2].replace(/\*\*/g, "");
      blocks.push(
        <h5
          key={`h-${blocks.length}`}
          className={cn(
            "font-black text-slate-950 tracking-tight",
            level === 1 ? "mt-1 text-[24px]" : level === 2 ? "mt-8 border-t border-slate-100 pt-6 text-[19px]" : "mt-5 text-[16px]",
          )}
        >
          {textValue}
        </h5>,
      );
      return;
    }

    const numberedSection = line.match(/^\d+\.\s+(.+)$/);
    const sectionTitles = [
      "Источник",
      "Участники и упомянутые люди",
      "Краткая сводка",
      "Операционные задачи",
      "Решения и статусы",
      "Темы обсуждения",
      "Выполненные факты",
      "Открытые вопросы",
      "Риски и контрольные точки",
      "Управленческая диагностика",
      "Поведенческие факторы",
      "Что контролировать на следующем созвоне",
    ];
    if (numberedSection && sectionTitles.includes(numberedSection[1].replace(/\*\*/g, "").trim())) {
      flushParagraph();
      flushList();
      blocks.push(
        <h5 key={`section-${blocks.length}`} className="mt-8 border-t border-slate-100 pt-6 text-[19px] font-black tracking-tight text-slate-950 first:mt-0 first:border-t-0 first:pt-0">
          {renderInlineMarkdown(line)}
        </h5>,
      );
      return;
    }

    const metadata = line.match(/^\*\*([^*:]{2,80}):\*\*\s*(.*)$/);
    if (metadata) {
      flushParagraph();
      flushList();
      blocks.push(
        <div key={`meta-${blocks.length}`} className="grid gap-1 rounded-xl border border-slate-100 bg-slate-50/70 px-4 py-3 sm:grid-cols-[150px_1fr]">
          <div className="text-[12px] font-black uppercase tracking-wider text-slate-400">{metadata[1]}</div>
          <div className="text-[14px] font-semibold leading-6 text-slate-700">{renderInlineMarkdown(metadata[2] || "—")}</div>
        </div>,
      );
      return;
    }

    const bullet = line.match(/^[-*—]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      listItems.push(bullet[1]);
      return;
    }

    const numbered = line.match(/^\d+\.\s+(.+)$/);
    if (numbered) {
      flushParagraph();
      flushList();
      blocks.push(
        <p key={`num-${blocks.length}`} className="text-[15px] leading-7 text-slate-700">
          {renderInlineMarkdown(line)}
        </p>,
      );
      return;
    }

    paragraph.push(line);
  });

  flushParagraph();
  flushList();
  flushTable();
  return blocks;
};

// Sidebar configuration
const MENU_ITEMS = [
  { label: "Сводная аналитика", icon: LayoutDashboard },
  { label: "Бухгалтерия", icon: Wallet },
  { label: "Склад", icon: Package },
  { label: "WB-кабинет", icon: Store },
  { label: "Зум-разговоры", icon: Video },
  { label: "О компании", icon: Building2 },
  { label: "Справочники", icon: BookText },
  { label: "Команда", icon: Users },
  { label: "Настройки", icon: Settings },
];
const HIDDEN_MAIN_MENU_LABELS = new Set(["Бухгалтерия", "Склад", "Справочники"]);
const VISIBLE_MENU_ITEMS = MENU_ITEMS.filter((item) => !HIDDEN_MAIN_MENU_LABELS.has(item.label));
const AGENT_MENU_ITEMS = [
  { label: "Диалоги", icon: MessageSquare },
  { label: "Агенты", icon: Bot },
  { label: "База знаний", icon: BookOpen },
  { label: "Мониторинг", icon: Activity },
  { label: "Использование", icon: BarChart3 },
];
// Every Центр Агента page has its own URL; the SPA syncs activeTab ↔ pathname
// (agent_center.py serves index.html on these paths for direct opens).
const AGENT_TAB_ROUTES: Record<string, string> = {
  "Агенты": "/agent",
  "Диалоги": "/agent-dialogs",
  "База знаний": "/agent-knowledge",
  "Мониторинг": "/agent-monitoring",
  "Использование": "/agent-usage",
};
const AGENT_ROUTE_TABS: Record<string, string> = Object.fromEntries(
  Object.entries(AGENT_TAB_ROUTES).map(([tab, path]) => [path, tab]),
);
// Match a tab by the URL's BASE, so deep-linked sub-paths (/agent/main,
// /agent-dialogs/main/22) still resolve to their tab; the view owns the suffix.
const agentTabForPath = (pathname: string): string | null => {
  for (const [tab, base] of Object.entries(AGENT_TAB_ROUTES)) {
    if (pathname === base || pathname.startsWith(base + "/")) return tab;
  }
  return null;
};
const isUnderAgentRoute = (pathname: string, base: string): boolean =>
  pathname === base || pathname.startsWith(base + "/");
const COMPANY_MENU_ITEMS = VISIBLE_MENU_ITEMS.filter((item) => item.label !== "Настройки");
const OTHER_MENU_ITEMS = VISIBLE_MENU_ITEMS.filter((item) => item.label === "Настройки");
const SIDEBAR_GROUPS = [
  { title: "Компания", items: COMPANY_MENU_ITEMS, accent: false },
  { title: "Центр Агента", items: AGENT_MENU_ITEMS, accent: true },
  { title: "Прочее", items: OTHER_MENU_ITEMS, accent: false },
];
const HIDDEN_HEADER_LINKS = new Set(["Бухгалтерия", "Склад"]);

const MONTHS = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];
const MONTHS_GENITIVE = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];
const REPORT_PLACEHOLDER_START_ISO = "2026-03-30";
const STARTUP_WEEK_START_ISO = "2026-03-30";
const STARTUP_WEEK_END_ISO = "2026-04-05";
const SHORT_MONTHS = [
  "янв.",
  "фев.",
  "мар.",
  "апр.",
  "мая",
  "июн.",
  "июл.",
  "авг.",
  "сен.",
  "окт.",
  "ноя.",
  "дек.",
];

// Data for Banks widget
type TaskRegistryRow = {
  task_id: number;
  title: string | null;
  status_code: string | null;
  status_label: string | null;
  responsible_id: number | null;
  responsible_name: string | null;
  creator_id: number | null;
  creator_name: string | null;
  created_date_text: string;
  deadline_text: string;
  closed_date_text: string;
  comments_count: number;
  results_count: number;
  history_count: number;
  checklist_count: number;
  last_synced_at_text: string;
};

type TaskRegistryStats = {
  total: number;
  filtered_total: number;
  by_status: Array<{ status: string; count: number }>;
};

type TaskRegistryFilters = {
  q: string;
  status: string;
  responsible_id: string;
  created_from: string;
  created_to: string;
  deadline_from: string;
  deadline_to: string;
  date_from: string;
  date_to: string;
};

type GoalRow = {
  goal_id: string;
  goal_title: string;
  goal_text: string | null;
  goal_level: string;
  period_type: string;
  period_start: string | null;
  period_end: string | null;
  owner_id: number | null;
  owner_name: string | null;
  success_metrics: string | null;
  expected_result: string | null;
  status: string;
  source_type: string | null;
  is_confirmed: boolean;
  confidence: number | string | null;
  created_at: string | null;
  updated_at: string | null;
  updated_at_text: string;
};

type GoalStats = {
  total: number;
  filtered_total: number;
  company: number;
  employee: number;
  active: number;
  manual: number;
  from_chats: number;
};

type GoalFilters = {
  q: string;
  goal_level: string;
  period_type: string;
  status: string;
  source_type: string;
  owner_id: string;
};

type GoalForm = {
  goal_level: string;
  owner_id: string;
  goal_title: string;
  goal_text: string;
  period_type: string;
  period_start: string;
  period_end: string;
  success_metrics: string;
  expected_result: string;
  status: string;
};

type CompanyProfile = {
  title: string;
  content: string;
  updated_at: string | null;
  updated_at_text: string;
};

type CompanyFolder = {
  id: string;
  parent_id: string | null;
  name: string;
  content: string;
  children_count: number;
  updated_at: string | null;
  updated_at_text: string;
  drive_source?: {
    google_file_id: string;
    mime_type: string;
    source_url: string | null;
    google_updated_at: string | null;
    google_updated_at_text?: string;
    blocks: Array<{
      type: string;
      text?: string;
      level?: number;
      title?: string;
      headers?: string[];
      rows?: string[][];
      records?: Array<Record<string, string>>;
    }>;
  };
};

const SHEET_MIME_TYPES = new Set([
  "application/vnd.google-apps.spreadsheet",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
]);

// Pick the tile icon by item type so folders, Google Sheets and Google Docs /
// Word files are visually distinct instead of all looking like folders.
function renderCompanyFolderIcon(folder: CompanyFolder) {
  const driveSource = folder.drive_source;
  if (!driveSource) {
    return (
      <Folder className="w-8 h-8 text-blue-500 group-hover:text-blue-600 mb-3" strokeWidth={2.2} />
    );
  }
  if (SHEET_MIME_TYPES.has(driveSource.mime_type || "")) {
    return <FileSpreadsheet className="w-8 h-8 text-emerald-600 mb-3" strokeWidth={2.2} />;
  }
  // Google Docs, .doc and .docx (and any other synced document).
  return <FileText className="w-8 h-8 text-blue-600 mb-3" strokeWidth={2.2} />;
}

type AiInstructionFolder = {
  id: string;
  parent_id: string | null;
  name: string;
  content: string;
  children_count: number;
  updated_at: string | null;
  updated_at_text: string;
};

type SelectOption = {
  value: string;
  label: string;
};

type ChatRegistryRow = {
  dialog_id: string;
  chat_id: number | null;
  title: string | null;
  type: string | null;
  member_count: number;
  is_excluded: number;
  last_activity_date_text: string;
  last_synced_at_text: string;
  members: Array<{
    user_id: number;
    name: string | null;
    work_position: string | null;
    is_bot: number;
    is_extranet: number;
  }>;
  reports?: Array<{
    report_id?: string;
    report_kind?: "daily" | "weekly";
    date: string;
    date_text: string;
    period_start?: string | null;
    period_end?: string | null;
    period_text?: string | null;
    title?: string | null;
    model: string | null;
    generated_at: string;
    generated_at_text: string;
    messages_count: number;
    version?: number;
    summary?: string | null;
    days_count?: number;
    daily_reports_count?: number;
    goals_created_count?: number;
    goal_updates_count?: number;
    commitments_count?: number;
    results_count?: number;
    next_steps_count?: number;
    risks_count?: number;
    blockers_count?: number;
    unresolved_questions_count?: number;
    text_status?: {
      status: string;
      status_text: string;
      image_files: number;
      ocr_success: number;
      ocr_pending: number;
      ocr_errors: number;
      is_processed: boolean;
      text_ready_status?: string;
      text_ready_status_text?: string;
      workflow_status?: string;
      workflow_status_text?: string;
    };
    report_status?: string;
    report_status_text?: string;
    workflow_status?: string;
    workflow_status_text?: string;
  }>;
  analytics?: Array<{
    date: string;
    date_text: string;
    summary_text: string | null;
    model: string | null;
    tasks_saved: number;
    goals_saved: number;
    facts_saved: number;
    confirmations_count: number;
    unknown_blocks_count: number;
    status: string;
    error: string | null;
    generated_at_text: string;
  }>;
};

type ChatDayPayload = {
  chat: { dialog_id: string; title: string | null } | null;
  date: string;
  date_from?: string;
  date_to?: string;
  period_text?: string;
  messages: Array<{
    message_id: number;
    author_id: number | null;
    author_name: string;
    message_date_text: string;
    text: string;
    files: Array<{
      file_id: number;
      name: string | null;
      extension: string | null;
      type: string | null;
      preview_url: string | null;
      show_url: string | null;
      download_url: string | null;
      is_image: boolean;
      ocr_status?: string | null;
      ocr_text?: string | null;
    }>;
  }>;
  transcript: string;
  text_status?: {
    status: string;
    status_text: string;
    image_files: number;
    ocr_success: number;
    ocr_pending: number;
    ocr_errors: number;
    is_processed: boolean;
    text_ready_status?: string;
    text_ready_status_text?: string;
    workflow_status?: string;
    workflow_status_text?: string;
  };
  report: {
    report_id?: string;
    id?: string;
    report_text: string;
    model: string | null;
    generated_at: string;
  } | null;
  weekly_report?: {
    report_id?: string;
    period_start?: string;
    period_end?: string;
    period_text: string;
    days_count: number;
    daily_reports_count: number;
    messages_count: number;
    goals_created_count: number;
    goal_updates_count: number;
    commitments_count: number;
    results_count: number;
    next_steps_count: number;
    risks_count: number;
    blockers_count: number;
    unresolved_questions_count: number;
    done_goal_updates_count: number;
    high_risk_goal_updates_count: number;
    summary: string | null;
    dynamics_summary: string | null;
    positives_summary: string | null;
    problems_summary: string | null;
    recommendations: string | null;
  };
  synced_count?: number;
  synced_by_date?: Record<string, number>;
};

type ZoomParticipant = {
  name: string | null;
  email: string | null;
};

type ZoomTranscriptSegment = {
  segment_index: number;
  cue_index: number;
  start_offset: string | null;
  end_offset: string | null;
  speaker: string | null;
  text: string;
};

type ZoomCall = {
  id: string;
  date: string;
  date_text: string;
  start_time_msk: string;
  end_time_msk: string | null;
  time_text: string;
  topic: string;
  technical_topic: string;
  participants: ZoomParticipant[];
  analytical_note: string;
  duration_min: number | null;
  synced_at?: string | null;
  synced_at_text?: string;
  transcript_text?: string;
  segments?: ZoomTranscriptSegment[];
};

type ZoomCallsTree = {
  years: Array<{
    year: number;
    months: Array<{
      month: number;
      title: string;
      dates: Array<{
        date: string;
        date_text: string;
        calls: ZoomCall[];
      }>;
    }>;
  }>;
  total: number;
  updated_at?: string | null;
  updated_at_text?: string;
};

type ZoomOperationalTasksPreview = {
  recipients: Array<{
    name: string;
    user_id: number;
  }>;
  task_cards?: Array<{
    recipient: {
      name: string;
      user_id: number;
    } | null;
    assignee_name: string;
    title: string;
    description: string;
    deadline: string | null;
    deadline_text: string;
    tasks?: Array<Record<string, unknown>>;
    card_kind?: "operational" | "participant_report";
  }>;
  unmatched_assignees?: string[];
  participant_reports_error?: string;
  title: string;
  description: string;
  deadline: string | null;
  deadline_text: string;
  operational_section: string;
  dispatch_summary?: string;
  unmatched_participants?: string[];
};

const ZOOM_OPERATIONAL_TASKS_DISPATCH_INTRO =
  "Ознакомьтесь со списком выделенных из созвона задач и поставьте себе самые важные в Битрикс. В комментариях к задаче укажите, что из предложенного сформировано ошибочно, а что вы взяли в работу";

type ChatOverallDailyReport = {
  report_id: string;
  report_date: string;
  date_text: string;
  version: number;
  generated_at_text: string;
  chats_count: number;
  messages_count: number;
  goals_created_count: number;
  goal_updates_count: number;
  commitments_count: number;
  results_count: number;
  next_steps_count: number;
  risks_count: number;
  blockers_count: number;
  unresolved_questions_count: number;
  done_goal_updates_count: number;
  high_risk_goal_updates_count: number;
  summary: string | null;
  dynamics_summary: string | null;
  positives_summary: string | null;
  problems_summary: string | null;
  report_text: string;
  raw_json?: {
    dynamics?: Record<string, { current: number; previous: number; delta: number }>;
    work_in_progress?: Array<{ text: string }>;
    not_done?: Array<{ text: string }>;
    positives?: Array<{ text: string }>;
    problems?: Array<{ text: string }>;
    goal_updates?: Array<{
      goal_title: string;
      owner_name: string | null;
      status_before: string;
      status_after: string;
      progress_percent: number | null;
      risk_level: string | null;
      progress_text: string;
    }>;
  };
};

type ChatOverallWeeklyReport = {
  report_id: string;
  period_start: string;
  period_end: string;
  period_text: string;
  version: number;
  is_current: boolean;
  ai_request_id: string | null;
  generated_at: string | null;
  generated_at_text: string;
  days_count: number;
  chats_count: number;
  daily_reports_count: number;
  messages_count: number;
  goals_created_count: number;
  goal_updates_count: number;
  commitments_count: number;
  results_count: number;
  next_steps_count: number;
  risks_count: number;
  blockers_count: number;
  unresolved_questions_count: number;
  done_goal_updates_count: number;
  high_risk_goal_updates_count: number;
  summary: string | null;
  dynamics_summary: string | null;
  positives_summary: string | null;
  problems_summary: string | null;
  recommendations: string | null;
  report_text: string;
};

type ChatOverallTimelineItem =
  | (ChatOverallDailyReport & { report_kind: "daily"; anchor_date: string })
  | (ChatOverallWeeklyReport & { report_kind: "weekly"; anchor_date: string });

type OwnerDailyReport = {
  report_id: string;
  report_date: string;
  date_text: string;
  version: number;
  generated_at_text: string;
  summary: string | null;
  dynamics_summary: string | null;
  risks_summary: string | null;
  recommendations: string | null;
  report_text: string;
};

type OwnerWeeklyReport = {
  report_id: string;
  period_start: string;
  period_end: string;
  period_text: string;
  version: number;
  generated_at_text: string;
  summary: string | null;
  dynamics_summary: string | null;
  risks_summary: string | null;
  recommendations: string | null;
  report_text: string;
};

type TeamMemberRow = {
  user_id: number;
  name: string | null;
  email: string | null;
  work_position: string | null;
  active: number;
  avatar_url: string | null;
  manager_id: number | null;
  manager_name: string | null;
  departments_text: string | null;
  last_synced_at_text: string;
};

type OwnerReportFeedItem = {
  id: string;
  kind: "daily" | "weekly" | "monthly";
  title: string;
  subtitle: string;
  status: string;
  reportText: string;
  reportDate?: string;
  summary?: string | null;
  dynamics?: string | null;
  risks?: string | null;
  recommendations?: string | null;
};

type OwnerReportRecommendationMap = Record<number, string>;

type EditableReport = {
  reportType: "chat_daily" | "chat_weekly" | "chat_overall_daily" | "chat_overall_weekly" | "owner_daily" | "owner_weekly";
  reportId: string;
  title: string;
  context?: "chat_day" | "chat_overall_history" | "owner";
  fields: {
    summary?: string;
    dynamics_summary?: string;
    positives_summary?: string;
    problems_summary?: string;
    risks_summary?: string;
    recommendations?: string;
    report_text?: string;
  };
};

const GOAL_LEVEL_LABELS: Record<string, string> = {
  company: "Компания",
  department: "Отдел",
  manager: "Руководитель",
  employee: "Сотрудник",
  project: "Проект",
};

const GOAL_PERIOD_LABELS: Record<string, string> = {
  day: "День",
  week: "Неделя",
  month: "Месяц",
  quarter: "Квартал",
  year: "Год",
  project: "Проект",
};

const GOAL_STATUS_LABELS: Record<string, string> = {
  draft: "Черновик",
  active: "Активна",
  done: "Выполнена",
  cancelled: "Отменена",
  archived: "Архив",
};

const GOAL_SOURCE_LABELS: Record<string, string> = {
  manual: "Вручную",
  chat: "Из чата",
  ocr: "Из OCR",
  ai: "ИИ",
  bitrix: "Bitrix",
};

const toSelectOptions = (items: Record<string, string>, emptyLabel?: string): SelectOption[] => [
  ...(emptyLabel ? [{ value: "", label: emptyLabel }] : []),
  ...Object.entries(items).map(([value, label]) => ({ value, label })),
];

const CHAT_REGISTRY_CACHE_KEY = "alberi.chatRegistry.rows.v2";

const weeklyValueLines = (value: unknown, emptyText: string): string[] => {
  if (Array.isArray(value)) {
    const lines = value
      .map((item) => {
        if (item && typeof item === "object") {
          const record = item as Record<string, unknown>;
          return String(record.summary || record.text || record.title || record.reason || JSON.stringify(record));
        }
        return String(item || "");
      })
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => `- ${item}`);
    return lines.length ? lines : [emptyText];
  }
  const text = String(value || "").trim();
  return text ? text.split(/\r?\n/) : [emptyText];
};

const parseWeeklyJsonText = (value: unknown): Record<string, unknown> | null => {
  const text = String(value || "").trim();
  if (!text.startsWith("{")) return null;
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const record = parsed as Record<string, unknown>;
    const analysis = record.analysis;
    return analysis && typeof analysis === "object" && !Array.isArray(analysis)
      ? (analysis as Record<string, unknown>)
      : record;
  } catch {
    return null;
  }
};

const buildReadableWeeklyReportText = (weeklyReport: Record<string, unknown>, fallbackPeriodText = "") => {
  const parsedReportText = parseWeeklyJsonText(weeklyReport.report_text);
  const rawJson = weeklyReport.raw_json && typeof weeklyReport.raw_json === "object" && !Array.isArray(weeklyReport.raw_json)
    ? (weeklyReport.raw_json as Record<string, unknown>)
    : {};
  const rawAnalysis = rawJson.analysis && typeof rawJson.analysis === "object" && !Array.isArray(rawJson.analysis)
    ? (rawJson.analysis as Record<string, unknown>)
    : {};
  const analysis: Record<string, unknown> = {
    ...rawAnalysis,
    ...(parsedReportText || {}),
  };
  ["summary", "dynamics_summary", "positives_summary", "problems_summary", "recommendations"].forEach((key) => {
    if (!String(analysis[key] || "").trim()) analysis[key] = weeklyReport[key];
  });
  const actionPlan = analysis.next_week_action_plan || analysis.hanging_tasks_by_owner || analysis.recommendations;
  return [
    "Еженедельная сводка по чату",
    `Период: ${weeklyReport.period_text || fallbackPeriodText || ""}`,
    "",
    "Что делать на следующей неделе:",
    ...weeklyValueLines(actionPlan, "Нет конкретных действий для контроля."),
    "",
    "Короткий итог:",
    String(analysis.summary || "Нет данных.").trim(),
    "",
    "Динамика по дням:",
    String(analysis.dynamics_summary || "Нет данных по динамике.").trim(),
    "",
    "Открытые задачи по ответственным:",
    ...weeklyValueLines(analysis.hanging_tasks_by_owner, "Нет явных зависших задач по ответственным."),
    "",
    "Что не сделано / не подтверждено:",
    ...weeklyValueLines(analysis.not_done, "Нет данных."),
    "",
    "Цели, которые требуют контроля:",
    ...weeklyValueLines(analysis.hanging_goals, "Нет явных зависших целей."),
    "",
    "Что находится в работе:",
    ...weeklyValueLines(analysis.work_in_progress, "Нет данных."),
    "",
    "Что хорошо:",
    ...weeklyValueLines(analysis.positives_summary, "Нет данных."),
    "",
    "Что плохо:",
    ...weeklyValueLines(analysis.problems_summary, "Нет данных."),
    "",
    "Дополнительные рекомендации:",
    ...weeklyValueLines(analysis.recommendations, "Нет рекомендаций."),
  ].join("\n").trim();
};

const normalizeWeeklyReportText = (weeklyReport: Record<string, unknown>, fallbackPeriodText = "") => {
  const text = String(weeklyReport.report_text || "").trim();
  if (text && !parseWeeklyJsonText(text)) return text;
  return buildReadableWeeklyReportText(weeklyReport, fallbackPeriodText);
};

const splitReportTableRow = (line: string): string[] =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());

const isReportTableSeparatorLine = (line: string) => {
  const cells = splitReportTableRow(line.trim());
  return cells.length >= 2 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s+/g, "")));
};

const isReportTableLine = (line: string) => {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return false;
  return splitReportTableRow(trimmed).length >= 2;
};

const isReportHeadingLine = (line: string) => {
  const trimmed = line.trim();
  if (/^\d{1,2}\.\s+\S/.test(trimmed)) {
    return trimmed.length <= 90 && !/[?]/.test(trimmed);
  }
  return /^[А-ЯA-ZЁ][^.!?]{2,90}:?$/.test(trimmed);
};

const cleanReportTextLine = (line: string) =>
  line
    .replace(/\*\*/g, "")
    .replace(/^#{1,6}\s*/, "")
    .trim();

const renderReportInlineText = (text: string) => {
  const cleaned = cleanReportTextLine(text);
  const strongMatch = cleaned.match(/^([^:]{2,80}):\s+(.+)$/);
  if (!strongMatch) return cleaned;
  return (
    <>
      <span className="font-medium text-slate-900">{strongMatch[1]}:</span> {strongMatch[2]}
    </>
  );
};

const normalizeRecommendationName = (value: string) =>
  String(value || "")
    .toLocaleLowerCase("ru-RU")
    .replace(/ё/g, "е")
    .replace(/[^a-zа-я0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

const recommendationNameVariants = (name: string | null) => {
  const normalized = normalizeRecommendationName(name || "");
  if (!normalized) return [];
  const parts = normalized.split(" ").filter(Boolean);
  const variants = new Set<string>([normalized]);
  if (parts.length >= 2) {
    variants.add(`${parts[0]} ${parts[1]}`);
    variants.add(`${parts[1]} ${parts[0]}`);
  }
  if (parts.length >= 3) {
    variants.add(`${parts[0]} ${parts[2]}`);
    variants.add(`${parts[1]} ${parts[2]}`);
  }
  return Array.from(variants).filter((item) => item.length >= 5);
};

const cleanRecommendationLine = (line: string) =>
  cleanReportTextLine(line)
    .replace(/^\|+|\|+$/g, "")
    .replace(/\s+/g, " ")
    .trim();

const normalizeRecommendationMessageText = (text: string) => {
  const lines = String(text || "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => cleanRecommendationLine(line))
    .filter((line) => line && !/^текст рекомендаций$/i.test(line));
  const result: string[] = [];
  lines.forEach((line, index) => {
    if (index > 0 && /^\d{1,2}[.)]\s+/.test(line) && result.length && result[result.length - 1] !== "") {
      result.push("");
    }
    result.push(line);
  });
  return result.join("\n").trim();
};

const isOwnerAddressedRecommendationHeading = (line: string) => {
  const cleaned = cleanReportTextLine(line);
  return /^\d+\.\s*Адресные\s+(вопросы|рекомендации)/i.test(cleaned);
};

const isOwnerRecommendationSectionBoundary = (line: string) => {
  const cleaned = cleanReportTextLine(line);
  return /^\d+\.\s*(Что\s+проверить|Рекомендации\s+собственнику|Ключевые\s+действия|Цели\s+и\s+движение|Полный\s+текст|Краткое\s+резюме)/i.test(cleaned);
};

const extractOwnerBitrixRecommendationSection = (text: string) => {
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  const startIndex = lines.findIndex((line) => isOwnerAddressedRecommendationHeading(line));
  if (startIndex < 0) return "";
  const sectionLines: string[] = [];
  for (let index = startIndex + 1; index < lines.length; index += 1) {
    const line = cleanReportTextLine(lines[index]);
    if (isOwnerRecommendationSectionBoundary(line)) break;
    sectionLines.push(lines[index]);
  }
  return sectionLines.join("\n").trim();
};

const extractOwnerRecommendationsByUser = (
  report: OwnerReportFeedItem,
  members: TeamMemberRow[],
): OwnerReportRecommendationMap => {
  const activeMembers = members.filter((member) => member.active !== 0 && member.user_id && member.name);
  const memberVariants = activeMembers.map((member) => ({
    userId: member.user_id,
    variants: recommendationNameVariants(member.name),
  }));
  const result: OwnerReportRecommendationMap = {};
  const source = extractOwnerBitrixRecommendationSection(report.reportText || "");
  if (!source) return result;
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  let currentUserIds: number[] = [];

  const findLineOwners = (line: string) => {
    const normalizedLine = normalizeRecommendationName(line);
    if (!normalizedLine) return [];
    const paddedLine = ` ${normalizedLine} `;
    const matched: Array<{ userId: number; variant: string }> = [];
    for (const item of memberVariants) {
      const matchedVariant = item.variants.find((variant) => normalizedLine === variant || paddedLine.includes(` ${variant} `));
      if (matchedVariant) {
        matched.push({ userId: item.userId, variant: matchedVariant });
      }
    }
    return matched;
  };

  const appendRecommendation = (userId: number, text: string) => {
    const cleaned = cleanRecommendationLine(text);
    if (!cleaned || isReportTableSeparatorLine(cleaned)) return;
    if (/^(текст рекомендаций|рекомендации|полный текст отчета|главный вывод|динамика|риски|темы дня)$/i.test(cleaned)) return;
    result[userId] = normalizeRecommendationMessageText([result[userId], cleaned].filter(Boolean).join("\n"));
  };

  lines.forEach((rawLine) => {
    const line = cleanRecommendationLine(rawLine);
    if (!line) return;
    const owners = !/^[-•]?\s*\d{1,2}[.)]\s+/.test(line) ? findLineOwners(line) : [];
    if (owners.length) {
      currentUserIds = Array.from(new Set(owners.map((owner) => owner.userId)));
      return;
    }
    if (currentUserIds.length) {
      currentUserIds.forEach((userId) => appendRecommendation(userId, line));
    }
  });

  return result;
};

const ReportTextWithTables = ({ text }: { text: string }) => {
  const source = String(text || "").trim();
  if (!source) return <span>Текст отчета отсутствует.</span>;

  const blocks: Array<{ type: "text"; lines: string[] } | { type: "table"; lines: string[] }> = [];
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  let index = 0;

  while (index < lines.length) {
    if (isReportTableLine(lines[index])) {
      const tableLines: string[] = [];
      while (index < lines.length && isReportTableLine(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      blocks.push({ type: "table", lines: tableLines });
      continue;
    }

    const textLines: string[] = [];
    while (index < lines.length && !isReportTableLine(lines[index])) {
      textLines.push(lines[index]);
      index += 1;
    }
    blocks.push({ type: "text", lines: textLines });
  }

  return (
    <div className="owner-report-rich-text">
      {blocks.map((block, blockIndex) => {
        if (block.type === "table") {
          const rows = block.lines
            .filter((line) => !isReportTableSeparatorLine(line))
            .map(splitReportTableRow)
            .filter((row) => row.length >= 2 && row.some(Boolean));
          const headers = rows[0] || [];
          const bodyRows = rows.slice(1);
          const columnCount = headers.length;
          if (!columnCount) return null;
          return (
            <div key={`table-${blockIndex}`} className="my-5 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm ring-1 ring-slate-100">
              <table className="w-full min-w-[820px] border-separate border-spacing-0 text-left text-[13px] leading-5">
                <thead className="bg-slate-100/90 text-slate-800">
                  <tr>
                    {headers.map((header, cellIndex) => (
                      <th key={cellIndex} className="min-w-[140px] border-b border-slate-200 px-4 py-3 font-black align-top">
                        {header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {bodyRows.map((row, rowIndex) => (
                    <tr key={rowIndex} className={rowIndex % 2 ? "bg-white" : "bg-slate-50/60"}>
                      {Array.from({ length: columnCount }).map((_, cellIndex) => (
                        <td key={cellIndex} className="min-w-[140px] max-w-[260px] break-words border-b border-slate-100 px-4 py-3 align-top font-normal text-slate-700">
                          {row[cellIndex] || ""}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }

        const content = block.lines.join("\n").trim();
        if (!content) return <div key={`space-${blockIndex}`} className="h-2" />;
        const lines = content.split("\n").map((line) => line.trim()).filter(Boolean);
        return (
          <div key={`text-${blockIndex}`} className="space-y-2">
            {lines.map((line, lineIndex) => {
              const cleaned = cleanReportTextLine(line);
              if (!cleaned) return null;
              if (isReportHeadingLine(cleaned)) {
                return (
                  <h4 key={lineIndex} className="pt-3 text-[15px] font-semibold text-slate-900">
                    {cleaned.replace(/:$/, "")}
                  </h4>
                );
              }
              if (/^[-•]\s+/.test(cleaned)) {
                return (
                  <div key={lineIndex} className="flex gap-2 text-[14px] font-normal leading-7 text-slate-700">
                    <span className="mt-2 h-1.5 w-1.5 rounded-full bg-[#5440F6] shrink-0"></span>
                    <span>{renderReportInlineText(cleaned.replace(/^[-•]\s+/, ""))}</span>
                  </div>
                );
              }
              return (
                <p key={lineIndex} className="text-[14px] font-normal leading-7 text-slate-700">
                  {renderReportInlineText(cleaned)}
                </p>
              );
            })}
          </div>
        );
      })}
    </div>
  );
};

const loadCachedChatRows = (): ChatRegistryRow[] => {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(CHAT_REGISTRY_CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const dateToIso = (value: Date) => {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const defaultTaskRegistryPeriod = () => {
  const today = new Date();
  const from = new Date(today);
  from.setDate(today.getDate() - 7);
  return {
    date_from: dateToIso(from),
    date_to: dateToIso(today),
  };
};

const isoToDate = (value: string) => {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
};

const formatControlDate = (value: string) => {
  const dateValue = isoToDate(value);
  if (!dateValue) return "Выберите дату";
  return `${String(dateValue.getDate()).padStart(2, "0")}.${String(dateValue.getMonth() + 1).padStart(2, "0")}.${dateValue.getFullYear()}`;
};

function CustomSelect({
  value,
  options,
  onChange,
  className,
  placeholder = "Выберите",
}: {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  className?: string;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const selected = options.find((option) => option.value === value);

  useEffect(() => {
    const handleOutside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, []);

  return (
    <div ref={ref} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex h-11 w-full items-center justify-between gap-2 rounded-xl border border-[#Eef0f4] bg-white px-3.5 text-left text-[13px] font-bold text-slate-700 shadow-sm outline-none transition-all hover:border-[#CBD5E1] focus:border-[#5440F6]/40 focus:ring-2 focus:ring-[#5440F6]/10"
      >
        <span className={cn("truncate", !selected && "text-slate-400")}>{selected?.label || placeholder}</span>
        <ChevronDown className={cn("h-4 w-4 shrink-0 text-slate-400 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full z-50 mt-2 max-h-64 overflow-auto rounded-xl border border-[#Eef0f4] bg-white p-1.5 shadow-xl shadow-slate-900/10">
          {options.map((option) => (
            <button
              key={`${option.value}-${option.label}`}
              type="button"
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
              className={cn(
                "flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left text-[13px] font-bold transition-colors",
                option.value === value
                  ? "bg-[#EEF2FF] text-[#5440F6]"
                  : "text-slate-600 hover:bg-[#F8FAFC] hover:text-slate-950",
              )}
            >
              <span className="truncate">{option.label}</span>
              {option.value === value && <CheckCircle2 className="h-4 w-4 shrink-0" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function CustomDateInput({
  value,
  onChange,
  className,
  max,
}: {
  value: string;
  onChange: (value: string) => void;
  className?: string;
  max?: string;
}) {
  const initialDate = isoToDate(value) || new Date();
  const [open, setOpen] = useState(false);
  const [viewDate, setViewDate] = useState(new Date(initialDate.getFullYear(), initialDate.getMonth(), 1));
  const ref = useRef<HTMLDivElement>(null);
  const selectedDate = isoToDate(value);
  const maxDate = max ? isoToDate(max) : null;
  const firstWeekday = (new Date(viewDate.getFullYear(), viewDate.getMonth(), 1).getDay() + 6) % 7;
  const daysInMonth = new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 0).getDate();
  const cells = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) => index + 1),
  ];

  useEffect(() => {
    const handleOutside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, []);

  return (
    <div ref={ref} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex h-11 w-full items-center justify-between gap-2 rounded-xl border border-[#Eef0f4] bg-white px-3.5 text-left text-[13px] font-bold text-slate-700 shadow-sm outline-none transition-all hover:border-[#CBD5E1] focus:border-[#5440F6]/40 focus:ring-2 focus:ring-[#5440F6]/10"
      >
        <span>{formatControlDate(value)}</span>
        <CalendarDays className="h-4 w-4 shrink-0 text-[#5440F6]" />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-2 w-80 rounded-2xl border border-[#Eef0f4] bg-white p-4 shadow-xl shadow-slate-900/10">
          <div className="mb-4 flex items-center justify-between">
            <button
              type="button"
              onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1))}
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#Eef0f4] text-slate-500 hover:bg-[#F8FAFC]"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <div className="text-[14px] font-black text-slate-950">
              {MONTHS[viewDate.getMonth()]} {viewDate.getFullYear()}
            </div>
            <button
              type="button"
              onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1))}
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#Eef0f4] text-slate-500 hover:bg-[#F8FAFC]"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
          <div className="mb-2 grid grid-cols-7 gap-1 text-center text-[11px] font-black uppercase text-slate-400">
            {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((day) => <div key={day}>{day}</div>)}
          </div>
          <div className="grid grid-cols-7 gap-1">
            {cells.map((day, index) => {
              if (!day) return <div key={`empty-${index}`} className="h-9" />;
              const cellDate = new Date(viewDate.getFullYear(), viewDate.getMonth(), day);
              const cellIso = dateToIso(cellDate);
              const disabled = Boolean(maxDate && cellDate > maxDate);
              const isSelected = Boolean(selectedDate && dateToIso(selectedDate) === cellIso);
              return (
                <button
                  key={cellIso}
                  type="button"
                  disabled={disabled}
                  onClick={() => {
                    onChange(cellIso);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex h-9 items-center justify-center rounded-xl text-[13px] font-bold transition-colors",
                    isSelected
                      ? "bg-[#5440F6] text-white shadow-md shadow-[#5440F6]/20"
                      : disabled
                        ? "cursor-not-allowed text-slate-300"
                        : "text-slate-700 hover:bg-[#EEF2FF] hover:text-[#5440F6]",
                  )}
                >
                  {day}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function CustomDateRangeInput({
  dateFrom,
  dateTo,
  onChange,
  className,
  max,
}: {
  dateFrom: string;
  dateTo: string;
  onChange: (dateFrom: string, dateTo: string) => void;
  className?: string;
  max?: string;
}) {
  const initialDate = isoToDate(dateTo || dateFrom) || new Date();
  const [open, setOpen] = useState(false);
  const [viewDate, setViewDate] = useState(new Date(initialDate.getFullYear(), initialDate.getMonth(), 1));
  const [draftFrom, setDraftFrom] = useState(dateFrom);
  const [draftTo, setDraftTo] = useState(dateTo);
  const ref = useRef<HTMLDivElement>(null);
  const maxDate = max ? isoToDate(max) : null;
  const firstWeekday = (new Date(viewDate.getFullYear(), viewDate.getMonth(), 1).getDay() + 6) % 7;
  const daysInMonth = new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 0).getDate();
  const cells = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) => index + 1),
  ];

  useEffect(() => {
    if (!open) {
      setDraftFrom(dateFrom);
      setDraftTo(dateTo);
    }
  }, [dateFrom, dateTo, open]);

  useEffect(() => {
    const handleOutside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, []);

  const selectDate = (cellIso: string) => {
    if (!draftFrom || (draftFrom && draftTo) || cellIso < draftFrom) {
      setDraftFrom(cellIso);
      setDraftTo("");
      onChange(cellIso, cellIso);
      return;
    }
    setDraftTo(cellIso);
    onChange(draftFrom, cellIso);
    setOpen(false);
  };

  const label = dateFrom === dateTo
    ? formatControlDate(dateFrom)
    : `${formatControlDate(dateFrom)} - ${formatControlDate(dateTo)}`;

  return (
    <div ref={ref} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex h-11 w-full items-center justify-between gap-2 rounded-xl border border-[#Eef0f4] bg-white px-3.5 text-left text-[13px] font-bold text-slate-700 shadow-sm outline-none transition-all hover:border-[#CBD5E1] focus:border-[#5440F6]/40 focus:ring-2 focus:ring-[#5440F6]/10"
      >
        <span className="truncate">{label}</span>
        <CalendarDays className="h-4 w-4 shrink-0 text-[#5440F6]" />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-2 w-80 rounded-2xl border border-[#Eef0f4] bg-white p-4 shadow-xl shadow-slate-900/10">
          <div className="mb-4 flex items-center justify-between">
            <button
              type="button"
              onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1))}
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#Eef0f4] text-slate-500 hover:bg-[#F8FAFC]"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <div className="text-[14px] font-black text-slate-950">
              {MONTHS[viewDate.getMonth()]} {viewDate.getFullYear()}
            </div>
            <button
              type="button"
              onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1))}
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#Eef0f4] text-slate-500 hover:bg-[#F8FAFC]"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
          <div className="mb-2 grid grid-cols-7 gap-1 text-center text-[11px] font-black uppercase text-slate-400">
            {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((day) => <div key={day}>{day}</div>)}
          </div>
          <div className="grid grid-cols-7 gap-1">
            {cells.map((day, index) => {
              if (!day) return <div key={`range-empty-${index}`} className="h-9" />;
              const cellDate = new Date(viewDate.getFullYear(), viewDate.getMonth(), day);
              const cellIso = dateToIso(cellDate);
              const disabled = Boolean(maxDate && cellDate > maxDate);
              const isStart = draftFrom === cellIso;
              const isEnd = draftTo === cellIso;
              const isInRange = Boolean(draftFrom && draftTo && cellIso > draftFrom && cellIso < draftTo);
              return (
                <button
                  key={cellIso}
                  type="button"
                  disabled={disabled}
                  onClick={() => selectDate(cellIso)}
                  className={cn(
                    "flex h-9 items-center justify-center rounded-xl text-[13px] font-bold transition-colors",
                    isStart || isEnd
                      ? "bg-[#5440F6] text-white shadow-md shadow-[#5440F6]/20"
                      : isInRange
                        ? "bg-[#EEF2FF] text-[#5440F6]"
                        : disabled
                          ? "cursor-not-allowed text-slate-300"
                          : "text-slate-700 hover:bg-[#EEF2FF] hover:text-[#5440F6]",
                  )}
                >
                  {day}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

const PROMPT_TEMPLATES = {
  daily: {
    label: "Ежедневный отчет по всем чатам",
    text: `Ты формируешь ежедневный управленческий отчет по всем чатам компании.

Обязательно:
- сравни с предыдущим днем;
- выдели, что улучшилось и что ухудшилось;
- отдельно вынеси критические точки: зависшие задачи, риски без реакции, блокеры;
- если вчера была незакрытая тема и сегодня нет сдвига, пометь это как срочный контроль.

Пиши коротко, управленчески, только по фактам из входного JSON.`,
  },
  weekly: {
    label: "Еженедельный отчет по всем чатам",
    text: `Ты делаешь еженедельную управленческую сводку по корпоративным чатам.

Ключевая логика:
- используй previous_week_report как базу сравнения;
- используй только 7 дневных сводок текущей недели;
- покажи: что тянется без движения, что сдвинулось, что закрыли.

На входе JSON: дневные отчеты чатов, дневные сводки, результаты, задачи, риски, следующие шаги и динамика целей.

Обязательно отрази:
- что делать на следующей неделе первым блоком;
- динамику недели;
- кто хорошо поработал;
- кто не очень поработал;
- кто реагировал на замечания;
- кто не реагировал;
- итоговые цели недели;
- закрытые маленькие цели;
- цели, которые висят;
- задачи, которые висят по итогу недели и на ком.

Жесткое правило конкретики:
- каждый пункт должен содержать дату, конкретного ответственного, конкретную договоренность/действие, срок или "срок не указан", статус и источник из входного JSON;
- рекомендации должны быть персональными и проверяемыми: кому, что сделать, по какой задаче/цели, к какому сроку, какой результат проверить;
- верни next_week_action_plan отдельным массивом: "владелец: действие; объект; срок; критерий проверки; источник/дата";
- нельзя писать общие формулировки без факта.
- отчет должен быть написан человеческим управленческим языком, не технической выгрузкой;
- в рекомендациях нельзя писать "хвост" или "назначить следующий шаг" без объяснения контекста.

Не выдумывай факты. Возвращай строго JSON по схеме backend weekly_report.`,
  },
  monthly: {
    label: "Ежемесячный отчет",
    text: `Ты формируешь ежемесячный управленческий отчет.

Покажи:
- общую динамику месяца;
- главные результаты;
- системные риски;
- открытые задачи и цели;
- управленческие действия на следующий месяц.

Не выдумывай факты. Возвращай строго JSON по схеме backend monthly_report.`,
  },
  chat_weekly: {
    label: "Еженедельный отчет по чату",
    text: `Ты формируешь еженедельный отчет по одному чату.

Обязательно:
- опирайся на previous_week_report и 7 дневных сводок текущей недели;
- фиксируй динамику по незакрытым задачам и рискам;
- отдельно отмечай: прошлонедельная проблема ушла / не ушла;
- первым блоком верни next_week_action_plan;
- рекомендации только адресные: кто, что, срок, ожидаемый результат.

Верни строго JSON по схеме backend chat_weekly_report.`,
  },
  yearly: {
    label: "Еженедельный отчет по всей программе",
    text: `Ты формируешь еженедельный отчет верхнего уровня по всей программе.

Покажи:
- общую динамику недели;
- главные достижения;
- системные риски и блокеры;
- 3-7 ключевых управленческих действий на следующую неделю.

Если есть previous_week_report, сравни текущую неделю с прошлой по ключевым проблемам и сдвигам.`,
  },
  owner_daily: {
    label: "Ежедневный общий отчет для собственника",
    text: `Ты формируешь ежедневный отчет по всей программе для СОБСТВЕННИКА.
Главная цель отчета: быстро показать, что требует управленческого внимания сегодня, и подготовить адресные вопросы/рекомендации руководителям для отправки в Bitrix.

Это не пересказ чатов и не каталог задач. Это рабочий контрольный лист собственника: что движется, что зависло, у кого спросить статус, кому дать рекомендацию, где нужна дата/владелец/решение.

ВХОДНЫЕ ДАННЫЕ (JSON):
- report_date - дата отчета.
- previous_owner_daily - отчет собственника за предыдущий день или null. Используй его для контроля повторяющихся хвостов.
- chat_overall_daily - сводка по всем чатам за день.
- chat_daily_reports - дневные отчеты по чатам: найденные задачи, вопросы, решения, риски, результаты и хвосты.
- zoom_calls_day - Zoom-созвоны за день с аналитическими заметками; это источник устных задач, решений и договоренностей.
- bitrix_registry - задачи Bitrix24: созданные сегодня, активные с дедлайнами в ближайшие 14 дней, просроченные и незакрытые.
- team - оргструктура: сотрудники, должности, отделы, руководители и bitrix_user_id.
- goal_progress_events - изменения целей за день.

ОБЯЗАТЕЛЬНО:
1. Сравни сегодня с предыдущим owner-днем: что улучшилось, что ухудшилось, что повторяется.
2. По каждому факту указывай имя ответственного, название задачи/темы, срок, статус и источник.
3. Bitrix-id без названия задачи недостаточен: пиши не "задача 318087", а "задача 318087 — внести планируемые платежи на две недели в платежный календарь".
4. Сформируй manager_recommendations и manager_messages по каждому адресату, которому есть что отправить.
5. message_text должен быть готовым текстом для отправки конкретному человеку в Bitrix. Без общего отчета, без вступления, только адресная рекомендация/вопрос.
6. Не выдумывай. Если по сотруднику нет фактов - не упоминай его.
7. Если информации недостаточно, формулируй наводящий вопрос: что именно нужно уточнить, у кого и к какому сроку.
8. report_text строго по разделам:
ЕЖЕДНЕВНЫЙ ОТЧЕТ СОБСТВЕННИКУ
Дата: <дата>

1. Главный вывод дня
2. Что сдвинулось
3. Что зависло и требует вопроса
4. Просроченные и без срока
5. Zoom и чаты: устные задачи, которые надо зафиксировать
6. Адресные вопросы и рекомендации для Bitrix
7. Что проверить завтра

Возвращай строго JSON без markdown:
{
  "summary": "...",
  "dynamics_summary": "...",
  "risks_summary": "...",
  "recommendations": ["..."],
  "manager_recommendations": [
    {
      "manager_name": "...",
      "department": "... или null",
      "actions": [
        {"subject":"...","action":"...","person":"... или null","task_ref":"Bitrix-id или тема","due":"YYYY-MM-DD или 'срок не указан'","priority":"low|medium|high|critical","expected_result":"...","source":"chat:<title> или bitrix:<id>"}
      ]
    }
  ],
  "manager_messages": [
    {
      "manager_name": "ФИО адресата",
      "priority": "low|medium|high|critical",
      "message_type": "question|recommendation|deadline_request|status_request|escalation",
      "subject": "короткая тема",
      "message_text": "готовый текст для отправки в Bitrix от лица собственника, 2-6 строк",
      "due": "YYYY-MM-DD или null",
      "topics": ["тема 1"],
      "source_facts": [{"source":"bitrix:<id> | chat:<title> | zoom:<call_id> | previous_owner_daily","fact":"конкретный факт"}]
    }
  ],
  "open_tasks": ["ФИО: задача; срок; статус; источник"],
  "overdue_tasks": ["Bitrix-id: ответственный; дедлайн; дней просрочки"],
  "no_response": ["ФИО: на что нет реакции; дата"],
  "goal_dynamics": ["цель: владелец; status_before -> status_after; риск"],
  "report_text": "Готовый текст отчета по разделам."
}`,
  },
  owner_weekly: {
    label: "Еженедельный общий отчет для собственника",
    text: `Ты формируешь еженедельный управленческий отчет для СОБСТВЕННИКА компании.

ВХОДНЫЕ ДАННЫЕ (JSON):
- period_start, period_end - границы недели.
- previous_owner_weekly - отчет за прошлую неделю (или null). Главная база сравнения.
- chat_overall_weekly - сводка по всем чатам за неделю.
- chat_weekly_reports - недельные отчеты по каждому чату (висящие задачи, цели, динамика).
- daily_owner_reports - 7 ежедневных owner-отчетов недели.
- bitrix_registry - актуальные задачи Bitrix за период.
- team - орг-структура.
- goal_progress_events_week - изменения целей.

ОБЯЗАТЕЛЬНО:
1. Сравни эту неделю с прошлой: что улучшилось, что ухудшилось, что повторяется без сдвига.
2. Не теряй ни одной висящей задачи и ни одной висящей цели из chat_weekly_reports и bitrix_registry.
3. Дай оценку динамики по каждому отделу/руководителю.
4. Сформируй manager_recommendations - адресные действия по каждому руководителю на следующую неделю.
5. Каждое действие проверяемо: ответственный, объект, срок, ожидаемый результат.
6. Не выдумывай данные.
7. report_text - готовый текст недельного отчета по разделам.

Возвращай строго JSON без markdown:
{
  "summary": "...",
  "dynamics_summary": "...",
  "risks_summary": "...",
  "recommendations": ["..."],
  "week_vs_previous": {"better":["..."],"worse":["..."],"stuck":["..."]},
  "top_performers": ["ФИО: за что; результаты"],
  "weak_performers": ["ФИО: что не сделано; риск"],
  "hanging_tasks_by_owner": ["ФИО: задача; срок; статус; источник; сколько дней висит"],
  "hanging_goals": ["цель: владелец; что не закрыто; риск; следующий контроль"],
  "overdue_bitrix": ["Bitrix-id: задача; ответственный; дедлайн; дней просрочки"],
  "manager_recommendations": [
    {
      "manager_name": "...",
      "department": "... или null",
      "actions": [
        {"subject":"...","action":"...","person":"... или null","task_ref":"Bitrix-id или тема","due":"YYYY-MM-DD или 'срок не указан'","priority":"low|medium|high|critical","expected_result":"...","source":"chat:<title> или bitrix:<id> или week_overview"}
      ]
    }
  ],
  "report_text": "Готовый недельный отчет по разделам."
}`,
  },
  zoom_processing: {
    label: "Обработка Зумов",
    text: `Ты выполняешь управленческую обработку расшифровки Zoom-созвона.

Главная задача: превратить транскрипт встречи в человеческий отчет и структурированный JSON. Не пересказывай весь разговор. Вытаскивай факты, решения, договоренности, задачи, риски, открытые вопросы, важные числа, даты, документы, владельцев и следующие шаги.

Обязательно фиксируй:
- краткую сводку встречи;
- темы обсуждения с таймкодами начала;
- карту решений и статусов;
- операционные задачи, планы и выполненные факты;
- управленческую диагностику качества решений;
- риски и контрольные точки;
- что нужно пересобрать как полноценное решение;
- поведенческие сигналы, если они значимы.

Если участник сказал выполненный факт, например "я купил моющие средства", запиши это как результат. Если сказан план, например "в среду буду мыть машину", запиши это как следующий шаг и нормализуй дату относительно даты созвона, если дата созвона передана.

Не выдумывай владельцев, сроки и статусы. Если владелец или срок не назван, пиши "требует назначения" или null в JSON. Верни строго валидный JSON с полем report_text.`,
  },
  chat: {
    label: "Ежедневный отчет по чату",
    text: `Ты анализируешь дневную переписку рабочего чата как единый второй слой аналитики.

Твоя задача:
- объединить обычные сообщения чата, OCR-текст картинок и отправителей картинок;
- структурировать отчет по чату, не создавая отдельный реестр задач;
- выделить решения, риски, блокеры, поручения, вопросы и упоминания Bitrix-задач;
- не превращать обычную переписку в задачи без явного основания.

Правила:
- Сообщение ≠ задача.
- OCR-текст картинки сам по себе ≠ задача. Решение принимается только здесь, с учетом переписки и памяти.
- Задача появляется только если есть явное поручение, плановое действие, обещание выполнить работу, дедлайн, ответственный или завершенное действие.
- Если задача/цель есть в OCR, но ответственный не указан, ответственным считается отправитель картинки.
- Если в чате есть подтверждение выполнения, зафиксируй это как результат/поручение внутри отчета.
- Если чат опровергает OCR/память или меняет срок/статус, зафиксируй это в confirmations.
- Если в чате написали новую задачу без скрина, зафиксируй ее как commitment внутри отчета, не как отдельную задачу реестра.
- Если в чате написали цель/фокус периода, зафиксируй ее в chat_goal внутри отчета.
- Если картинка или сообщение содержит левую информацию, сохрани только fact type=unclassified_attachment, не создавай задачу/цель.
- Если не уверен, не создавай задачу; положи фрагмент в unknown_blocks.

Стандартизация:
- Цели проекта/года/квартала/месяца/недели/дня возвращай в chat_goal, если это главная цель чата/периода.
- Дневные планы возвращай в tasks со status=planned, если нет признака выполнения.
- Галочки, "выполнено", "готово", "результат" возвращай в facts type=completed_work и при необходимости tasks со status=done.
- "Ожидание результата" — это expected_result/description, не отдельная задача.
- Риски, ошибки, блокеры, решения, встречи возвращай в facts.

Формат ответа — строго JSON без markdown:
{
  "chat_goal": null или {
    "title": "...",
    "description": "...",
    "period_type": "day|week|month|quarter|year|period",
    "owner_name": "... или null",
    "success_metrics": "... или null",
    "status": "active",
    "confidence": 0.0
  },
  "tasks": [
    {
      "title": "...",
      "description": "... или null",
      "assignee_name": "... или null",
      "deadline": "YYYY-MM-DD или null",
      "status": "planned|in_progress|done|blocked|unknown",
      "priority": "low|normal|high|null",
      "confidence": 0.0,
      "evidence_message_ids": [123]
    }
  ],
  "facts": [
    {
      "type": "completed_work|progress_update|risk|blocker|decision|meeting|plan_update|unclassified_attachment",
      "date": "YYYY-MM-DD или null",
      "person_name": "... или null",
      "text": "...",
      "linked_task_title": "... или null",
      "linked_goal_title": "... или null",
      "confidence": 0.0,
      "evidence_message_ids": [123]
    }
  ],
  "confirmations": [
    {
      "target_type": "goal|task|fact|ocr",
      "target_title": "...",
      "result": "confirmed|refuted|updated|insufficient_context",
      "reason": "...",
      "evidence_message_ids": [123]
    }
  ],
  "unknown_blocks": [
    {
      "text": "...",
      "reason": "..."
    }
  ]
}`,
  },
  img: {
    label: "Обработка IMG",
    text: `Ты выполняешь только OCR/расшифровку изображения из рабочего чата.

Главное правило:
- На этом этапе НЕ создавай цели, задачи, факты, выводы, статусы и ответственных.
- Не классифицируй документ как план/отчет/цель.
- Не стандартизируй данные.
- Не придумывай отсутствующий текст.
- Не исправляй смысл документа.

Что нужно сделать:
1. Максимально точно распознать весь видимый текст.
2. Сохранить структуру: строки, колонки, заголовки, даты, галочки, нумерацию, переносы.
3. Если это таблица, передать ее как текстовую таблицу с понятными строками и колонками.
4. Если текст плохо виден, указать "[неразборчиво]" на месте сомнительного фрагмента.
5. Если текста нет, кратко описать видимое содержимое изображения.

Ответ верни только обычным текстом OCR, без JSON и без управленческих выводов.

Пример формата:
Цель проекта: ...
Цель на апрель: ...

Дата | Задача | Ожидание | Результат
27.04 | ... | ... | Выполнено

Вложения/значки:
- Зеленая галочка рядом с пунктом: ...

Запрещено:
- Создавать отдельные записи целей, задач или фактов вне отчета чата.
- Назначать ответственных.
- Делать вывод, что является задачей или целью.
- Писать пояснения вроде "на изображении таблица"; только если текста нет.`,
  },
} as const;

type PromptKey = keyof typeof PROMPT_TEMPLATES;

const HIDDEN_PROMPT_TABS = new Set<PromptKey>(["daily", "weekly"]);

type PromptVersion = {
  id: string;
  prompt_key: string | null;
  title: string | null;
  text: string;
  version: number;
  is_active: boolean;
  created_at: string | null;
  ai_requests_count: number;
};

export default function App() {
  const [activeTab, setActiveTab] = useState(
    () => agentTabForPath(window.location.pathname) || "Сводная аналитика",
  );

  useEffect(() => {
    const route = AGENT_TAB_ROUTES[activeTab];
    const current = window.location.pathname;
    if (route) {
      // Keep any agent/dialog sub-path the view manages; only switch when the base differs.
      if (!isUnderAgentRoute(current, route)) window.history.pushState({}, "", route);
    } else if (agentTabForPath(current)) {
      window.history.pushState({}, "", "/main");
    }
  }, [activeTab]);

  useEffect(() => {
    const onPopState = () => {
      setActiveTab(agentTabForPath(window.location.pathname) || "Сводная аналитика");
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  const [summaryTab, setSummaryTab] = useState<"overview" | "reports">("reports");
  const [tasksSubTab, setTasksSubTab] = useState<"reports" | "registry" | "chats" | "prompts" | "ai_requests">(
    window.location.pathname === "/registry" ? "registry" : "reports",
  );
  const [promptTab, setPromptTab] = useState<PromptKey>("owner_weekly");
  const [promptTexts, setPromptTexts] = useState<Record<PromptKey, string>>({
    daily: PROMPT_TEMPLATES.daily.text,
    weekly: PROMPT_TEMPLATES.weekly.text,
    monthly: PROMPT_TEMPLATES.monthly.text,
    chat_weekly: PROMPT_TEMPLATES.chat_weekly.text,
    yearly: PROMPT_TEMPLATES.yearly.text,
    owner_daily: PROMPT_TEMPLATES.owner_daily.text,
    owner_weekly: PROMPT_TEMPLATES.owner_weekly.text,
    zoom_processing: PROMPT_TEMPLATES.zoom_processing.text,
    chat: PROMPT_TEMPLATES.chat.text,
    img: PROMPT_TEMPLATES.img.text,
  });
  const [promptMessage, setPromptMessage] = useState("");
  const [promptSaving, setPromptSaving] = useState(false);
  const [promptHistoryLoading, setPromptHistoryLoading] = useState(false);
  const [promptHistory, setPromptHistory] = useState<Record<PromptKey, PromptVersion[]>>({
    daily: [],
    weekly: [],
    monthly: [],
    chat_weekly: [],
    yearly: [],
    owner_daily: [],
    owner_weekly: [],
    zoom_processing: [],
    chat: [],
    img: [],
  });
  const [reportKind, setReportKind] = useState<"daily" | "weekly" | "monthly" | "quarterly" | "yearly">("daily");
  const [taskRows, setTaskRows] = useState<TaskRegistryRow[]>([]);
  const [taskStats, setTaskStats] = useState<TaskRegistryStats | null>(null);
  const [taskFilters, setTaskFilters] = useState<TaskRegistryFilters>(() => ({
    q: "",
    status: "",
    responsible_id: "",
    created_from: "",
    created_to: "",
    deadline_from: "",
    deadline_to: "",
    ...defaultTaskRegistryPeriod(),
  }));
  const [taskRegistryLoading, setTaskRegistryLoading] = useState(false);
  const [fullSyncLoading, setFullSyncLoading] = useState(false);
  const [taskRegistryMessage, setTaskRegistryMessage] = useState("");
  const [taskRegistryDownloadUrl, setTaskRegistryDownloadUrl] = useState("");
  const [teamRows, setTeamRows] = useState<TeamMemberRow[]>([]);
  const [teamLoading, setTeamLoading] = useState(false);
  const [teamMessage, setTeamMessage] = useState("");
  const [teamLastSyncedAtText, setTeamLastSyncedAtText] = useState("");
  const [goalRows, setGoalRows] = useState<GoalRow[]>([]);
  const [goalStats, setGoalStats] = useState<GoalStats | null>(null);
  const [goalFilters, setGoalFilters] = useState<GoalFilters>({
    q: "",
    goal_level: "",
    period_type: "",
    status: "",
    source_type: "",
    owner_id: "",
  });
  const [goalsLoading, setGoalsLoading] = useState(false);
  const [goalsMessage, setGoalsMessage] = useState("");
  const [goalForm, setGoalForm] = useState<GoalForm>({
    goal_level: "company",
    owner_id: "",
    goal_title: "",
    goal_text: "",
    period_type: "month",
    period_start: "2026-04-01",
    period_end: "2026-04-30",
    success_metrics: "",
    expected_result: "",
    status: "active",
  });
  const [companyProfile, setCompanyProfile] = useState<CompanyProfile>({
    title: "О компании",
    content: "",
    updated_at: null,
    updated_at_text: "Еще не сохранялось",
  });
  const [companyDraft, setCompanyDraft] = useState("");
  const [companyLoading, setCompanyLoading] = useState(false);
  const [companySaving, setCompanySaving] = useState(false);
  const [companyMessage, setCompanyMessage] = useState("");
  const [companyFolderId, setCompanyFolderId] = useState<string | null>(null);
  const [companyCurrentFolder, setCompanyCurrentFolder] = useState<CompanyFolder | null>(null);
  const [companyFolders, setCompanyFolders] = useState<CompanyFolder[]>([]);
  const [companyPath, setCompanyPath] = useState<Array<{ id: string; name: string }>>([]);
  const [companyFolderContextMenu, setCompanyFolderContextMenu] = useState<{ folder: CompanyFolder; x: number; y: number } | null>(null);
  const [companySpaceContextMenu, setCompanySpaceContextMenu] = useState<{ x: number; y: number } | null>(null);
  const [renamingCompanyFolderId, setRenamingCompanyFolderId] = useState<string | null>(null);
  const [renamingCompanyFolderName, setRenamingCompanyFolderName] = useState("");
  const [companyFolderDeleteCandidate, setCompanyFolderDeleteCandidate] = useState<CompanyFolder | null>(null);
  // Access management lives in Центр Агента → Агенты (Команда и доступы) now;
  // Настройки keeps only the AI instructions editor.
  const [settingsSubTab, setSettingsSubTab] = useState<"ai_instructions">("ai_instructions");
  const [aiInstructionDraft, setAiInstructionDraft] = useState("");
  const [aiInstructionLoading, setAiInstructionLoading] = useState(false);
  const [aiInstructionSaving, setAiInstructionSaving] = useState(false);
  const [aiInstructionMessage, setAiInstructionMessage] = useState("");
  const [aiInstructionFolderId, setAiInstructionFolderId] = useState<string | null>(null);
  const [aiInstructionCurrentFolder, setAiInstructionCurrentFolder] = useState<AiInstructionFolder | null>(null);
  const [aiInstructionFolders, setAiInstructionFolders] = useState<AiInstructionFolder[]>([]);
  const [aiInstructionPath, setAiInstructionPath] = useState<Array<{ id: string; name: string }>>([]);
  const [aiInstructionFolderContextMenu, setAiInstructionFolderContextMenu] = useState<{ folder: AiInstructionFolder; x: number; y: number } | null>(null);
  const [aiInstructionSpaceContextMenu, setAiInstructionSpaceContextMenu] = useState<{ x: number; y: number } | null>(null);
  const [renamingAiInstructionFolderId, setRenamingAiInstructionFolderId] = useState<string | null>(null);
  const [renamingAiInstructionFolderName, setRenamingAiInstructionFolderName] = useState("");
  const [aiInstructionFolderDeleteCandidate, setAiInstructionFolderDeleteCandidate] = useState<AiInstructionFolder | null>(null);
  const [chatRows, setChatRows] = useState<ChatRegistryRow[]>(() => loadCachedChatRows());
  const [chatRegistryLoading, setChatRegistryLoading] = useState(false);
  const [chatRegistryMessage, setChatRegistryMessage] = useState("");
  const [chatContextMenu, setChatContextMenu] = useState<{ chat: ChatRegistryRow; x: number; y: number } | null>(null);
  const [selectedChat, setSelectedChat] = useState<ChatRegistryRow | null>(null);
  const [chatSummaryDate, setChatSummaryDate] = useState("2026-04-29");
  const [chatActionDate, setChatActionDate] = useState("2026-04-29");
  const [chatPeriodFrom, setChatPeriodFrom] = useState("2026-04-27");
  const [chatPeriodTo, setChatPeriodTo] = useState("2026-04-29");
  const [chatDay, setChatDay] = useState<ChatDayPayload | null>(null);
  const [chatDayLoading, setChatDayLoading] = useState(false);
  const [chatDayMessage, setChatDayMessage] = useState("");
  const [chatOverallReport, setChatOverallReport] = useState<ChatOverallDailyReport | null>(null);
  const [chatOverallDailyHistory, setChatOverallDailyHistory] = useState<ChatOverallDailyReport[]>([]);
  const [selectedChatOverallDailyReport, setSelectedChatOverallDailyReport] = useState<ChatOverallDailyReport | null>(null);
  const [chatOverallLoading, setChatOverallLoading] = useState(false);
  const [chatWeeklyHistory, setChatWeeklyHistory] = useState<ChatOverallWeeklyReport[]>([]);
  const [chatWeeklyHistoryLoading, setChatWeeklyHistoryLoading] = useState(false);
  const [chatHistoryKind, setChatHistoryKind] = useState<"daily" | "weekly">("weekly");
  const [isChatWeeklyHistoryOpen, setIsChatWeeklyHistoryOpen] = useState(false);
  const [selectedChatWeeklyReport, setSelectedChatWeeklyReport] = useState<ChatOverallWeeklyReport | null>(null);
  const [isChatDayModalOpen, setIsChatDayModalOpen] = useState(false);
  const [chatRegistryInitialLoaded, setChatRegistryInitialLoaded] = useState(false);
  const [isReportModalOpen, setIsReportModalOpen] = useState(false);
  const [reportModalMonth, setReportModalMonth] = useState(new Date(2026, 3, 1)); // Start at current month
  const [selectedReportWeekStart, setSelectedReportWeekStart] = useState<Date | null>(null);
  const [selectedReportDay, setSelectedReportDay] = useState<Date | null>(null);
  const [selectedReportMonth, setSelectedReportMonth] = useState<{ year: number; month: number } | null>(null);
  const [selectedReportQuarter, setSelectedReportQuarter] = useState<{ year: number; quarter: 1 | 2 | 3 | 4 } | null>(null);
  const [selectedReportYear, setSelectedReportYear] = useState<number | null>(null);
  const [reportCreateLoading, setReportCreateLoading] = useState(false);
  const [reportCreateMessage, setReportCreateMessage] = useState("");
  const [reportPrecheckLoading, setReportPrecheckLoading] = useState(false);
  const [reportPrecheckError, setReportPrecheckError] = useState("");
  const [reportsFeedLoading, setReportsFeedLoading] = useState(false);
  const [reportsFeedItems, setReportsFeedItems] = useState<OwnerReportFeedItem[]>([]);
  const [reportContextMenu, setReportContextMenu] = useState<{ item: OwnerReportFeedItem; x: number; y: number } | null>(null);
  const [selectedOwnerReport, setSelectedOwnerReport] = useState<OwnerReportFeedItem | null>(null);
  const [isOwnerReportSendOpen, setIsOwnerReportSendOpen] = useState(false);
  const [ownerReportSendMode, setOwnerReportSendMode] = useState<"full" | "recommendations">("recommendations");
  const [ownerReportRecipientIds, setOwnerReportRecipientIds] = useState<number[]>([]);
  const [ownerReportRecommendationTexts, setOwnerReportRecommendationTexts] = useState<OwnerReportRecommendationMap>({});
  const [ownerReportSendLoading, setOwnerReportSendLoading] = useState(false);
  const [ownerReportSendMessage, setOwnerReportSendMessage] = useState("");
  const [ownerReportSendStatus, setOwnerReportSendStatus] = useState<"idle" | "success" | "error">("idle");
  const [editableReport, setEditableReport] = useState<EditableReport | null>(null);
  const [reportEditSaving, setReportEditSaving] = useState(false);
  const [reportEditMessage, setReportEditMessage] = useState("");
  const [aiRequestsLoading, setAiRequestsLoading] = useState(false);
  const [aiRequests, setAiRequests] = useState<Array<{
    id: string;
    request_type: string;
    provider: string;
    model: string;
    status: string;
    created_at_text: string;
    duration_ms: number | null;
    error_text: string | null;
    prompt_text_snapshot: string | null;
    input_payload: any;
    response_text: string;
    response_json: any;
    raw_response_json: any;
  }>>([]);
  const [selectedAiRequest, setSelectedAiRequest] = useState<string | null>(null);
  const [zoomCallsTree, setZoomCallsTree] = useState<ZoomCallsTree>({ years: [], total: 0 });
  const [zoomCallsLoading, setZoomCallsLoading] = useState(false);
  const [zoomCallsMessage, setZoomCallsMessage] = useState("");
  const [selectedZoomCall, setSelectedZoomCall] = useState<ZoomCall | null>(null);
  const [zoomCallDetailLoading, setZoomCallDetailLoading] = useState(false);
  const [zoomDispatchPreview, setZoomDispatchPreview] = useState<ZoomOperationalTasksPreview | null>(null);
  const [zoomDispatchCall, setZoomDispatchCall] = useState<ZoomCall | null>(null);
  const [zoomTranscriptVisible, setZoomTranscriptVisible] = useState(false);
  const [zoomFolder, setZoomFolder] = useState<{ year?: number; month?: number; date?: string }>({});
  const [accountingTab, setAccountingTab] = useState<
    "requests" | "statements" | "registry"
  >("requests");
  const [registry, setRegistry] = useState<PaymentItem[]>(INITIAL_REGISTRY);
  const [showAddPayment, setShowAddPayment] = useState(false);
  const [paymentForm, setPaymentForm] = useState({
    date: "2026-04-27",
    type: "outgoing",
    amount: "",
    desc: "",
  });
  const [selectedDate, setSelectedDate] = useState(new Date(2026, 3, 27)); // April 27, 2026
  const [calendarViewDate, setCalendarViewDate] = useState(
    new Date(2026, 3, 27),
  ); // April 27, 2026
  const [isDatePickerOpen, setIsDatePickerOpen] = useState(false);
  const datePickerRef = useRef<HTMLDivElement>(null);

  // Close DatePicker on Outside Click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        datePickerRef.current &&
        !datePickerRef.current.contains(event.target as Node)
      ) {
        setIsDatePickerOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const loadTaskRegistry = async (filters = taskFilters) => {
    setTaskRegistryLoading(true);
    setTaskRegistryMessage("");
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]: [string, string]) => {
      if (value) params.set(key, value);
    });
    try {
      const response = await fetch(`/api/registry?${params.toString()}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить реестр задач");
      setTaskRows(payload.tasks || []);
      setTaskStats(payload.stats || null);
      setTaskFilters((current) => ({ ...current, ...(payload.filters || {}) }));
    } catch (error) {
      setTaskRegistryMessage(error instanceof Error ? error.message : "Не удалось загрузить реестр задач");
    } finally {
      setTaskRegistryLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "Сводная аналитика" && summaryTab === "reports" && tasksSubTab === "registry") {
      void loadTaskRegistry();
    }
  }, [activeTab, summaryTab, tasksSubTab]);

  const loadReportsFeed = async (kind: "daily" | "weekly" | "monthly" | "quarterly" | "yearly" = reportKind) => {
    if (kind === "monthly" || kind === "quarterly" || kind === "yearly") {
      setReportsFeedItems([]);
      return;
    }
    setReportsFeedLoading(true);
    try {
      if (kind === "daily") {
        const payload = await fetchJsonSafe("/api/owner/daily-reports?limit=100", undefined, 60000);
        const reports: OwnerDailyReport[] = payload.reports || [];
        setReportsFeedItems(
          reports.map((item) => ({
            id: item.report_id,
            kind: "daily" as const,
            title: `Ежедневный отчет ${item.date_text}`,
            subtitle: `${item.date_text} • версия ${item.version}`,
            status: "Сформирован",
            reportText: item.report_text || "",
            reportDate: item.report_date,
            summary: item.summary,
            dynamics: item.dynamics_summary,
            risks: item.risks_summary,
            recommendations: item.recommendations,
          })),
        );
        return;
      }
      const payload = await fetchJsonSafe("/api/owner/weekly-reports?limit=100", undefined, 60000);
      const reports: OwnerWeeklyReport[] = payload.reports || [];
      setReportsFeedItems(
        reports.map((item) => ({
          id: item.report_id,
          kind: "weekly" as const,
          title: `Еженедельный отчет ${item.period_text}`,
          subtitle: `${item.period_text} • версия ${item.version}`,
          status: "Сформирован",
          reportText: item.report_text || "",
          summary: item.summary,
          dynamics: item.dynamics_summary,
          risks: item.risks_summary,
          recommendations: item.recommendations,
        })),
      );
    } catch (error) {
      setReportCreateMessage(error instanceof Error ? error.message : "Не удалось загрузить список отчетов.");
      setReportsFeedItems([]);
    } finally {
      setReportsFeedLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "Сводная аналитика" && summaryTab === "reports" && tasksSubTab === "reports") {
      void loadReportsFeed(reportKind);
    }
  }, [activeTab, summaryTab, tasksSubTab, reportKind]);

  const loadAiRequests = async () => {
    setAiRequestsLoading(true);
    try {
      const payload = await fetchJsonSafe("/api/ai-requests?limit=150", undefined, 60000);
      const rows = payload.requests || [];
      setAiRequests(rows);
      setSelectedAiRequest((current) => {
        if (!rows.length) return null;
        if (!current) return rows[0].id;
        return rows.find((row: any) => row.id === current)?.id || rows[0].id;
      });
    } catch (error) {
      setReportCreateMessage(error instanceof Error ? error.message : "Не удалось загрузить ИИ запросы.");
      setAiRequests([]);
      setSelectedAiRequest(null);
    } finally {
      setAiRequestsLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "Сводная аналитика" && summaryTab === "reports" && tasksSubTab === "ai_requests") {
      void loadAiRequests();
    }
  }, [activeTab, summaryTab, tasksSubTab]);

  const updateTaskFilter = (key: keyof TaskRegistryFilters, value: string) => {
    setTaskFilters((current) => ({ ...current, [key]: value }));
  };

  const syncTaskRegistry = async () => {
    setTaskRegistryLoading(true);
    setTaskRegistryMessage("");
    setTaskRegistryDownloadUrl("");
    try {
      const response = await fetch("/api/registry/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          date_from: taskFilters.date_from,
          date_to: taskFilters.date_to,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось обновить данные из Bitrix");
      setTaskRows(payload.tasks || []);
      setTaskStats(payload.stats || null);
      setTaskFilters((current) => ({ ...current, ...(payload.filters || {}) }));
      setTaskRegistryMessage(payload.message || "Синхронизация завершена");
      setTaskRegistryDownloadUrl(payload.download_url || "");
    } catch (error) {
      setTaskRegistryMessage(error instanceof Error ? error.message : "Не удалось обновить данные из Bitrix");
    } finally {
      setTaskRegistryLoading(false);
    }
  };

  const syncAllBitrixData = async () => {
    setFullSyncLoading(true);
    setTaskRegistryLoading(true);
    setTaskRegistryMessage("");
    setTaskRegistryDownloadUrl("");
    try {
      const response = await fetch("/api/sync/full", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          date_from: taskFilters.date_from,
          date_to: taskFilters.date_to,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось выполнить полное обновление");
      setTaskRows(payload.tasks || []);
      setTaskStats(payload.stats || null);
      setTaskFilters((current) => ({ ...current, ...(payload.filters || {}) }));
      setTeamRows(payload.team || []);
      setChatRows(payload.chats || []);
      setTaskRegistryMessage(payload.message || "Полное обновление завершено");
      setTaskRegistryDownloadUrl(payload.download_url || "");
    } catch (error) {
      setTaskRegistryMessage(error instanceof Error ? error.message : "Не удалось выполнить полное обновление");
    } finally {
      setFullSyncLoading(false);
      setTaskRegistryLoading(false);
    }
  };

  const loadTeam = async () => {
    setTeamLoading(true);
    setTeamMessage("");
    try {
      const response = await fetch("/api/team");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить команду");
      const rows = payload.team || [];
      setTeamRows(rows);
      setTeamLastSyncedAtText(payload.last_synced_at_text || "");
      return rows;
    } catch (error) {
      setTeamMessage(error instanceof Error ? error.message : "Не удалось загрузить команду");
      return [];
    } finally {
      setTeamLoading(false);
    }
  };

  const openOwnerReportSend = async (
    report: OwnerReportFeedItem | null = selectedOwnerReport,
    mode: "full" | "recommendations" = "recommendations",
  ) => {
    if (!report || (report.kind !== "daily" && report.kind !== "weekly")) return;
    setSelectedOwnerReport(report);
    setOwnerReportSendMode(mode);
    setOwnerReportSendMessage("");
    setOwnerReportSendStatus("idle");
    setIsOwnerReportSendOpen(true);
    let members = teamRows;
    if (!teamRows.length) {
      members = await loadTeam();
    }
    const recommendationsByUser = extractOwnerRecommendationsByUser(report, members);
    const recommendedUserIds = Object.keys(recommendationsByUser).map(Number).filter(Boolean);
    setOwnerReportRecommendationTexts(recommendationsByUser);
    setOwnerReportRecipientIds(mode === "recommendations" ? recommendedUserIds : []);
  };

  const toggleOwnerReportRecipient = (userId: number) => {
    if (ownerReportSendMode === "recommendations" && !ownerReportRecommendationTexts[userId]) return;
    setOwnerReportRecipientIds((current) =>
      current.includes(userId) ? current.filter((id) => id !== userId) : [...current, userId],
    );
  };

  const sendOwnerReportToBitrix = async () => {
    if (!selectedOwnerReport || (selectedOwnerReport.kind !== "daily" && selectedOwnerReport.kind !== "weekly")) return;
    if (!ownerReportRecipientIds.length) {
      setOwnerReportSendMessage("Выберите хотя бы одного получателя.");
      return;
    }
    setOwnerReportSendLoading(true);
    setOwnerReportSendMessage("");
    setOwnerReportSendStatus("idle");
    try {
      const reportScope = selectedOwnerReport.kind === "weekly" ? "weekly-reports" : "daily-reports";
      const actionPath = ownerReportSendMode === "full" ? "send-full" : "send";
      const payload = await fetchJsonSafe(
        `/api/owner/${reportScope}/${encodeURIComponent(selectedOwnerReport.id)}/${actionPath}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            recipient_ids: ownerReportRecipientIds,
            ...(ownerReportSendMode === "recommendations"
              ? {
                  recipient_recommendations: Object.fromEntries(
                    ownerReportRecipientIds.map((userId) => [String(userId), ownerReportRecommendationTexts[userId]]),
                  ),
                }
              : {}),
          }),
        },
        120000,
      );
      const failed = Number(payload.failed || 0);
      setOwnerReportSendStatus(failed > 0 ? "error" : "success");
      setOwnerReportSendMessage(payload.message || (ownerReportSendMode === "full" ? "PDF-отчет успешно отправлен в Bitrix." : "Рекомендации поставлены задачами в Bitrix."));
    } catch (error) {
      setOwnerReportSendStatus("error");
      setOwnerReportSendMessage(error instanceof Error ? error.message : "Не удалось отправить отчет.");
    } finally {
      setOwnerReportSendLoading(false);
    }
  };

  const openReportEditor = (report: EditableReport) => {
    setEditableReport(report);
    setReportEditMessage("");
  };

  const ownerReportType = (kind: OwnerReportFeedItem["kind"]): EditableReport["reportType"] | null => {
    if (kind === "daily") return "owner_daily";
    if (kind === "weekly") return "owner_weekly";
    return null;
  };

  const deleteOwnerReport = async (item: OwnerReportFeedItem) => {
    const reportType = ownerReportType(item.kind);
    setReportContextMenu(null);
    if (!reportType) {
      setReportCreateMessage("Удаление отчетов этого типа пока не подключено.");
      return;
    }
    const confirmed = window.confirm(`Удалить отчет «${item.title}»? Это действие нельзя отменить.`);
    if (!confirmed) return;
    setReportsFeedLoading(true);
    setReportCreateMessage("");
    try {
      const payload = await fetchJsonSafe(
        `/api/reports/${reportType}/${encodeURIComponent(item.id)}`,
        { method: "DELETE" },
        60000,
      );
      if (selectedOwnerReport?.id === item.id) {
        setSelectedOwnerReport(null);
      }
      setReportCreateMessage(payload.message || "Отчет удален.");
      await loadReportsFeed(reportKind);
    } catch (error) {
      setReportCreateMessage(error instanceof Error ? error.message : "Не удалось удалить отчет.");
    } finally {
      setReportsFeedLoading(false);
    }
  };

  const updateEditableReportField = (field: keyof EditableReport["fields"], value: string) => {
    setEditableReport((current) =>
      current ? { ...current, fields: { ...current.fields, [field]: value } } : current,
    );
  };

  const saveReportEdit = async () => {
    if (!editableReport) return;
    setReportEditSaving(true);
    setReportEditMessage("");
    try {
      const payload = await fetchJsonSafe(
        `/api/reports/${editableReport.reportType}/${encodeURIComponent(editableReport.reportId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ fields: editableReport.fields }),
        },
        120000,
      );
      setReportEditMessage(payload.message || "Правки сохранены.");
      const context = editableReport.context;
      const reportType = editableReport.reportType;
      if (context === "chat_day" && selectedChat) {
        if (reportType === "chat_weekly" && chatDay?.weekly_report) {
          const bounds = weekBoundsFromIso(chatSummaryDate);
          await loadChatWeeklyReportDetail(selectedChat, {
            date: chatSummaryDate,
            period_start: bounds.period_start,
            period_end: bounds.period_end,
            report_kind: "weekly",
          } as any);
        } else {
          await loadChatDay(selectedChat.dialog_id, chatSummaryDate);
        }
        await loadChatRegistry();
      } else if (context === "chat_overall_history") {
        await Promise.all([loadChatOverallDailyHistory(), loadChatWeeklyHistory(false)]);
      } else if (context === "owner") {
        await loadReportsFeed(selectedOwnerReport?.kind || reportKind);
        const updated = payload.report;
        if (updated && selectedOwnerReport) {
          setSelectedOwnerReport({
            ...selectedOwnerReport,
            id: updated.report_id || selectedOwnerReport.id,
            subtitle: updated.date_text || updated.period_text || selectedOwnerReport.subtitle,
            reportText: updated.report_text || "",
            summary: updated.summary,
            dynamics: updated.dynamics_summary,
            risks: updated.risks_summary,
            recommendations: updated.recommendations,
          });
        }
      }
      setEditableReport(null);
    } catch (error) {
      setReportEditMessage(error instanceof Error ? error.message : "Не удалось сохранить правки.");
    } finally {
      setReportEditSaving(false);
    }
  };

  useEffect(() => {
    if (activeTab === "Команда") {
      void loadTeam();
    }
  }, [activeTab]);

  const loadGoals = async (filters: GoalFilters = goalFilters) => {
    setGoalsLoading(true);
    setGoalsMessage("");
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    try {
      const response = await fetch(`/api/chat-goals?${params.toString()}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить цели");
      setGoalRows(payload.goals || []);
      setGoalStats(payload.stats || null);
    } catch (error) {
      setGoalsMessage(error instanceof Error ? error.message : "Не удалось загрузить цели");
    } finally {
      setGoalsLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "Цели") {
      void loadGoals();
      if (!teamRows.length) void loadTeam();
    }
  }, [activeTab]);

  const updateGoalFilter = (key: keyof GoalFilters, value: string) => {
    setGoalFilters((current) => ({ ...current, [key]: value }));
  };

  const updateGoalForm = (key: keyof GoalForm, value: string) => {
    setGoalForm((current) => ({
      ...current,
      [key]: value,
      ...(key === "goal_level" && value === "company" ? { owner_id: "" } : {}),
    }));
  };

  const saveGoal = async () => {
    setGoalsLoading(true);
    setGoalsMessage("");
    try {
      const response = await fetch("/api/chat-goals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(goalForm),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось сохранить цель");
      setGoalsMessage(payload.message || "Цель сохранена");
      setGoalForm((current) => ({
        ...current,
        goal_title: "",
        goal_text: "",
        success_metrics: "",
        expected_result: "",
      }));
      await loadGoals(goalFilters);
    } catch (error) {
      setGoalsMessage(error instanceof Error ? error.message : "Не удалось сохранить цель");
    } finally {
      setGoalsLoading(false);
    }
  };

  const loadCompanyFolders = async (folderId: string | null = companyFolderId) => {
    setCompanyLoading(true);
    setCompanyMessage("");
    try {
      const params = new URLSearchParams();
      if (folderId) params.set("folder_id", folderId);
      const payload = await fetchJsonSafe(`/api/company-folders?${params.toString()}`, undefined, 60000);
      const current = payload.current || null;
      setCompanyFolderId(folderId);
      setCompanyCurrentFolder(current);
      setCompanyFolders(payload.children || []);
      setCompanyPath(payload.path || []);
      setCompanyDraft(current?.content || "");
    } catch (error) {
      setCompanyMessage(error instanceof Error ? error.message : "Не удалось загрузить папки компании");
    } finally {
      setCompanyLoading(false);
    }
  };

  const syncCompanyGoogleDrive = async () => {
    setCompanyLoading(true);
    setCompanyMessage("");
    try {
      const payload = await fetchJsonSafe("/api/company-folders/sync-google-drive", { method: "POST" }, 600000);
      const result = payload.result || {};
      await loadCompanyFolders(companyFolderId);
      const importProblems = Number(result.document_errors_count ?? 0) + Number(result.skipped_files_count ?? 0);
      const problemsText =
        importProblems > 0
          ? ` Ошибок/пропусков ${importProblems}: ошибок конвертации ${result.document_errors_count ?? 0}, неподдержанных файлов ${result.skipped_files_count ?? 0}.`
          : "";
      setCompanyMessage(
        `Google Drive: папок ${result.folders_total ?? 0}, документов ${result.documents_total ?? 0}, создано ${result.created ?? 0}, обновлено ${result.updated ?? 0}, без изменений ${result.unchanged ?? 0}, удалено ${result.deleted ?? 0}.${problemsText}`,
      );
    } catch (error) {
      setCompanyMessage(error instanceof Error ? error.message : "Не удалось подтянуть данные из Google Drive");
    } finally {
      setCompanyLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "О компании") {
      void loadCompanyFolders(companyFolderId);
    }
  }, [activeTab]);

  const createCompanyFolder = async () => {
    setCompanySaving(true);
    setCompanyMessage("");
    setCompanySpaceContextMenu(null);
    try {
      const response = await fetch("/api/company-folders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent_id: companyFolderId, name: "Новая папка" }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось создать папку");
      const folder = payload.folder as CompanyFolder;
      setRenamingCompanyFolderId(folder.id);
      setRenamingCompanyFolderName(folder.name);
      await loadCompanyFolders(companyFolderId);
    } catch (error) {
      setCompanyMessage(error instanceof Error ? error.message : "Не удалось создать папку");
    } finally {
      setCompanySaving(false);
    }
  };

  const startRenameCompanyFolder = (folder: CompanyFolder) => {
    setCompanyFolderContextMenu(null);
    setRenamingCompanyFolderId(folder.id);
    setRenamingCompanyFolderName(folder.name);
  };

  const commitRenameCompanyFolder = async (folder: CompanyFolder) => {
    const name = renamingCompanyFolderName.trim();
    if (!name) {
      setCompanyMessage("Введите название папки");
      return;
    }
    if (name === folder.name) {
      setRenamingCompanyFolderId(null);
      setRenamingCompanyFolderName("");
      return;
    }
    setCompanySaving(true);
    setCompanyMessage("");
    try {
      const response = await fetch(`/api/company-folders/${encodeURIComponent(folder.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось переименовать папку");
      setCompanyMessage("Папка переименована");
      setRenamingCompanyFolderId(null);
      setRenamingCompanyFolderName("");
      await loadCompanyFolders(companyFolderId);
    } catch (error) {
      setCompanyMessage(error instanceof Error ? error.message : "Не удалось переименовать папку");
    } finally {
      setCompanySaving(false);
    }
  };

  const requestDeleteCompanyFolder = (folder: CompanyFolder) => {
    setCompanyFolderContextMenu(null);
    setCompanyFolderDeleteCandidate(folder);
  };

  const deleteCompanyFolder = async () => {
    const folder = companyFolderDeleteCandidate;
    if (!folder) return;
    setCompanySaving(true);
    setCompanyMessage("");
    try {
      const response = await fetch(`/api/company-folders/${encodeURIComponent(folder.id)}`, { method: "DELETE" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось удалить папку");
      setCompanyMessage(payload.message || "Папка удалена");
      setCompanyFolderDeleteCandidate(null);
      if (companyCurrentFolder?.id === folder.id) {
        const parent = companyPath.length > 1 ? companyPath[companyPath.length - 2].id : null;
        await loadCompanyFolders(parent);
      } else {
        await loadCompanyFolders(companyFolderId);
      }
    } catch (error) {
      setCompanyMessage(error instanceof Error ? error.message : "Не удалось удалить папку");
    } finally {
      setCompanySaving(false);
    }
  };

  const saveCompanyFolderText = async () => {
    if (!companyCurrentFolder) return;
    setCompanySaving(true);
    setCompanyMessage("");
    try {
      const response = await fetch(`/api/company-folders/${encodeURIComponent(companyCurrentFolder.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: companyDraft }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось сохранить текст");
      setCompanyCurrentFolder(payload.folder || companyCurrentFolder);
      setCompanyDraft((payload.folder || companyCurrentFolder).content || "");
      setCompanyMessage("Текст сохранен в БД");
    } catch (error) {
      setCompanyMessage(error instanceof Error ? error.message : "Не удалось сохранить текст");
    } finally {
      setCompanySaving(false);
    }
  };

  const loadAiInstructionFolders = async (folderId: string | null = aiInstructionFolderId) => {
    setAiInstructionLoading(true);
    setAiInstructionMessage("");
    try {
      const params = new URLSearchParams();
      if (folderId) params.set("folder_id", folderId);
      const payload = await fetchJsonSafe(`/api/ai-instruction-folders?${params.toString()}`, undefined, 60000);
      const current = payload.current || null;
      setAiInstructionFolderId(folderId);
      setAiInstructionCurrentFolder(current);
      setAiInstructionFolders(payload.children || []);
      setAiInstructionPath(payload.path || []);
      setAiInstructionDraft(current?.content || "");
    } catch (error) {
      setAiInstructionMessage(error instanceof Error ? error.message : "Не удалось загрузить инструкции");
    } finally {
      setAiInstructionLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "Настройки" && settingsSubTab === "ai_instructions") {
      void loadAiInstructionFolders(aiInstructionFolderId);
    }
  }, [activeTab, settingsSubTab]);

  const createAiInstructionFolder = async () => {
    setAiInstructionSaving(true);
    setAiInstructionMessage("");
    setAiInstructionSpaceContextMenu(null);
    try {
      const response = await fetch("/api/ai-instruction-folders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent_id: aiInstructionFolderId, name: "Новая инструкция" }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось создать папку");
      const folder = payload.folder as AiInstructionFolder;
      setRenamingAiInstructionFolderId(folder.id);
      setRenamingAiInstructionFolderName(folder.name);
      await loadAiInstructionFolders(aiInstructionFolderId);
    } catch (error) {
      setAiInstructionMessage(error instanceof Error ? error.message : "Не удалось создать папку");
    } finally {
      setAiInstructionSaving(false);
    }
  };

  const startRenameAiInstructionFolder = (folder: AiInstructionFolder) => {
    setAiInstructionFolderContextMenu(null);
    setRenamingAiInstructionFolderId(folder.id);
    setRenamingAiInstructionFolderName(folder.name);
  };

  const commitRenameAiInstructionFolder = async (folder: AiInstructionFolder) => {
    const name = renamingAiInstructionFolderName.trim();
    if (!name) {
      setAiInstructionMessage("Введите название папки");
      return;
    }
    if (name === folder.name) {
      setRenamingAiInstructionFolderId(null);
      setRenamingAiInstructionFolderName("");
      return;
    }
    setAiInstructionSaving(true);
    setAiInstructionMessage("");
    try {
      const response = await fetch(`/api/ai-instruction-folders/${encodeURIComponent(folder.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось переименовать папку");
      setRenamingAiInstructionFolderId(null);
      setRenamingAiInstructionFolderName("");
      setAiInstructionMessage("Папка переименована");
      await loadAiInstructionFolders(aiInstructionFolderId);
    } catch (error) {
      setAiInstructionMessage(error instanceof Error ? error.message : "Не удалось переименовать папку");
    } finally {
      setAiInstructionSaving(false);
    }
  };

  const saveAiInstructionText = async () => {
    if (!aiInstructionCurrentFolder) return;
    setAiInstructionSaving(true);
    setAiInstructionMessage("");
    try {
      const response = await fetch(`/api/ai-instruction-folders/${encodeURIComponent(aiInstructionCurrentFolder.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: aiInstructionDraft }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось сохранить текст");
      setAiInstructionCurrentFolder(payload.folder || aiInstructionCurrentFolder);
      setAiInstructionDraft((payload.folder || aiInstructionCurrentFolder).content || "");
      setAiInstructionMessage("Инструкция сохранена. MCP увидит ее при следующем запросе.");
    } catch (error) {
      setAiInstructionMessage(error instanceof Error ? error.message : "Не удалось сохранить текст");
    } finally {
      setAiInstructionSaving(false);
    }
  };

  const requestDeleteAiInstructionFolder = (folder: AiInstructionFolder) => {
    setAiInstructionFolderContextMenu(null);
    setAiInstructionFolderDeleteCandidate(folder);
  };

  const deleteAiInstructionFolder = async () => {
    const folder = aiInstructionFolderDeleteCandidate;
    if (!folder) return;
    setAiInstructionSaving(true);
    setAiInstructionMessage("");
    try {
      const response = await fetch(`/api/ai-instruction-folders/${encodeURIComponent(folder.id)}`, { method: "DELETE" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось удалить папку");
      setAiInstructionFolders((items) => items.filter((item) => item.id !== folder.id));
      setAiInstructionMessage(payload.message || "Папка удалена");
      if (aiInstructionCurrentFolder?.id === folder.id) {
        const parent = aiInstructionPath.length > 1 ? aiInstructionPath[aiInstructionPath.length - 2].id : null;
        await loadAiInstructionFolders(parent);
      } else {
        await loadAiInstructionFolders(aiInstructionFolderId);
      }
      setAiInstructionFolderDeleteCandidate(null);
    } catch (error) {
      setAiInstructionMessage(error instanceof Error ? error.message : "Не удалось удалить папку");
    } finally {
      setAiInstructionSaving(false);
    }
  };

  const renderCompanyDriveDocument = (folder: CompanyFolder) => {
    const blocks = folder.drive_source?.blocks || [];
    const hasBlocks = blocks.length > 0;
    const renderDriveCellText = (value: string) =>
      String(value || "")
        .split(/<br\s*\/?>/i)
        .map((line, index) => (
          <React.Fragment key={index}>
            {index > 0 && <br />}
            {line}
          </React.Fragment>
        ));
    const splitDriveMarkdownRow = (line: string) => {
      const trimmed = line.trim();
      if (!trimmed.startsWith("|") || !trimmed.endsWith("|")) return null;
      const cells: string[] = [];
      let current = "";
      let escaped = false;
      for (const char of trimmed.slice(1, -1)) {
        if (escaped) {
          current += char;
          escaped = false;
          continue;
        }
        if (char === "\\") {
          escaped = true;
          continue;
        }
        if (char === "|") {
          cells.push(current.trim());
          current = "";
          continue;
        }
        current += char;
      }
      cells.push(current.trim());
      return cells;
    };
    const isDriveMarkdownSeparator = (line: string) => {
      const cells = splitDriveMarkdownRow(line);
      return Boolean(cells?.length && cells.every((cell) => /^:?-{3,}:?$/.test(cell.trim())));
    };
    const renderDriveTable = (headers: string[], rows: string[][], key: React.Key) => {
      const width = Math.max(headers.length, ...rows.map((row) => row.length), 1);
      const normalizedHeaders = Array.from({ length: width }, (_, index) => headers[index] || `Колонка ${index + 1}`);
      const normalizedRows = rows.map((row) => Array.from({ length: width }, (_, index) => row[index] || ""));
      return (
        <div key={key} className="space-y-4">
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full border-collapse text-left text-sm">
              <thead className="bg-slate-50">
                <tr>
                  {normalizedHeaders.map((header, index) => (
                    <th key={index} className="border-b border-slate-200 px-4 py-3 font-black text-slate-700 align-top">
                      {renderDriveCellText(header)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {normalizedRows.map((row, rowIndex) => (
                  <tr key={rowIndex} className="odd:bg-white even:bg-slate-50/60">
                    {normalizedHeaders.map((_, cellIndex) => (
                      <td key={cellIndex} className="border-b border-slate-100 px-4 py-3 font-semibold text-slate-800 align-top">
                        {renderDriveCellText(row[cellIndex] || "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      );
    };

    if (!hasBlocks) {
      const fallbackBlocks: React.ReactNode[] = [];
      const tableLines: string[] = [];
      const flushTable = () => {
        if (!tableLines.length) return;
        const rows = tableLines
          .filter((line) => !isDriveMarkdownSeparator(line))
          .map((line) => splitDriveMarkdownRow(line))
          .filter((row): row is string[] => Boolean(row));
        if (rows.length) {
          fallbackBlocks.push(renderDriveTable(rows[0] || [], rows.slice(1), `fallback-table-${fallbackBlocks.length}`));
        }
        tableLines.length = 0;
      };
      (folder.content || "").split(/\r?\n/).forEach((line, index) => {
        if (splitDriveMarkdownRow(line) || isDriveMarkdownSeparator(line)) {
          tableLines.push(line);
          return;
        }
        flushTable();
        const text = line.trim();
        if (!text) return;
        fallbackBlocks.push(
          <p key={`fallback-p-${index}`} className="text-[15px] font-semibold leading-7 text-slate-800 whitespace-pre-wrap">
            {renderDriveCellText(text)}
          </p>,
        );
      });
      flushTable();
      return (
        <div className="space-y-4">
          {fallbackBlocks.length ? fallbackBlocks : (
            <p className="text-[15px] font-semibold leading-7 text-slate-800">Документ пуст.</p>
          )}
        </div>
      );
    }

    return (
      <div className="space-y-7">
        {blocks.map((block, blockIndex) => {
          if (block.type === "heading") {
            const HeadingTag = block.level === 2 ? "h3" : "h2";
            return (
              <HeadingTag key={blockIndex} className="text-xl font-black text-slate-900">
                {block.text}
              </HeadingTag>
            );
          }
          if (block.type === "paragraph") {
            return (
              <p key={blockIndex} className="text-[15px] font-semibold leading-7 text-slate-800 whitespace-pre-wrap">
                {block.text}
              </p>
            );
          }
          if (block.type === "list_item") {
            return (
              <div key={blockIndex} className="flex gap-2 text-[15px] font-semibold leading-7 text-slate-800">
                <span className="text-slate-400">•</span>
                <span>{block.text}</span>
              </div>
            );
          }
          if (block.type === "table") {
            const headers = block.headers || [];
            const rows = block.rows || [];
            return renderDriveTable(headers, rows, blockIndex);
          }
          return null;
        })}
      </div>
    );
  };

  const renderCompanyProfile = () => {
    const atRoot = !companyCurrentFolder;
    const isDriveSyncedFolder = Boolean(companyCurrentFolder?.drive_source);
    const hasChanges = companyCurrentFolder ? companyDraft !== (companyCurrentFolder.content || "") : false;
    const folderTileClass =
      "group h-28 rounded-xl border border-slate-200 bg-slate-50 hover:bg-blue-50/60 hover:border-blue-200 transition-colors p-4 text-left flex flex-col justify-between";

    return (
      <div className="flex flex-col gap-6 animate-in fade-in duration-300 h-full">
        <div className="bg-white rounded-3xl p-8 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] h-full overflow-y-auto">
          <div className="flex flex-wrap items-center justify-between gap-4 mb-8">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center shrink-0 border border-blue-100">
                <Building2 className="w-6 h-6" strokeWidth={2.5} />
              </div>
              <div>
                <h2 className="text-2xl font-bold text-slate-900 tracking-tight">О компании</h2>
                <p className="text-sm text-slate-500 font-medium mt-1">Папки и тексты компании, сохраняются в БД и доступны MCP-серверу</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => void syncCompanyGoogleDrive()}
                disabled={companyLoading || companySaving}
                className="flex items-center gap-2 bg-[#5440F6] hover:bg-[#4532db] text-white px-4 py-2.5 rounded-xl font-bold text-sm transition-all disabled:opacity-60"
              >
                <Upload className="w-4 h-4" strokeWidth={2.5} />
                {companyLoading ? "Подтягиваем..." : "Подтянуть данные"}
              </button>
              <button
                onClick={() => void loadCompanyFolders(companyFolderId)}
                disabled={companyLoading || companySaving}
                className="flex items-center gap-2 border border-[#Eef0f4] bg-white hover:border-[#CBD5E1] text-slate-700 px-4 py-2.5 rounded-xl font-bold text-sm transition-all disabled:opacity-60"
              >
                <RefreshCw className={cn("w-4 h-4", companyLoading && "animate-spin")} strokeWidth={2.5} />
                Обновить
              </button>
            </div>
          </div>

          {companyMessage && (
            <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] px-4 py-3 text-[13px] font-bold text-slate-700 mb-5">
              {companyMessage}
            </div>
          )}

          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2 text-[13px] font-bold">
              <button
                onClick={() => void loadCompanyFolders(null)}
                className={cn(
                  "px-3 py-1.5 rounded-lg border transition-colors",
                  atRoot ? "bg-blue-50 text-blue-700 border-blue-100" : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50",
                )}
              >
                Папки
              </button>
              {companyPath.map((item, index) => (
                <React.Fragment key={item.id}>
                  <ChevronRight className="w-4 h-4 text-slate-300" />
                  <button
                    onClick={() => void loadCompanyFolders(item.id)}
                    className={cn(
                      "px-3 py-1.5 rounded-lg border transition-colors",
                      index === companyPath.length - 1 ? "bg-blue-50 text-blue-700 border-blue-100" : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50",
                    )}
                  >
                    {item.name}
                  </button>
                </React.Fragment>
              ))}
            </div>

            <div className="flex items-center justify-between gap-4">
              <h3
                className="text-[22px] font-black text-slate-900"
                onContextMenu={(event) => {
                  if (!companyCurrentFolder) return;
                  event.preventDefault();
                  setCompanyFolderContextMenu({ folder: companyCurrentFolder, x: event.clientX, y: event.clientY });
                }}
              >
                {companyCurrentFolder?.name || "Папки"}
              </h3>
              <div className="flex flex-wrap items-center gap-2">
                {!atRoot && (
                  <button
                    onClick={() => {
                      const parent = companyPath.length > 1 ? companyPath[companyPath.length - 2].id : null;
                      void loadCompanyFolders(parent);
                    }}
                    className="inline-flex items-center gap-2 h-10 px-4 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-600 font-bold text-[13px]"
                  >
                    <ChevronLeft className="w-4 h-4" />
                    Назад
                  </button>
                )}
              </div>
            </div>

            {companyLoading && companyFolders.length === 0 && atRoot ? (
              <div className="h-48 flex items-center justify-center text-sm font-bold text-slate-400">Загрузка папок...</div>
            ) : (
              <>
                <div
                  className={cn(
                    "grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3 content-start rounded-2xl",
                    atRoot || companyFolders.length > 0 ? "min-h-[220px]" : "min-h-0",
                  )}
                  onContextMenu={(event) => {
                    const target = event.target as HTMLElement;
                    if (target.closest("[data-company-folder-tile]")) return;
                    event.preventDefault();
                    setCompanyFolderContextMenu(null);
                    setCompanySpaceContextMenu({ x: event.clientX, y: event.clientY });
                  }}
                >
                  {companyFolders.map((folder) => (
                    <div
                      data-company-folder-tile
                      key={folder.id}
                      className={folderTileClass}
                      onContextMenu={(event) => {
                        event.preventDefault();
                        setCompanyFolderContextMenu({ folder, x: event.clientX, y: event.clientY });
                      }}
                    >
                      <div
                        role="button"
                        tabIndex={0}
                        onClick={() => renamingCompanyFolderId !== folder.id && void loadCompanyFolders(folder.id)}
                        onKeyDown={(event) => {
                          if ((event.key === "Enter" || event.key === " ") && renamingCompanyFolderId !== folder.id) {
                            void loadCompanyFolders(folder.id);
                          }
                        }}
                        className="flex-1 text-left"
                      >
                        {renderCompanyFolderIcon(folder)}
                        <div>
                          {renamingCompanyFolderId === folder.id ? (
                            <input
                              value={renamingCompanyFolderName}
                              onChange={(event) => setRenamingCompanyFolderName(event.target.value)}
                              onClick={(event) => event.stopPropagation()}
                              onKeyDown={(event) => {
                                if (event.key === "Enter") void commitRenameCompanyFolder(folder);
                                if (event.key === "Escape") {
                                  setRenamingCompanyFolderId(null);
                                  setRenamingCompanyFolderName("");
                                }
                              }}
                              onBlur={() => void commitRenameCompanyFolder(folder)}
                              autoFocus
                              className="w-full rounded-lg border border-blue-200 bg-white px-2 py-1 text-[13px] font-black text-slate-900 outline-none focus:border-[#5440F6] focus:ring-4 focus:ring-[#5440F6]/10"
                            />
                          ) : (
                            <div className="truncate text-[15px] font-black text-slate-900">{folder.name}</div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                {!atRoot && companyCurrentFolder && (
                  <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] p-5">
                    {isDriveSyncedFolder ? (
                      <div className="rounded-2xl border border-[#Eef0f4] bg-white px-5 py-5">
                        <div className="mb-6 flex flex-wrap items-center gap-2 text-xs font-bold text-slate-500">
                          {companyCurrentFolder.drive_source?.source_url && (
                            <a
                              href={companyCurrentFolder.drive_source.source_url}
                              target="_blank"
                              rel="noreferrer"
                              className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-blue-700 hover:border-blue-200 hover:bg-blue-50"
                            >
                              Открыть в Google Drive
                            </a>
                          )}
                          {companyCurrentFolder.drive_source?.google_updated_at_text && (
                            <span className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5">
                              Обновлено: {companyCurrentFolder.drive_source.google_updated_at_text}
                            </span>
                          )}
                          <span className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5">
                            Синхронизируется из Google Drive
                          </span>
                        </div>
                        {renderCompanyDriveDocument(companyCurrentFolder)}
                      </div>
                    ) : (
                      <>
                        <div className="flex justify-end mb-4">
                          <button
                            onClick={() => void saveCompanyFolderText()}
                            disabled={companySaving || companyLoading || !hasChanges}
                            className="inline-flex items-center justify-center gap-2 rounded-xl bg-[#5440F6] px-5 py-2.5 text-[14px] font-bold text-white shadow-md shadow-[#5440F6]/20 transition-all hover:bg-[#4532db] active:scale-95 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
                          >
                            <FileCheck className="h-4 w-4" strokeWidth={2.5} />
                            {companySaving ? "Сохраняем..." : "Сохранить"}
                          </button>
                        </div>
                        <textarea
                          value={companyDraft}
                          onChange={(event) => setCompanyDraft(event.target.value)}
                          disabled={companyLoading}
                          className="min-h-[520px] w-full resize-y rounded-2xl border border-[#Eef0f4] bg-white px-5 py-4 text-[15px] font-semibold leading-7 text-slate-800 outline-none transition-all placeholder:text-slate-300 focus:border-[#A99CFF] focus:ring-4 focus:ring-[#5440F6]/10 disabled:opacity-60"
                          placeholder="Введите текст для этой папки."
                        />
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    );
  };

  const renderSettingsTabs = () => (
    <div className="flex items-center gap-2 bg-white p-1.5 rounded-xl border border-[#Eef0f4] w-max shadow-sm">
      <button
        onClick={() => setSettingsSubTab("ai_instructions")}
        className={cn(
          "px-5 py-2 rounded-lg text-[13px] font-bold transition-all",
          settingsSubTab === "ai_instructions" ? "bg-[#5440F6] text-white shadow-md shadow-[#5440F6]/20" : "text-slate-500 hover:text-slate-900",
        )}
      >
        Инструкции для ИИ
      </button>
      <button
        onClick={() => setActiveTab("Агенты")}
        className="px-5 py-2 rounded-lg text-[13px] font-bold transition-all text-slate-500 hover:text-slate-900"
        title="Доступы переехали: Центр Агента → Агенты → Команда и доступы"
      >
        Доступы → в «Агентах»
      </button>
    </div>
  );

  const renderAiInstructionsSettings = () => {
    const atRoot = !aiInstructionCurrentFolder;
    const hasChanges = aiInstructionCurrentFolder ? aiInstructionDraft !== (aiInstructionCurrentFolder.content || "") : false;
    const folderTileClass =
      "group h-28 rounded-xl border border-slate-200 bg-slate-50 hover:bg-blue-50/60 hover:border-blue-200 transition-colors p-4 text-left flex flex-col justify-between";

    return (
      <div className="flex flex-col gap-6 animate-in fade-in duration-300 h-full">
        {renderSettingsTabs()}

        <div className="bg-white rounded-3xl p-8 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] h-full overflow-y-auto">
          <div className="flex flex-wrap items-center justify-between gap-4 mb-8">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center shrink-0 border border-blue-100">
                <Settings className="w-6 h-6" strokeWidth={2.5} />
              </div>
              <div>
                <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Инструкции для ИИ</h2>
                <p className="text-sm text-slate-500 font-medium mt-1">Правила поведения, порядок поиска и форматы ответов для MCP</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={() => void loadAiInstructionFolders(aiInstructionFolderId)}
                disabled={aiInstructionLoading || aiInstructionSaving}
                className="flex items-center gap-2 border border-[#Eef0f4] bg-white hover:border-[#CBD5E1] text-slate-700 px-4 py-2.5 rounded-xl font-bold text-sm transition-all disabled:opacity-60"
              >
                <RefreshCw className={cn("w-4 h-4", aiInstructionLoading && "animate-spin")} strokeWidth={2.5} />
                Обновить
              </button>
            </div>
          </div>

          {aiInstructionMessage && (
            <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] px-4 py-3 text-[13px] font-bold text-slate-700 mb-5">
              {aiInstructionMessage}
            </div>
          )}

          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2 text-[13px] font-bold">
              <button
                onClick={() => void loadAiInstructionFolders(null)}
                className={cn(
                  "px-3 py-1.5 rounded-lg border transition-colors",
                  atRoot ? "bg-blue-50 text-blue-700 border-blue-100" : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50",
                )}
              >
                Папки
              </button>
              {aiInstructionPath.map((item, index) => (
                <React.Fragment key={item.id}>
                  <ChevronRight className="w-4 h-4 text-slate-300" />
                  <button
                    onClick={() => void loadAiInstructionFolders(item.id)}
                    className={cn(
                      "px-3 py-1.5 rounded-lg border transition-colors",
                      index === aiInstructionPath.length - 1 ? "bg-blue-50 text-blue-700 border-blue-100" : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50",
                    )}
                  >
                    {item.name}
                  </button>
                </React.Fragment>
              ))}
            </div>

            <div className="flex items-center justify-between gap-4">
              {aiInstructionCurrentFolder && renamingAiInstructionFolderId === aiInstructionCurrentFolder.id ? (
                <input
                  value={renamingAiInstructionFolderName}
                  onChange={(event) => setRenamingAiInstructionFolderName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void commitRenameAiInstructionFolder(aiInstructionCurrentFolder);
                    if (event.key === "Escape") {
                      setRenamingAiInstructionFolderId(null);
                      setRenamingAiInstructionFolderName("");
                    }
                  }}
                  onBlur={() => void commitRenameAiInstructionFolder(aiInstructionCurrentFolder)}
                  autoFocus
                  className="rounded-xl border border-blue-200 bg-white px-3 py-2 text-[20px] font-black text-slate-900 outline-none"
                />
              ) : (
                <h3
                  className="text-[22px] font-black text-slate-900"
                  onContextMenu={(event) => {
                    if (!aiInstructionCurrentFolder) return;
                    event.preventDefault();
                    setAiInstructionFolderContextMenu({ folder: aiInstructionCurrentFolder, x: event.clientX, y: event.clientY });
                  }}
                >
                  {aiInstructionCurrentFolder?.name || "Папки"}
                </h3>
              )}
              {!atRoot && (
                <button
                  onClick={() => {
                    const parent = aiInstructionPath.length > 1 ? aiInstructionPath[aiInstructionPath.length - 2].id : null;
                    void loadAiInstructionFolders(parent);
                  }}
                  className="inline-flex items-center gap-2 h-10 px-4 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-600 font-bold text-[13px]"
                >
                  <ChevronLeft className="w-4 h-4" />
                  Назад
                </button>
              )}
            </div>

            {aiInstructionLoading && aiInstructionFolders.length === 0 && atRoot ? (
              <div className="h-48 flex items-center justify-center text-sm font-bold text-slate-400">Загрузка инструкций...</div>
            ) : (
              <>
                <div
                  className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3 content-start rounded-2xl min-h-[220px]"
                  onContextMenu={(event) => {
                    const target = event.target as HTMLElement;
                    if (target.closest("[data-ai-instruction-folder-tile]")) return;
                    event.preventDefault();
                    setAiInstructionFolderContextMenu(null);
                    setAiInstructionSpaceContextMenu({ x: event.clientX, y: event.clientY });
                  }}
                >
                  {aiInstructionFolders.map((folder) => (
                    <div
                      data-ai-instruction-folder-tile
                      key={folder.id}
                      className={folderTileClass}
                      onContextMenu={(event) => {
                        event.preventDefault();
                        setAiInstructionFolderContextMenu({ folder, x: event.clientX, y: event.clientY });
                      }}
                    >
                      <div
                        role="button"
                        tabIndex={0}
                        onClick={() => renamingAiInstructionFolderId !== folder.id && void loadAiInstructionFolders(folder.id)}
                        onKeyDown={(event) => {
                          if ((event.key === "Enter" || event.key === " ") && renamingAiInstructionFolderId !== folder.id) {
                            void loadAiInstructionFolders(folder.id);
                          }
                        }}
                        className="flex-1 text-left"
                      >
                        <Folder className="w-8 h-8 text-blue-500 group-hover:text-blue-600 mb-3" strokeWidth={2.2} />
                        {renamingAiInstructionFolderId === folder.id ? (
                          <input
                            value={renamingAiInstructionFolderName}
                            onChange={(event) => setRenamingAiInstructionFolderName(event.target.value)}
                            onClick={(event) => event.stopPropagation()}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") void commitRenameAiInstructionFolder(folder);
                              if (event.key === "Escape") {
                                setRenamingAiInstructionFolderId(null);
                                setRenamingAiInstructionFolderName("");
                              }
                            }}
                            onBlur={() => void commitRenameAiInstructionFolder(folder)}
                            autoFocus
                            className="w-full rounded-lg border border-blue-200 bg-white px-2 py-1 text-[13px] font-black text-slate-900 outline-none"
                          />
                        ) : (
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0 truncate text-[15px] font-black text-slate-900">{folder.name}</div>
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                requestDeleteAiInstructionFolder(folder);
                              }}
                              className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-lg text-red-500 hover:bg-red-50 group-hover:flex"
                              title="Удалить папку"
                            >
                              <Trash2 className="h-4 w-4" strokeWidth={2.4} />
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>

                {!atRoot && aiInstructionCurrentFolder && (
                  <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] p-5">
                    <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
                      <div className="text-[12px] font-bold text-slate-500">
                        Изменения доступны MCP сразу после сохранения, без перезапуска сервера.
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => requestDeleteAiInstructionFolder(aiInstructionCurrentFolder)}
                          disabled={aiInstructionSaving || aiInstructionLoading}
                          className="inline-flex items-center justify-center gap-2 rounded-xl border border-red-100 bg-white px-4 py-2.5 text-[14px] font-bold text-red-600 transition-all hover:bg-red-50 active:scale-95 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          <Trash2 className="h-4 w-4" strokeWidth={2.5} />
                          Удалить
                        </button>
                        <button
                          onClick={() => void saveAiInstructionText()}
                          disabled={aiInstructionSaving || aiInstructionLoading || !hasChanges}
                          className="inline-flex items-center justify-center gap-2 rounded-xl bg-[#5440F6] px-5 py-2.5 text-[14px] font-bold text-white shadow-md shadow-[#5440F6]/20 transition-all hover:bg-[#4532db] active:scale-95 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
                        >
                          <FileCheck className="h-4 w-4" strokeWidth={2.5} />
                          {aiInstructionSaving ? "Сохраняем..." : "Сохранить"}
                        </button>
                      </div>
                    </div>
                    <textarea
                      value={aiInstructionDraft}
                      onChange={(event) => setAiInstructionDraft(event.target.value)}
                      disabled={aiInstructionLoading}
                      className="min-h-[520px] w-full resize-y rounded-2xl border border-[#Eef0f4] bg-white px-5 py-4 text-[15px] font-semibold leading-7 text-slate-800 outline-none transition-all placeholder:text-slate-300 focus:border-[#A99CFF] focus:ring-4 focus:ring-[#5440F6]/10 disabled:opacity-60"
                      placeholder="Опишите правило поведения ИИ, порядок поиска или формат ответа."
                    />
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    );
  };

  const syncTeam = async () => {
    setTeamLoading(true);
    setTeamMessage("");
    try {
      const response = await fetch("/api/team/sync", { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось обновить команду из Bitrix");
      setTeamRows(payload.team || []);
      setTeamLastSyncedAtText(payload.last_synced_at_text || "");
      setTeamMessage(payload.message || "Синхронизация команды завершена");
    } catch (error) {
      setTeamMessage(error instanceof Error ? error.message : "Не удалось обновить команду из Bitrix");
    } finally {
      setTeamLoading(false);
    }
  };

  const loadPromptHistory = async (key: PromptKey = promptTab) => {
    setPromptHistoryLoading(true);
    try {
      const response = await fetch(`/api/prompts/history?prompt_key=${encodeURIComponent(key)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить историю промта");
      setPromptHistory((current) => ({ ...current, [key]: payload.history || [] }));
    } catch (error) {
      setPromptMessage(error instanceof Error ? error.message : "Не удалось загрузить историю промта");
    } finally {
      setPromptHistoryLoading(false);
    }
  };

  const loadPrompts = async () => {
    try {
      const response = await fetch("/api/prompts");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить промты");
      const prompts = payload.prompts || {};
      setPromptTexts((current) => {
        const next = { ...current };
        (Object.keys(next) as PromptKey[]).forEach((key) => {
          if (prompts[key]?.text) next[key] = prompts[key].text;
        });
        return next;
      });
    } catch (error) {
      setPromptMessage(error instanceof Error ? error.message : "Не удалось загрузить промты");
    }
  };

  useEffect(() => {
    if (activeTab === "Сводная аналитика" && summaryTab === "reports" && tasksSubTab === "prompts") {
      void loadPrompts();
      void loadPromptHistory(promptTab);
    }
  }, [activeTab, summaryTab, tasksSubTab]);

  useEffect(() => {
    if (activeTab === "Сводная аналитика" && summaryTab === "reports" && tasksSubTab === "prompts") {
      void loadPromptHistory(promptTab);
    }
  }, [promptTab, activeTab, summaryTab, tasksSubTab]);

  const savePrompt = async () => {
    setPromptMessage("");
    setPromptSaving(true);
    try {
      const response = await fetch("/api/prompts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt_key: promptTab, prompt_text: promptTexts[promptTab] }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось сохранить промт");
      setPromptMessage(`Промт сохранен. Активная версия: ${payload.version}`);
      await loadPrompts();
      await loadPromptHistory(promptTab);
    } catch (error) {
      setPromptMessage(error instanceof Error ? error.message : "Не удалось сохранить промт");
    } finally {
      setPromptSaving(false);
    }
  };

  const usePromptVersion = (version: PromptVersion) => {
    setPromptTexts((current) => ({ ...current, [promptTab]: version.text }));
    setPromptMessage(`Версия ${version.version} загружена в редактор. Нажмите "Сохранить", чтобы сделать ее новой активной версией.`);
  };

  const deletePromptVersion = async (version: PromptVersion) => {
    if (version.is_active) {
      setPromptMessage("Активную версию удалить нельзя. Сначала сохраните новую версию, затем удалите старую.");
      return;
    }
    const confirmed = window.confirm(`Удалить версию ${version.version}? Это действие нельзя отменить.`);
    if (!confirmed) return;
    setPromptMessage("");
    try {
      const response = await fetch(`/api/prompts/${encodeURIComponent(version.id)}`, { method: "DELETE" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось удалить версию промта");
      setPromptMessage(`Версия ${version.version} удалена.`);
      await loadPromptHistory(promptTab);
    } catch (error) {
      setPromptMessage(error instanceof Error ? error.message : "Не удалось удалить версию промта");
    }
  };

  const exportTaskRegistry = async () => {
    setTaskRegistryLoading(true);
    setTaskRegistryMessage("");
    try {
      const params = new URLSearchParams({
        date_from: taskFilters.date_from,
        date_to: taskFilters.date_to,
      });
      const response = await fetch(`/api/registry/export?${params.toString()}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось подготовить JSON");
      setTaskRegistryDownloadUrl(payload.download_url || "");
      if (payload.download_url) window.location.href = payload.download_url;
    } catch (error) {
      setTaskRegistryMessage(error instanceof Error ? error.message : "Не удалось подготовить JSON");
    } finally {
      setTaskRegistryLoading(false);
    }
  };

  const loadChatRegistry = async () => {
    setChatRegistryLoading(true);
    setChatRegistryMessage("");
    try {
      const response = await fetch("/api/chats");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить реестр чатов");
      const rows: ChatRegistryRow[] = payload.chats || [];
      setChatRows(rows);
      setSelectedChat((current) => {
        if (!current) return current;
        return rows.find((row) => row.dialog_id === current.dialog_id) || current;
      });
      return rows;
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось загрузить реестр чатов");
      return [];
    } finally {
      setChatRegistryLoading(false);
    }
  };

  useEffect(() => {
    if (chatRows.length > 0 && typeof window !== "undefined") {
      window.localStorage.setItem(CHAT_REGISTRY_CACHE_KEY, JSON.stringify(chatRows));
    }
  }, [chatRows]);

  useEffect(() => {
    if (
      activeTab === "Сводная аналитика" &&
      summaryTab === "reports" &&
      tasksSubTab === "chats" &&
      !chatRegistryInitialLoaded
    ) {
      setChatRegistryInitialLoaded(true);
      void loadChatRegistry();
    }
  }, [activeTab, summaryTab, tasksSubTab, chatRegistryInitialLoaded]);

  const mergeChatDayIntoChat = (chat: ChatRegistryRow, payload: ChatDayPayload): ChatRegistryRow => {
    const dateValue = payload.date || chatSummaryDate;
    const [year, month, day] = dateValue.split("-");
    const dateText = year && month && day ? `${day}.${month}.${year}` : dateValue;
    const nextReport = {
      report_kind: "daily" as const,
      date: dateValue,
      date_text: dateText,
      title: `Сводка ${dateText}`,
      model: payload.report?.model || null,
      generated_at: payload.report?.generated_at || "",
      generated_at_text: payload.report?.generated_at || "",
      messages_count: payload.messages?.length ?? 0,
      text_status: payload.text_status,
      workflow_status: payload.text_status?.workflow_status,
      workflow_status_text: payload.text_status?.workflow_status_text,
    };
    const reports = [...(chat.reports || [])];
    const reportIndex = reports.findIndex((report) => (report.report_kind || "daily") === "daily" && report.date === dateValue);
    if (reportIndex >= 0) {
      reports[reportIndex] = { ...reports[reportIndex], ...nextReport };
    } else {
      reports.unshift(nextReport);
    }
    reports.sort((a, b) => {
      const dateCompare = b.date.localeCompare(a.date);
      if (dateCompare !== 0) return dateCompare;
      return (b.report_kind || "daily").localeCompare(a.report_kind || "daily");
    });
    return { ...chat, reports };
  };

  const applyChatDayPayload = (dialogId: string, payload: ChatDayPayload) => {
    setSelectedChat((current) => current && current.dialog_id === dialogId ? mergeChatDayIntoChat(current, payload) : current);
    setChatRows((rows) => rows.map((chat) => chat.dialog_id === dialogId ? mergeChatDayIntoChat(chat, payload) : chat));
  };

  const syncChatRegistry = async () => {
    setChatRegistryLoading(true);
    setChatRegistryMessage("");
    try {
      const response = await fetch("/api/chats/sync", { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось обновить чаты из Bitrix");
      setChatRows(payload.chats || []);
      setChatRegistryMessage(payload.message || "Синхронизация чатов завершена");
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось обновить чаты из Bitrix");
    } finally {
      setChatRegistryLoading(false);
    }
  };

  const syncChatRegistryPeriod = async () => {
    setChatRegistryLoading(true);
    setChatRegistryMessage("");
    try {
      const response = await fetch("/api/chats/daily-sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date_from: chatPeriodFrom, date_to: chatPeriodTo, generate_reports: false }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить сообщения за период");
      setChatRows(payload.chats || []);
      setChatRegistryMessage(payload.message || "Загрузка сообщений за период завершена");
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось загрузить сообщения за период");
    } finally {
      setChatRegistryLoading(false);
    }
  };

  const analyzeChatRegistryPeriod = async () => {
    if (chatActionDate > moscowTodayIso()) {
      setChatRegistryMessage("Нельзя формировать результаты за будущую дату.");
      return;
    }
    setChatRegistryLoading(true);
    setChatRegistryMessage("");
    try {
      const response = await fetch("/api/chats/daily-sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date_from: chatActionDate, date_to: chatActionDate, generate_reports: true }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось выполнить ежедневную аналитику чатов");
      await loadChatRegistry();
      setChatRegistryMessage(payload.message || "Ежедневная аналитика чатов завершена");
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось выполнить ежедневную аналитику чатов");
    } finally {
      setChatRegistryLoading(false);
    }
  };

  const loadChatOverallReport = async (dateValue = chatActionDate) => {
    setChatOverallLoading(true);
    try {
      const response = await fetch(`/api/chats/overall-daily-report?date=${encodeURIComponent(dateValue)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить сводный отчет по чатам");
      setChatOverallReport(payload.report || null);
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось загрузить сводный отчет по чатам");
    } finally {
      setChatOverallLoading(false);
    }
  };

  const dateFromIso = (value: string) => {
    const [year, month, day] = value.split("-").map(Number);
    return new Date(year, month - 1, day);
  };

  const isoFromDate = (value: Date) =>
    `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;

  const loadZoomCalls = async () => {
    setZoomCallsLoading(true);
    setZoomCallsMessage("");
    try {
      const payload = await fetchJsonSafe("/api/zoom-calls", undefined, 60000);
      setZoomCallsTree({
        years: payload.years || [],
        total: payload.total || 0,
        updated_at: payload.updated_at || null,
        updated_at_text: payload.updated_at_text || "",
      });
    } catch (error) {
      setZoomCallsMessage(error instanceof Error ? error.message : "Не удалось загрузить зум-созвоны.");
    } finally {
      setZoomCallsLoading(false);
    }
  };

  const syncZoomCalls = async () => {
    setZoomCallsLoading(true);
    setZoomCallsMessage("");
    try {
      const payload = await fetchJsonSafe("/api/zoom-calls/sync?from=2026-01-01", { method: "POST" }, 180000);
      if (payload.tree) {
        setZoomCallsTree({
          years: payload.tree.years || [],
          total: payload.tree.total || 0,
          updated_at: payload.tree.updated_at || null,
          updated_at_text: payload.tree.updated_at_text || "",
        });
      }
      const participantsWarning = payload.participant_errors?.length
        ? ` Участники по ${payload.participant_errors.length} созвонам недоступны в Zoom API, сохранен fallback.`
        : "";
      setZoomCallsMessage(
        `Записи Zoom подтянуты: ${payload.calls_synced || 0}; транскрибации: ${payload.transcript_files_synced || 0}.${participantsWarning}`,
      );
    } catch (error) {
      setZoomCallsMessage(error instanceof Error ? error.message : "Не удалось синхронизировать Zoom.");
    } finally {
      setZoomCallsLoading(false);
    }
  };

  const syncDriveCallTranscripts = async () => {
    setZoomCallsLoading(true);
    setZoomCallsMessage("");
    try {
      const payload = await fetchJsonSafe("/api/zoom-calls/sync-google-drive", { method: "POST" }, 600000);
      if (payload.tree) {
        setZoomCallsTree({
          years: payload.tree.years || [],
          total: payload.tree.total || 0,
          updated_at: payload.tree.updated_at || null,
          updated_at_text: payload.tree.updated_at_text || "",
        });
      }
      setZoomCallsMessage(
        `transcript.txt из Google Drive подтянуты: созвонов ${payload.calls_synced || 0}; участников ${payload.participants_synced || 0}; реплик ${payload.segments_synced || 0}; удалено ${payload.removed_calls || 0}.`,
      );
    } catch (error) {
      setZoomCallsMessage(error instanceof Error ? error.message : "Не удалось подтянуть transcript.txt из Google Drive.");
    } finally {
      setZoomCallsLoading(false);
    }
  };

  const openZoomCall = async (call: ZoomCall) => {
    setSelectedZoomCall(call);
    setZoomTranscriptVisible(false);
    setZoomCallDetailLoading(true);
    try {
      const payload = await fetchJsonSafe(`/api/zoom-calls/${encodeURIComponent(call.id)}`, undefined, 180000);
      setSelectedZoomCall(payload.call || call);
    } catch (error) {
      setZoomCallsMessage(error instanceof Error ? error.message : "Не удалось открыть отчет о созвоне.");
    } finally {
      setZoomCallDetailLoading(false);
    }
  };

  const generateZoomCallReport = async (call: ZoomCall) => {
    setZoomCallDetailLoading(true);
    setZoomCallsMessage("");
    try {
      const payload = await fetchJsonSafe(`/api/zoom-calls/${encodeURIComponent(call.id)}/report`, { method: "POST" }, 180000);
      const updatedCall = payload.call || call;
      setSelectedZoomCall(updatedCall);
      setZoomCallsTree((current) => ({
        ...current,
        years: current.years.map((year) => ({
          ...year,
          months: year.months.map((month) => ({
            ...month,
            dates: month.dates.map((day) => ({
              ...day,
              calls: day.calls.map((item) => (item.id === call.id ? updatedCall : item)),
            })),
          })),
        })),
      }));
      setZoomCallsMessage(payload.message || "Отчет по созвону сформирован.");
    } catch (error) {
      setZoomCallsMessage(error instanceof Error ? error.message : "Не удалось сформировать отчет по созвону.");
    } finally {
      setZoomCallDetailLoading(false);
    }
  };

  const deleteZoomCallReport = async (call: ZoomCall) => {
    const confirmed = window.confirm("Удалить отчет по созвону? Транскрибация и сам созвон останутся.");
    if (!confirmed) return;
    setZoomCallDetailLoading(true);
    setZoomCallsMessage("");
    try {
      const payload = await fetchJsonSafe(`/api/zoom-calls/${encodeURIComponent(call.id)}/report`, { method: "DELETE" }, 60000);
      const updatedCall = payload.call || { ...call, analytical_note: "" };
      setSelectedZoomCall(updatedCall);
      setZoomCallsTree((current) => ({
        ...current,
        years: current.years.map((year) => ({
          ...year,
          months: year.months.map((month) => ({
            ...month,
            dates: month.dates.map((day) => ({
              ...day,
              calls: day.calls.map((item) => (item.id === call.id ? { ...item, analytical_note: "" } : item)),
            })),
          })),
        })),
      }));
      setZoomCallsMessage(payload.message || "Отчет по созвону удален.");
      setZoomTranscriptVisible(false);
    } catch (error) {
      setZoomCallsMessage(error instanceof Error ? error.message : "Не удалось удалить отчет по созвону.");
    } finally {
      setZoomCallDetailLoading(false);
    }
  };

  const extractZoomOperationalSection = (note: string) => {
    const lines = String(note || "").trim().split(/\r?\n/);
    const sectionLines: string[] = [];
    let collecting = false;
    for (const raw of lines) {
      const line = raw.trim();
      if (!collecting) {
        if (/^\s*(?:4[.)]|IV[.)]?)\s*\**\s*Операционные задачи/i.test(line)) {
          collecting = true;
        }
        continue;
      }
      if (/^\s*(?:[5-9][.)]|1[0-2][.)]|V[.)]?|VI[.)]?|VII[.)]?|VIII[.)]?|IX[.)]?)\s+\**\s*(?:Поведенческие|Риски|Проблемы|Блокеры|Решения|Итоги|Вывод|Следующие|Контроль|Рекомендации)/i.test(line)) break;
      sectionLines.push(raw.trimEnd());
    }
    return sectionLines.join("\n").trim();
  };

  const cleanZoomOperationalSection = (section: string) => {
    return parseLocalZoomOperationalTasks(section)
      .map((task, index) => formatLocalZoomOperationalTask(task, index + 1))
      .join("\n");
  };

  const splitLocalZoomOperationalItems = (section: string) => {
    const items: string[] = [];
    String(section || "")
      .split(/\r?\n/)
      .map((raw) => raw.trim())
      .filter(Boolean)
      .forEach((line) => {
        const markers = [...line.matchAll(/(?:^|\s)(\d+)[).]\s+/g)];
        if (markers.length <= 1) {
          items.push(line);
          return;
        }
        markers.forEach((marker, index) => {
          const start = marker.index || 0;
          const end = index + 1 < markers.length ? markers[index + 1].index || line.length : line.length;
          items.push(line.slice(start, end).trim());
        });
      });
    return items;
  };

  const sentenceCaseLocal = (value: string) => {
    const text = value.trim().replace(/\.$/, "");
    return text ? `${text.charAt(0).toUpperCase()}${text.slice(1)}` : text;
  };

  const parseLocalZoomOperationalTasks = (section: string) => splitLocalZoomOperationalItems(section)
    .map((raw, index) => {
      let body = raw.replace(/^\s*\d+[.)]\s*/, "").trim();
      let assigneeName = "Требует назначения";
      const strictAssignee = body.match(/^Ответственный:\s*(.*?)\.\s*(.*)$/i);
      if (strictAssignee) {
        assigneeName = strictAssignee[1].trim();
        body = strictAssignee[2].trim();
      } else if (body.includes("—")) {
        const parts = body.split("—");
        assigneeName = parts.shift()?.trim() || assigneeName;
        body = parts.join("—").trim();
      } else if (body.includes(" - ")) {
        const parts = body.split(" - ");
        assigneeName = parts.shift()?.trim() || assigneeName;
        body = parts.join(" - ").trim();
      }
      body = body.replace(/^Задача:\s*/i, "").trim();
      const labels = [...body.matchAll(/(Срок|Критерий(?:\s+результата)?|Статус|Источник)\s*:/gi)];
      const taskText = (labels.length ? body.slice(0, labels[0].index) : body).trim().replace(/\.$/, "");
      const parts: Record<string, string> = {};
      labels.forEach((label, labelIndex) => {
        const key = label[1].toLowerCase().replace(/\s+/g, "_");
        const start = (label.index || 0) + label[0].length;
        const end = labelIndex + 1 < labels.length ? labels[labelIndex + 1].index || body.length : body.length;
        parts[key] = body.slice(start, end).trim().replace(/\.$/, "");
      });
      if (!taskText) return null;
      return {
        number: index + 1,
        assignee_name: assigneeName,
        task_text: sentenceCaseLocal(taskText),
        deadline_text: parts["срок"] || "срок не указан",
        result_criteria: parts["критерий_результата"] || parts["критерий"] || "",
      };
    })
    .filter(Boolean) as Array<{
      number: number;
      assignee_name: string;
      task_text: string;
      deadline_text: string;
      result_criteria: string;
    }>;

  const formatLocalZoomOperationalTask = (
    task: { task_text: string; deadline_text: string; result_criteria: string },
    number: number,
  ) => {
    const criteria = task.result_criteria ? ` Критерий результата: ${task.result_criteria.replace(/\.$/, "")}.` : "";
    return `${number}. ${task.task_text.replace(/\.$/, "")}.${criteria} Дедлайн - ${task.deadline_text.replace(/\.$/, "")}.`;
  };

  const zoomCallDispatchDeadline = (call: ZoomCall) => {
    const dateIso = call.date || call.start_time_msk.slice(0, 10) || isoFromDate(moscowNow());
    const [year, month, day] = dateIso.split("-").map(Number);
    const label = `${String(day).padStart(2, "0")}.${String(month).padStart(2, "0")}.${year} 19:00 МСК`;
    return { deadline: `${dateIso}T19:00:00+03:00`, deadline_text: label };
  };

  const personNamesMatch = (left?: string | null, right?: string | null) => {
    const leftText = String(left || "").trim().toLowerCase();
    const rightText = String(right || "").trim().toLowerCase();
    if (!leftText || !rightText) return false;
    if (leftText === rightText) return true;
    const aliases: Record<string, string> = {
      "настя": "анастасия",
      "дима": "дмитрий",
      "саша": "александр",
      "женя": "евгений",
    };
    const nameTokens = (value: string) => [...value.replaceAll("ё", "е").matchAll(/[a-zа-я0-9]+/gi)]
      .map((match) => aliases[match[0]] || match[0]);
    const leftTokens = nameTokens(leftText);
    const rightTokens = nameTokens(rightText);
    const smaller = leftTokens.length <= rightTokens.length ? leftTokens : rightTokens;
    const larger = leftTokens.length <= rightTokens.length ? rightTokens : leftTokens;
    return smaller.length > 0 && smaller.every((token) => (
      token.length === 1 ? larger.some((candidate) => candidate.startsWith(token)) : larger.includes(token)
    ));
  };

  const buildLocalZoomOperationalPreview = async (call: ZoomCall): Promise<ZoomOperationalTasksPreview> => {
    const operationalSection = extractZoomOperationalSection(call.analytical_note);
    if (!operationalSection) throw new Error("В отчете нет раздела «4. Операционные задачи».");
    const operationalTasks = parseLocalZoomOperationalTasks(operationalSection);
    const cleanedSection = operationalTasks.map((task, index) => formatLocalZoomOperationalTask(task, index + 1)).join("\n");
    if (!cleanedSection) throw new Error("В разделе «4. Операционные задачи» нет задач для отправки.");
    let members = teamRows;
    if (!members.length) members = await loadTeam();
    const toMoscowHHMM = (iso: string | undefined | null): string => {
      if (!iso) return "";
      const d = new Date(iso);
      if (isNaN(d.getTime())) return "";
      return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow" });
    };
    const periodText = call.time_text && call.time_text.includes("-")
      ? call.time_text.split(" ")[0]
      : (() => {
          const startHhmm = toMoscowHHMM(call.start_time_msk);
          const endHhmm = toMoscowHHMM(call.end_time_msk);
          if (startHhmm && endHhmm) return `${startHhmm}-${endHhmm}`;
          return startHhmm || "созвон";
        })();
    const deadline = zoomCallDispatchDeadline(call);
    const title = `Итоги созвона ${periodText}`.trim();
    const taskCardMap = new Map<string, NonNullable<ZoomOperationalTasksPreview["task_cards"]>[number]>();
    operationalTasks.forEach((task) => {
      const member = members.find((teamMember) => teamMember.user_id && teamMember.name && personNamesMatch(teamMember.name, task.assignee_name));
      const recipient = member?.user_id ? { name: member.name || task.assignee_name, user_id: member.user_id } : null;
      const key = recipient ? `user:${recipient.user_id}` : `name:${task.assignee_name}`;
      const card = taskCardMap.get(key) || {
        recipient,
        assignee_name: task.assignee_name,
        title,
        description: "",
        deadline: deadline.deadline,
        deadline_text: deadline.deadline_text,
        tasks: [],
      };
      card.tasks = [...(card.tasks || []), task as unknown as Record<string, unknown>];
      taskCardMap.set(key, card);
    });
    const taskCards = [...taskCardMap.values()].map((card) => ({
      ...card,
      description: [
        ZOOM_OPERATIONAL_TASKS_DISPATCH_INTRO,
        "",
        (card.tasks || []).map((task, index) => formatLocalZoomOperationalTask(task as any, index + 1)).join("\n"),
      ].join("\n"),
    }));
    const unmatchedAssignees = taskCards.filter((card) => !card.recipient).map((card) => card.assignee_name);
    const recipients = taskCards
      .map((card) => card.recipient)
      .filter(Boolean) as Array<{ name: string; user_id: number }>;
    if (!recipients.length) throw new Error("Не удалось сопоставить ответственных из раздела «4. Операционные задачи» с оргструктурой (team).");
    return {
      recipients,
      task_cards: taskCards,
      unmatched_assignees: unmatchedAssignees,
      title,
      description: [
        ZOOM_OPERATIONAL_TASKS_DISPATCH_INTRO,
        "",
        "Выделенные задачи с дедлайнами:",
        cleanedSection,
      ].join("\n"),
      deadline: deadline.deadline,
      deadline_text: deadline.deadline_text,
      operational_section: cleanedSection,
    };
  };

  const previewZoomOperationalTasks = async (call: ZoomCall) => {
    setZoomCallDetailLoading(true);
    setZoomCallsMessage("");
    try {
      let payload: any = null;
      try {
        payload = await fetchJsonSafe(
          `/api/zoom-calls/${encodeURIComponent(call.id)}/dispatch-operational-tasks/preview`,
          undefined,
          60000,
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : "";
        if (!message.includes("(404)")) throw error;
      }
      const preview = payload?.result || await buildLocalZoomOperationalPreview(call);
      setZoomDispatchCall(call);
      setZoomDispatchPreview(preview);
    } catch (error) {
      setZoomCallsMessage(error instanceof Error ? error.message : "Не удалось подготовить список отправки.");
    } finally {
      setZoomCallDetailLoading(false);
    }
  };

  const dispatchZoomOperationalTasks = async () => {
    if (!zoomDispatchCall) return;
    const call = zoomDispatchCall;
    setZoomCallDetailLoading(true);
    setZoomCallsMessage("");
    try {
      const payload = await fetchJsonSafe(
        `/api/zoom-calls/${encodeURIComponent(call.id)}/dispatch-operational-tasks`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ preview: zoomDispatchPreview }),
        },
        180000,
      );
      const updatedCall = payload.call || call;
      setSelectedZoomCall(updatedCall);
      setZoomCallsTree((current) => ({
        ...current,
        years: current.years.map((year) => ({
          ...year,
          months: year.months.map((month) => ({
            ...month,
            dates: month.dates.map((day) => ({
              ...day,
              calls: day.calls.map((item) => (item.id === call.id ? updatedCall : item)),
            })),
          })),
        })),
      }));
      setZoomCallsMessage(payload.message || "Задачи отправлены исполнителям.");
      setZoomDispatchPreview(null);
      setZoomDispatchCall(null);
    } catch (error) {
      setZoomCallsMessage(error instanceof Error ? error.message : "Не удалось отправить задачи исполнителям.");
    } finally {
      setZoomCallDetailLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === "Зум-разговоры") {
      void loadZoomCalls();
    }
  }, [activeTab]);

  useEffect(() => {
    if (!selectedZoomCall) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedZoomCall(null);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [selectedZoomCall]);

  const weekBoundsFromIso = (value: string) => {
    const date = dateFromIso(value);
    const day = date.getDay() || 7;
    const periodStart = new Date(date);
    periodStart.setDate(date.getDate() - day + 1);
    const periodEnd = new Date(periodStart);
    periodEnd.setDate(periodStart.getDate() + 6);
    return {
      period_start: isoFromDate(periodStart),
      period_end: isoFromDate(periodEnd),
    };
  };

  const loadChatWeeklyHistory = async (openModal = false) => {
    setChatWeeklyHistoryLoading(true);
    setChatRegistryMessage("");
    try {
      const response = await fetch("/api/chats/overall-weekly-reports?limit=30");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить историю недельных сводок");
      const reports = payload.reports || [];
      setChatWeeklyHistory(reports);
      setSelectedChatWeeklyReport((current) => {
        if (!reports.length) return null;
        if (!current) return reports[0];
        return reports.find((report: ChatOverallWeeklyReport) => report.report_id === current.report_id) || reports[0];
      });
      if (openModal) setIsChatWeeklyHistoryOpen(true);
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось загрузить историю недельных сводок");
    } finally {
      setChatWeeklyHistoryLoading(false);
    }
  };

  const loadChatOverallDailyHistory = async () => {
    setChatWeeklyHistoryLoading(true);
    setChatRegistryMessage("");
    try {
      const response = await fetch("/api/chats/overall-daily-reports?limit=30");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить историю ежедневных сводок");
      const reports = payload.reports || [];
      setChatOverallDailyHistory(reports);
      setSelectedChatOverallDailyReport((current) => {
        if (!reports.length) return null;
        if (!current) return reports[0];
        return reports.find((report: ChatOverallDailyReport) => report.report_id === current.report_id) || reports[0];
      });
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось загрузить историю ежедневных сводок");
    } finally {
      setChatWeeklyHistoryLoading(false);
    }
  };

  const openChatReportsHistory = async () => {
    setIsChatWeeklyHistoryOpen(true);
    await Promise.all([loadChatWeeklyHistory(false), loadChatOverallDailyHistory()]);
  };

  const generateChatWeeklyReport = async () => {
    const period = weekBoundsFromIso(chatActionDate);
    setChatHistoryKind("weekly");
    setChatWeeklyHistoryLoading(true);
    setChatRegistryMessage("");
    try {
      const response = await fetch("/api/chats/overall-weekly-report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(period),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось сформировать недельную сводку по чатам");
      const report = payload.report || null;
      if (report) {
        setSelectedChatWeeklyReport(report);
        setChatWeeklyHistory((current) => [report, ...current.filter((item) => item.report_id !== report.report_id)]);
      }
      setIsChatWeeklyHistoryOpen(true);
      setChatRegistryMessage(payload.message || "Недельная сводка по чатам сформирована");
      await loadChatWeeklyHistory(false);
      await loadChatRegistry();
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось сформировать недельную сводку по чатам");
    } finally {
      setChatWeeklyHistoryLoading(false);
    }
  };

  const generateChatOverallReport = async () => {
    if (chatActionDate > moscowTodayIso()) {
      setChatRegistryMessage("Нельзя формировать сводный отчет за будущую дату.");
      return;
    }
    setChatOverallLoading(true);
    setChatRegistryMessage("");
    try {
      const response = await fetch("/api/chats/overall-daily-report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: chatActionDate }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось сформировать сводный отчет по чатам");
      setChatOverallReport(payload.report || null);
      setChatRegistryMessage(payload.message || "Сводный отчет по чатам сформирован");
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось сформировать сводный отчет по чатам");
    } finally {
      setChatOverallLoading(false);
    }
  };

  const processChatRegistryImages = async () => {
    if (chatActionDate > moscowTodayIso()) {
      setChatRegistryMessage("Нельзя обрабатывать будущую дату.");
      return;
    }
    setChatRegistryLoading(true);
    setChatRegistryMessage("");
    try {
      const response = await fetch("/api/chats/images/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date_from: chatActionDate, date_to: chatActionDate }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось обработать картинки");
      await loadChatRegistry();
      const ocr = payload.ocr || {};
      setChatRegistryMessage(`Обработка картинок за ${formatIsoDateRu(chatActionDate)} завершена: OCR ${ocr.processed ?? 0}, пропущено ${ocr.skipped ?? 0}, ошибок ${(ocr.errors || []).length}.`);
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось обработать картинки");
    } finally {
      setChatRegistryLoading(false);
    }
  };

  const toggleChatExcluded = async (chat: ChatRegistryRow) => {
    setChatContextMenu(null);
    setChatRegistryLoading(true);
    setChatRegistryMessage("");
    try {
      const nextExcluded = !chat.is_excluded;
      const response = await fetch(`/api/chats/${encodeURIComponent(chat.dialog_id)}/exclude`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_excluded: nextExcluded }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось изменить настройку чата");
      setChatRows(payload.chats || []);
      setChatRegistryMessage(payload.message || (nextExcluded ? "Чат исключен" : "Чат включен"));
    } catch (error) {
      setChatRegistryMessage(error instanceof Error ? error.message : "Не удалось изменить настройку чата");
    } finally {
      setChatRegistryLoading(false);
    }
  };

  type ChatReportListItem = NonNullable<ChatRegistryRow["reports"]>[number];
  const sortedChatReports = (reports: ChatRegistryRow["reports"] = []): ChatReportListItem[] => {
    const items = [...reports];
    const daily = items
      .filter((item) => (item.report_kind || "daily") !== "weekly")
      .sort((left, right) => (right.date || "").localeCompare(left.date || ""));
    const weekly = items
      .filter((item) => (item.report_kind || "daily") === "weekly")
      .sort((left, right) => {
        const rightAnchor = right.period_end || right.date || "";
        const leftAnchor = left.period_end || left.date || "";
        return rightAnchor.localeCompare(leftAnchor);
      });

    const weeklyByEndDate = new Map<string, ChatReportListItem[]>();
    for (const weeklyItem of weekly) {
      const endDate = weeklyItem.period_end || weeklyItem.date || "";
      if (!endDate) continue;
      const bucket = weeklyByEndDate.get(endDate) || [];
      bucket.push(weeklyItem);
      weeklyByEndDate.set(endDate, bucket);
    }

    const timeline: ChatReportListItem[] = [];
    for (const dailyItem of daily) {
      const dateValue = dailyItem.date || "";
      const matchingWeekly = weeklyByEndDate.get(dateValue) || [];
      for (const weeklyItem of matchingWeekly) timeline.push(weeklyItem);
      weeklyByEndDate.delete(dateValue);
      timeline.push(dailyItem);
    }

    const orphanWeekly = [...weeklyByEndDate.entries()]
      .sort((left, right) => right[0].localeCompare(left[0]))
      .flatMap(([, bucket]) => bucket);

    return [...orphanWeekly, ...timeline];
  };

  const buildOverallChatHistoryTimeline = (
    dailyReports: ChatOverallDailyReport[] = [],
    weeklyReports: ChatOverallWeeklyReport[] = [],
  ): ChatOverallTimelineItem[] => {
    const daily = [...dailyReports]
      .sort((left, right) => (right.report_date || "").localeCompare(left.report_date || ""))
      .map((item) => ({ ...item, report_kind: "daily" as const, anchor_date: item.report_date }));
    const weekly = [...weeklyReports]
      .sort((left, right) => (right.period_end || "").localeCompare(left.period_end || ""))
      .map((item) => ({ ...item, report_kind: "weekly" as const, anchor_date: item.period_end }));

    const weeklyBySunday = new Map<string, ChatOverallTimelineItem[]>();
    for (const weeklyItem of weekly) {
      const sunday = weeklyItem.period_end || "";
      if (!sunday) continue;
      const bucket = weeklyBySunday.get(sunday) || [];
      bucket.push(weeklyItem);
      weeklyBySunday.set(sunday, bucket);
    }

    const timeline: ChatOverallTimelineItem[] = [];
    for (const dailyItem of daily) {
      const matchingWeekly = weeklyBySunday.get(dailyItem.report_date) || [];
      for (const weeklyItem of matchingWeekly) timeline.push(weeklyItem);
      weeklyBySunday.delete(dailyItem.report_date);
      timeline.push(dailyItem);
    }

    const orphanWeekly = [...weeklyBySunday.entries()]
      .sort((left, right) => right[0].localeCompare(left[0]))
      .flatMap(([, bucket]) => bucket);

    return [...orphanWeekly, ...timeline];
  };

  const openChatSummary = async (chat: ChatRegistryRow) => {
    setSelectedChat(chat);
    setIsChatDayModalOpen(false);
    setChatDay(null);
    setChatDayMessage("");
    const reportDate = sortedChatReports(chat.reports)[0]?.date || chatSummaryDate;
    setChatSummaryDate(reportDate);
    setChatPeriodFrom(reportDate);
    setChatPeriodTo(reportDate);
  };

  const openChatDaySummary = async (chat: ChatRegistryRow, report: ChatReportListItem) => {
    const dateValue = report.date;
    setSelectedChat(chat);
    setChatSummaryDate(dateValue);
    setChatPeriodFrom((report.report_kind || "daily") === "weekly" && report.period_start ? report.period_start : dateValue);
    setChatPeriodTo((report.report_kind || "daily") === "weekly" && report.period_end ? report.period_end : dateValue);
    setIsChatDayModalOpen(true);
    setChatDayMessage("");
    if ((report.report_kind || "daily") === "weekly" && report.period_start && report.period_end) {
      await loadChatWeeklyReportDetail(chat, report);
      return;
    }
    await loadChatDay(chat.dialog_id, dateValue);
  };

  const loadChatWeeklyReportDetail = async (chat: ChatRegistryRow, report: ChatReportListItem) => {
    if (!report.period_start || !report.period_end) return;
    setChatDayLoading(true);
    setChatDayMessage("");
    try {
      const params = new URLSearchParams({ period_start: report.period_start, period_end: report.period_end });
      const response = await fetch(`/api/chats/${encodeURIComponent(chat.dialog_id)}/weekly-report?${params.toString()}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить недельный отчет чата");
      let weeklyReport = payload.report;
      if (!weeklyReport) {
        const createResponse = await fetch(`/api/chats/${encodeURIComponent(chat.dialog_id)}/weekly-report`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ period_start: report.period_start, period_end: report.period_end }),
        });
        const createPayload = await createResponse.json();
        if (!createResponse.ok) throw new Error(createPayload.error || "Не удалось сформировать недельный отчет чата");
        weeklyReport = createPayload.report;
        setChatDayMessage(createPayload.message || "Недельный отчет чата сформирован");
        const rows = await loadChatRegistry();
        const updatedChat = rows.find((row: ChatRegistryRow) => row.dialog_id === chat.dialog_id);
        if (updatedChat) setSelectedChat(updatedChat);
      }
      weeklyReport = weeklyReport || report;
      const periodParams = new URLSearchParams({ date_from: report.period_start, date_to: report.period_end, date: report.period_end });
      const periodResponse = await fetch(`/api/chats/${encodeURIComponent(chat.dialog_id)}/day?${periodParams.toString()}`);
      const periodPayload = await periodResponse.json();
      if (!periodResponse.ok) throw new Error(periodPayload.error || "Не удалось загрузить переписку за неделю");
      const weeklyMessages = Number(weeklyReport.messages_count ?? report.messages_count ?? 0);
      const weeklySummary = String(weeklyReport.summary ?? report.summary ?? "");
      const isWeeklyPending = weeklyMessages === 0 || weeklySummary.toLowerCase().includes("заглушка");
      const statsText = [
        `Период: ${weeklyReport.period_text || report.period_text || ""}`,
        `Дней в неделе: ${weeklyReport.days_count ?? report.days_count ?? 7}`,
        `Дневных сводок: ${weeklyReport.daily_reports_count ?? report.daily_reports_count ?? 0}`,
        `Сообщений: ${weeklyReport.messages_count ?? report.messages_count ?? 0}`,
        `Результатов: ${weeklyReport.results_count ?? report.results_count ?? 0}`,
        `Задач/обязательств: ${weeklyReport.commitments_count ?? report.commitments_count ?? 0}`,
        `Следующих шагов: ${weeklyReport.next_steps_count ?? report.next_steps_count ?? 0}`,
        `Рисков: ${weeklyReport.risks_count ?? report.risks_count ?? 0}`,
        `Обновлений целей: ${weeklyReport.goal_updates_count ?? report.goal_updates_count ?? 0}`,
      ].join("\n");
      const periodTextStatus = periodPayload.text_status || {};
      const normalizedWeeklyReportText = normalizeWeeklyReportText(
        weeklyReport,
        weeklyReport.period_text || report.period_text || periodPayload.period_text || "",
      );
      setChatDay({
        chat: { dialog_id: chat.dialog_id, title: chat.title },
        date: weeklyReport.date || report.date,
        date_from: report.period_start,
        date_to: report.period_end,
        period_text: weeklyReport.period_text || report.period_text || periodPayload.period_text || "",
        messages: periodPayload.messages || [],
        transcript: periodPayload.transcript || statsText,
        text_status: {
          status: isWeeklyPending ? "needs_report" : "done",
          status_text: isWeeklyPending ? "Не обработан" : "Недельный отчет сформирован",
          image_files: periodTextStatus.image_files ?? 0,
          ocr_success: periodTextStatus.ocr_success ?? 0,
          ocr_pending: periodTextStatus.ocr_pending ?? 0,
          ocr_errors: periodTextStatus.ocr_errors ?? 0,
          is_processed: !isWeeklyPending,
          workflow_status: isWeeklyPending ? "needs_report" : "report_formed",
          workflow_status_text: isWeeklyPending ? "Не обработан" : "Недельный отчет сформирован",
        },
        report: {
          report_text: normalizedWeeklyReportText || "Текст недельного отчета не сохранен.",
          model: weeklyReport.raw_json?.model || "weekly",
          generated_at: weeklyReport.generated_at_text || report.generated_at_text || "",
        },
        weekly_report: {
          report_id: weeklyReport.report_id,
          period_start: report.period_start,
          period_end: report.period_end,
          period_text: weeklyReport.period_text || report.period_text || "",
          days_count: weeklyReport.days_count ?? report.days_count ?? 7,
          daily_reports_count: weeklyReport.daily_reports_count ?? report.daily_reports_count ?? 0,
          messages_count: weeklyReport.messages_count ?? report.messages_count ?? 0,
          goals_created_count: weeklyReport.goals_created_count ?? report.goals_created_count ?? 0,
          goal_updates_count: weeklyReport.goal_updates_count ?? report.goal_updates_count ?? 0,
          commitments_count: weeklyReport.commitments_count ?? report.commitments_count ?? 0,
          results_count: weeklyReport.results_count ?? report.results_count ?? 0,
          next_steps_count: weeklyReport.next_steps_count ?? report.next_steps_count ?? 0,
          risks_count: weeklyReport.risks_count ?? report.risks_count ?? 0,
          blockers_count: weeklyReport.blockers_count ?? report.blockers_count ?? 0,
          unresolved_questions_count: weeklyReport.unresolved_questions_count ?? report.unresolved_questions_count ?? 0,
          done_goal_updates_count: weeklyReport.done_goal_updates_count ?? 0,
          high_risk_goal_updates_count: weeklyReport.high_risk_goal_updates_count ?? 0,
          summary: weeklyReport.summary ?? report.summary ?? null,
          dynamics_summary: weeklyReport.dynamics_summary ?? null,
          positives_summary: weeklyReport.positives_summary ?? null,
          problems_summary: weeklyReport.problems_summary ?? null,
          recommendations: weeklyReport.recommendations ?? null,
        },
      });
    } catch (error) {
      setChatDayMessage(error instanceof Error ? error.message : "Не удалось загрузить недельный отчет чата");
    } finally {
      setChatDayLoading(false);
    }
  };

  const loadChatDay = async (dialogId = selectedChat?.dialog_id, dateValue = chatSummaryDate) => {
    if (!dialogId) return;
    setChatDayLoading(true);
    setChatDayMessage("");
    try {
      const response = await fetch(`/api/chats/${encodeURIComponent(dialogId)}/day?date=${encodeURIComponent(dateValue)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить сводку чата");
      setChatDay(payload);
    } catch (error) {
      setChatDayMessage(error instanceof Error ? error.message : "Не удалось загрузить сводку чата");
    } finally {
      setChatDayLoading(false);
    }
  };

  const syncChatMessages = async () => {
    if (!selectedChat) return;
    setChatDayLoading(true);
    setChatDayMessage("");
    try {
      const response = await fetch(`/api/chats/${encodeURIComponent(selectedChat.dialog_id)}/messages/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date_from: chatPeriodFrom, date_to: chatPeriodTo }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось загрузить сообщения");
      setChatSummaryDate(payload.date || chatPeriodTo);
      setChatDay(payload);
      setIsChatDayModalOpen(true);
      applyChatDayPayload(selectedChat.dialog_id, payload);
      const byDate = payload.synced_by_date && typeof payload.synced_by_date === "object"
        ? Object.entries(payload.synced_by_date)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([dateValue, count]) => `${formatIsoDateRu(dateValue)}: ${count}`)
            .join("; ")
        : "";
      setChatDayMessage(
        `${payload.message || `Загружено сообщений: ${payload.synced_count ?? payload.messages?.length ?? 0}`}${byDate ? ` (${byDate})` : ""}`,
      );
      void loadChatRegistry();
    } catch (error) {
      setChatDayMessage(error instanceof Error ? error.message : "Не удалось загрузить сообщения");
    } finally {
      setChatDayLoading(false);
    }
  };

  const processSelectedChatImages = async () => {
    if (!selectedChat) return;
    if (chatSummaryDate > moscowTodayIso()) {
      setChatDayMessage("Нельзя обрабатывать будущую дату.");
      return;
    }
    setChatDayLoading(true);
    setChatDayMessage("");
    try {
      const response = await fetch(`/api/chats/${encodeURIComponent(selectedChat.dialog_id)}/images/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: chatSummaryDate }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось обработать картинки");
      setChatDay(payload);
      applyChatDayPayload(selectedChat.dialog_id, payload);
      setChatDayMessage(payload.message || "Обработка картинок завершена");
      const rows = await loadChatRegistry();
      const updatedChat = rows.find((chat: ChatRegistryRow) => chat.dialog_id === selectedChat.dialog_id);
      if (updatedChat) setSelectedChat(updatedChat);
    } catch (error) {
      setChatDayMessage(error instanceof Error ? error.message : "Не удалось обработать картинки");
    } finally {
      setChatDayLoading(false);
    }
  };

  const generateAllChatReports = async () => {
    if (!selectedChat) return;
    if (chatSummaryDate > moscowTodayIso()) {
      setChatDayMessage("Нельзя формировать результат за будущую дату.");
      return;
    }
    setChatDayLoading(true);
    setChatDayMessage("");
    try {
      const response = await fetch("/api/chats/daily-sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date_from: chatSummaryDate, date_to: chatSummaryDate, generate_reports: true }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось сформировать ИИ-отчеты по всем чатам");
      const rows = await loadChatRegistry();
      const updatedChat = rows.find((chat: ChatRegistryRow) => chat.dialog_id === selectedChat.dialog_id);
      if (updatedChat) {
        setSelectedChat(updatedChat);
        await loadChatDay(updatedChat.dialog_id, chatSummaryDate);
      }
      const errorsCount = Array.isArray(payload.errors) ? payload.errors.length : 0;
      setChatDayMessage(
        payload.message ||
          `Отчеты по всем чатам сформированы: ${payload.reports_generated ?? 0}. Ошибок: ${errorsCount}.`,
      );
    } catch (error) {
      setChatDayMessage(error instanceof Error ? error.message : "Не удалось сформировать ИИ-отчеты по всем чатам");
    } finally {
      setChatDayLoading(false);
    }
  };

  const deleteSelectedChatDailyReport = async () => {
    if (!selectedChat || !chatDay?.report || chatDay.weekly_report) return;
    const confirmed = window.confirm(`Удалить ежедневный отчет по чату за ${formatIsoDateRu(chatSummaryDate)}?`);
    if (!confirmed) return;
    setChatDayLoading(true);
    setChatDayMessage("");
    try {
      const response = await fetch(
        `/api/chats/${encodeURIComponent(selectedChat.dialog_id)}/report?date=${encodeURIComponent(chatSummaryDate)}`,
        { method: "DELETE" },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось удалить отчет по чату");
      setChatDay(payload);
      applyChatDayPayload(selectedChat.dialog_id, payload);
      setChatDayMessage(payload.message || "Ежедневный отчет по чату удален");
      const rows = await loadChatRegistry();
      const updatedChat = rows.find((chat: ChatRegistryRow) => chat.dialog_id === selectedChat.dialog_id);
      if (updatedChat) setSelectedChat(updatedChat);
    } catch (error) {
      setChatDayMessage(error instanceof Error ? error.message : "Не удалось удалить отчет по чату");
    } finally {
      setChatDayLoading(false);
    }
  };

  const deleteSelectedChatWeeklyReport = async () => {
    if (!selectedChat || !chatDay?.report || !chatDay.weekly_report) return;
    const periodStart = chatDay.weekly_report.period_start || chatPeriodFrom;
    const periodEnd = chatDay.weekly_report.period_end || chatPeriodTo;
    const periodText = chatDay.weekly_report.period_text || `${formatIsoDateRu(periodStart)} - ${formatIsoDateRu(periodEnd)}`;
    const confirmed = window.confirm(`Удалить недельный отчет по чату за ${periodText}?`);
    if (!confirmed) return;
    setChatDayLoading(true);
    setChatDayMessage("");
    try {
      const params = new URLSearchParams({ period_start: periodStart, period_end: periodEnd });
      const response = await fetch(
        `/api/chats/${encodeURIComponent(selectedChat.dialog_id)}/weekly-report?${params.toString()}`,
        { method: "DELETE" },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось удалить недельный отчет по чату");
      setChatDay(payload);
      setChatDayMessage(payload.message || "Недельный отчет по чату удален");
      const rows = await loadChatRegistry();
      const updatedChat = rows.find((chat: ChatRegistryRow) => chat.dialog_id === selectedChat.dialog_id);
      if (updatedChat) setSelectedChat(updatedChat);
    } catch (error) {
      setChatDayMessage(error instanceof Error ? error.message : "Не удалось удалить недельный отчет по чату");
    } finally {
      setChatDayLoading(false);
    }
  };

  const generateSelectedChatWeeklyReport = async () => {
    if (!selectedChat) return;
    const period = weekBoundsFromIso(chatSummaryDate);
    setChatDayLoading(true);
    setChatDayMessage("");
    try {
      const response = await fetch(`/api/chats/${encodeURIComponent(selectedChat.dialog_id)}/weekly-report`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...period, force: true }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Не удалось сформировать недельный отчет чата");
      setChatDayMessage(payload.message || "Недельный отчет чата сформирован");
      const rows = await loadChatRegistry();
      const updatedChat = rows.find((chat: ChatRegistryRow) => chat.dialog_id === selectedChat.dialog_id);
      if (updatedChat) {
        setSelectedChat(updatedChat);
        const report = updatedChat.reports?.find(
          (item) => item.report_kind === "weekly" && item.period_start === period.period_start && item.period_end === period.period_end,
        );
        if (report) await openChatDaySummary(updatedChat, report);
      }
    } catch (error) {
      setChatDayMessage(error instanceof Error ? error.message : "Не удалось сформировать недельный отчет чата");
    } finally {
      setChatDayLoading(false);
    }
  };

  const formatDateShort = (d: Date) =>
    `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${d.getFullYear()}`;
  const formatDateFull = (d: Date) => formatDateShort(d);
  const formatDateWithTime = (d: Date) => `${formatDateShort(d)} 07:00`;
  const formatIsoDateRu = (value: string) => {
    const [year, month, day] = value.split("-");
    return year && month && day ? `${day}.${month}.${year}` : value;
  };
  const formatPromptDateTime = (value: string | null) => {
    if (!value) return "без даты";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Europe/Moscow",
    });
  };

  const teamDebtsCount = 8;
  const teamDebtsDetails = "Тагирова (3), Романов (4), Анастасия (1). Дедлайны сорваны на срок более 24 часов.";

  // Generate Calendar Data for WB based on chosen date's month
  // Assumes starting on Wednesday for April 2026
  const formatTime = (d: Date) =>
    `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

  const currentMonthIdx = calendarViewDate.getMonth();
  const currentYear = calendarViewDate.getFullYear();

  const handlePrevMonth = () => {
    setCalendarViewDate(new Date(currentYear, currentMonthIdx - 1, 1));
  };

  const handleNextMonth = () => {
    setCalendarViewDate(new Date(currentYear, currentMonthIdx + 1, 1));
  };

  const baseBalance = 6550000;
  const calculateForecast = (daysAhead: number) => {
    const today = selectedDate;
    const futureDate = new Date(today);
    futureDate.setDate(today.getDate() + daysAhead);

    const periodPayments = registry.filter((p) => {
      const pDate = new Date(p.date);
      return pDate > today && pDate <= futureDate;
    });

    const incoming = periodPayments
      .filter((p) => p.type === "incoming")
      .reduce((sum, p) => sum + p.amount, 0);
    const outgoing = periodPayments
      .filter((p) => p.type === "outgoing")
      .reduce((sum, p) => sum + p.amount, 0);

    return { value: baseBalance + incoming - outgoing, incoming, outgoing };
  };

  const forecast7 = calculateForecast(7);
  const forecast14 = calculateForecast(14);
  const forecast30 = calculateForecast(30);

  const formatCompact = (val: number) => {
    if (val >= 1000000) return (val / 1000000).toFixed(1) + "M ₽";
    if (val >= 1000) return (val / 1000).toFixed(0) + "K ₽";
    return val + " ₽";
  };

  const formatMoney = (val: number) => {
    return val.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };

  const reportCalendarWeeks = React.useMemo(() => {
    const year = reportModalMonth.getFullYear();
    const month = reportModalMonth.getMonth();
    const firstDay = new Date(year, month, 1);
    
    let startOffset = firstDay.getDay() - 1;
    if (startOffset < 0) startOffset = 6;
    
    const startDate = new Date(year, month, 1 - startOffset);
    const weeks = [];
    
    for (let w = 0; w < 6; w++) {
      const weekDays = [];
      let weekHasCurrentMonthDays = false;
      for (let d = 0; d < 7; d++) {
        const currentDate = new Date(startDate.getFullYear(), startDate.getMonth(), startDate.getDate() + (w * 7) + d);
        if (currentDate.getMonth() === month) weekHasCurrentMonthDays = true;
        weekDays.push(currentDate);
      }
      if (w === 5 && !weekHasCurrentMonthDays) continue;
      
      const mon = weekDays[0];
      const thurs = new Date(mon.getFullYear(), mon.getMonth(), mon.getDate() + 3);
      const yearStart = new Date(thurs.getFullYear(), 0, 1);
      const weekNum = Math.ceil((((thurs.getTime() - yearStart.getTime()) / 86400000) + 1)/7);
      
      weeks.push({ weekNum, days: weekDays, start: mon, end: weekDays[6] });
    }
    return weeks;
  }, [reportModalMonth]);

  const getQuarterRange = (year: number, quarter: 1 | 2 | 3 | 4) => {
    const startMonth = (quarter - 1) * 3;
    const endMonth = startMonth + 2;
    return {
      start: new Date(year, startMonth, 1),
      end: new Date(year, endMonth + 1, 0),
    };
  };

  const hasReportPeriodSelected =
    (reportKind === "daily" && !!selectedReportDay) ||
    (reportKind === "weekly" && !!selectedReportWeekStart) ||
    (reportKind === "monthly" && !!selectedReportMonth) ||
    (reportKind === "quarterly" && !!selectedReportQuarter) ||
    (reportKind === "yearly" && !!selectedReportYear);

  useEffect(() => {
    if (!isReportModalOpen) {
      setReportPrecheckError("");
      setReportPrecheckLoading(false);
      return;
    }
    let cancelled = false;
    const runPrecheck = async () => {
      setReportPrecheckError("");
      if (reportKind !== "daily" && reportKind !== "weekly") return;
      if (reportKind === "daily" && !selectedReportDay) return;
      if (reportKind === "weekly" && !selectedReportWeekStart) return;
      setReportPrecheckLoading(true);
      try {
        if (reportKind === "daily" && selectedReportDay) {
          const dayIso = isoFromDate(selectedReportDay);
          if (dayIso > REPORT_PLACEHOLDER_START_ISO) {
            const prevDay = new Date(selectedReportDay);
            prevDay.setDate(prevDay.getDate() - 1);
            const prevIso = isoFromDate(prevDay);
            await fetchJsonSafe(`/api/owner/daily-report?date=${encodeURIComponent(prevIso)}`);
          }
        }
        if (reportKind === "weekly" && selectedReportWeekStart) {
          const periodStart = isoFromDate(selectedReportWeekStart);
          const periodEndDate = new Date(selectedReportWeekStart);
          periodEndDate.setDate(periodEndDate.getDate() + 6);
          const periodEnd = isoFromDate(periodEndDate);
          const isStartupWeek = periodStart === STARTUP_WEEK_START_ISO && periodEnd === STARTUP_WEEK_END_ISO;
          if (!isStartupWeek) {
            const prevWeekEnd = new Date(selectedReportWeekStart);
            prevWeekEnd.setDate(prevWeekEnd.getDate() - 1);
            const prevWeekStart = new Date(selectedReportWeekStart);
            prevWeekStart.setDate(prevWeekStart.getDate() - 7);
            const prevStartIso = isoFromDate(prevWeekStart);
            const prevEndIso = isoFromDate(prevWeekEnd);
            await Promise.all([
              fetchJsonSafe(`/api/chats/overall-weekly-report?period_start=${encodeURIComponent(prevStartIso)}&period_end=${encodeURIComponent(prevEndIso)}`),
              fetchJsonSafe(`/api/owner/weekly-report?period_start=${encodeURIComponent(prevStartIso)}&period_end=${encodeURIComponent(prevEndIso)}`),
            ]);
          }
        }
      } catch (error) {
        if (!cancelled) {
          setReportPrecheckError(error instanceof Error ? error.message : "Не пройдена проверка последовательности отчетов.");
        }
      } finally {
        if (!cancelled) setReportPrecheckLoading(false);
      }
    };
    void runPrecheck();
    return () => {
      cancelled = true;
    };
  }, [isReportModalOpen, reportKind, selectedReportDay, selectedReportWeekStart]);

  const handleCreateReport = async () => {
    if (!hasReportPeriodSelected || reportCreateLoading || reportPrecheckLoading || !!reportPrecheckError) return;
    setReportCreateLoading(true);
    setReportCreateMessage("");
    try {
      if (reportKind === "daily") {
        if (!selectedReportDay) throw new Error("Выберите день.");
        const dayIso = isoFromDate(selectedReportDay);
        const payload = await fetchJsonSafe(
          "/api/pipeline/run-day",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ date: dayIso, force: false }),
          },
          240000,
        );
        setReportCreateMessage(payload.message || `Ежедневный отчет за ${formatIsoDateRu(dayIso)} сформирован.`);
        setIsReportModalOpen(false);
        await Promise.all([loadChatRegistry(), loadChatOverallDailyHistory(), loadChatWeeklyHistory(false), loadReportsFeed("daily")]);
        return;
      }
      if (reportKind === "weekly") {
        if (!selectedReportWeekStart) throw new Error("Выберите неделю.");
        const periodStart = isoFromDate(selectedReportWeekStart);
        const periodEndDate = new Date(selectedReportWeekStart);
        periodEndDate.setDate(periodEndDate.getDate() + 6);
        const periodEnd = isoFromDate(periodEndDate);
        const payload = await fetchJsonSafe(
          "/api/pipeline/run-week",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ period_start: periodStart, period_end: periodEnd }),
          },
          240000,
        );
        setReportCreateMessage(payload.message || `Недельный отчет за ${formatIsoDateRu(periodStart)} - ${formatIsoDateRu(periodEnd)} сформирован.`);
        setIsReportModalOpen(false);
        await Promise.all([loadChatRegistry(), loadChatOverallDailyHistory(), loadChatWeeklyHistory(false), loadReportsFeed("weekly")]);
        return;
      }
      throw new Error("Для выбранного типа пока не настроен автоматический пайплайн. Доступно: ежедневный и еженедельный.");
    } catch (error) {
      setReportCreateMessage(error instanceof Error ? error.message : "Не удалось сформировать отчет.");
    } finally {
      setReportCreateLoading(false);
    }
  };

  // Real calendar mapping from Registry bounds
  const wbCalendarData = Array.from({ length: 35 }).map((_, i) => {
    const startOffset = new Date(currentYear, currentMonthIdx, 1).getDay() - 1; // Basic offset logic
    const normalizedOffset = startOffset < 0 ? 6 : startOffset;

    let dayDisplay = i - normalizedOffset + 1;
    let isDayValid =
      dayDisplay > 0 &&
      dayDisplay <= new Date(currentYear, currentMonthIdx + 1, 0).getDate();

    if (!isDayValid) return { day: null, dayPayments: [] };

    const dateStr = `${currentYear}-${String(currentMonthIdx + 1).padStart(2, "0")}-${String(dayDisplay).padStart(2, "0")}`;
    const dayPayments = registry.filter((p) => p.date === dateStr);

    let isSelected =
      dayDisplay === selectedDate.getDate() &&
      currentMonthIdx === selectedDate.getMonth() &&
      currentYear === selectedDate.getFullYear();

    return {
      day: dayDisplay,
      isProjected: dayDisplay >= selectedDate.getDate(),
      active: isSelected,
      dayPayments,
    };
  });

  const renderDatePickerPopver = () => {
    if (!isDatePickerOpen) return null;

    // Create a 7x5 array for the date picker month grid
    const daysInMonth = new Date(currentYear, currentMonthIdx + 1, 0).getDate();
    const pickerDays = Array.from({ length: daysInMonth }).map((_, i) => i + 1);

    return (
      <div
        ref={datePickerRef}
        className="absolute top-full mt-2 right-0 w-[320px] bg-white rounded-3xl p-5 shadow-[0_10px_40px_-10px_rgba(0,0,0,0.15)] border border-[#Eef0f4] z-50 animate-in fade-in slide-in-from-top-2"
      >
        <div className="flex items-center justify-between mb-4">
          <button className="w-8 h-8 rounded-lg hover:bg-slate-50 flex items-center justify-center text-slate-500">
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div className="font-bold text-slate-800 text-[15px]">
            {MONTHS[currentMonthIdx]} {currentYear}
          </div>
          <button className="w-8 h-8 rounded-lg hover:bg-slate-50 flex items-center justify-center text-slate-500">
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
        <div className="grid grid-cols-7 gap-1 mb-2">
          {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((d) => (
            <div
              key={d}
              className="text-center text-[10px] font-bold text-slate-400 uppercase tracking-wider"
            >
              {d}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1">
          {Array.from({
            length: new Date(currentYear, currentMonthIdx, 1).getDay() - 1,
          }).map((_, i) => (
            <div key={`empty-${i}`} />
          ))}
          {pickerDays.map((day) => {
            const isSel = day === selectedDate.getDate();
            return (
              <button
                key={day}
                onClick={() => {
                  setSelectedDate(new Date(currentYear, currentMonthIdx, day));
                  setIsDatePickerOpen(false);
                }}
                className={cn(
                  "h-9 w-full rounded-xl flex items-center justify-center text-[13px] font-semibold transition-all",
                  isSel
                    ? "bg-[#5440F6] text-white shadow-md shadow-[#5440F6]/20"
                    : "text-slate-700 hover:bg-slate-100",
                )}
              >
                {day}
              </button>
            );
          })}
        </div>
      </div>
    );
  };

  const renderContent = () => {
    if (activeTab === "Бухгалтерия") {
      return (
        <div className="flex flex-col gap-6 animate-in fade-in duration-300 h-full">
          {/* Sub Navigation */}
          <div className="flex justify-between items-center mb-2">
            <div className="flex items-center gap-2 bg-white p-1.5 rounded-xl border border-[#Eef0f4] w-max shadow-sm">
              <button
                onClick={() => setAccountingTab("requests")}
                className={cn(
                  "px-5 py-2 rounded-lg text-[13px] font-bold transition-all",
                  accountingTab === "requests"
                    ? "bg-[#5440F6] text-white shadow-md shadow-[#5440F6]/20"
                    : "text-slate-500 hover:text-slate-900",
                )}
              >
                Заявки на оплату
              </button>
              <button
                onClick={() => setAccountingTab("statements")}
                className={cn(
                  "px-5 py-2 rounded-lg text-[13px] font-bold transition-all",
                  accountingTab === "statements"
                    ? "bg-[#5440F6] text-white shadow-md shadow-[#5440F6]/20"
                    : "text-slate-500 hover:text-slate-900",
                )}
              >
                Выписки банков
              </button>
              <button
                onClick={() => setAccountingTab("registry")}
                className={cn(
                  "px-5 py-2 rounded-lg text-[13px] font-bold transition-all",
                  accountingTab === "registry"
                    ? "bg-[#5440F6] text-white shadow-md shadow-[#5440F6]/20"
                    : "text-slate-500 hover:text-slate-900",
                )}
              >
                Реестр платежей
              </button>
            </div>

            {accountingTab === "requests" && (
              <button className="flex items-center gap-2 bg-[#5440F6] hover:bg-[#4532db] text-white rounded-xl px-5 py-2.5 text-[13px] font-bold transition-all shadow-md shadow-[#5440F6]/20 active:scale-95">
                <Plus className="w-4 h-4" strokeWidth={2.5} />
                Создать заявку
              </button>
            )}
          </div>

          {/* Accounting View Content */}
          {accountingTab === "requests" ? (
            <div className="grid gap-4">
              {[
                {
                  id: 1,
                  docDate: "26.04.2026",
                  counterparty: 'ООО "ЛайнЛогистик"',
                  sum: "185 450,00 ₽",
                  purpose: "Транспортные услуги по договору №45/2",
                  status: "approved",
                  statusStr: "К оплате (Согласовано)",
                },
                {
                  id: 2,
                  docDate: "27.04.2026",
                  counterparty: "ИП Смирнов А.А.",
                  sum: "42 000,00 ₽",
                  purpose: "Закупка упаковочных материалов",
                  status: "pending",
                  statusStr: "На согласовании",
                },
                {
                  id: 3,
                  docDate: "25.04.2026",
                  counterparty: 'ПАО "МегаФон"',
                  sum: "4 550,00 ₽",
                  purpose: "Корпоративная связь",
                  status: "draft",
                  statusStr: "Черновик",
                },
              ].map((req) => (
                <div
                  key={req.id}
                  className="bg-white rounded-3xl p-6 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex items-center justify-between group hover:border-[#CBD5E1] transition-colors cursor-pointer"
                >
                  <div className="flex gap-5 items-center">
                    <div
                      className={cn(
                        "w-12 h-12 rounded-xl flex items-center justify-center shrink-0 shadow-sm",
                        req.status === "approved"
                          ? "bg-emerald-50 text-emerald-600"
                          : req.status === "pending"
                            ? "bg-amber-50 text-amber-500"
                            : "bg-slate-100 text-slate-500",
                      )}
                    >
                      {req.status === "approved" ? (
                        <CheckCircle2 className="w-6 h-6" strokeWidth={2.5} />
                      ) : req.status === "pending" ? (
                        <CircleDashed
                          className="w-6 h-6 animate-[spin_4s_linear_infinite]"
                          strokeWidth={2.5}
                        />
                      ) : (
                        <FileText className="w-6 h-6" strokeWidth={2.5} />
                      )}
                    </div>
                    <div>
                      <div className="flex items-center gap-3 mb-1">
                        <span className="font-bold text-[16px] text-slate-800">
                          {req.counterparty}
                        </span>
                        <span
                          className={cn(
                            "text-[10px] font-bold px-2 py-0.5 rounded tracking-wide uppercase",
                            req.status === "approved"
                              ? "bg-[#E7F8ED] text-emerald-700"
                              : req.status === "pending"
                                ? "bg-[#FEF3C7] text-amber-700"
                                : "bg-slate-100 text-slate-600",
                          )}
                        >
                          {req.statusStr}
                        </span>
                      </div>
                      <div className="text-[13px] font-medium text-slate-500 mt-0.5 max-w-2xl truncate">
                        {req.purpose}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-[18px] font-bold text-slate-900 tracking-tight">
                      {req.sum}
                    </div>
                    <div className="text-[12px] font-semibold text-slate-400 mt-1">
                      От {req.docDate}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : accountingTab === "statements" ? (
            <div className="bg-white rounded-3xl border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] overflow-hidden">
              <div className="p-6 pb-4 border-b border-[#Eef0f4] flex justify-between items-center bg-[#F8FAFC]">
                <h3 className="text-[16px] font-bold text-slate-900">
                  Последние выписки (Распознанные)
                </h3>
              </div>
              <div className="divide-y divide-[#Eef0f4]">
                {[
                  {
                    id: 11,
                    docDate: "27.04.2026",
                    type: "incoming",
                    sum: "+ 1 450 000,00 ₽",
                    counterparty: 'ООО "Вайлдберриз"',
                    purpose:
                      "Еженедельная выплата по договору реализации продукта А-213",
                    docType: "txt",
                  },
                  {
                    id: 12,
                    docDate: "26.04.2026",
                    type: "outgoing",
                    sum: "- 185 450,00 ₽",
                    counterparty: 'ООО "ЛайнЛогистик"',
                    purpose:
                      "Транспортные услуги за апрель. Основание: Акт №452 от 25.04.2026",
                    docType: "pdf",
                  },
                  {
                    id: 13,
                    docDate: "25.04.2026",
                    type: "outgoing",
                    sum: "- 10 000,00 ₽",
                    counterparty: "ФНС России",
                    purpose: "Оплата налогов и сборов",
                    docType: "txt",
                  },
                ].map((st) => (
                  <div
                    key={st.id}
                    className="p-5 flex items-center justify-between hover:bg-slate-50 transition-colors cursor-pointer"
                  >
                    <div className="flex gap-5 items-center w-2/3">
                      <div
                        className={cn(
                          "w-10 h-10 rounded-xl flex items-center justify-center shrink-0 border",
                          st.type === "incoming"
                            ? "bg-emerald-50 border-emerald-100 text-emerald-600"
                            : "bg-white border-slate-200 text-slate-600",
                        )}
                      >
                        <Landmark className="w-5 h-5" strokeWidth={2} />
                      </div>
                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-bold text-[14px] text-slate-900">
                            {st.counterparty}
                          </span>
                          <span className="text-[12px] font-semibold text-slate-400">
                            &middot; {st.docDate}
                          </span>
                        </div>
                        <div className="text-[13px] font-medium text-slate-500 truncate mt-0.5 bg-slate-100 px-2 py-1 rounded inline-block max-w-full">
                          <span className="text-slate-400 mr-1">
                            Основание:
                          </span>
                          {st.purpose}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-6">
                      <div className="text-right">
                        <div
                          className={cn(
                            "text-[16px] font-bold tracking-tight",
                            st.type === "incoming"
                              ? "text-emerald-600"
                              : "text-slate-800",
                          )}
                        >
                          {st.sum}
                        </div>
                      </div>
                      <button className="flex items-center justify-center p-2 rounded-lg bg-slate-100 text-slate-500 hover:bg-[#5440F6] hover:text-white transition-colors">
                        <FileCheck className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="bg-white rounded-3xl border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] overflow-hidden flex flex-col min-h-[500px]">
              <div className="p-6 pb-4 border-b border-[#Eef0f4] flex justify-between items-center bg-[#F8FAFC]">
                <h3 className="text-[16px] font-bold text-slate-900">
                  Платежи и прогнозы
                </h3>
                <button
                  onClick={() => setShowAddPayment(!showAddPayment)}
                  className="flex items-center gap-2 bg-[#5440F6] hover:bg-[#4532db] text-white rounded-xl px-4 py-2 text-[13px] font-bold transition-all"
                >
                  <Plus className="w-4 h-4" /> Добавить платеж
                </button>
              </div>

              {showAddPayment && (
                <div className="p-5 bg-slate-50 border-b border-[#Eef0f4] flex gap-4 items-end animate-in fade-in slide-in-from-top-2">
                  <div className="flex-1">
                    <label className="text-[12px] font-bold text-slate-500 mb-1 block">
                      Дата
                    </label>
                    <CustomDateInput
                      value={paymentForm.date}
                      onChange={(value) => setPaymentForm({ ...paymentForm, date: value })}
                    />
                  </div>
                  <div className="flex-1">
                    <label className="text-[12px] font-bold text-slate-500 mb-1 block">
                      Тип
                    </label>
                    <CustomSelect
                      value={paymentForm.type}
                      onChange={(value) =>
                        setPaymentForm({ ...paymentForm, type: value as "incoming" | "outgoing" })
                      }
                      options={[
                        { value: "incoming", label: "Поступление" },
                        { value: "outgoing", label: "Расход" },
                      ]}
                    />
                  </div>
                  <div className="flex-1">
                    <label className="text-[12px] font-bold text-slate-500 mb-1 block">
                      Сумма (₽)
                    </label>
                    <input
                      type="number"
                      placeholder="0"
                      value={paymentForm.amount}
                      onChange={(e) =>
                        setPaymentForm({
                          ...paymentForm,
                          amount: e.target.value,
                        })
                      }
                      className="w-full rounded-xl border border-slate-200 px-3 py-2 text-[13px] outline-none focus:border-[#5440F6]"
                    />
                  </div>
                  <div className="flex-[2]">
                    <label className="text-[12px] font-bold text-slate-500 mb-1 block">
                      Основание
                    </label>
                    <input
                      type="text"
                      placeholder="Назначение платежа"
                      value={paymentForm.desc}
                      onChange={(e) =>
                        setPaymentForm({ ...paymentForm, desc: e.target.value })
                      }
                      className="w-full rounded-xl border border-slate-200 px-3 py-2 text-[13px] outline-none focus:border-[#5440F6]"
                    />
                  </div>
                  <div>
                    <button
                      onClick={() => {
                        if (!paymentForm.amount || !paymentForm.desc) return;
                        setRegistry((prev) =>
                          [
                            ...prev,
                            {
                              id: Math.random().toString(),
                              date: paymentForm.date,
                              type: paymentForm.type as any,
                              amount: Number(paymentForm.amount),
                              source: "manual",
                              description: paymentForm.desc,
                              isProjected:
                                new Date(paymentForm.date) >= new Date(),
                            },
                          ].sort((a, b) => a.date.localeCompare(b.date)),
                        );
                        setShowAddPayment(false);
                        setPaymentForm({
                          date: "2026-04-27",
                          type: "outgoing",
                          amount: "",
                          desc: "",
                        });
                      }}
                      className="bg-slate-800 text-white px-5 py-2.5 rounded-xl text-[13px] font-bold hover:bg-slate-700"
                    >
                      Сохранить
                    </button>
                  </div>
                </div>
              )}

              <div className="flex-1 overflow-y-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-[#Eef0f4] text-[12px] text-slate-400 font-bold uppercase tracking-wider bg-white">
                      <th className="px-6 py-4 font-bold">Дата</th>
                      <th className="px-6 py-4 font-bold">
                        Основание / Контрагент
                      </th>
                      <th className="px-6 py-4 font-bold text-right">Сумма</th>
                      <th className="px-6 py-4 font-bold text-right">
                        Источник
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#Eef0f4]">
                    {registry.map((item) => (
                      <tr
                        key={item.id}
                        className="hover:bg-slate-50 transition-colors"
                      >
                        <td className="px-6 py-4 whitespace-nowrap text-[13px] text-slate-600 font-medium">
                          {item.date.split("-").reverse().join(".")}{" "}
                          {item.isProjected && (
                            <span className="ml-2 text-[10px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-400 uppercase font-bold tracking-wide">
                              Прогноз
                            </span>
                          )}
                        </td>
                        <td className="px-6 py-4 text-[14px] text-slate-800 font-semibold flex items-center gap-2">
                          {item.type === "incoming" ? (
                            <ArrowDownRight className="w-4 h-4 text-emerald-500" />
                          ) : (
                            <ArrowUpRight className="w-4 h-4 text-red-500" />
                          )}
                          {item.description}
                        </td>
                        <td
                          className={cn(
                            "px-6 py-4 whitespace-nowrap text-[14px] font-bold text-right",
                            item.type === "incoming"
                              ? "text-emerald-600"
                              : "text-slate-900",
                          )}
                        >
                          {item.type === "incoming" ? "+" : "-"}{" "}
                          {item.amount.toLocaleString("ru-RU")}{" "}
                          <span className="text-[13px]">₽</span>
                        </td>
                        <td className="px-6 py-4 text-right">
                          {item.source === "wb_api" ? (
                            <span className="inline-flex items-center gap-1.5 bg-[#F5F3FF] text-[#5440F6] text-[10px] font-bold uppercase px-2 py-1.5 rounded-md tracking-wider">
                              <RefreshCw className="w-3.5 h-3.5" /> API WB
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 bg-slate-100 text-slate-600 text-[10px] font-bold uppercase px-2 py-1.5 rounded-md tracking-wider">
                              Вручную
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      );
    }

    if (activeTab === "Зум-разговоры") {
      const selectedZoomYear = zoomCallsTree.years.find((year) => year.year === zoomFolder.year) || null;
      const selectedZoomMonth = selectedZoomYear?.months.find((month) => month.month === zoomFolder.month) || null;
      const selectedZoomDay = selectedZoomMonth?.dates.find((day) => day.date === zoomFolder.date) || null;
      const zoomLevelTitle = selectedZoomDay?.date_text || selectedZoomMonth?.title || (selectedZoomYear ? String(selectedZoomYear.year) : "Годы");
      const zoomFolderTileClass =
        "group h-32 rounded-2xl border border-slate-200 bg-slate-50 hover:bg-blue-50/60 hover:border-blue-200 transition-colors p-5 text-left flex flex-col justify-between";
      const zoomCountCalls = (calls: ZoomCall[]) => calls.length;
      const zoomCountMonth = (month: ZoomCallsTree["years"][number]["months"][number]) =>
        month.dates.reduce((sum, day) => sum + day.calls.length, 0);
      const zoomCountYear = (year: ZoomCallsTree["years"][number]) =>
        year.months.reduce((sum, month) => sum + zoomCountMonth(month), 0);

      return (
        <div className="flex flex-col gap-6 animate-in fade-in duration-300 h-full">
          <div className="bg-white rounded-3xl p-8 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] h-full overflow-y-auto">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-8">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center shrink-0 border border-blue-100">
                  <Video className="w-6 h-6" strokeWidth={2.5} />
                </div>
                <div>
                  <h2 className="text-2xl font-bold text-slate-900 tracking-tight">
                    Журнал Зум-разговоров
                  </h2>
                  <p className="text-sm text-slate-500 font-medium mt-1">
                    Папки годов, месяцев, дней и транскрибации созвонов
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {zoomCallsTree.updated_at_text && (
                  <span className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm font-bold text-slate-500">
                    Обновлено: {zoomCallsTree.updated_at_text}
                  </span>
                )}
                <button
                  onClick={syncDriveCallTranscripts}
                  disabled={zoomCallsLoading}
                  className="flex items-center gap-2 bg-white hover:bg-blue-50 text-blue-700 border border-blue-100 px-5 py-2.5 rounded-xl font-bold text-sm shadow-sm transition-all active:scale-95 disabled:opacity-60"
                >
                  <FileText className="w-4 h-4" strokeWidth={2.5} />
                  {zoomCallsLoading ? "Подтягиваем..." : "Подтянуть transcript.txt"}
                </button>
                <button
                  onClick={syncZoomCalls}
                  disabled={zoomCallsLoading}
                  className="flex items-center gap-2 bg-[#5440F6] hover:bg-[#4532db] text-white px-5 py-2.5 rounded-xl font-bold text-sm shadow-md shadow-[#5440F6]/20 transition-all active:scale-95 disabled:opacity-60"
                >
                  <RefreshCw className={cn("w-4 h-4", zoomCallsLoading && "animate-spin")} strokeWidth={2.5} />
                  {zoomCallsLoading ? "Подтягиваем записи..." : "Подтянуть записи Zoom"}
                </button>
              </div>
            </div>

            {zoomCallsMessage && (
              <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] px-4 py-3 text-[13px] font-bold text-slate-700 mb-5">
                {zoomCallsMessage}
              </div>
            )}

            {zoomCallsLoading && zoomCallsTree.total === 0 ? (
              <div className="h-48 flex items-center justify-center text-sm font-bold text-slate-400">
                Загрузка зум-созвонов...
              </div>
            ) : zoomCallsTree.total === 0 ? (
              <div className="h-48 flex flex-col items-center justify-center text-center rounded-2xl border border-dashed border-slate-200 bg-slate-50">
                <Video className="w-8 h-8 text-slate-300 mb-3" />
                <p className="text-sm font-bold text-slate-500">В БД пока нет зум-созвонов</p>
                <p className="text-xs font-medium text-slate-400 mt-1">Нажмите «Подтянуть записи Zoom».</p>
              </div>
            ) : (
              <div className="space-y-5">
                <div className="flex flex-wrap items-center gap-2 text-[13px] font-bold">
                  <button
                    onClick={() => setZoomFolder({})}
                    className={cn(
                      "px-3 py-1.5 rounded-lg border transition-colors",
                      !zoomFolder.year ? "bg-blue-50 text-blue-700 border-blue-100" : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50",
                    )}
                  >
                    Годы
                  </button>
                  {selectedZoomYear && (
                    <>
                      <ChevronRight className="w-4 h-4 text-slate-300" />
                      <button
                        onClick={() => setZoomFolder({ year: selectedZoomYear.year })}
                        className={cn(
                          "px-3 py-1.5 rounded-lg border transition-colors",
                          !zoomFolder.month ? "bg-blue-50 text-blue-700 border-blue-100" : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50",
                        )}
                      >
                        {selectedZoomYear.year}
                      </button>
                    </>
                  )}
                  {selectedZoomMonth && (
                    <>
                      <ChevronRight className="w-4 h-4 text-slate-300" />
                      <button
                        onClick={() => setZoomFolder({ year: selectedZoomYear?.year, month: selectedZoomMonth.month })}
                        className={cn(
                          "px-3 py-1.5 rounded-lg border transition-colors",
                          !zoomFolder.date ? "bg-blue-50 text-blue-700 border-blue-100" : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50",
                        )}
                      >
                        {selectedZoomMonth.title}
                      </button>
                    </>
                  )}
                  {selectedZoomDay && (
                    <>
                      <ChevronRight className="w-4 h-4 text-slate-300" />
                      <span className="px-3 py-1.5 rounded-lg border bg-blue-50 text-blue-700 border-blue-100">
                        {selectedZoomDay.date_text}
                      </span>
                    </>
                  )}
                </div>

                <div className="flex items-center justify-between gap-4">
                  <h3 className="text-[22px] font-black text-slate-900">{zoomLevelTitle}</h3>
                  {(zoomFolder.year || zoomFolder.month || zoomFolder.date) && (
                    <button
                      onClick={() => {
                        if (zoomFolder.date) setZoomFolder({ year: zoomFolder.year, month: zoomFolder.month });
                        else if (zoomFolder.month) setZoomFolder({ year: zoomFolder.year });
                        else setZoomFolder({});
                      }}
                      className="inline-flex items-center gap-2 h-10 px-4 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-600 font-bold text-[13px]"
                    >
                      <ChevronLeft className="w-4 h-4" />
                      Назад
                    </button>
                  )}
                </div>

                {!selectedZoomYear ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
                    {zoomCallsTree.years.map((year) => (
                      <button key={year.year} onClick={() => setZoomFolder({ year: year.year })} className={zoomFolderTileClass}>
                        <Folder className="w-10 h-10 text-blue-500 group-hover:text-blue-600" strokeWidth={2.2} />
                        <div>
                          <div className="text-[20px] font-black text-slate-900">{year.year}</div>
                          <div className="text-xs font-bold text-slate-400 mt-1">{zoomCountYear(year)} созвонов</div>
                        </div>
                      </button>
                    ))}
                  </div>
                ) : !selectedZoomMonth ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
                    {selectedZoomYear.months.map((month) => (
                      <button key={month.month} onClick={() => setZoomFolder({ year: selectedZoomYear.year, month: month.month })} className={zoomFolderTileClass}>
                        <Folder className="w-10 h-10 text-blue-500 group-hover:text-blue-600" strokeWidth={2.2} />
                        <div>
                          <div className="text-[18px] font-black text-slate-900">{month.title}</div>
                          <div className="text-xs font-bold text-slate-400 mt-1">{zoomCountMonth(month)} созвонов</div>
                        </div>
                      </button>
                    ))}
                  </div>
                ) : !selectedZoomDay ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
                    {selectedZoomMonth.dates.map((day) => (
                      <button key={day.date} onClick={() => setZoomFolder({ year: selectedZoomYear.year, month: selectedZoomMonth.month, date: day.date })} className={zoomFolderTileClass}>
                        <FolderOpen className="w-10 h-10 text-blue-500 group-hover:text-blue-600" strokeWidth={2.2} />
                        <div>
                          <div className="text-[18px] font-black text-slate-900">{day.date_text}</div>
                          <div className="text-xs font-bold text-slate-400 mt-1">{zoomCountCalls(day.calls)} созвонов</div>
                        </div>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="grid gap-4">
                    {selectedZoomDay.calls.map((call) => (
                      <button
                        key={call.id}
                        onClick={() => void openZoomCall(call)}
                        className="w-full text-left bg-slate-50 rounded-2xl p-6 border border-slate-100 hover:border-blue-200 hover:bg-blue-50/30 transition-colors"
                      >
                        <div className="flex justify-between items-start mb-4 gap-4">
                          <div className="min-w-0">
                            <h5 className="text-[18px] font-bold text-slate-900 mb-2 truncate">
                              {call.technical_topic}
                            </h5>
                            <div className="flex flex-wrap items-center gap-3 text-sm font-semibold text-slate-500 mb-3">
                              <div className="flex items-center justify-center bg-white px-3 py-1 rounded-md border border-slate-200 shadow-sm">
                                {call.date_text}
                              </div>
                              <div className="flex items-center justify-center bg-white px-3 py-1 rounded-md border border-slate-200 shadow-sm">
                                {call.time_text}
                              </div>
                            </div>
                          </div>
                          <ArrowRight className="w-5 h-5 text-slate-300 shrink-0 mt-1" />
                        </div>

                        <div className="mb-4">
                          <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Участники</p>
                          <div className="flex flex-wrap gap-2">
                            {call.participants.length ? call.participants.map((participant, idx) => (
                              <span key={`${participant.email || participant.name || idx}`} className="bg-blue-100/50 text-blue-700 text-xs font-bold px-2.5 py-1 rounded flex items-center gap-1.5">
                                <Users className="w-3 h-3" />
                                {participant.name || participant.email}
                              </span>
                            )) : (
                              <span className="text-xs font-bold text-slate-400">Не определены</span>
                            )}
                          </div>
                        </div>

                        <p
                          className="text-[14px] text-slate-600 font-medium leading-relaxed overflow-hidden"
                          style={{ display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical" }}
                        >
                          {call.analytical_note ? zoomReportPreview(call.analytical_note) : "Пока пусто"}
                        </p>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

          </div>
        </div>
      );
    }

    if (activeTab === "Команда") {
      const managersCount = new Set(teamRows.filter((member) => member.manager_id).map((member) => member.manager_id)).size;
      return (
        <div className="flex flex-col gap-6 animate-in fade-in duration-300 h-full">
          <div className="bg-white rounded-3xl p-8 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] h-full flex flex-col">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-2xl bg-blue-50 text-blue-600 flex items-center justify-center shrink-0 border border-blue-100">
                  <Users className="w-6 h-6" strokeWidth={2.5} />
                </div>
                <div>
                  <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Команда</h2>
                  <p className="text-sm text-slate-500 font-medium mt-1">Сотрудники Bitrix, должности, подразделения и руководители</p>
                  <div className="mt-2 inline-flex items-center rounded-full border border-indigo-100 bg-indigo-50 px-3 py-1 text-[12px] font-bold text-indigo-700">
                    Обновлено: {teamLastSyncedAtText || "еще не выполнялось"}
                  </div>
                </div>
              </div>
              <button
                onClick={syncTeam}
                disabled={teamLoading}
                className="flex items-center gap-2 bg-[#5440F6] hover:bg-[#4532db] text-white px-5 py-2.5 rounded-xl font-bold text-sm shadow-md shadow-[#5440F6]/20 transition-all active:scale-95 disabled:opacity-60"
              >
                <RefreshCw className={cn("w-4 h-4", teamLoading && "animate-spin")} strokeWidth={2.5} />
                {teamLoading ? "Обновление..." : "Обновить из Bitrix"}
              </button>
            </div>

            {teamMessage && (
              <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] px-4 py-3 text-[13px] font-bold text-slate-700 mb-4">
                {teamMessage}
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
              <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-4">
                <div className="text-[11px] uppercase font-black text-slate-500 mb-2">Сотрудников</div>
                <div className="text-[24px] font-black text-slate-950">{teamRows.length}</div>
              </div>
              <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-4">
                <div className="text-[11px] uppercase font-black text-slate-500 mb-2">Руководителей</div>
                <div className="text-[24px] font-black text-slate-950">{managersCount}</div>
              </div>
              <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-4">
                <div className="text-[11px] uppercase font-black text-slate-500 mb-2">Источник</div>
                <div className="text-[18px] font-black text-slate-950">Bitrix user.get</div>
              </div>
            </div>

            <div className="border border-[#Eef0f4] rounded-2xl overflow-hidden flex-1 bg-white min-h-[460px]">
              <div className="overflow-auto h-full">
                <table className="w-full text-left text-[14px]">
                  <thead className="sticky top-0 bg-[#F8FAFC] border-b border-[#Eef0f4] text-slate-900 z-10">
                    <tr>
                      <th className="px-6 py-4 font-bold">Сотрудник</th>
                      <th className="px-6 py-4 font-bold">Должность</th>
                      <th className="px-6 py-4 font-bold">Подразделение</th>
                      <th className="px-6 py-4 font-bold">Руководитель</th>
                      <th className="px-6 py-4 font-bold">Синхронизация</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#Eef0f4]">
                    {teamRows.map((member) => (
                      <tr key={member.user_id} className="hover:bg-[#F8FAFC] transition-colors">
                        <td className="px-6 py-5 min-w-[260px]">
                          <div className="flex items-center gap-3">
                            {member.avatar_url ? (
                              <img src={member.avatar_url} alt={member.name || "Сотрудник"} className="w-10 h-10 rounded-xl object-cover border border-[#Eef0f4] bg-white" />
                            ) : (
                              <div className="w-10 h-10 rounded-xl bg-slate-100 text-slate-500 flex items-center justify-center font-black">
                                {(member.name || "?").slice(0, 1)}
                              </div>
                            )}
                            <div>
                              <div className="font-black text-slate-900">{member.name || `ID ${member.user_id}`}</div>
                              <div className="text-[12px] text-slate-400 font-semibold mt-1">
                                ID {member.user_id}{member.email ? ` · ${member.email}` : ""}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-5 text-slate-700 font-semibold min-w-[240px]">{member.work_position || "-"}</td>
                        <td className="px-6 py-5 text-slate-600 font-semibold min-w-[220px]">{member.departments_text || "-"}</td>
                        <td className="px-6 py-5 text-slate-700 font-semibold whitespace-nowrap">
                          {member.manager_name || "-"}
                          {member.manager_id ? <div className="text-[12px] text-slate-400 mt-1">ID {member.manager_id}</div> : null}
                        </td>
                        <td className="px-6 py-5 text-slate-600 font-semibold whitespace-nowrap">{member.last_synced_at_text}</td>
                      </tr>
                    ))}
                    {!teamLoading && teamRows.length === 0 && (
                      <tr>
                        <td colSpan={5} className="px-6 py-10 text-center text-slate-500 font-bold">
                          В базе пока нет сотрудников. Нажмите «Обновить из Bitrix».
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      );
    }

    if (activeTab === "О компании") {
      return renderCompanyProfile();
    }

    if (activeTab === "Цели") {
      const stats = goalStats || {
        total: goalRows.length,
        filtered_total: goalRows.length,
        company: goalRows.filter((goal) => goal.goal_level === "company").length,
        employee: goalRows.filter((goal) => goal.goal_level === "employee").length,
        active: goalRows.filter((goal) => goal.status === "active").length,
        manual: goalRows.filter((goal) => goal.source_type === "manual").length,
        from_chats: goalRows.filter((goal) => ["chat", "ocr", "ai"].includes(goal.source_type || "")).length,
      };
      const activeEmployees = teamRows.filter((member) => member.active !== 0);

      return (
        <div className="flex flex-col gap-6 animate-in fade-in duration-300 h-full">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
            {[
              { label: "Всего", value: stats.filtered_total },
              { label: "Компания", value: stats.company },
              { label: "Сотрудники", value: stats.employee },
              { label: "Активные", value: stats.active },
              { label: "Из чатов/ИИ", value: stats.from_chats },
            ].map((item) => (
              <div key={item.label} className="rounded-2xl border border-[#Eef0f4] bg-white px-5 py-4">
                <div className="text-[11px] uppercase font-black text-slate-500 mb-2">{item.label}</div>
                <div className="text-[24px] font-black text-slate-950">{item.value}</div>
              </div>
            ))}
          </div>

          <div className="bg-white rounded-3xl p-6 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)]">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-2xl bg-[#EEF2FF] text-[#5440F6] flex items-center justify-center shrink-0 border border-[#E0E7FF]">
                  <Target className="w-6 h-6" strokeWidth={2.5} />
                </div>
                <div>
                  <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Цели</h2>
                  <p className="text-sm text-slate-500 font-medium mt-1">Ручные цели и цели, найденные ИИ в чатах</p>
                </div>
              </div>
              <button
                onClick={() => void loadGoals(goalFilters)}
                disabled={goalsLoading}
                className="flex items-center gap-2 border border-[#Eef0f4] bg-white hover:border-[#CBD5E1] text-slate-700 px-5 py-2.5 rounded-xl font-bold text-sm transition-all disabled:opacity-60"
              >
                <RefreshCw className={cn("w-4 h-4", goalsLoading && "animate-spin")} strokeWidth={2.5} />
                Обновить
              </button>
            </div>

            {goalsMessage && (
              <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] px-4 py-3 text-[13px] font-bold text-slate-700 mb-5">
                {goalsMessage}
              </div>
            )}

            <div className="grid grid-cols-1 xl:grid-cols-[420px_minmax(0,1fr)] gap-5">
              <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] p-5">
                <div className="text-[15px] font-black text-slate-950 mb-4">Добавить цель вручную</div>
                <div className="grid grid-cols-1 gap-3">
                  <label className="text-[12px] font-black text-slate-500 uppercase">
                    Уровень
                    <CustomSelect
                      value={goalForm.goal_level}
                      onChange={(value) => updateGoalForm("goal_level", value)}
                      options={toSelectOptions(GOAL_LEVEL_LABELS)}
                      className="mt-1"
                    />
                  </label>

                  {goalForm.goal_level !== "company" && (
                    <label className="text-[12px] font-black text-slate-500 uppercase">
                      Ответственный
                      <CustomSelect
                        value={goalForm.owner_id}
                        onChange={(value) => updateGoalForm("owner_id", value)}
                        options={[
                          { value: "", label: "Не выбран" },
                          ...activeEmployees.map((member) => ({
                            value: String(member.user_id),
                            label: member.name || `ID ${member.user_id}`,
                          })),
                        ]}
                        className="mt-1"
                      />
                    </label>
                  )}

                  <label className="text-[12px] font-black text-slate-500 uppercase">
                    Название цели
                    <input
                      value={goalForm.goal_title}
                      onChange={(e) => updateGoalForm("goal_title", e.target.value)}
                      className="mt-1 w-full rounded-xl border border-[#Eef0f4] bg-white px-3 py-2.5 text-[14px] font-semibold text-slate-800 outline-none"
                      placeholder="Например: выйти на прибыль 45 млн"
                    />
                  </label>

                  <label className="text-[12px] font-black text-slate-500 uppercase">
                    Формулировка
                    <textarea
                      value={goalForm.goal_text}
                      onChange={(e) => updateGoalForm("goal_text", e.target.value)}
                      className="mt-1 min-h-[88px] w-full resize-none rounded-xl border border-[#Eef0f4] bg-white px-3 py-2.5 text-[14px] font-semibold text-slate-800 outline-none"
                    />
                  </label>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <label className="text-[12px] font-black text-slate-500 uppercase">
                      Период
                      <CustomSelect
                        value={goalForm.period_type}
                        onChange={(value) => updateGoalForm("period_type", value)}
                        options={toSelectOptions(GOAL_PERIOD_LABELS)}
                        className="mt-1"
                      />
                    </label>
                    <label className="text-[12px] font-black text-slate-500 uppercase">
                      С
                      <CustomDateInput
                        value={goalForm.period_start}
                        onChange={(value) => updateGoalForm("period_start", value)}
                        className="mt-1"
                      />
                    </label>
                    <label className="text-[12px] font-black text-slate-500 uppercase">
                      До
                      <CustomDateInput
                        value={goalForm.period_end}
                        onChange={(value) => updateGoalForm("period_end", value)}
                        className="mt-1"
                      />
                    </label>
                  </div>

                  <label className="text-[12px] font-black text-slate-500 uppercase">
                    Метрики успеха
                    <textarea
                      value={goalForm.success_metrics}
                      onChange={(e) => updateGoalForm("success_metrics", e.target.value)}
                      className="mt-1 min-h-[70px] w-full resize-none rounded-xl border border-[#Eef0f4] bg-white px-3 py-2.5 text-[14px] font-semibold text-slate-800 outline-none"
                    />
                  </label>

                  <label className="text-[12px] font-black text-slate-500 uppercase">
                    Ожидаемый результат
                    <input
                      value={goalForm.expected_result}
                      onChange={(e) => updateGoalForm("expected_result", e.target.value)}
                      className="mt-1 w-full rounded-xl border border-[#Eef0f4] bg-white px-3 py-2.5 text-[14px] font-semibold text-slate-800 outline-none"
                    />
                  </label>

                  <button
                    onClick={() => void saveGoal()}
                    disabled={goalsLoading || !goalForm.goal_title.trim()}
                    className="mt-1 inline-flex items-center justify-center gap-2 rounded-xl bg-[#5440F6] px-5 py-3 text-[14px] font-bold text-white shadow-md shadow-[#5440F6]/20 transition-all hover:bg-[#4532db] active:scale-95 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
                  >
                    <Plus className="h-4 w-4" />
                    Сохранить цель
                  </button>
                </div>
              </div>

              <div className="flex min-w-0 flex-col gap-4">
                <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] p-4">
                  <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-3">
                    <input
                      value={goalFilters.q}
                      onChange={(e) => updateGoalFilter("q", e.target.value)}
                      className="rounded-xl border border-[#Eef0f4] bg-white px-3 py-2.5 text-[13px] font-semibold outline-none"
                      placeholder="Поиск"
                    />
                    <CustomSelect value={goalFilters.goal_level} onChange={(value) => updateGoalFilter("goal_level", value)} options={toSelectOptions(GOAL_LEVEL_LABELS, "Все уровни")} />
                    <CustomSelect value={goalFilters.period_type} onChange={(value) => updateGoalFilter("period_type", value)} options={toSelectOptions(GOAL_PERIOD_LABELS, "Все периоды")} />
                    <CustomSelect value={goalFilters.status} onChange={(value) => updateGoalFilter("status", value)} options={toSelectOptions(GOAL_STATUS_LABELS, "Все статусы")} />
                    <CustomSelect value={goalFilters.source_type} onChange={(value) => updateGoalFilter("source_type", value)} options={toSelectOptions(GOAL_SOURCE_LABELS, "Все источники")} />
                    <button
                      onClick={() => void loadGoals(goalFilters)}
                      className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-[13px] font-bold text-white transition-all hover:bg-slate-800"
                    >
                      <Search className="h-4 w-4" />
                      Найти
                    </button>
                  </div>
                </div>

                <div className="border border-[#Eef0f4] rounded-2xl overflow-hidden bg-white min-h-[560px]">
                  <div className="overflow-auto max-h-[720px]">
                    <table className="w-full text-left text-[14px]">
                      <thead className="sticky top-0 bg-[#F8FAFC] border-b border-[#Eef0f4] text-slate-900 z-10">
                        <tr>
                          <th className="px-5 py-4 font-bold min-w-[300px]">Цель</th>
                          <th className="px-5 py-4 font-bold">Уровень</th>
                          <th className="px-5 py-4 font-bold">Ответственный</th>
                          <th className="px-5 py-4 font-bold">Период</th>
                          <th className="px-5 py-4 font-bold">Статус</th>
                          <th className="px-5 py-4 font-bold">Источник</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[#Eef0f4]">
                        {goalRows.map((goal) => (
                          <tr key={goal.goal_id} className="hover:bg-[#F8FAFC] transition-colors align-top">
                            <td className="px-5 py-5">
                              <div className="font-black text-slate-900">{goal.goal_title}</div>
                              {goal.goal_text && <div className="mt-2 text-[13px] font-semibold leading-5 text-slate-600">{goal.goal_text}</div>}
                              {goal.success_metrics && <div className="mt-2 text-[12px] font-bold text-slate-500">Метрики: {goal.success_metrics}</div>}
                              {goal.expected_result && <div className="mt-1 text-[12px] font-bold text-slate-500">Результат: {goal.expected_result}</div>}
                            </td>
                            <td className="px-5 py-5 whitespace-nowrap font-semibold text-slate-700">{GOAL_LEVEL_LABELS[goal.goal_level] || goal.goal_level}</td>
                            <td className="px-5 py-5 whitespace-nowrap font-semibold text-slate-700">{goal.owner_name || (goal.goal_level === "company" ? "Компания" : "-")}</td>
                            <td className="px-5 py-5 whitespace-nowrap font-semibold text-slate-700">
                              <div>{GOAL_PERIOD_LABELS[goal.period_type] || goal.period_type}</div>
                              <div className="mt-1 text-[12px] text-slate-400">{goal.period_start || "-"} - {goal.period_end || "-"}</div>
                            </td>
                            <td className="px-5 py-5 whitespace-nowrap">
                              <span className={cn(
                                "rounded-full px-2.5 py-1 text-[11px] font-black uppercase",
                                goal.status === "done"
                                  ? "bg-emerald-50 text-emerald-700"
                                  : goal.status === "active"
                                    ? "bg-blue-50 text-blue-700"
                                    : goal.status === "cancelled"
                                      ? "bg-red-50 text-red-700"
                                      : "bg-slate-100 text-slate-600",
                              )}>
                                {GOAL_STATUS_LABELS[goal.status] || goal.status}
                              </span>
                            </td>
                            <td className="px-5 py-5 whitespace-nowrap">
                              <div className="font-semibold text-slate-700">{GOAL_SOURCE_LABELS[goal.source_type || ""] || goal.source_type || "-"}</div>
                              <div className="mt-1 text-[12px] font-semibold text-slate-400">{goal.updated_at_text}</div>
                            </td>
                          </tr>
                        ))}
                        {!goalsLoading && goalRows.length === 0 && (
                          <tr>
                            <td colSpan={6} className="px-6 py-12 text-center text-slate-500 font-bold">
                              Цели не найдены. Добавьте вручную или сформируйте ИИ-отчеты по чатам.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      );
    }

    if (activeTab === "Диалоги") {
      return (
        <div className="animate-in fade-in duration-200">
          <DialogsView />
        </div>
      );
    }

    if (activeTab === "Агенты") {
      return (
        <div className="animate-in fade-in duration-200">
          <AgentsView />
        </div>
      );
    }

    if (activeTab === "База знаний") {
      return (
        <div className="animate-in fade-in duration-200">
          <KnowledgeBaseView />
        </div>
      );
    }

    if (activeTab === "Мониторинг") {
      return (
        <div className="animate-in fade-in duration-200">
          <MonitoringView />
        </div>
      );
    }

    if (activeTab === "Использование") {
      return (
        <div className="animate-in fade-in duration-200">
          <UsageView />
        </div>
      );
    }

    if (activeTab === "Настройки") {
      // Access management moved to Центр Агента → Агенты (Команда и доступы);
      // the old agent_settings panel is gone.
      return renderAiInstructionsSettings();
    }

    if (activeTab !== "Сводная аналитика") {
      return (
        <div className="flex min-h-[560px] items-center justify-center animate-in fade-in duration-200">
          <div className="w-full max-w-xl text-center">
            <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-3xl border border-[#E6EAF2] bg-white text-[#5440F6] shadow-[0_18px_50px_-28px_rgba(15,23,42,0.45)]">
              <Clock className="h-9 w-9" strokeWidth={2.4} />
            </div>
            <h2 className="mb-3 text-[28px] font-black tracking-tight text-slate-950">
              Раздел «{activeTab}»
            </h2>
            <p className="mx-auto mb-7 max-w-md text-[15px] font-semibold leading-7 text-slate-500">
              Раздел готовится к внедрению. Сейчас здесь нет активных рабочих виджетов.
            </p>
            <button
              onClick={() => setActiveTab("Сводная аналитика")}
              className="inline-flex items-center justify-center rounded-xl bg-[#5440F6] px-6 py-3 text-[14px] font-black text-white shadow-md shadow-[#5440F6]/20 transition-all hover:bg-[#4532db] active:scale-95"
            >
              Вернуться к сводке
            </button>
          </div>
        </div>
      );
    }

    if (activeTab === "Сводная аналитика" && summaryTab === "reports") {
      const sectionTabs = [
        { id: "reports" as const, label: "Отчеты" },
        { id: "registry" as const, label: "Реестр задач" },
        { id: "chats" as const, label: "Реестр чатов" },
        { id: "prompts" as const, label: "Настройка промтов" },
        { id: "ai_requests" as const, label: "ИИ запросы" },
      ];
      const reportKinds = [
        { id: "daily" as const, label: "Ежедневные отчеты", itemTitle: "Ежедневный отчет" },
        { id: "weekly" as const, label: "Еженедельные отчеты", itemTitle: "Еженедельный отчет" },
        { id: "monthly" as const, label: "Ежемесячные отчеты", itemTitle: "Ежемесячный отчет" },
        { id: "quarterly" as const, label: "Ежеквартальные отчеты", itemTitle: "Ежеквартальный отчет" },
        { id: "yearly" as const, label: "Ежегодные отчеты", itemTitle: "Ежегодный отчет" },
      ];
      const sectionMeta = {
        reports: {
          title: "Отчеты по задачам",
          subtitle: "Недельные и ежемесячные сводки по команде",
          icon: FileCheck,
        },
        registry: {
          title: "Реестр задач",
          subtitle: "Список всех задач с отслеживанием статусов",
          icon: ListTodo,
        },
        chats: {
          title: "Реестр чатов",
          subtitle: "Список чатов и их участники",
          icon: MessageSquareText,
        },
        prompts: {
          title: "Настройка промтов",
          subtitle: "Настройте промты для автоматической генерации отчетов",
          icon: SlidersHorizontal,
        },
        ai_requests: {
          title: "ИИ запросы",
          subtitle: "Показывает, что отправляем в ИИ и что получаем в ответ",
          icon: History,
        },
      }[tasksSubTab];
      const SectionIcon = sectionMeta.icon;

      return (
        <div className="flex flex-col gap-6 animate-in fade-in duration-300 h-full">
          {/* Sub Navigation */}
          <div className="flex justify-between items-center mb-0">
            <div className="flex items-center gap-2 bg-white p-1.5 rounded-xl border border-[#Eef0f4] w-max shadow-sm">
              <button
                onClick={() => setSummaryTab("reports")}
                className={cn(
                  "px-5 py-2 rounded-lg text-[13px] font-bold transition-all",
                  summaryTab === "reports"
                    ? "bg-[#5440F6] text-white shadow-md shadow-[#5440F6]/20"
                    : "text-slate-500 hover:text-slate-900",
                )}
              >
                Отчет по задачам
              </button>
            </div>
          </div>

          <div className="bg-white rounded-3xl p-8 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] h-full flex flex-col">
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-2xl bg-amber-50 text-amber-600 flex items-center justify-center shrink-0 border border-amber-100">
                  <SectionIcon className="w-6 h-6" strokeWidth={2.5} />
                </div>
                <div>
                  <h2 className="text-2xl font-bold text-slate-900 tracking-tight">
                    {sectionMeta.title}
                  </h2>
                  <p className="text-sm text-slate-500 font-medium mt-1">
                    {sectionMeta.subtitle}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-1 bg-slate-100 p-1.5 rounded-xl">
                  {sectionTabs.map((tab) => (
                    <button
                      key={tab.id}
                      onClick={() => {
                        setTasksSubTab(tab.id);
                        window.history.pushState(null, "", tab.id === "registry" ? "/registry" : "/main");
                      }}
                      className={cn(
                        "px-4 py-1.5 rounded-lg text-[13px] font-bold transition-all whitespace-nowrap",
                        tasksSubTab === tab.id
                          ? "bg-white text-slate-900 shadow-sm"
                          : "text-slate-500 hover:text-slate-700 hover:bg-slate-200/50"
                      )}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
                {tasksSubTab === "reports" ? (
                  <button 
                    onClick={() => {
                      const now = new Date();
                      setReportModalMonth(new Date(now.getFullYear(), now.getMonth(), 1));
                      setReportCreateMessage("");
                      setSelectedReportDay(null);
                      setSelectedReportWeekStart(null);
                      setSelectedReportMonth(null);
                      setSelectedReportQuarter(null);
                      setSelectedReportYear(null);
                      setIsReportModalOpen(true);
                    }}
                    className="flex items-center gap-2 bg-[#5440F6] hover:bg-[#4532db] text-white px-5 py-2.5 rounded-xl font-bold text-sm shadow-md shadow-[#5440F6]/20 transition-all active:scale-95">
                    <Plus className="w-4 h-4" strokeWidth={3} /> Создать новый отчет
                  </button>
                ) : tasksSubTab === "registry" ? (
                  <button
                    onClick={syncAllBitrixData}
                    disabled={fullSyncLoading || taskRegistryLoading}
                    className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-white px-5 py-2.5 rounded-xl font-bold text-sm shadow-md transition-all active:scale-95 disabled:opacity-60"
                  >
                    <RefreshCw className={cn("w-4 h-4", fullSyncLoading && "animate-spin")} strokeWidth={2.5} />
                    {fullSyncLoading ? "Обновление..." : "Обновить данные"}
                  </button>
                ) : tasksSubTab === "chats" ? (
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    <button
                      onClick={syncChatRegistry}
                      disabled={chatRegistryLoading}
                      className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-white px-4 py-2.5 rounded-xl font-bold text-sm shadow-md transition-all active:scale-95 disabled:opacity-60"
                    >
                      <RefreshCw className={cn("w-4 h-4", chatRegistryLoading && "animate-spin")} strokeWidth={2.5} />
                      {chatRegistryLoading ? "Обновление..." : "Обновить чаты"}
                    </button>
                  </div>
                ) : tasksSubTab === "ai_requests" ? (
                  <button
                    onClick={() => void loadAiRequests()}
                    disabled={aiRequestsLoading}
                    className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-white px-5 py-2.5 rounded-xl font-bold text-sm shadow-md transition-all active:scale-95 disabled:opacity-60"
                  >
                    <RefreshCw className={cn("w-4 h-4", aiRequestsLoading && "animate-spin")} strokeWidth={2.5} /> Обновить
                  </button>
                ) : null}
              </div>
            </div>

            {tasksSubTab === "reports" && (
              <div className="flex flex-col gap-5 flex-1 min-h-0">
                <div className="flex flex-wrap items-center gap-3 border-b border-[#Eef0f4] pb-4">
                  {reportKinds.map((kind) => (
                    <button
                      key={kind.id}
                      onClick={() => setReportKind(kind.id)}
                      className={cn(
                        "px-5 py-2.5 rounded-lg text-[14px] font-bold transition-all",
                        reportKind === kind.id
                          ? "bg-[#E2E8F0] text-slate-900"
                          : "text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                      )}
                    >
                      {kind.label}
                    </button>
                  ))}
                </div>
                {reportCreateMessage && (
                  <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] px-4 py-3 text-[13px] font-bold text-slate-700">
                    {reportCreateMessage}
                  </div>
                )}

                <div className="grid gap-4 flex-1 overflow-y-auto pr-2">
                  {reportsFeedLoading ? (
                    <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-6 text-[14px] font-bold text-slate-500">
                      Загрузка отчетов...
                    </div>
                  ) : reportsFeedItems.length === 0 ? (
                    <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-6 text-[14px] font-bold text-slate-500">
                      Отчеты этого типа пока не сформированы.
                    </div>
                  ) : (
                    reportsFeedItems.map((item) => (
                      <div
                        key={item.id}
                        onClick={() => setSelectedOwnerReport({
                          id: item.id,
                          kind: item.kind,
                          title: item.title,
                          subtitle: item.subtitle,
                          reportText: item.reportText,
                          reportDate: item.reportDate,
                          summary: item.summary,
                          dynamics: item.dynamics,
                          risks: item.risks,
                          recommendations: item.recommendations,
                        })}
                        onContextMenu={(event) => {
                          event.preventDefault();
                          setReportContextMenu({ item, x: event.clientX, y: event.clientY });
                        }}
                        className="flex justify-between items-center bg-white border border-[#Eef0f4] hover:border-[#CBD5E1] hover:shadow-sm transition-all p-4 rounded-2xl cursor-pointer"
                      >
                        <div className="flex items-center gap-5">
                          <div className="w-11 h-11 rounded-[12px] bg-[#F8FAFC] border border-[#Eef0f4] flex items-center justify-center shadow-sm">
                            <FileText className="w-5 h-5 text-slate-400" strokeWidth={2.5} />
                          </div>
                          <div>
                            <h3 className="text-[15px] font-bold text-slate-900 leading-tight">
                              {item.title}
                            </h3>
                            <div className="flex items-center gap-2 mt-1.5">
                              <CalendarDays className="w-3.5 h-3.5 text-slate-400" />
                              <span className="text-[13px] font-medium text-slate-500">{item.subtitle}</span>
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {item.kind === "daily" && (
                            <button
                              onClick={(event) => {
                                event.stopPropagation();
                                const report = {
                                  id: item.id,
                                  kind: item.kind,
                                  title: item.title,
                                  subtitle: item.subtitle,
                                  status: item.status,
                                  reportText: item.reportText,
                                  reportDate: item.reportDate,
                                  summary: item.summary,
                                  dynamics: item.dynamics,
                                  risks: item.risks,
                                  recommendations: item.recommendations,
                                };
                                void openOwnerReportSend(report);
                              }}
                              className="h-9 px-3 rounded-xl bg-[#5440F6] text-white font-bold text-[12px] flex items-center gap-1.5 shadow-md shadow-[#5440F6]/20"
                            >
                              <Send className="w-3.5 h-3.5" strokeWidth={2.5} />
                              Отправить
                            </button>
                          )}
                          <span className="bg-emerald-50 border border-emerald-100 text-emerald-600 px-3 py-1 rounded-lg text-[12px] font-bold shadow-sm inline-flex items-center gap-1.5">
                            <CheckCircle2 className="w-3.5 h-3.5" /> {item.status}
                          </span>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}

            {tasksSubTab === "registry" && (
              <div className="flex flex-col flex-1 h-full min-h-0 gap-4">
                <div className="grid grid-cols-1 xl:grid-cols-[1fr_1fr_auto_auto_auto] gap-3 items-end">
                  <label className="flex flex-col gap-1.5 text-[12px] font-bold text-slate-500">
                    Период с
                    <CustomDateInput value={taskFilters.date_from} onChange={(value) => updateTaskFilter("date_from", value)} />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[12px] font-bold text-slate-500">
                    Период по
                    <CustomDateInput value={taskFilters.date_to} onChange={(value) => updateTaskFilter("date_to", value)} />
                  </label>
                  <button onClick={syncTaskRegistry} disabled={taskRegistryLoading || fullSyncLoading} className="h-11 px-5 rounded-xl bg-slate-900 text-white font-bold text-[13px] disabled:opacity-60">
                    {taskRegistryLoading ? "Обновление..." : "Обновить из Bitrix"}
                  </button>
                  <button onClick={exportTaskRegistry} disabled={taskRegistryLoading || fullSyncLoading} className="h-11 px-5 rounded-xl bg-[#5440F6] text-white font-bold text-[13px] disabled:opacity-60">
                    Скачать JSON
                  </button>
                  {taskRegistryDownloadUrl && (
                    <a href={taskRegistryDownloadUrl} className="h-11 px-4 rounded-xl bg-emerald-50 text-emerald-700 border border-emerald-100 font-bold text-[13px] inline-flex items-center justify-center">
                      JSON готов
                    </a>
                  )}
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-3">
                  <label className="flex flex-col gap-1.5 text-[12px] font-bold text-slate-500">
                    Поиск
                    <div className="relative">
                      <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                      <input value={taskFilters.q} onChange={(e) => updateTaskFilter("q", e.target.value)} placeholder="Умный поиск по задачам" className="w-full h-11 pl-10 pr-3 rounded-xl bg-[#F8FAFC] border border-[#Eef0f4] outline-none text-[13px] font-semibold" />
                    </div>
                  </label>
                  <label className="flex flex-col gap-1.5 text-[12px] font-bold text-slate-500">
                    Код статуса
                    <input value={taskFilters.status} onChange={(e) => updateTaskFilter("status", e.target.value)} placeholder="completed" className="h-11 px-4 rounded-xl bg-[#F8FAFC] border border-[#Eef0f4] outline-none text-[13px] font-semibold" />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[12px] font-bold text-slate-500">
                    ID исполнителя
                    <input value={taskFilters.responsible_id} onChange={(e) => updateTaskFilter("responsible_id", e.target.value)} placeholder="30207" className="h-11 px-4 rounded-xl bg-[#F8FAFC] border border-[#Eef0f4] outline-none text-[13px] font-semibold" />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[12px] font-bold text-slate-500">
                    Создана с
                    <CustomDateInput value={taskFilters.created_from} onChange={(value) => updateTaskFilter("created_from", value)} />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[12px] font-bold text-slate-500">
                    Дедлайн с
                    <CustomDateInput value={taskFilters.deadline_from} onChange={(value) => updateTaskFilter("deadline_from", value)} />
                  </label>
                  <button onClick={() => loadTaskRegistry()} disabled={taskRegistryLoading || fullSyncLoading} className="h-11 self-end rounded-xl bg-[#EEF2F7] text-slate-700 font-bold text-[13px] disabled:opacity-60">
                    Фильтровать
                  </button>
                </div>

                {taskRegistryMessage && (
                  <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] px-4 py-3 text-[13px] font-bold text-slate-700">
                    {taskRegistryMessage}
                  </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-3">
                  {[
                    ["Задач в базе", taskStats?.total ?? 0],
                    ["Показано по фильтру", taskStats?.filtered_total ?? 0],
                    ...(taskStats?.by_status || []).slice(0, 4).map((item) => [item.status, item.count] as [string, number]),
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-4">
                      <div className="text-[11px] uppercase font-black text-slate-500 mb-2">{label}</div>
                      <div className="text-[24px] font-black text-slate-950">{value}</div>
                    </div>
                  ))}
                </div>

                <div className="border border-[#Eef0f4] rounded-2xl overflow-hidden flex-1 flex flex-col min-h-[420px]">
                  <div className="overflow-auto flex-1">
                    <table className="w-full text-left text-[13px] text-slate-600">
                      <thead className="sticky top-0 bg-[#F8FAFC] text-slate-900 font-bold border-b border-[#Eef0f4] whitespace-nowrap">
                        <tr className="text-[13px]">
                          <th className="px-5 py-4 font-bold">ID</th>
                          <th className="px-5 py-4 font-bold">Задача</th>
                          <th className="px-5 py-4 font-bold">Статус</th>
                          <th className="px-5 py-4 font-bold">Исполнитель</th>
                          <th className="px-5 py-4 font-bold">Постановщик</th>
                          <th className="px-5 py-4 font-bold">Создана</th>
                          <th className="px-5 py-4 font-bold">Дедлайн</th>
                          <th className="px-5 py-4 font-bold">Закрыта</th>
                          <th className="px-5 py-4 font-bold">Данные</th>
                          <th className="px-5 py-4 font-bold">Синхронизация</th>
                          <th className="px-5 py-4 font-bold">JSON</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[#Eef0f4] font-medium">
                        {taskRows.map((item) => (
                          <tr key={item.task_id} className="hover:bg-[#F8FAFC] transition-colors">
                            <td className="px-5 py-4 text-slate-500 whitespace-nowrap">{item.task_id}</td>
                            <td className="px-5 py-4 text-[#475569] min-w-[280px] max-w-md whitespace-normal">{item.title || "-"}</td>
                            <td className="px-5 py-4 whitespace-nowrap">
                              <span className="px-2.5 py-1.5 rounded-full text-[12px] font-bold bg-[#F0F4FF] text-[#3366CC]">
                                {item.status_label || item.status_code || "-"}
                              </span>
                            </td>
                            <td className="px-5 py-4 whitespace-nowrap text-slate-700">
                              <div>{item.responsible_name || "-"}</div>
                              {item.responsible_id && <div className="text-slate-400 text-[12px] font-normal mt-0.5">ID {item.responsible_id}</div>}
                            </td>
                            <td className="px-5 py-4 whitespace-nowrap text-slate-700">
                              <div>{item.creator_name || "-"}</div>
                              {item.creator_id && <div className="text-slate-400 text-[12px] font-normal mt-0.5">ID {item.creator_id}</div>}
                            </td>
                            <td className="px-5 py-4 whitespace-nowrap text-slate-700">{item.created_date_text}</td>
                            <td className="px-5 py-4 whitespace-nowrap text-slate-700">{item.deadline_text}</td>
                            <td className="px-5 py-4 whitespace-nowrap text-slate-700">{item.closed_date_text}</td>
                            <td className="px-5 py-4 whitespace-nowrap text-slate-600">
                              <div className="flex gap-3">
                                <span>К: {item.comments_count}</span>
                                <span>Р: {item.results_count}</span>
                                <span>И: {item.history_count}</span>
                                <span>Ч: {item.checklist_count}</span>
                              </div>
                            </td>
                            <td className="px-5 py-4 whitespace-nowrap text-slate-700">{item.last_synced_at_text}</td>
                            <td className="px-5 py-4 whitespace-nowrap">
                              <a className="text-[#3366CC] hover:underline font-bold underline-offset-2" href={`/task/${item.task_id}`} target="_blank">
                                открыть
                              </a>
                            </td>
                          </tr>
                        ))}
                        {!taskRegistryLoading && taskRows.length === 0 && (
                          <tr><td colSpan={11} className="px-5 py-10 text-center text-slate-500 font-bold">В базе пока нет задач по текущему фильтру.</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

            {tasksSubTab === "chats" && (
              <div className="flex flex-col gap-4 flex-1">
                {chatRegistryMessage && (
                  <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] px-4 py-3 text-[13px] font-bold text-slate-700">
                    {chatRegistryMessage}
                  </div>
                )}
                <div className="flex items-center justify-end">
                  <button
                    onClick={() => void openChatReportsHistory()}
                    disabled={chatWeeklyHistoryLoading}
                    className="h-10 px-4 rounded-xl bg-white border border-[#Eef0f4] text-slate-800 font-bold text-[13px] disabled:opacity-60 flex items-center gap-2"
                  >
                    <History className="w-4 h-4" />
                    История отчетов
                  </button>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-4">
                    <div className="text-[11px] uppercase font-black text-slate-500 mb-2">Активных чатов</div>
                    <div className="text-[24px] font-black text-slate-950">{chatRows.filter((chat) => !chat.is_excluded).length}</div>
                  </div>
                  <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-4">
                    <div className="text-[11px] uppercase font-black text-slate-500 mb-2">Исключено</div>
                    <div className="text-[24px] font-black text-amber-600">{chatRows.filter((chat) => chat.is_excluded).length}</div>
                  </div>
                  <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-4">
                    <div className="text-[11px] uppercase font-black text-slate-500 mb-2">Источник</div>
                    <div className="text-[18px] font-black text-slate-950">Bitrix IM</div>
                  </div>
                </div>
                <div className="border border-[#Eef0f4] rounded-2xl overflow-hidden flex-1 bg-white">
                <table className="w-full text-left text-[14px]">
                  <thead className="bg-[#F8FAFC] border-b border-[#Eef0f4] text-slate-900">
                    <tr>
                      <th className="px-6 py-4 font-bold">Название чата</th>
                      <th className="px-6 py-4 font-bold">Участники</th>
                      <th className="px-6 py-4 font-bold">Активность</th>
                      <th className="px-6 py-4 font-bold">Последняя сводка</th>
                      <th className="px-6 py-4 font-bold">Аналитика</th>
                      <th className="px-6 py-4 font-bold">Синхронизация</th>
                      <th className="px-6 py-4 font-bold text-right">Действия</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#Eef0f4]">
                    {chatRows.map((chat) => (
                      <tr
                        key={chat.dialog_id}
                        onContextMenu={(event) => {
                          event.preventDefault();
                          setChatContextMenu({ chat, x: event.clientX, y: event.clientY });
                        }}
                        className={cn(
                          "transition-colors",
                          chat.is_excluded
                            ? "bg-amber-50/80 hover:bg-amber-100/70"
                            : "hover:bg-[#F8FAFC]"
                        )}
                      >
                        <td className="px-6 py-5">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-bold text-slate-900">{chat.title || chat.dialog_id}</span>
                            {chat.is_excluded ? (
                              <span className="px-2 py-0.5 rounded-md bg-amber-100 text-amber-700 text-[11px] font-black">
                                Исключен
                              </span>
                            ) : null}
                          </div>
                          <div className="text-[12px] text-slate-400 font-semibold mt-1">
                            {chat.dialog_id}{chat.chat_id ? ` · ID ${chat.chat_id}` : ""}
                          </div>
                        </td>
                        <td className="px-6 py-5">
                          <div className="flex flex-wrap gap-2">
                            {chat.members.slice(0, 8).map((member) => (
                              <span key={member.user_id} className="px-3 py-1 rounded-lg bg-slate-100 text-slate-600 text-[13px] font-medium">
                                {member.name || `ID ${member.user_id}`}
                              </span>
                            ))}
                            {chat.member_count > 8 && (
                              <span className="px-3 py-1 rounded-lg bg-slate-200 text-slate-700 text-[13px] font-bold">
                                +{chat.member_count - 8}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-6 py-5 text-slate-600 font-semibold whitespace-nowrap">{chat.last_activity_date_text}</td>
                        <td className="px-6 py-5">
                          {chat.reports?.[0] ? (
                            <span className="px-2.5 py-1 rounded-lg bg-emerald-50 text-emerald-700 text-[12px] font-black whitespace-nowrap">
                              {chat.reports[0].date_text}
                            </span>
                          ) : (
                            <span className="text-[12px] font-bold text-slate-400">Нет</span>
                          )}
                        </td>
                        <td className="px-6 py-5 min-w-[180px]">
                          {chat.analytics?.[0] ? (
                            <div className="flex flex-col gap-1">
                              <span className={cn(
                                "px-2.5 py-1 rounded-lg text-[12px] font-black whitespace-nowrap w-max",
                                chat.analytics[0].status === "done"
                                  ? "bg-blue-50 text-blue-700"
                                  : chat.analytics[0].status === "no_data"
                                    ? "bg-slate-100 text-slate-500"
                                    : "bg-red-50 text-red-700"
                              )}>
                                {chat.analytics[0].date_text}
                              </span>
                              <span className="text-[11px] font-bold text-slate-400">
                                З: {chat.analytics[0].tasks_saved} · Ф: {chat.analytics[0].facts_saved} · Ц: {chat.analytics[0].goals_saved}
                              </span>
                            </div>
                          ) : (
                            <span className="text-[12px] font-bold text-slate-400">Нет</span>
                          )}
                        </td>
                        <td className="px-6 py-5 text-slate-600 font-semibold whitespace-nowrap">{chat.last_synced_at_text}</td>
                        <td className="px-6 py-5 text-right">
                          <button
                            onClick={() => void openChatSummary(chat)}
                            className="text-[#1D4ED8] hover:underline underline-offset-2 font-semibold"
                          >
                            Открыть сводку
                          </button>
                        </td>
                      </tr>
                    ))}
                    {!chatRegistryLoading && chatRows.length === 0 && (
                      <tr>
                        <td colSpan={7} className="px-6 py-10 text-center text-slate-500 font-bold">
                          В базе пока нет командных чатов. Нажмите «Обновить чаты».
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
                </div>
                {false && selectedChat && (
                  <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] p-5">
                    <div className="flex flex-wrap items-end justify-between gap-4 mb-5">
                      <div>
                        <h3 className="text-[20px] font-black text-slate-950">{selectedChat.title || selectedChat.dialog_id}</h3>
                        <p className="text-[13px] font-semibold text-slate-500 mt-1">Сводка сообщений за выбранный день</p>
                      </div>
                      <div className="flex flex-wrap items-end gap-3">
                        <label className="flex flex-col gap-1.5 text-[12px] font-bold text-slate-500">
                          День
                          <CustomDateInput
                            value={chatSummaryDate}
                            onChange={(value) => {
                              setChatSummaryDate(value);
                              void loadChatDay(selectedChat.dialog_id, value);
                            }}
                          />
                        </label>
                        <button onClick={syncChatMessages} disabled={chatDayLoading} className="h-11 px-5 rounded-xl bg-slate-900 text-white font-bold text-[13px] disabled:opacity-60">
                          Загрузить сообщения
                        </button>
                        <button onClick={generateAllChatReports} disabled={chatDayLoading} className="h-11 px-5 rounded-xl bg-[#5440F6] text-white font-bold text-[13px] disabled:opacity-60">
                          Отчеты по всем чатам
                        </button>
                        <button onClick={() => setSelectedChat(null)} className="h-11 px-4 rounded-xl bg-white border border-[#Eef0f4] text-slate-600 font-bold text-[13px]">
                          Закрыть
                        </button>
                      </div>
                    </div>

                    {chatDayMessage && (
                      <div className="rounded-xl border border-[#Eef0f4] bg-white px-4 py-3 text-[13px] font-bold text-slate-700 mb-4">
                        {chatDayMessage}
                      </div>
                    )}

                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                      <div className="rounded-2xl border border-[#Eef0f4] bg-white p-5 min-h-[360px]">
                        <div className="flex items-center justify-between mb-4">
                          <h4 className="text-[16px] font-black text-slate-950">Расшифровка дня</h4>
                          <span className="text-[12px] font-bold text-slate-400">{chatDay?.messages.length ?? 0} сообщений</span>
                        </div>
                        <div className="max-h-[420px] overflow-auto pr-2 text-[13px] leading-6">
                          {!chatDay || chatDay.messages.length === 0 ? (
                            <div className="text-slate-500 font-bold">Нет сообщений</div>
                          ) : (
                            chatDay.messages.map((message) => (
                              <div key={message.message_id} className="border-b border-slate-100 py-3 last:border-b-0">
                                <div className="flex flex-wrap items-center gap-2 mb-1">
                                  <span className="font-black text-slate-900">{message.author_name}</span>
                                  <span className="text-[12px] font-semibold text-slate-400">{message.message_date_text}</span>
                                </div>
                                <div className="text-slate-700 whitespace-pre-wrap">{message.text || (message.files?.length ? "Вложение без текста" : "Без текста")}</div>
                                {message.files?.length > 0 && (
                                  <div className="mt-3 flex flex-wrap gap-3">
                                    {message.files.map((file) => {
                                      const fileUrl = file.preview_url || file.show_url || file.download_url || "";
                                      return (
                                        <a
                                          key={file.file_id}
                                          href={file.show_url || file.download_url || file.preview_url || "#"}
                                          target="_blank"
                                          className="block rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-2 hover:border-[#CBD5E1] transition-colors"
                                        >
                                          {file.is_image && fileUrl ? (
                                            <img
                                              src={fileUrl}
                                              alt={file.name || "Фото"}
                                              className="h-32 w-44 object-cover rounded-lg border border-slate-100 bg-white"
                                            />
                                          ) : (
                                            <div className="h-20 w-44 rounded-lg bg-white border border-slate-100 flex items-center justify-center text-slate-400">
                                              <FileText className="w-7 h-7" />
                                            </div>
                                          )}
                                          <div className="mt-2 max-w-44 truncate text-[12px] font-bold text-slate-600">
                                            {file.name || `Файл ${file.file_id}`}
                                          </div>
                                        </a>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            ))
                          )}
                        </div>
                      </div>

                      <div className="rounded-2xl border border-[#Eef0f4] bg-white p-5 min-h-[360px]">
                        <div className="flex items-center justify-between mb-4">
                          <h4 className="text-[16px] font-black text-slate-950">ИИ-отчет</h4>
                          <span className="text-[12px] font-bold text-slate-400">{chatDay?.report?.model || "не сформирован"}</span>
                        </div>
                        <div className="max-h-[420px] overflow-auto whitespace-pre-wrap text-[14px] leading-7 text-slate-700 font-medium">
                          {chatDay?.report?.report_text || "Отчет еще не сформирован."}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {tasksSubTab === "prompts" && (
              <div className="flex flex-col flex-1 min-h-[520px] rounded-2xl border border-[#Eef0f4] overflow-hidden bg-[#F8FAFC]">
                <div className="flex flex-wrap gap-3 px-6 py-5 border-b border-[#Eef0f4]">
                  {(Object.entries(PROMPT_TEMPLATES) as Array<[PromptKey, (typeof PROMPT_TEMPLATES)[PromptKey]]>)
                    .filter(([key]) => !HIDDEN_PROMPT_TABS.has(key))
                    .map(([key, item]) => (
                    <button
                      key={key}
                      onClick={() => setPromptTab(key)}
                      className={cn(
                        "px-5 py-2.5 rounded-lg text-[14px] font-bold transition-all",
                        promptTab === key ? "bg-[#5440F6] text-white shadow-md shadow-[#5440F6]/20" : "text-slate-500 hover:text-slate-900 hover:bg-white"
                      )}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
                <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_420px] gap-5 flex-1 p-6">
                  <div className="flex min-h-[520px] flex-col">
                    <textarea
                      value={promptTexts[promptTab]}
                      onChange={(e) => setPromptTexts((current) => ({ ...current, [promptTab]: e.target.value }))}
                      className="flex-1 min-h-[420px] resize-none rounded-2xl border border-[#Eef0f4] bg-white p-6 text-[15px] leading-7 text-slate-700 outline-none focus:border-[#5440F6] focus:ring-2 focus:ring-[#5440F6]/10 transition-all"
                    />
                    <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className={cn("min-h-5 text-[13px] font-bold", promptMessage ? "text-slate-600" : "text-transparent")}>
                        {promptMessage || "."}
                      </div>
                      <button
                        onClick={() => void savePrompt()}
                        disabled={promptSaving || !promptTexts[promptTab].trim()}
                        className="inline-flex items-center justify-center gap-2 rounded-xl bg-[#5440F6] px-7 py-3 text-[14px] font-bold text-white shadow-md shadow-[#5440F6]/20 transition-all hover:bg-[#4532db] active:scale-95 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
                      >
                        <FileCheck className="h-4 w-4" />
                        {promptSaving ? "Сохраняю" : "Сохранить"}
                      </button>
                    </div>
                  </div>

                  <aside className="flex min-h-[520px] flex-col rounded-2xl border border-[#Eef0f4] bg-white">
                    <div className="flex items-center justify-between gap-3 border-b border-[#Eef0f4] px-5 py-4">
                      <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-slate-50 text-[#5440F6]">
                          <History className="h-5 w-5" />
                        </div>
                        <div>
                          <h4 className="text-[15px] font-black text-slate-950">История изменений</h4>
                          <p className="text-[12px] font-bold text-slate-400">{PROMPT_TEMPLATES[promptTab].label}</p>
                        </div>
                      </div>
                      <button
                        onClick={() => void loadPromptHistory(promptTab)}
                        className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-[#Eef0f4] text-slate-500 transition-all hover:border-[#5440F6]/30 hover:text-[#5440F6]"
                        title="Обновить историю"
                      >
                        <RefreshCw className={cn("h-4 w-4", promptHistoryLoading && "animate-spin")} />
                      </button>
                    </div>

                    <div className="flex-1 overflow-auto p-4">
                      {promptHistoryLoading && !(promptHistory[promptTab] || []).length ? (
                        <div className="py-12 text-center text-[13px] font-bold text-slate-400">Загружаю историю...</div>
                      ) : !(promptHistory[promptTab] || []).length ? (
                        <div className="py-12 text-center text-[13px] font-bold text-slate-400">Истории пока нет.</div>
                      ) : (
                        <div className="flex flex-col gap-3">
                          {(promptHistory[promptTab] || []).map((version) => (
                            <div key={version.id} className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-4">
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-[14px] font-black text-slate-950">Версия {version.version}</span>
                                    {version.is_active && (
                                      <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-black uppercase text-emerald-700">
                                        активная
                                      </span>
                                    )}
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] font-bold text-slate-400">
                                    <span>{formatPromptDateTime(version.created_at)}</span>
                                    <span>{version.ai_requests_count} использ.</span>
                                  </div>
                                </div>
                                <button
                                  onClick={() => void deletePromptVersion(version)}
                                  disabled={version.is_active}
                                  className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-transparent text-slate-400 transition-all hover:border-red-100 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:hover:border-transparent disabled:hover:bg-transparent disabled:hover:text-slate-300"
                                  title={version.is_active ? "Активную версию нельзя удалить" : "Удалить версию"}
                                >
                                  <Trash2 className="h-4 w-4" />
                                </button>
                              </div>

                              <details className="mt-3">
                                <summary className="cursor-pointer select-none text-[12px] font-black text-[#5440F6]">
                                  Показать текст
                                </summary>
                                <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-white p-3 text-[12px] leading-5 text-slate-600">
                                  {version.text}
                                </pre>
                              </details>

                              <button
                                onClick={() => usePromptVersion(version)}
                                className="mt-3 inline-flex items-center gap-2 rounded-lg border border-[#Eef0f4] bg-white px-3 py-2 text-[12px] font-black text-slate-600 transition-all hover:border-[#5440F6]/30 hover:text-[#5440F6]"
                              >
                                <FileText className="h-4 w-4" />
                                В редактор
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </aside>
                </div>
              </div>
            )}

            {tasksSubTab === "ai_requests" && (
              <div className="grid grid-cols-1 xl:grid-cols-[420px_minmax(0,1fr)] gap-5 flex-1 min-h-[520px]">
                <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] overflow-hidden flex flex-col">
                  <div className="px-5 py-4 border-b border-[#Eef0f4] text-[13px] font-black text-slate-500">
                    Последние запросы в ИИ
                  </div>
                  <div className="flex-1 overflow-auto p-3 space-y-2">
                    {aiRequestsLoading && aiRequests.length === 0 ? (
                      <div className="py-10 text-center text-[13px] font-bold text-slate-400">Загрузка...</div>
                    ) : aiRequests.length === 0 ? (
                      <div className="py-10 text-center text-[13px] font-bold text-slate-400">Запросов пока нет.</div>
                    ) : (
                      aiRequests.map((req) => (
                        <button
                          key={req.id}
                          onClick={() => setSelectedAiRequest(req.id)}
                          className={cn(
                            "w-full text-left rounded-xl border p-3 transition-all",
                            selectedAiRequest === req.id ? "border-[#5440F6]/30 bg-white" : "border-[#Eef0f4] bg-white hover:border-[#CBD5E1]"
                          )}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <div className="text-[12px] font-black text-slate-900 truncate">{req.request_type}</div>
                            <span className={cn(
                              "text-[10px] px-2 py-0.5 rounded-full font-black uppercase",
                              req.status === "success" ? "bg-emerald-50 text-emerald-700" : req.status === "error" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-700"
                            )}>
                              {req.status}
                            </span>
                          </div>
                          <div className="mt-1 text-[11px] font-semibold text-slate-500">{req.provider} • {req.model}</div>
                          <div className="mt-1 text-[11px] font-semibold text-slate-400">{req.created_at_text}</div>
                        </button>
                      ))
                    )}
                  </div>
                </div>
                <div className="rounded-2xl border border-[#Eef0f4] bg-white overflow-hidden flex flex-col">
                  <div className="px-6 py-4 border-b border-[#Eef0f4]">
                    <div className="text-[14px] font-black text-slate-900">Детали запроса</div>
                    <div className="text-[12px] font-semibold text-slate-500 mt-1">Промт, payload, ответ и ошибка</div>
                  </div>
                  <div className="flex-1 overflow-auto p-6">
                    {!selectedAiRequest ? (
                      <div className="text-[13px] font-bold text-slate-400">Выберите запрос слева.</div>
                    ) : (
                      (() => {
                        const req = aiRequests.find((item) => item.id === selectedAiRequest);
                        if (!req) return <div className="text-[13px] font-bold text-slate-400">Запрос не найден.</div>;
                        return (
                          <div className="space-y-5">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                              <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-3 text-[12px] font-semibold text-slate-700">Тип: <span className="font-black">{req.request_type}</span></div>
                              <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-3 text-[12px] font-semibold text-slate-700">Статус: <span className="font-black">{req.status}</span></div>
                              <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-3 text-[12px] font-semibold text-slate-700">Провайдер: <span className="font-black">{req.provider}</span></div>
                              <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-3 text-[12px] font-semibold text-slate-700">Модель: <span className="font-black">{req.model}</span></div>
                            </div>
                            {req.error_text && (
                              <div>
                                <div className="text-[12px] font-black text-red-700 uppercase mb-2">Ошибка</div>
                                <pre className="rounded-xl border border-red-100 bg-red-50 p-4 text-[12px] whitespace-pre-wrap text-red-800">{req.error_text}</pre>
                              </div>
                            )}
                            <div>
                              <div className="text-[12px] font-black text-slate-700 uppercase mb-2">Промт</div>
                              <pre className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-4 text-[12px] whitespace-pre-wrap text-slate-700">{req.prompt_text_snapshot || "-"}</pre>
                            </div>
                            <div>
                              <div className="text-[12px] font-black text-slate-700 uppercase mb-2">Input payload</div>
                              <pre className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-4 text-[12px] whitespace-pre-wrap text-slate-700">{JSON.stringify(req.input_payload || {}, null, 2)}</pre>
                            </div>
                            <div>
                              <div className="text-[12px] font-black text-slate-700 uppercase mb-2">Response text</div>
                              <pre className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-4 text-[12px] whitespace-pre-wrap text-slate-700">{req.response_text || "-"}</pre>
                            </div>
                            <div>
                              <div className="text-[12px] font-black text-slate-700 uppercase mb-2">Response JSON</div>
                              <pre className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-4 text-[12px] whitespace-pre-wrap text-slate-700">{JSON.stringify(req.response_json || {}, null, 2)}</pre>
                            </div>
                          </div>
                        );
                      })()
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      );
    }

    return (
      <div className="flex flex-col gap-6 animate-in fade-in duration-300 h-full">
        {/* Sub Navigation */}
        <div className="flex justify-between items-center mb-0">
          <div className="flex items-center gap-2 bg-white p-1.5 rounded-xl border border-[#Eef0f4] w-max shadow-sm">
            <button
              onClick={() => setSummaryTab("reports")}
              className={cn(
                "px-5 py-2 rounded-lg text-[13px] font-bold transition-all",
                summaryTab === "reports"
                  ? "bg-[#5440F6] text-white shadow-md shadow-[#5440F6]/20"
                  : "text-slate-500 hover:text-slate-900",
              )}
            >
              Отчет по задачам
            </button>
          </div>
        </div>

        <div className="animate-in fade-in duration-300">
          {/* TOP METRICS GRID */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8 text-slate-900">
          {/* Card 1: Bank Balance */}
          <div className="bg-white rounded-3xl p-6 px-7 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex items-start flex-col justify-center min-h-[145px] relative">
            <span className="absolute top-4 right-4 bg-slate-50 text-slate-400 font-bold text-[10px] uppercase tracking-wider px-2 py-1 rounded-md">
              Актуально на {formatDateWithTime(selectedDate)}
            </span>
            <div className="flex gap-4 items-center w-full mt-2">
              <div className="w-12 h-12 rounded-[14px] bg-[#EEF2FF] flex justify-center items-center shrink-0">
                <Landmark
                  className="w-[22px] h-[22px] text-[#5440F6]"
                  strokeWidth={2.5}
                />
              </div>
              <div className="flex flex-col">
                <p className="text-slate-500 text-[12px] mb-1 font-bold tracking-wide uppercase">
                  Баланс трех банков
                </p>
                <div className="flex items-center gap-2 mt-0.5">
                  <h3 className="text-[28px] font-bold leading-none tracking-tight">
                    6 550 000,00{" "}
                    <span className="text-slate-400 text-xl font-semibold">
                      ₽
                    </span>
                  </h3>
                </div>
              </div>
            </div>
          </div>

          {/* Card 2 & 3 (Wide): Liquidity Forecast */}
          <div className="md:col-span-2 bg-white rounded-3xl p-6 px-7 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex flex-col justify-center min-h-[145px] relative overflow-hidden">
            <span className="absolute top-4 right-4 bg-slate-50 text-slate-400 font-bold text-[10px] uppercase tracking-wider px-2 py-1 rounded-md hidden sm:block">
              Остаток + Поступления - Платежи
            </span>

            <div className="flex items-center gap-3 mb-4 mt-1">
              <div className="w-6 h-6 rounded-lg bg-[#E7F8ED] shrink-0 flex items-center justify-center">
                <TrendingUp
                  className="w-3.5 h-3.5 text-emerald-600"
                  strokeWidth={3}
                />
              </div>
              <p className="text-slate-900 text-[15px] font-bold tracking-tight">
                Прогноз ликвидности
              </p>
            </div>

            <div className="flex items-stretch justify-between gap-4">
              {/* 7 Days */}
              <div className="flex-1 flex flex-col justify-between border-r border-[#Eef0f4] pr-4">
                <div className="text-[12px] font-bold text-slate-400 mb-1 leading-none">
                  7 дней
                </div>
                <div className="text-[22px] font-bold text-slate-800 leading-none mb-2">
                  {formatMoney(forecast7.value)}{" "}
                  <span className="text-[18px] text-slate-400">₽</span>
                </div>
                <div className="flex items-center gap-2 text-[11px] font-bold">
                  <span className="text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                    <ArrowDownRight className="w-3 h-3" />{" "}
                    {formatMoney(forecast7.incoming)}
                  </span>
                  <span className="text-slate-500 bg-slate-50 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                    <ArrowUpRight className="w-3 h-3" />{" "}
                    {formatMoney(forecast7.outgoing)}
                  </span>
                </div>
              </div>

              {/* 14 Days - Dynamic Risk if negative */}
              <div className="flex-1 flex flex-col justify-between border-r border-[#Eef0f4] pr-4 relative">
                {forecast14.value < 0 && (
                  <div className="absolute top-0 right-4 w-2 h-2 rounded-full bg-red-500 animate-pulse"></div>
                )}
                <div
                  className={cn(
                    "text-[12px] font-bold mb-1 leading-none uppercase tracking-wide",
                    forecast14.value < 0 ? "text-red-500" : "text-slate-400",
                  )}
                >
                  14 дней {forecast14.value < 0 && "(Риск)"}
                </div>
                <div
                  className={cn(
                    "text-[22px] font-bold leading-none mb-2",
                    forecast14.value < 0 ? "text-red-600" : "text-slate-800",
                  )}
                >
                  {formatMoney(forecast14.value)}{" "}
                  <span
                    className={cn(
                      "text-[18px]",
                      forecast14.value < 0 ? "text-red-400" : "text-slate-400",
                    )}
                  >
                    ₽
                  </span>
                </div>
                <div className="flex items-center gap-2 text-[11px] font-bold">
                  <span className="text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                    <ArrowDownRight className="w-3 h-3" />{" "}
                    {formatMoney(forecast14.incoming)}
                  </span>
                  <span
                    className={cn(
                      "px-1.5 py-0.5 rounded flex items-center gap-0.5",
                      forecast14.value < 0
                        ? "text-red-600 bg-red-50 shadow-[0_0_0_1px_rgba(239,68,68,0.2)]"
                        : "text-slate-500 bg-slate-50",
                    )}
                  >
                    <ArrowUpRight className="w-3 h-3" />{" "}
                    {formatMoney(forecast14.outgoing)}
                  </span>
                </div>
              </div>

              {/* 30 Days */}
              <div className="flex-1 flex flex-col justify-between pl-2">
                <div className="text-[12px] font-bold text-slate-400 mb-1 leading-none">
                  30 дней
                </div>
                <div
                  className={cn(
                    "text-[22px] font-bold leading-none mb-2",
                    forecast30.value < 0 ? "text-red-600" : "text-slate-800",
                  )}
                >
                  {formatMoney(forecast30.value)}{" "}
                  <span
                    className={cn(
                      "text-[18px]",
                      forecast30.value < 0 ? "text-red-400" : "text-slate-400",
                    )}
                  >
                    ₽
                  </span>
                </div>
                <div className="flex items-center gap-2 text-[11px] font-bold">
                  <span className="text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                    <ArrowDownRight className="w-3 h-3" />{" "}
                    {formatMoney(forecast30.incoming)}
                  </span>
                  <span className="text-slate-500 bg-slate-50 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                    <ArrowUpRight className="w-3 h-3" />{" "}
                    {formatMoney(forecast30.outgoing)}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Card 4: Overdue Tasks */}
          <div className="bg-white rounded-3xl p-6 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex items-start flex-col justify-center min-h-[145px] cursor-pointer group hover:border-amber-200/50 transition-colors relative">
            <div className="absolute right-0 top-0 w-32 h-32 bg-amber-50/50 rounded-full blur-[40px] -mr-10 -mt-10 pointer-events-none group-hover:bg-amber-100/50 transition-colors"></div>
            <div className="flex gap-4 items-center w-full relative z-10 p-1 mt-2">
              <div className="w-12 h-12 rounded-[14px] bg-[#FFFBEB] flex justify-center items-center shrink-0">
                <ListTodo
                  className="w-[22px] h-[22px] text-amber-500"
                  strokeWidth={2.5}
                />
              </div>
              <div className="flex flex-col">
                <p className="text-slate-500 text-[12px] mb-1 font-bold tracking-wide uppercase group-hover:text-amber-600 transition-colors">
                  Долги команды
                </p>
                <div className="flex items-center gap-2 mt-0.5 mb-1.5">
                  <h3 className="text-[28px] font-bold leading-none tracking-tight text-amber-600">
                    {teamDebtsCount}
                  </h3>
                  <span className="bg-[#FEF3C7] text-amber-700 text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wide">
                    Задач
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* MAIN TWO-COLUMN LAYOUT */}
        <div className="grid grid-cols-1 xl:grid-cols-[2fr_1.1fr] gap-[30px]">
          {/* LEFT COLUMN */}
          <div className="flex flex-col gap-[30px]">
            {/* CALENDAR MODULE */}
            <div>
              <div className="flex items-center justify-between mb-5">
                <div className="flex items-center gap-3">
                  <CalendarDays className="w-6 h-6 text-[#5440F6]" />
                  <h2 className="text-[22px] font-bold text-slate-900">
                    Календарь платежей
                  </h2>
                </div>
                <div className="bg-slate-200/50 px-3 py-1.5 rounded-lg text-[12px] font-bold text-slate-500 hidden sm:block">
                  Понедельник
                </div>
              </div>

              <div className="bg-white rounded-3xl border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.02)] overflow-hidden">
                {/* Calendar Header with selected month */}
                <div className="flex items-center justify-between px-6 py-4 bg-white border-b border-[#Eef0f4]">
                  <div className="text-xl font-bold bg-white text-slate-800 flex items-center gap-3">
                    <button
                      onClick={handlePrevMonth}
                      className="w-8 h-8 flex items-center justify-center hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"
                    >
                      <ChevronLeft className="w-5 h-5" />
                    </button>
                    <div className="min-w-[120px] text-center">
                      {MONTHS[currentMonthIdx]}{" "}
                      <span className="text-slate-400 font-medium ml-1">
                        {currentYear}
                      </span>
                    </div>
                    <button
                      onClick={handleNextMonth}
                      className="w-8 h-8 flex items-center justify-center hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"
                    >
                      <ChevronRight className="w-5 h-5" />
                    </button>
                  </div>
                  <div
                    className="text-[13px] font-bold text-[#5440F6] bg-[#F5F3FF] px-3 py-1.5 rounded-lg cursor-pointer hover:bg-[#EEF2FF] transition-colors"
                    onClick={() => setCalendarViewDate(selectedDate)}
                  >
                    Вернуться к {formatDateShort(selectedDate)}
                  </div>
                </div>

                {/* Calendar Grid */}
                <div className="grid grid-cols-7 w-full bg-[#f8fafc]">
                  {/* Days of week */}
                  {["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"].map((day) => (
                    <div
                      key={day}
                      className="py-4 text-center text-[10px] uppercase font-bold text-slate-400 tracking-wider bg-white border-b border-r border-[#Eef0f4] last:border-r-0"
                    >
                      {day}
                    </div>
                  ))}

                  {/* Dates */}
                  {wbCalendarData.map((item, idx) => {
                    const isLastRow = Math.floor(idx / 7) === 4;
                    const isLastCol = idx % 7 === 6;

                    const wbPayment = item.dayPayments?.find(
                      (p) => p.source === "wb_api",
                    );
                    const manualIncoming =
                      item.dayPayments
                        ?.filter(
                          (p) => p.type === "incoming" && p.source !== "wb_api",
                        )
                        .reduce((sum, p) => sum + p.amount, 0) || 0;
                    const manualOutgoing =
                      item.dayPayments
                        ?.filter((p) => p.type === "outgoing")
                        .reduce((sum, p) => sum + p.amount, 0) || 0;
                    const hasAnyPayment = (item.dayPayments?.length || 0) > 0;

                    return (
                      <div
                        key={idx}
                        className={cn(
                          "min-h-[120px] p-2.5 relative bg-white border-[#Eef0f4] group transition-colors",
                          !isLastCol && "border-r",
                          !isLastRow && "border-b",
                          hasAnyPayment &&
                            "hover:bg-slate-50/50 cursor-pointer",
                          item.active && "bg-[#F5F3FF]/30",
                        )}
                      >
                        {item.day && (
                          <>
                            <div className="flex justify-between items-start absolute top-3 left-3 right-3 z-20">
                              <span
                                className={cn(
                                  "font-bold text-[13px]",
                                  item.active
                                    ? "text-white"
                                    : hasAnyPayment
                                      ? "text-slate-800 font-extrabold"
                                      : "text-slate-500",
                                )}
                              >
                                {item.day}
                              </span>
                            </div>

                            {item.active && (
                              <div className="absolute top-[8px] left-[8px] w-[26px] h-[26px] rounded-full bg-[#5440F6] flex items-center justify-center shadow-md shadow-[#5440F6]/30 z-10"></div>
                            )}

                            <div className="mt-[28px] flex flex-col gap-[3px] z-20 relative">
                              {/* WB Payout Block */}
                              {wbPayment && (
                                <div
                                  className={cn(
                                    "flex flex-col gap-[1px] p-1.5 rounded-lg transition-all",
                                    item.isProjected || wbPayment.isProjected
                                      ? "bg-[#F8FAFC] border border-dashed border-[#CBD5E1]"
                                      : "bg-[#EEF2FF] border border-[#E0E7FF]",
                                  )}
                                >
                                  <span
                                    className={cn(
                                      "text-[9px] font-extrabold uppercase tracking-wide",
                                      item.isProjected || wbPayment.isProjected
                                        ? "text-slate-400"
                                        : "text-[#5440F6]",
                                    )}
                                  >
                                    WB
                                  </span>
                                  <span
                                    className={cn(
                                      "text-[13px] font-bold tracking-tight",
                                      item.isProjected || wbPayment.isProjected
                                        ? "text-slate-600"
                                        : "text-[#4F46E5]",
                                    )}
                                  >
                                    {formatCompact(wbPayment.amount)}
                                  </span>
                                </div>
                              )}

                              {/* Incoming other than WB */}
                              {manualIncoming > 0 && (
                                <div className="flex items-center justify-between bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded text-[11px] font-bold border border-emerald-100">
                                  <div className="flex items-center gap-0.5">
                                    <ArrowDownRight className="w-3 h-3 text-emerald-500" />
                                  </div>
                                  <span>+{formatCompact(manualIncoming)}</span>
                                </div>
                              )}

                              {/* Outgoing */}
                              {manualOutgoing > 0 && (
                                <div className="flex items-center justify-between bg-red-50 text-red-700 px-1.5 py-0.5 rounded text-[11px] font-bold border border-red-100">
                                  <div className="flex items-center gap-0.5">
                                    <ArrowUpRight className="w-3 h-3 text-red-500" />
                                  </div>
                                  <span>-{formatCompact(manualOutgoing)}</span>
                                </div>
                              )}
                            </div>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>

          {/* RIGHT COLUMN */}
          <div className="flex flex-col gap-[30px]">
            {/* BANKS DETAILS MODULE */}
            <div className="bg-white rounded-3xl p-7 py-8 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex flex-col relative min-h-[300px]">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-[22px] font-bold text-slate-900">
                  Детализация счетов
                </h2>
                <button className="text-[#5440F6] hover:bg-[#F5F3FF] p-2 rounded-xl transition-colors">
                  <ArrowRight className="w-5 h-5" strokeWidth={2.5} />
                </button>
              </div>
              <p className="text-[13px] font-semibold text-slate-400 mb-6 flex items-center gap-1.5">
                Актуально на{" "}
                <span className="bg-slate-100 text-slate-600 px-2 py-0.5 rounded text-[11px] uppercase tracking-wider">
                  {formatDateWithTime(selectedDate)}
                </span>
              </p>

              <div className="flex flex-col gap-4 flex-1">
                {BANKS_DATA.map((bank, idx) => (
                  <div
                    key={bank.id}
                    className="group relative p-4 rounded-2xl border border-[#F1F5F9] bg-[#F8FAFC] hover:bg-white hover:border-[#Eef0f4] hover:shadow-sm cursor-pointer transition-all flex items-center justify-between"
                  >
                    <div className="flex items-center gap-4">
                      <div
                        className={cn(
                          "w-12 h-12 rounded-xl flex items-center justify-center font-bold text-lg shadow-sm border border-black/5",
                          bank.color,
                          bank.textColor,
                        )}
                      >
                        {bank.logo}
                      </div>
                      <div>
                        <div className="text-slate-900 font-bold text-[15px]">
                          {bank.name}
                        </div>
                        <div className="text-slate-400 font-semibold text-[12px]">
                          {bank.type}
                        </div>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-bold text-[17px] text-slate-800 tracking-tight">
                        {bank.amount} ₽
                      </div>
                      <div className="text-emerald-500 font-bold text-[11px] uppercase tracking-wide flex items-center justify-end gap-1 mt-0.5">
                        Синхронизировано
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* ACTION CENTER / URGENT ALERTS */}
            <div className="bg-white rounded-3xl p-7 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex flex-col flex-1">
              <h2 className="text-[20px] font-bold text-slate-900 mb-4">
                Журнал внимания
              </h2>

              <div className="flex flex-col gap-3 mt-2">
                {forecast14.value < 0 ? (
                  <div className="p-4 rounded-2xl bg-red-50/50 border border-red-100 flex gap-4 cursor-pointer hover:bg-red-50 transition-colors">
                    <div className="mt-0.5 shrink-0">
                      <AlertTriangle
                        className="w-5 h-5 text-red-500"
                        strokeWidth={2.5}
                      />
                    </div>
                    <div>
                      <h4 className="text-[14px] font-bold text-red-900 mb-1">
                        Опасность кассового разрыва (В течение 14 дней)
                      </h4>
                      <p className="text-[13px] text-red-700/80 font-medium leading-relaxed">
                        По нашим прогнозам, зафиксирован риск ухода в минус. Ожидаемый ликвидный баланс через 14 дней:{" "}
                        <span className="font-bold text-red-700 bg-red-100/50 px-1 py-0.5 rounded cursor-help" title={`Поступления: +${formatMoney(forecast14.incoming)}, Расходы: -${formatMoney(forecast14.outgoing)}`}>
                          {formatMoney(forecast14.value)} ₽
                        </span>
                        . Пересмотрите запланированные крупные платежи.
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="p-4 rounded-2xl bg-emerald-50/50 border border-emerald-100 flex gap-4 cursor-pointer hover:bg-emerald-50 transition-colors">
                    <div className="mt-0.5 shrink-0">
                      <CheckCircle2
                        className="w-5 h-5 text-emerald-500"
                        strokeWidth={2.5}
                      />
                    </div>
                    <div>
                      <h4 className="text-[14px] font-bold text-emerald-900 mb-1">
                        Финансовое положение стабильно
                      </h4>
                      <p className="text-[13px] text-emerald-700/80 font-medium leading-relaxed">
                        Ожидаемый ликвидный баланс через 14 дней составит{" "}
                        <span className="font-bold text-emerald-700 bg-emerald-100/50 px-1 py-0.5 rounded cursor-help" title={`Поступления: +${formatMoney(forecast14.incoming)}, Расходы: -${formatMoney(forecast14.outgoing)}`}>
                          {formatMoney(forecast14.value)} ₽
                        </span>
                        . Риск разрыва отсутствует.
                      </p>
                    </div>
                  </div>
                )}

                {teamDebtsCount > 0 && (
                  <div className="p-4 rounded-2xl bg-amber-50/50 border border-amber-100 flex gap-4 cursor-pointer hover:bg-amber-50 transition-colors">
                    <div className="mt-0.5 shrink-0">
                      <ListTodo
                        className="w-5 h-5 text-amber-500"
                        strokeWidth={2.5}
                      />
                    </div>
                    <div>
                      <h4 className="text-[14px] font-bold text-amber-900 mb-1">
                        Просрочки команды ({teamDebtsCount} шт.)
                      </h4>
                      <p className="text-[13px] text-amber-700/80 font-medium leading-relaxed">
                        {teamDebtsDetails}
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* WILDBERRIES METRICS DASHBOARD */}
        <div className="mt-[40px]">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-xl bg-[#F5F3FF] flex justify-center items-center shadow-sm">
              <Store className="w-5 h-5 text-[#5440F6]" />
            </div>
            <h2 className="text-[24px] font-bold text-slate-900 tracking-tight">
              Аналитика Wildberries
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-[30px]">
            {/* Problem SKU */}
            <div className="bg-white rounded-3xl p-6 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex items-center gap-4 relative overflow-hidden group">
              <div className="absolute top-0 right-0 w-24 h-24 bg-red-50/50 rounded-full blur-[30px] -mr-8 -mt-8 pointer-events-none group-hover:bg-red-100/60 transition-colors"></div>
              <div className="w-14 h-14 rounded-2xl bg-red-50 text-red-500 flex items-center justify-center shrink-0 border border-red-100/50">
                <AlertOctagon className="w-7 h-7" strokeWidth={2} />
              </div>
              <div className="relative z-10">
                <p className="text-[12px] font-bold text-slate-500 uppercase tracking-wide mb-1">
                  Проблемные артикулы
                </p>
                <div className="flex items-baseline gap-2">
                  <span className="text-[28px] font-bold text-slate-900 leading-none">
                    14
                  </span>
                  <span className="text-[12px] font-bold text-red-500 bg-red-50 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                    <ArrowUpRight className="w-3 h-3" /> 2 с вчера
                  </span>
                </div>
              </div>
            </div>

            {/* OOS Warning */}
            <div className="bg-white rounded-3xl p-6 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex items-center gap-4 relative overflow-hidden group">
              <div className="absolute top-0 right-0 w-24 h-24 bg-amber-50/50 rounded-full blur-[30px] -mr-8 -mt-8 pointer-events-none group-hover:bg-amber-100/60 transition-colors"></div>
              <div className="w-14 h-14 rounded-2xl bg-amber-50 text-amber-500 flex items-center justify-center shrink-0 border border-amber-100/50">
                <PackageX className="w-7 h-7" strokeWidth={2} />
              </div>
              <div className="relative z-10">
                <p className="text-[12px] font-bold text-slate-500 uppercase tracking-wide mb-1">
                  Риск Out Of Stock
                </p>
                <div className="flex items-baseline gap-2">
                  <span className="text-[28px] font-bold text-slate-900 leading-none">
                    28
                  </span>
                  <span className="text-[12px] font-bold text-slate-400">
                    до 3 дней
                  </span>
                </div>
              </div>
            </div>

            {/* Return Rate (Daily total) */}
            <div className="bg-white rounded-3xl p-6 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex items-center gap-4 relative overflow-hidden group">
              <div className="absolute top-0 right-0 w-24 h-24 bg-blue-50/50 rounded-full blur-[30px] -mr-8 -mt-8 pointer-events-none group-hover:bg-blue-100/60 transition-colors"></div>
              <div className="w-14 h-14 rounded-2xl bg-blue-50 text-blue-500 flex items-center justify-center shrink-0 border border-blue-100/50">
                <RefreshCw className="w-7 h-7" strokeWidth={2} />
              </div>
              <div className="relative z-10">
                <p className="text-[12px] font-bold text-slate-500 uppercase tracking-wide mb-1">
                  Возвраты (За 7 дней)
                </p>
                <div className="flex items-baseline gap-2">
                  <span className="text-[28px] font-bold text-slate-900 leading-none">
                    106
                  </span>
                  <span className="text-[12px] font-bold text-emerald-500 bg-emerald-50 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                    <ArrowDownRight className="w-3 h-3" /> -12%
                  </span>
                </div>
              </div>
            </div>

            {/* DRR Avrg */}
            <div className="bg-white rounded-3xl p-6 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex items-center gap-4 relative overflow-hidden group">
              <div className="absolute top-0 right-0 w-24 h-24 bg-emerald-50/50 rounded-full blur-[30px] -mr-8 -mt-8 pointer-events-none group-hover:bg-emerald-100/60 transition-colors"></div>
              <div className="w-14 h-14 rounded-2xl bg-[#E7F8ED] text-emerald-600 flex items-center justify-center shrink-0 border border-emerald-100/50">
                <TrendingUp className="w-7 h-7" strokeWidth={2} />
              </div>
              <div className="relative z-10">
                <p className="text-[12px] font-bold text-slate-500 uppercase tracking-wide mb-1">
                  Ср. ДРР (За 7 дней)
                </p>
                <div className="flex items-baseline gap-2">
                  <span className="text-[28px] font-bold text-slate-900 leading-none">
                    4.8%
                  </span>
                  <span className="text-[12px] font-bold text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded">
                    {"<5% (Ок)"}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-[30px]">
            {/* DRR % daily */}
            <div className="bg-white rounded-3xl p-6 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex flex-col min-h-[320px]">
              <div className="flex justify-between items-center mb-6">
                <div>
                  <h3 className="text-[16px] font-bold text-slate-900">
                    ДРР по дням
                  </h3>
                  <p className="text-[12px] font-semibold text-slate-400 mt-1">
                    Доля рекламных расходов (лимит 5%)
                  </p>
                </div>
                <div className="px-2 py-1 bg-slate-100 text-slate-600 rounded text-[11px] font-bold uppercase tracking-wider">
                  7 дней
                </div>
              </div>
              <div className="w-full min-w-0 h-[220px]">
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart
                    data={WB_DRR_DAILY}
                    margin={{ top: 10, right: 0, left: -20, bottom: 0 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      vertical={false}
                      stroke="#Eef0f4"
                    />
                    <XAxis
                      dataKey="day"
                      axisLine={false}
                      tickLine={false}
                      tick={{ fontSize: 12, fill: "#94a3b8", fontWeight: 600 }}
                      dy={10}
                    />
                    <YAxis
                      axisLine={false}
                      tickLine={false}
                      tick={{ fontSize: 12, fill: "#94a3b8", fontWeight: 600 }}
                      tickFormatter={(val) => val + "%"}
                    />
                    <Tooltip
                      cursor={{ fill: "#f8fafc" }}
                      contentStyle={{
                        borderRadius: "12px",
                        border: "1px solid #Eef0f4",
                        boxShadow: "0 4px 12px rgba(0,0,0,0.05)",
                        fontWeight: "bold",
                        fontSize: "13px",
                      }}
                      itemStyle={{ color: "#0f172a" }}
                    />
                    <ReferenceLine
                      y={5}
                      stroke="#ef4444"
                      strokeDasharray="4 4"
                      label={{
                        position: "top",
                        value: "Лимит 5%",
                        fill: "#ef4444",
                        fontSize: 11,
                        fontWeight: "bold",
                      }}
                    />
                    <Bar dataKey="drr" radius={[6, 6, 0, 0]} maxBarSize={40}>
                      {WB_DRR_DAILY.map((entry, index) => (
                        <Cell
                          key={`cell-${index}`}
                          fill={entry.drr > 5 ? "#f87171" : "#34d399"}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Weekly Revenue */}
            <div className="bg-white rounded-3xl p-6 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex flex-col min-h-[320px]">
              <div className="flex justify-between items-center mb-6">
                <div>
                  <h3 className="text-[16px] font-bold text-slate-900">
                    Выручка
                  </h3>
                  <p className="text-[12px] font-semibold text-slate-400 mt-1">
                    В миллионах рублей (по неделям)
                  </p>
                </div>
                <div className="px-2 py-1 bg-slate-100 text-slate-600 rounded text-[11px] font-bold uppercase tracking-wider">
                  5 недель
                </div>
              </div>
              <div className="w-full min-w-0 h-[220px]">
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart
                    data={WB_REVENUE_WEEKS}
                    margin={{ top: 10, right: 0, left: -20, bottom: 0 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      vertical={false}
                      stroke="#Eef0f4"
                    />
                    <XAxis
                      dataKey="week"
                      axisLine={false}
                      tickLine={false}
                      tick={{ fontSize: 12, fill: "#94a3b8", fontWeight: 600 }}
                      dy={10}
                    />
                    <YAxis
                      axisLine={false}
                      tickLine={false}
                      tick={{ fontSize: 12, fill: "#94a3b8", fontWeight: 600 }}
                      tickFormatter={(val) => val + "M"}
                    />
                    <Tooltip
                      cursor={{ fill: "#f8fafc" }}
                      contentStyle={{
                        borderRadius: "12px",
                        border: "1px solid #Eef0f4",
                        boxShadow: "0 4px 12px rgba(0,0,0,0.05)",
                        fontWeight: "bold",
                        fontSize: "13px",
                      }}
                      formatter={(value) => [`${value} млн ₽`, "Выручка"]}
                    />
                    <Bar
                      dataKey="revenue"
                      fill="#5440F6"
                      radius={[6, 6, 0, 0]}
                      maxBarSize={40}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Daily returns */}
            <div className="bg-white rounded-3xl p-6 border border-[#Eef0f4] shadow-[0_4px_20px_-8px_rgba(0,0,0,0.03)] flex flex-col min-h-[320px]">
              <div className="flex justify-between items-center mb-6">
                <div>
                  <h3 className="text-[16px] font-bold text-slate-900">
                    Возвраты товаров
                  </h3>
                  <p className="text-[12px] font-semibold text-slate-400 mt-1">
                    Количество единиц ежедневно
                  </p>
                </div>
              </div>
              <div className="w-full min-w-0 h-[220px]">
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart
                    data={WB_RETURNS_DAILY}
                    margin={{ top: 10, right: 0, left: -20, bottom: 0 }}
                  >
                    <defs>
                      <linearGradient
                        id="colorReturns"
                        x1="0"
                        y1="0"
                        x2="0"
                        y2="1"
                      >
                        <stop
                          offset="5%"
                          stopColor="#3b82f6"
                          stopOpacity={0.2}
                        />
                        <stop
                          offset="95%"
                          stopColor="#3b82f6"
                          stopOpacity={0}
                        />
                      </linearGradient>
                    </defs>
                    <CartesianGrid
                      strokeDasharray="3 3"
                      vertical={false}
                      stroke="#Eef0f4"
                    />
                    <XAxis
                      dataKey="day"
                      axisLine={false}
                      tickLine={false}
                      tick={{ fontSize: 12, fill: "#94a3b8", fontWeight: 600 }}
                      dy={10}
                    />
                    <YAxis
                      axisLine={false}
                      tickLine={false}
                      tick={{ fontSize: 12, fill: "#94a3b8", fontWeight: 600 }}
                    />
                    <Tooltip
                      contentStyle={{
                        borderRadius: "12px",
                        border: "1px solid #Eef0f4",
                        boxShadow: "0 4px 12px rgba(0,0,0,0.05)",
                        fontWeight: "bold",
                        fontSize: "13px",
                      }}
                      formatter={(value) => [`${value} шт.`, "Возвраты"]}
                    />
                    <Area
                      type="monotone"
                      dataKey="returns"
                      stroke="#3b82f6"
                      strokeWidth={3}
                      fillOpacity={1}
                      fill="url(#colorReturns)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </div>
      </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-[#F4F5F8] flex font-sans text-slate-800">
      {/* SIDEBAR */}
      <aside className="w-[260px] bg-white border-r border-[#DDE3EC] flex flex-col fixed inset-y-0 z-10 shrink-0 shadow-[4px_0_24px_-16px_rgba(15,23,42,0.35)]">
        <div className="px-6 pt-8 pb-4 flex items-center gap-3">
          <div className="w-11 h-11 bg-gradient-to-br from-[#6A55FF] to-[#4D38EE] rounded-xl flex items-center justify-center text-white font-bold text-xl shadow-lg shadow-[#5440F6]/25">
            A
          </div>
          <div className="leading-tight">
            <div className="font-extrabold text-[20px] tracking-tight text-slate-950">
              Alberi
            </div>
          </div>
        </div>

        <nav className="flex-1 px-4 py-6 flex flex-col gap-7 overflow-y-auto custom-scrollbar">
          {SIDEBAR_GROUPS.map((group) => (
            <div key={group.title}>
              <h3
                className={cn(
                  "px-3 text-xs font-semibold tracking-wider uppercase mb-3",
                  group.accent ? "text-[#8B7CFF]" : "text-slate-400",
                )}
              >
                {group.title}
              </h3>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const isActive = activeTab === item.label;
                  return (
                    <button
                      key={item.label}
                      onClick={() => setActiveTab(item.label)}
                      className={cn(
                        "w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-[14px] font-bold transition-all duration-200 group",
                        isActive
                          ? "bg-[#EEF2FF] text-[#5440F6]"
                          : "text-slate-600 hover:bg-slate-100 hover:text-slate-950",
                      )}
                    >
                      <item.icon
                        className={cn(
                          "w-[18px] h-[18px] transition-transform",
                          isActive
                            ? "text-[#5440F6]"
                            : "text-slate-400 group-hover:text-slate-700 group-hover:scale-110",
                        )}
                        strokeWidth={isActive ? 2.6 : 2.2}
                      />
                      {item.label}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="p-4 flex flex-col gap-2 border-t border-[#E2E8F0] bg-white">
          <button className="flex items-center gap-3 w-full px-4 py-3 rounded-xl bg-[#F8FAFC] text-slate-700 hover:bg-slate-100 hover:text-slate-950 transition-colors font-bold text-[13px]">
            <Settings
              className="w-[18px] h-[18px] text-slate-500"
              strokeWidth={2.5}
            />
            Мой профиль
          </button>
          <button className="flex items-center gap-2 w-full px-4 py-3 rounded-xl text-slate-400 hover:text-slate-700 transition-colors font-semibold text-[13px] mt-1 -ml-1">
            <ChevronLeft className="w-5 h-5" strokeWidth={2} />
            Свернуть панель
          </button>
        </div>
      </aside>

      {/* MAIN LAYOUT */}
      <div className="flex-1 ml-[260px] flex flex-col min-h-screen">
        {/* HEADER */}
        <header className="flex items-center justify-between px-10 py-5 bg-[#F4F5F8] border-b border-transparent z-20">
          <div className="flex flex-wrap items-center gap-6">
            <div className="flex items-center gap-2 bg-white shadow-[0_2px_8px_-4px_rgba(0,0,0,0.05)] border border-[#Eef0f4] rounded-full px-5 py-2">
              <span className="font-bold text-[#5440F6] text-[14px]">
                Alberi Workspace
              </span>
              <X
                className="w-4 h-4 text-slate-300 ml-1 hover:text-slate-500 cursor-pointer transition-colors"
                strokeWidth={3}
              />
            </div>

            <nav className="flex items-center gap-8 text-[14px] font-semibold text-slate-400 pl-2">
              {["Бухгалтерия", "Склад", "WB-кабинет"].filter((item) => !HIDDEN_HEADER_LINKS.has(item)).map((item) => (
                <span
                  key={item}
                  onClick={() => setActiveTab(item)}
                  className={cn(
                    "cursor-pointer transition-colors flex-shrink-0",
                    activeTab === item
                      ? "text-slate-900"
                      : "hover:text-slate-700",
                  )}
                >
                  {item}
                </span>
              ))}
            </nav>
          </div>

          <div className="flex items-center gap-5 ml-4">
            <div className="relative cursor-pointer group">
              <Bell
                className="w-[22px] h-[22px] text-slate-500 group-hover:text-amber-500 transition-colors"
                strokeWidth={2.5}
              />
              <span className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-red-500 rounded-full border-2 border-[#F4F5F8]"></span>
            </div>
            <div className="w-[40px] h-[40px] bg-white border border-[#Eef0f4] rounded-full flex items-center justify-center font-extrabold text-[15px] text-slate-700 shadow-[0_2px_8px_-4px_rgba(0,0,0,0.1)] cursor-pointer hover:shadow-md transition-shadow">
              Е
            </div>
          </div>
        </header>

        {/* CONTENT AREA */}
        <main className="flex-1 px-10 pb-10 flex flex-col relative z-10">
          {/* PAGE HEADER */}
          <div className="flex items-end justify-between mb-8 pb-2 shrink-0">
            <div>
              <h1 className="text-[34px] font-bold tracking-tight text-slate-900 mb-1 flex items-center gap-3">
                {activeTab}{" "}
                {activeTab === "Сводная аналитика" && (
                  <>
                    <span className="text-slate-300 font-normal text-3xl">
                      /
                    </span>
                    <span className="text-slate-400 font-medium text-[32px]">
                      {summaryTab === "overview"
                        ? "Обзор"
                        : tasksSubTab === "reports"
                          ? "Отчеты по задачам"
                          : tasksSubTab === "registry"
                            ? "Реестр задач"
                            : tasksSubTab === "chats"
                              ? "Реестр чатов"
                              : tasksSubTab === "ai_requests"
                                ? "ИИ запросы"
                                : "Настройка промтов"}
                    </span>
                  </>
                )}
              </h1>
              <p className="text-slate-500 text-[15px] font-medium pl-1 mt-2">
                Евгений, это данные вашей компании
              </p>
            </div>

            <div className="flex items-center gap-3 relative">
              {/* DATE PICKER */}
              <div className="relative">
                <button
                  onClick={() => setIsDatePickerOpen(!isDatePickerOpen)}
                  className="bg-white border border-[#Eef0f4] rounded-xl flex items-center gap-2 px-5 py-2.5 text-[14px] font-bold text-slate-700 shadow-sm cursor-pointer hover:shadow-md hover:border-[#CBD5E1] transition-all active:scale-95"
                >
                  <Calendar
                    className="w-[18px] h-[18px] text-[#5440F6]"
                    strokeWidth={2.5}
                  />
                  <span>На {formatDateShort(selectedDate)}</span>
                  <ChevronDown
                    className={cn(
                      "w-4 h-4 text-slate-400 transition-transform ml-1",
                      isDatePickerOpen && "rotate-180",
                    )}
                    strokeWidth={2.5}
                  />
                </button>

                {renderDatePickerPopver()}
              </div>

              <button className="w-11 h-11 bg-[#5440F6] hover:bg-[#4532db] text-white rounded-xl flex items-center justify-center shadow-md shadow-[#5440F6]/20 transition-all active:scale-95 border border-transparent hover:border-[#2e1db8]">
                <RefreshCw className="w-[18px] h-[18px]" strokeWidth={2.5} />
              </button>
            </div>
          </div>

          {/* DYNAMIC CONTENT */}
          <div className="flex-1">{renderContent()}</div>
        </main>
      </div>

      {selectedZoomCall && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 sm:p-6 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-slate-950/35 backdrop-blur-md" onClick={() => setSelectedZoomCall(null)}></div>
          <div
            className="relative bg-white/95 backdrop-blur-xl rounded-3xl w-full max-w-5xl max-h-[88vh] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300 flex flex-col ring-1 ring-white/70"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="px-6 py-5 border-b border-[#Eef0f4] flex items-start justify-between gap-4 bg-white/90">
              <div className="min-w-0">
                <h3 className="text-xl font-black text-slate-950 truncate">{selectedZoomCall.technical_topic}</h3>
                <div className="flex flex-wrap gap-2 mt-3 text-xs font-bold text-slate-500">
                  <span className="px-3 py-1 rounded-md bg-slate-50 border border-slate-100">{selectedZoomCall.date_text}</span>
                  <span className="px-3 py-1 rounded-md bg-slate-50 border border-slate-100">{selectedZoomCall.time_text}</span>
                  <span className="px-3 py-1 rounded-md bg-slate-50 border border-slate-100">{selectedZoomCall.topic}</span>
                  {selectedZoomCall.synced_at_text && (
                    <span className="px-3 py-1 rounded-md bg-slate-50 border border-slate-100">
                      Обновлено: {selectedZoomCall.synced_at_text}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2 shrink-0">
                <button
                  onClick={() => void generateZoomCallReport(selectedZoomCall)}
                  disabled={zoomCallDetailLoading || Boolean(selectedZoomCall.analytical_note)}
                  className="h-10 px-4 rounded-xl bg-emerald-50 hover:bg-emerald-100 text-emerald-700 text-[13px] font-bold flex items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Sparkles className="w-4 h-4" />
                  Сформировать отчет
                </button>
                <button
                  onClick={() => setZoomTranscriptVisible((current) => !current)}
                  className="h-10 px-4 rounded-xl bg-[#5440F6] hover:bg-[#4532db] text-white text-[13px] font-bold flex items-center justify-center gap-2 shadow-md shadow-[#5440F6]/20"
                >
                  <FileText className="w-4 h-4" />
                  {zoomTranscriptVisible ? "Посмотреть отчет" : "Посмотреть транскрибацию"}
                </button>
                <button
                  onClick={() => {
                    window.location.href = `/api/zoom-calls/${encodeURIComponent(selectedZoomCall.id)}/export.md`;
                  }}
                  className="h-10 px-4 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 text-[13px] font-bold flex items-center justify-center gap-2"
                  title="Скачать транскрипт встречи в Markdown"
                >
                  <Download className="w-4 h-4" />
                  Скачать .md
                </button>
                <button
                  onClick={() => void previewZoomOperationalTasks(selectedZoomCall)}
                  disabled={zoomCallDetailLoading || !selectedZoomCall.analytical_note}
                  className="w-10 h-10 rounded-xl bg-blue-50 hover:bg-blue-100 text-blue-700 flex items-center justify-center disabled:cursor-not-allowed disabled:opacity-50"
                  title="Отправить задачи исполнителям"
                  aria-label="Отправить задачи исполнителям"
                >
                  <ArrowRight className="w-4 h-4" />
                </button>
                <button
                  onClick={() => void deleteZoomCallReport(selectedZoomCall)}
                  disabled={zoomCallDetailLoading || !selectedZoomCall.analytical_note}
                  className="h-10 px-4 rounded-xl bg-red-50 hover:bg-red-100 text-red-700 text-[13px] font-bold flex items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Trash2 className="w-4 h-4" />
                  Удалить отчет
                </button>
                <button
                  onClick={() => setSelectedZoomCall(null)}
                  className="w-10 h-10 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-500 flex items-center justify-center"
                  aria-label="Закрыть"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <div className="px-6 py-4 border-b border-[#Eef0f4] bg-slate-50/70">
              <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Участники</p>
              <div className="flex flex-wrap gap-2">
                {selectedZoomCall.participants.length ? selectedZoomCall.participants.map((participant, idx) => (
                  <span key={`${participant.email || participant.name || idx}`} className="bg-blue-100/60 text-blue-700 text-xs font-bold px-2.5 py-1 rounded flex items-center gap-1.5">
                    <Users className="w-3 h-3" />
                    {participant.name || participant.email}
                  </span>
                )) : (
                  <span className="text-xs font-bold text-slate-400">Не определены</span>
                )}
              </div>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto p-6 bg-white">
              {zoomCallDetailLoading ? (
                <div className="h-40 flex items-center justify-center text-sm font-bold text-slate-400">
                  Загрузка отчета о созвоне...
                </div>
              ) : zoomTranscriptVisible ? (
                <>
                  <h4 className="text-xs font-black uppercase tracking-wider text-slate-400 mb-3">Полная транскрибация</h4>
                  {selectedZoomCall.segments?.length ? (
                    <div className="space-y-2">
                      {selectedZoomCall.segments.map((segment) => (
                        <div key={`${segment.segment_index}-${segment.cue_index}`} className="grid grid-cols-[170px_1fr] gap-4 py-2 border-b border-slate-100 last:border-0">
                          <div className="text-[12px] font-bold text-slate-400">
                            {segment.start_offset} - {segment.end_offset}
                          </div>
                          <div className="text-[14px] leading-relaxed text-slate-800">
                            {segment.speaker && <span className="font-black text-blue-700">{segment.speaker}: </span>}
                            {segment.text}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <pre className="whitespace-pre-wrap text-[14px] leading-relaxed text-slate-800 font-sans">
                      {selectedZoomCall.transcript_text || "Транскрибация отсутствует."}
                    </pre>
                  )}
                </>
              ) : (
                <>
                  <h4 className="text-xs font-black uppercase tracking-wider text-slate-400 mb-3">Отчет о созвоне</h4>
                  <div className="rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
                    {selectedZoomCall.analytical_note ? (
                      <div className="space-y-4">
                        {renderZoomReportText(selectedZoomCall.analytical_note)}
                      </div>
                    ) : (
                      <p className="text-[14px] font-bold text-slate-400">Пока пусто</p>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {zoomDispatchPreview && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center p-4 sm:p-6 animate-in fade-in duration-150">
          <div className="absolute inset-0 bg-slate-950/45 backdrop-blur-sm" onClick={() => setZoomDispatchPreview(null)}></div>
          <div
            className="relative w-full max-w-2xl max-h-[86vh] overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-slate-200 flex flex-col"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="px-5 py-4 border-b border-slate-100 flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h3 className="text-lg font-black text-slate-950">Отправка задач</h3>
                <p className="mt-1 text-xs font-bold text-slate-400">Проверьте получателей и текст перед отправкой</p>
              </div>
              <button
                onClick={() => setZoomDispatchPreview(null)}
                className="w-9 h-9 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-500 flex items-center justify-center"
                aria-label="Закрыть"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-5 space-y-5">
              <section>
                <p className="text-xs font-black uppercase tracking-wider text-slate-400 mb-2">Кому</p>
                <div className="flex flex-wrap gap-2">
                  {zoomDispatchPreview.recipients.map((recipient) => (
                    <span key={recipient.user_id} className="inline-flex items-center gap-1.5 rounded-lg bg-blue-50 px-3 py-1.5 text-xs font-black text-blue-700">
                      <Users className="h-3.5 w-3.5" />
                      {recipient.name}
                    </span>
                  ))}
                </div>
              </section>

              <section>
                <p className="text-xs font-black uppercase tracking-wider text-slate-400 mb-2">Карточки задач и персональных итогов</p>
                <div className="space-y-3">
                  {(zoomDispatchPreview.task_cards?.length
                    ? zoomDispatchPreview.task_cards
                    : [{
                        recipient: zoomDispatchPreview.recipients[0] || null,
                        assignee_name: zoomDispatchPreview.recipients[0]?.name || "Получатель",
                        title: zoomDispatchPreview.title,
                        description: zoomDispatchPreview.description,
                        deadline: zoomDispatchPreview.deadline,
                        deadline_text: zoomDispatchPreview.deadline_text,
                      }]).map((card, index) => (
                    <div key={`${card.recipient?.user_id || card.assignee_name}-${index}`} className="rounded-xl border border-slate-100 bg-white p-4 shadow-sm">
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0">
                          <div className="text-xs font-black uppercase tracking-wider text-slate-400">
                            {card.card_kind === "participant_report" ? "Участник" : "Исполнитель"}
                          </div>
                          <div className={cn(
                            "mt-1 inline-flex max-w-full items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-black",
                            card.card_kind === "participant_report" ? "bg-violet-50 text-violet-700" : "bg-blue-50 text-blue-700",
                          )}>
                            <Users className="h-3.5 w-3.5 shrink-0" />
                            <span className="truncate">{card.recipient?.name || card.assignee_name}</span>
                          </div>
                        </div>
                        {!card.recipient && (
                          <span className="rounded-lg bg-red-50 px-3 py-1.5 text-xs font-black text-red-600">
                            Не найден Bitrix ID
                          </span>
                        )}
                      </div>
                      <div className="mt-4 grid gap-2 sm:grid-cols-[96px_1fr] text-sm">
                        <div className="font-black text-slate-400">Заголовок</div>
                        <div className="font-bold text-slate-900">{card.title}</div>
                        <div className="font-black text-slate-400">Дедлайн</div>
                        <div className="font-bold text-slate-900">{card.deadline_text}</div>
                      </div>
                      <pre className="mt-4 max-h-56 overflow-y-auto whitespace-pre-wrap rounded-xl border border-slate-100 bg-slate-50 p-4 text-[13px] leading-relaxed text-slate-800 font-sans">
                        {card.description}
                      </pre>
                    </div>
                  ))}
                </div>
                {zoomDispatchPreview.unmatched_assignees?.length ? (
                  <div className="mt-3 rounded-xl bg-red-50 px-4 py-3 text-[13px] font-bold text-red-700">
                    Не найдены в Bitrix: {zoomDispatchPreview.unmatched_assignees.join(", ")}
                  </div>
                ) : null}
                {zoomDispatchPreview.participant_reports_error ? (
                  <div className="mt-3 rounded-xl bg-amber-50 px-4 py-3 text-[13px] font-bold text-amber-700">
                    Персональные итоги не добавлены: {zoomDispatchPreview.participant_reports_error}
                  </div>
                ) : null}
              </section>
            </div>

            <div className="px-5 py-4 border-t border-slate-100 bg-slate-50 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              {zoomCallsMessage ? (
                <div className="text-[13px] font-bold text-red-600">{zoomCallsMessage}</div>
              ) : (
                <div></div>
              )}
              <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setZoomDispatchPreview(null)}
                className="h-10 px-4 rounded-xl bg-white hover:bg-slate-100 text-slate-600 text-[13px] font-black border border-slate-200"
              >
                Отмена
              </button>
              <button
                onClick={() => void dispatchZoomOperationalTasks()}
                disabled={zoomCallDetailLoading}
                className="h-10 px-4 rounded-xl bg-blue-600 hover:bg-blue-700 text-white text-[13px] font-black flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ArrowRight className="w-4 h-4" />
                Отправить
              </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {chatContextMenu && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setChatContextMenu(null)}></div>
          <div
            className="fixed z-50 w-56 rounded-2xl border border-[#Eef0f4] bg-white shadow-2xl p-2 animate-in fade-in zoom-in-95 duration-150"
            style={{
              left: Math.min(chatContextMenu.x, window.innerWidth - 240),
              top: Math.min(chatContextMenu.y, window.innerHeight - 92),
            }}
          >
            <button
              onClick={() => void toggleChatExcluded(chatContextMenu.chat)}
              className={cn(
                "w-full px-3 py-2.5 rounded-xl text-left text-[13px] font-black transition-colors",
                chatContextMenu.chat.is_excluded
                  ? "text-emerald-700 hover:bg-emerald-50"
                  : "text-amber-700 hover:bg-amber-50"
              )}
            >
              {chatContextMenu.chat.is_excluded ? "Включить чат" : "Исключить чат"}
            </button>
          </div>
        </>
      )}

      {reportContextMenu && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setReportContextMenu(null)}></div>
          <div
            className="fixed z-50 w-56 rounded-2xl border border-[#Eef0f4] bg-white shadow-2xl p-2 animate-in fade-in zoom-in-95 duration-150"
            style={{
              left: Math.min(reportContextMenu.x, window.innerWidth - 240),
              top: Math.min(reportContextMenu.y, window.innerHeight - 72),
            }}
          >
            <button
              onClick={() => void deleteOwnerReport(reportContextMenu.item)}
              className="flex w-full items-center gap-2 px-3 py-2.5 rounded-xl text-left text-[13px] font-black text-red-700 hover:bg-red-50 transition-colors"
            >
              <Trash2 className="h-4 w-4" strokeWidth={2.5} />
              Удалить отчет
            </button>
          </div>
        </>
      )}

      {companySpaceContextMenu && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setCompanySpaceContextMenu(null)}></div>
          <div
            className="fixed z-50 w-56 rounded-2xl border border-[#Eef0f4] bg-white shadow-2xl p-2 animate-in fade-in zoom-in-95 duration-150"
            style={{
              left: Math.min(companySpaceContextMenu.x, window.innerWidth - 240),
              top: Math.min(companySpaceContextMenu.y, window.innerHeight - 72),
            }}
          >
            <button
              onClick={() => void createCompanyFolder()}
              className="flex w-full items-center gap-2 px-3 py-2.5 rounded-xl text-left text-[13px] font-black text-slate-700 hover:bg-slate-50 transition-colors"
            >
              <Plus className="h-4 w-4" strokeWidth={2.5} />
              Создать папку
            </button>
          </div>
        </>
      )}

      {companyFolderContextMenu && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setCompanyFolderContextMenu(null)}></div>
          <div
            className="fixed z-50 w-56 rounded-2xl border border-[#Eef0f4] bg-white shadow-2xl p-2 animate-in fade-in zoom-in-95 duration-150"
            style={{
              left: Math.min(companyFolderContextMenu.x, window.innerWidth - 240),
              top: Math.min(companyFolderContextMenu.y, window.innerHeight - 132),
            }}
          >
            <button
              onClick={() => startRenameCompanyFolder(companyFolderContextMenu.folder)}
              className="flex w-full items-center gap-2 px-3 py-2.5 rounded-xl text-left text-[13px] font-black text-slate-700 hover:bg-slate-50 transition-colors"
            >
              <Pencil className="h-4 w-4" strokeWidth={2.5} />
              Переименовать
            </button>
            <button
              onClick={() => requestDeleteCompanyFolder(companyFolderContextMenu.folder)}
              className="flex w-full items-center gap-2 px-3 py-2.5 rounded-xl text-left text-[13px] font-black text-red-700 hover:bg-red-50 transition-colors"
            >
              <Trash2 className="h-4 w-4" strokeWidth={2.5} />
              Удалить
            </button>
          </div>
        </>
      )}

      {companyFolderDeleteCandidate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div
            className="absolute inset-0 bg-slate-900/35 backdrop-blur-md"
            onClick={() => setCompanyFolderDeleteCandidate(null)}
          ></div>
          <div className="relative w-full max-w-md rounded-3xl border border-white/60 bg-white/90 p-6 shadow-2xl backdrop-blur-xl animate-in zoom-in-95 duration-200">
            <div className="flex items-start gap-4">
              <div className="h-11 w-11 rounded-2xl bg-red-50 text-red-600 flex items-center justify-center shrink-0">
                <Trash2 className="h-5 w-5" strokeWidth={2.5} />
              </div>
              <div className="min-w-0">
                <h3 className="text-[20px] font-black text-slate-950">Удалить папку?</h3>
                <p className="mt-2 text-[14px] font-semibold leading-6 text-slate-600">
                  «{companyFolderDeleteCandidate.name}» будет удалена вместе со всеми вложенными папками и текстом.
                </p>
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button
                onClick={() => setCompanyFolderDeleteCandidate(null)}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-[13px] font-black text-slate-600 hover:bg-slate-50"
              >
                Отмена
              </button>
              <button
                onClick={() => void deleteCompanyFolder()}
                disabled={companySaving}
                className="rounded-xl bg-red-600 px-4 py-2.5 text-[13px] font-black text-white shadow-md shadow-red-600/20 hover:bg-red-700 disabled:opacity-60"
              >
                {companySaving ? "Удаляем..." : "Удалить"}
              </button>
            </div>
          </div>
        </div>
      )}

      {aiInstructionSpaceContextMenu && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setAiInstructionSpaceContextMenu(null)}></div>
          <div
            className="fixed z-50 w-56 rounded-2xl border border-[#Eef0f4] bg-white shadow-2xl p-2 animate-in fade-in zoom-in-95 duration-150"
            style={{
              left: Math.min(aiInstructionSpaceContextMenu.x, window.innerWidth - 240),
              top: Math.min(aiInstructionSpaceContextMenu.y, window.innerHeight - 72),
            }}
          >
            <button
              onClick={() => void createAiInstructionFolder()}
              className="flex w-full items-center gap-2 px-3 py-2.5 rounded-xl text-left text-[13px] font-black text-slate-700 hover:bg-slate-50 transition-colors"
            >
              <Plus className="h-4 w-4" strokeWidth={2.5} />
              Создать папку
            </button>
          </div>
        </>
      )}

      {aiInstructionFolderContextMenu && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setAiInstructionFolderContextMenu(null)}></div>
          <div
            className="fixed z-50 w-56 rounded-2xl border border-[#Eef0f4] bg-white shadow-2xl p-2 animate-in fade-in zoom-in-95 duration-150"
            style={{
              left: Math.min(aiInstructionFolderContextMenu.x, window.innerWidth - 240),
              top: Math.min(aiInstructionFolderContextMenu.y, window.innerHeight - 132),
            }}
          >
            <button
              onClick={() => startRenameAiInstructionFolder(aiInstructionFolderContextMenu.folder)}
              className="flex w-full items-center gap-2 px-3 py-2.5 rounded-xl text-left text-[13px] font-black text-slate-700 hover:bg-slate-50 transition-colors"
            >
              <Pencil className="h-4 w-4" strokeWidth={2.5} />
              Переименовать
            </button>
            <button
              onClick={() => requestDeleteAiInstructionFolder(aiInstructionFolderContextMenu.folder)}
              className="flex w-full items-center gap-2 px-3 py-2.5 rounded-xl text-left text-[13px] font-black text-red-700 hover:bg-red-50 transition-colors"
            >
              <Trash2 className="h-4 w-4" strokeWidth={2.5} />
              Удалить
            </button>
          </div>
        </>
      )}

      {aiInstructionFolderDeleteCandidate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div
            className="absolute inset-0 bg-slate-900/35 backdrop-blur-md"
            onClick={() => setAiInstructionFolderDeleteCandidate(null)}
          ></div>
          <div className="relative w-full max-w-md rounded-3xl border border-white/60 bg-white/90 p-6 shadow-2xl backdrop-blur-xl animate-in zoom-in-95 duration-200">
            <div className="flex items-start gap-4">
              <div className="h-11 w-11 rounded-2xl bg-red-50 text-red-600 flex items-center justify-center shrink-0">
                <Trash2 className="h-5 w-5" strokeWidth={2.5} />
              </div>
              <div className="min-w-0">
                <h3 className="text-[20px] font-black text-slate-950">Удалить папку?</h3>
                <p className="mt-2 text-[14px] font-semibold leading-6 text-slate-600">
                  «{aiInstructionFolderDeleteCandidate.name}» будет удалена вместе со всеми вложенными папками и текстом.
                </p>
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button
                onClick={() => setAiInstructionFolderDeleteCandidate(null)}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-[13px] font-black text-slate-600 hover:bg-slate-50"
              >
                Отмена
              </button>
              <button
                onClick={() => void deleteAiInstructionFolder()}
                disabled={aiInstructionSaving}
                className="rounded-xl bg-red-600 px-4 py-2.5 text-[13px] font-black text-white shadow-md shadow-red-600/20 hover:bg-red-700 disabled:opacity-60"
              >
                {aiInstructionSaving ? "Удаляем..." : "Удалить"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Weekly Chat Reports History Modal */}
      {isChatWeeklyHistoryOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-slate-900/45 backdrop-blur-sm" onClick={() => setIsChatWeeklyHistoryOpen(false)}></div>
          <div className="relative bg-white rounded-3xl w-full max-w-7xl h-[86vh] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300 flex flex-col">
            <div className="px-8 py-6 border-b border-[#Eef0f4] flex flex-wrap items-start justify-between gap-4 bg-white">
              <div>
                <div className="text-[12px] uppercase font-black text-slate-400 mb-1">Чаты</div>
                <h2 className="text-[24px] font-black text-slate-950">История отчетов по чатам</h2>
                <div className="text-[13px] font-semibold text-slate-500 mt-1">
                  Единая лента: ежедневные отчеты и недельные итоги после каждого воскресенья
                </div>
              </div>
              {(() => {
                const overallTimeline = buildOverallChatHistoryTimeline(chatOverallDailyHistory, chatWeeklyHistory);
                return (
              <div className="flex flex-wrap items-center justify-end gap-2">
                <button
                  onClick={generateChatWeeklyReport}
                  disabled={chatWeeklyHistoryLoading}
                  className="h-10 px-4 rounded-xl bg-[#5440F6] text-white font-bold text-[13px] disabled:opacity-60 flex items-center gap-2"
                >
                  <TrendingUp className="w-4 h-4" />
                  {chatWeeklyHistoryLoading ? "Формирование..." : "Сформировать неделю"}
                </button>
                <button
                  onClick={() => void Promise.all([loadChatOverallDailyHistory(), loadChatWeeklyHistory(false)])}
                  disabled={chatWeeklyHistoryLoading}
                  className="h-10 px-4 rounded-xl bg-white border border-[#Eef0f4] text-slate-800 font-bold text-[13px] disabled:opacity-60 flex items-center gap-2"
                >
                  <RefreshCw className={cn("w-4 h-4", chatWeeklyHistoryLoading && "animate-spin")} />
                  Обновить
                </button>
                <button onClick={() => setIsChatWeeklyHistoryOpen(false)} className="w-10 h-10 flex items-center justify-center rounded-xl hover:bg-slate-100 text-slate-500 transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>
                );
              })()}
            </div>

            {chatDayMessage && (
              <div className="px-8 py-3 border-b border-[#Eef0f4] bg-white text-[13px] font-bold text-slate-600">
                {chatDayMessage}
              </div>
            )}

            <div className="flex-1 overflow-y-auto px-8 py-7 bg-white">
              {(() => {
                const overallTimeline = buildOverallChatHistoryTimeline(chatOverallDailyHistory, chatWeeklyHistory);
                return (
                  <>
                    <div className="flex items-center justify-between gap-4 mb-5">
                      <div>
                        <h3 className="text-[18px] font-black text-slate-950">Список сводок</h3>
                        <p className="text-[13px] font-semibold text-slate-500 mt-1">Ежедневные сводки и недельные итоги после воскресенья</p>
                      </div>
                      <span className="text-[13px] font-black text-slate-400">{overallTimeline.length} сводок</span>
                    </div>

                    <div className="flex flex-col gap-4">
                      {overallTimeline.length > 0 ? (
                        overallTimeline.map((report, index) => {
                          const raw = (report as { raw_json?: { source?: string } }).raw_json;
                          const source = raw?.source || "";
                          const isPlaceholder =
                            source.includes("placeholder") ||
                            (report.summary || "").toLowerCase().includes("заглушка") ||
                            (report.report_text || "").toLowerCase().includes("заглушка");
                          const badgeClass = isPlaceholder
                            ? "bg-amber-50 text-amber-700 border-amber-100"
                            : "bg-blue-50 text-blue-700 border-blue-100";
                          const badgeText = isPlaceholder
                            ? "Не обработан"
                            : report.report_kind === "weekly"
                              ? "Недельный отчет сформирован"
                              : "Отчет сформирован";

                          return (
                            <div
                              key={`${report.report_kind}-${report.report_id}`}
                              className="w-full rounded-2xl border border-[#Eef0f4] bg-white px-5 py-4 flex items-center justify-between gap-4 text-left"
                            >
                              <div className="flex items-center gap-5 min-w-0">
                                <div className="w-12 h-12 rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] flex items-center justify-center text-slate-400 shadow-sm shrink-0">
                                  <FileText className="w-6 h-6" strokeWidth={2.2} />
                                </div>
                                <div className="min-w-0">
                                  <div className="font-black text-[15px] text-slate-900 truncate">
                                    {report.report_kind === "weekly" ? `Неделя ${report.period_text}` : `Сводка ${report.date_text}`}
                                  </div>
                                  <div className="flex flex-wrap items-center gap-3 mt-2 text-[12px] font-bold text-slate-400">
                                    <span className="flex items-center gap-1.5">
                                      <Calendar className="w-3.5 h-3.5" />
                                      {report.report_kind === "weekly" ? report.period_text : report.date_text}
                                    </span>
                                    <span>{report.messages_count} сообщений</span>
                                  </div>
                                </div>
                              </div>
                              <div className="flex flex-wrap items-center justify-end gap-2 shrink-0">
                                <span className={cn("inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] font-black shadow-sm", badgeClass)}>
                                  {isPlaceholder ? <CircleDashed className="w-3.5 h-3.5" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                                  {badgeText}
                                </span>
                                <button
                                  onClick={() =>
                                    openReportEditor({
                                      reportType: report.report_kind === "weekly" ? "chat_overall_weekly" : "chat_overall_daily",
                                      reportId: report.report_id,
                                      title: report.report_kind === "weekly" ? `Сводка чатов за ${report.period_text}` : `Сводка чатов за ${report.date_text}`,
                                      context: "chat_overall_history",
                                      fields: {
                                        summary: report.summary || "",
                                        dynamics_summary: report.dynamics_summary || "",
                                        positives_summary: report.positives_summary || "",
                                        problems_summary: report.problems_summary || "",
                                        recommendations: report.report_kind === "weekly" ? report.recommendations || "" : "",
                                        report_text: report.report_text || "",
                                      },
                                    })
                                  }
                                  className="h-8 px-3 rounded-lg border border-[#Eef0f4] bg-white text-slate-700 font-black text-[12px] inline-flex items-center gap-1.5"
                                >
                                  <Pencil className="w-3.5 h-3.5" />
                                  Править
                                </button>
                                <span className="text-[12px] font-bold text-slate-300">#{index + 1}</span>
                              </div>
                            </div>
                          );
                        })
                      ) : (
                        <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-8 text-center text-slate-500 font-bold">
                          Ежедневные сводные отчеты еще не сформированы.
                        </div>
                      )}
                    </div>
                  </>
                );
              })()}
            </div>
          </div>
        </div>
      )}

      {/* Chat Summaries List Modal */}
      {selectedChat && !isChatDayModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-slate-900/45 backdrop-blur-sm" onClick={() => setSelectedChat(null)}></div>
          <div className="relative bg-white rounded-3xl w-full max-w-6xl h-[82vh] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300 flex flex-col">
            <div className="px-8 py-6 border-b border-[#Eef0f4] flex flex-wrap items-start justify-between gap-4 bg-white">
              <div>
                <div className="text-[12px] uppercase font-black text-slate-400 mb-1">Диалог</div>
                <h2 className="text-[24px] font-black text-slate-950">{selectedChat.title || selectedChat.dialog_id}</h2>
                <div className="text-[13px] font-semibold text-slate-500 mt-1">
                  {selectedChat.dialog_id}{selectedChat.chat_id ? ` · ID ${selectedChat.chat_id}` : ""} · {selectedChat.member_count} участников
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <CustomDateRangeInput
                  dateFrom={chatPeriodFrom}
                  dateTo={chatPeriodTo}
                  max={moscowTodayIso()}
                  onChange={(dateFrom, dateTo) => {
                    setChatPeriodFrom(dateFrom);
                    setChatPeriodTo(dateTo);
                    setChatSummaryDate(dateTo);
                  }}
                  className="w-72"
                />
                <button onClick={syncChatMessages} disabled={chatDayLoading} className="h-10 px-4 rounded-xl bg-slate-900 text-white font-bold text-[13px] disabled:opacity-60">
                  Загрузить сообщения
                </button>
                <button onClick={processSelectedChatImages} disabled={chatDayLoading} className="h-10 px-4 rounded-xl bg-white border border-[#Eef0f4] text-slate-800 font-bold text-[13px] disabled:opacity-60">
                  Обработка картинок
                </button>
                <button onClick={generateAllChatReports} disabled={chatDayLoading} className="h-10 px-4 rounded-xl bg-[#5440F6] text-white font-bold text-[13px] disabled:opacity-60">
                  Отчеты по всем чатам
                </button>
                <button onClick={generateSelectedChatWeeklyReport} disabled={chatDayLoading} className="h-10 px-4 rounded-xl bg-white border border-[#Eef0f4] text-slate-800 font-bold text-[13px] disabled:opacity-60">
                  Отчет за неделю
                </button>
                <button onClick={() => setSelectedChat(null)} className="w-10 h-10 flex items-center justify-center rounded-xl hover:bg-slate-100 text-slate-500 transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-8 py-7 bg-white">
              <div className="flex items-center justify-between gap-4 mb-5">
                <div>
                  <h3 className="text-[18px] font-black text-slate-950">Список сводок</h3>
                  <p className="text-[13px] font-semibold text-slate-500 mt-1">Дневные и недельные сводки по этому диалогу</p>
                </div>
                <span className="text-[13px] font-black text-slate-400">{selectedChat.reports?.length ?? 0} сводок</span>
              </div>

              <div className="flex flex-col gap-4">
                {selectedChat.reports && selectedChat.reports.length > 0 ? (
                  sortedChatReports(selectedChat.reports).map((report, index) => (
                    <button
                      key={`${report.report_kind || "daily"}-${report.report_id || report.date}`}
                      onClick={() => void openChatDaySummary(selectedChat, report)}
                      className="group w-full rounded-2xl border border-[#Eef0f4] bg-white hover:border-[#CBD5E1] hover:shadow-[0_8px_24px_-18px_rgba(15,23,42,0.45)] transition-all px-5 py-4 flex items-center justify-between gap-4 text-left"
                    >
                      <div className="flex items-center gap-5 min-w-0">
                        <div className="w-12 h-12 rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] flex items-center justify-center text-slate-400 shadow-sm shrink-0 group-hover:text-[#5440F6]">
                          <FileText className="w-6 h-6" strokeWidth={2.2} />
                        </div>
                        <div className="min-w-0">
                          <div className="font-black text-[15px] text-slate-900 truncate">
                            {report.title || ((report.report_kind || "daily") === "weekly" ? `Неделя ${report.period_text}` : `Сводка ${report.date_text || formatIsoDateRu(report.date)}`)}
                          </div>
                          <div className="flex flex-wrap items-center gap-3 mt-2 text-[12px] font-bold text-slate-400">
                            <span className="flex items-center gap-1.5">
                              <Calendar className="w-3.5 h-3.5" />
                              {(report.report_kind || "daily") === "weekly" ? report.period_text : report.date_text || formatIsoDateRu(report.date)}
                            </span>
                            <span>{report.messages_count} сообщений</span>
                            {(report.report_kind || "daily") === "weekly" ? <span>{report.version ? `версия ${report.version}` : "недельный"}</span> : null}
                          </div>
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center justify-end gap-2 shrink-0">
                        <span className={cn(
                          "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] font-black shadow-sm",
                          chatDayWorkflowStatusClass(report.text_status),
                        )}>
                          {(report.workflow_status || report.text_status?.workflow_status) === "report_formed" ? (
                            <CheckCircle2 className="w-3.5 h-3.5" />
                          ) : (
                            <CircleDashed className="w-3.5 h-3.5" />
                          )}
                          {report.workflow_status_text || report.text_status?.workflow_status_text || report.text_status?.status_text || "Не обработан"}
                        </span>
                        <span className="text-[12px] font-bold text-slate-300">#{index + 1}</span>
                      </div>
                    </button>
                  ))
                ) : (
                  <div className="rounded-2xl border border-[#Eef0f4] bg-[#F8FAFC] px-5 py-8 text-center text-slate-500 font-bold">
                    Для этого диалога пока нет сохраненных сводок. Выберите период и нажмите «Загрузить сообщения».
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Chat Summary Detail Modal */}
      {selectedChat && isChatDayModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-slate-900/45 backdrop-blur-sm" onClick={() => setIsChatDayModalOpen(false)}></div>
          <div className="relative bg-white rounded-3xl w-full max-w-7xl h-[88vh] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300 flex flex-col">
            <div className="px-6 py-5 border-b border-[#Eef0f4] flex flex-wrap items-start justify-between gap-4 bg-[#F8FAFC]">
              <div>
                <div className="text-[12px] uppercase font-black text-slate-400 mb-1">Диалог</div>
                <h2 className="text-[22px] font-black text-slate-950">{selectedChat.title || selectedChat.dialog_id}</h2>
                <div className="text-[13px] font-semibold text-slate-500 mt-1">
                  {selectedChat.dialog_id}{selectedChat.chat_id ? ` · ID ${selectedChat.chat_id}` : ""} · {selectedChat.member_count} участников
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <CustomDateRangeInput
                  dateFrom={chatPeriodFrom}
                  dateTo={chatPeriodTo}
                  max={moscowTodayIso()}
                  onChange={(dateFrom, dateTo) => {
                    setChatPeriodFrom(dateFrom);
                    setChatPeriodTo(dateTo);
                    setChatSummaryDate(dateTo);
                    if (dateFrom === dateTo) void loadChatDay(selectedChat.dialog_id, dateTo);
                  }}
                  className="w-72"
                />
                <button onClick={syncChatMessages} disabled={chatDayLoading} className="h-10 px-4 rounded-xl bg-slate-900 text-white font-bold text-[13px] disabled:opacity-60">
                  Загрузить сообщения
                </button>
                <button onClick={processSelectedChatImages} disabled={chatDayLoading} className="h-10 px-4 rounded-xl bg-white border border-[#Eef0f4] text-slate-800 font-bold text-[13px] disabled:opacity-60">
                  Обработка картинок
                </button>
                <button onClick={generateAllChatReports} disabled={chatDayLoading} className="h-10 px-4 rounded-xl bg-[#5440F6] text-white font-bold text-[13px] disabled:opacity-60">
                  Отчеты по всем чатам
                </button>
                <button onClick={generateSelectedChatWeeklyReport} disabled={chatDayLoading} className="h-10 px-4 rounded-xl bg-white border border-[#Eef0f4] text-slate-800 font-bold text-[13px] disabled:opacity-60">
                  Отчет за неделю
                </button>
                {chatDay?.report && (
                  <button
                    onClick={() => {
                      const reportId = chatDay.weekly_report?.report_id || chatDay.report?.report_id || chatDay.report?.id;
                      if (!reportId) {
                        setChatDayMessage("У отчета нет id для редактирования. Обновите сводку.");
                        return;
                      }
                      openReportEditor({
                        reportType: chatDay.weekly_report ? "chat_weekly" : "chat_daily",
                        reportId,
                        title: chatDay.weekly_report ? `Недельный отчет: ${chatDay.weekly_report.period_text}` : `Отчет чата за ${formatIsoDateRu(chatSummaryDate)}`,
                        context: "chat_day",
                        fields: {
                          summary: chatDay.weekly_report?.summary || "",
                          dynamics_summary: chatDay.weekly_report?.dynamics_summary || "",
                          positives_summary: chatDay.weekly_report?.positives_summary || "",
                          problems_summary: chatDay.weekly_report?.problems_summary || "",
                          recommendations: chatDay.weekly_report?.recommendations || "",
                          report_text: chatDay.report?.report_text || "",
                        },
                      });
                    }}
                    className="h-10 px-4 rounded-xl bg-white border border-[#Eef0f4] text-slate-800 font-bold text-[13px] flex items-center gap-2"
                  >
                    <Pencil className="w-4 h-4" />
                    Редактировать
                  </button>
                )}
                {chatDay?.report && (
                  <button
                    onClick={() => chatDay.weekly_report ? void deleteSelectedChatWeeklyReport() : void deleteSelectedChatDailyReport()}
                    disabled={chatDayLoading}
                    className="h-10 px-4 rounded-xl bg-white border border-red-100 text-red-600 font-bold text-[13px] flex items-center gap-2 disabled:opacity-60"
                    title={chatDay.weekly_report ? "Удалить недельный отчет по чату" : "Удалить ежедневный отчет по чату"}
                  >
                    <Trash2 className="w-4 h-4" />
                    Удалить отчет
                  </button>
                )}
                <button onClick={() => setIsChatDayModalOpen(false)} className="w-10 h-10 flex items-center justify-center rounded-xl hover:bg-white text-slate-500 transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>
            {chatDayMessage && (
              <div className="px-6 py-3 border-b border-[#Eef0f4] bg-white text-[13px] font-bold text-slate-600">
                {chatDayMessage}
              </div>
            )}
            {chatDay?.text_status && (
              <div className="px-6 py-3 border-b border-[#Eef0f4] bg-white flex flex-wrap items-center gap-3 text-[12px] font-black">
                <span className={cn(
                  "inline-flex items-center rounded-lg px-3 py-1.5 border",
                  chatDayWorkflowStatusClass(chatDay.text_status),
                )}>
                  {chatDay.text_status.workflow_status_text || chatDay.text_status.status_text}
                </span>
                <span className="text-slate-400">
                  Картинок: {chatDay.text_status.image_files}, OCR готово: {chatDay.text_status.ocr_success}, ошибок: {chatDay.text_status.ocr_errors}
                </span>
              </div>
            )}

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-0 flex-1 min-h-0">
              <section className="min-h-0 border-r border-[#Eef0f4] flex flex-col">
                <div className="px-6 py-4 border-b border-[#Eef0f4] flex items-center justify-between">
                  <div>
                    <h3 className="text-[16px] font-black text-slate-950">Вся переписка</h3>
                    {chatDay?.period_text ? (
                      <div className="mt-1 text-[12px] font-bold text-slate-400">{chatDay.period_text}</div>
                    ) : null}
                  </div>
                  <span className="text-[12px] font-bold text-slate-400">
                    {`${chatDay?.messages.length ?? 0} сообщений`}
                  </span>
                </div>
                <div className="flex-1 overflow-auto px-6 py-4 text-[13px] leading-6 bg-white">
                  {!chatDay || chatDay.messages.length === 0 ? (
                    <div className="text-slate-500 font-bold">Нет сообщений за выбранный период</div>
                  ) : (
                    chatDay.messages.map((message, index) => {
                      const dateLabel = (message.message_date_text || "").split(" ")[0] || "";
                      const previousDateLabel = index > 0 ? (chatDay.messages[index - 1].message_date_text || "").split(" ")[0] || "" : "";
                      return (
                        <div key={`${dateLabel}-${message.message_id}`} className="border-b border-slate-100 py-3 last:border-b-0">
                          {dateLabel && dateLabel !== previousDateLabel ? (
                            <div className="mb-3 mt-1 inline-flex rounded-lg bg-[#EEF2FF] px-3 py-1 text-[12px] font-black text-[#5440F6]">
                              {dateLabel}
                            </div>
                          ) : null}
                          <div className="flex flex-wrap items-center gap-2 mb-1">
                            <span className="font-black text-slate-900">{message.author_name}</span>
                            <span className="text-[12px] font-semibold text-slate-400">{message.message_date_text}</span>
                          </div>
                          <div className="text-slate-700 whitespace-pre-wrap">{message.text || (message.files?.length ? "Вложение без текста" : "Без текста")}</div>
                          {message.files?.length > 0 && (
                            <div className="mt-3 flex flex-wrap gap-3">
                              {message.files.map((file) => {
                                const fileUrl = file.preview_url || file.show_url || file.download_url || "";
                                return (
                                  <a
                                    key={file.file_id}
                                    href={file.show_url || file.download_url || file.preview_url || "#"}
                                    target="_blank"
                                    className="block rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] p-2 hover:border-[#CBD5E1] transition-colors"
                                  >
                                    {file.is_image && fileUrl ? (
                                      <img src={fileUrl} alt={file.name || "Фото"} className="h-28 w-40 object-cover rounded-lg border border-slate-100 bg-white" />
                                    ) : (
                                      <div className="h-20 w-40 rounded-lg bg-white border border-slate-100 flex items-center justify-center text-slate-400">
                                        <FileText className="w-7 h-7" />
                                      </div>
                                    )}
                                    <div className="mt-2 max-w-40 truncate text-[12px] font-bold text-slate-600">
                                      {file.name || `Файл ${file.file_id}`}
                                    </div>
                                  </a>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              </section>

              <section className="min-h-0 flex flex-col bg-[#F8FAFC]">
                <div className="px-6 py-4 border-b border-[#Eef0f4] flex items-center justify-between">
                  <h3 className="text-[16px] font-black text-slate-950">ИИ расшифровка</h3>
                  <span className="text-[12px] font-bold text-slate-400">{chatDay?.report?.model || "не сформирована"}</span>
                </div>
                <div className="flex-1 overflow-auto px-6 py-5 whitespace-pre-wrap text-[14px] leading-7 text-slate-700 font-medium">
                  {chatDay?.report?.report_text || "Сводка еще не сформирована. Нажмите «Отчеты по всем чатам» после загрузки сообщений."}
                </div>
              </section>
            </div>
          </div>
        </div>
      )}

      {/* Create Report Modal */}
      {isReportModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" onClick={() => setIsReportModalOpen(false)}></div>
          <div className="relative bg-white rounded-3xl w-full max-w-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300">
            <div className="flex justify-between items-center p-6 border-b border-slate-100 pb-5">
              <div>
                <h2 className="text-xl font-bold text-slate-900 tracking-tight">Создание нового отчета</h2>
                <p className="text-[13px] text-slate-500 mt-1 font-medium">
                  {reportKind === "daily" && "Выберите дату отчета"}
                  {reportKind === "weekly" && "Выберите отчетную неделю с понедельника по воскресенье"}
                  {reportKind === "monthly" && "Выберите отчетный месяц"}
                  {reportKind === "quarterly" && "Выберите отчетный квартал"}
                  {reportKind === "yearly" && "Выберите отчетный год"}
                </p>
              </div>
              <button onClick={() => setIsReportModalOpen(false)} className="w-10 h-10 flex items-center justify-center rounded-xl hover:bg-slate-100 text-slate-500 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-8 bg-slate-50/50 flex flex-col items-center">
              <div className="bg-white p-7 rounded-3xl shadow-[0_4px_20px_-8px_rgba(0,0,0,0.05)] border border-[#Eef0f4] w-full max-w-2xl relative">
                {(reportKind === "daily" || reportKind === "weekly") && (
                  <>
                    <div className="flex items-center justify-between mb-8">
                      <h3 className="font-bold text-slate-800 text-[16px] capitalize">
                        {MONTHS[reportModalMonth.getMonth()]} {reportModalMonth.getFullYear()}
                      </h3>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setReportModalMonth(new Date(reportModalMonth.getFullYear(), reportModalMonth.getMonth() - 1, 1))}
                          className="w-9 h-9 flex items-center justify-center rounded-xl hover:bg-slate-100 text-slate-600 transition-colors border border-transparent hover:border-[#Eef0f4]">
                          <ChevronLeft className="w-5 h-5" />
                        </button>
                        <button
                          onClick={() => setReportModalMonth(new Date(reportModalMonth.getFullYear(), reportModalMonth.getMonth() + 1, 1))}
                          className="w-9 h-9 flex items-center justify-center rounded-xl hover:bg-slate-100 text-slate-600 transition-colors border border-transparent hover:border-[#Eef0f4]">
                          <ChevronRight className="w-5 h-5" />
                        </button>
                      </div>
                    </div>
                    <div className="flex mb-4 items-center">
                      <div className="w-12 text-center text-[10px] font-black text-slate-400 uppercase tracking-widest pl-1">{reportKind === "weekly" ? "Нед." : ""}</div>
                      <div className="flex-1 grid grid-cols-7 gap-1.5">
                        {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map(d => (
                          <div key={d} className="text-center text-[11px] font-black text-slate-400 uppercase tracking-widest">{d}</div>
                        ))}
                      </div>
                    </div>
                    <div className="flex flex-col gap-1.5">
                      {reportCalendarWeeks.map((week, idx) => {
                        const isSelectedWeek = selectedReportWeekStart && week.start.getTime() === selectedReportWeekStart.getTime();
                        return (
                          <div
                            key={idx}
                            onClick={() => reportKind === "weekly" && setSelectedReportWeekStart(week.start)}
                            className={cn(
                              "flex items-center p-1 rounded-2xl transition-all border group",
                              reportKind === "weekly" ? "cursor-pointer" : "",
                              reportKind === "weekly" && isSelectedWeek ? "bg-[#5440F6]/5 border-[#5440F6]/20 pr-1.5" : "border-transparent hover:bg-slate-50 pr-1.5"
                            )}
                          >
                            <div className={cn(
                              "w-12 text-center text-[13px] font-bold transition-colors",
                              reportKind === "weekly" && isSelectedWeek ? "text-[#5440F6]" : "text-slate-400 group-hover:text-slate-600"
                            )}>
                              {reportKind === "weekly" ? week.weekNum : ""}
                            </div>
                            <div className="flex-1 grid grid-cols-7 gap-1.5">
                              {week.days.map((day, dIdx) => {
                                const isCurrentMonth = day.getMonth() === reportModalMonth.getMonth();
                                const isSelectedDay = selectedReportDay && day.toDateString() === selectedReportDay.toDateString();
                                return (
                                  <div
                                    key={dIdx}
                                    onClick={(e) => {
                                      if (reportKind === "daily") {
                                        e.stopPropagation();
                                        setSelectedReportDay(day);
                                      }
                                    }}
                                    className={cn(
                                      "h-10 flex items-center justify-center text-[14px] font-semibold rounded-xl transition-all",
                                      reportKind === "weekly" && isSelectedWeek
                                        ? "bg-[#5440F6] text-white shadow-sm font-bold"
                                        : reportKind === "daily" && isSelectedDay
                                          ? "bg-[#5440F6] text-white shadow-sm font-bold cursor-pointer"
                                          : isCurrentMonth
                                            ? "text-slate-700 bg-slate-50 group-hover:bg-white"
                                            : "text-slate-300 bg-transparent"
                                    )}
                                  >
                                    {day.getDate()}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </>
                )}

                {reportKind === "monthly" && (
                  <div className="grid grid-cols-3 gap-3">
                    {MONTHS.map((m, idx) => {
                      const selected = selectedReportMonth?.year === reportModalMonth.getFullYear() && selectedReportMonth?.month === idx;
                      return (
                        <button
                          key={m}
                          onClick={() => setSelectedReportMonth({ year: reportModalMonth.getFullYear(), month: idx })}
                          className={cn("px-3 py-3 rounded-xl border text-[13px] font-bold", selected ? "bg-[#5440F6] text-white border-[#5440F6]" : "bg-slate-50 text-slate-700 border-[#Eef0f4]")}
                        >
                          {m} {reportModalMonth.getFullYear()}
                        </button>
                      );
                    })}
                  </div>
                )}

                {reportKind === "quarterly" && (
                  <div className="grid grid-cols-2 gap-3">
                    {([1, 2, 3, 4] as Array<1 | 2 | 3 | 4>).map((q) => {
                      const selected = selectedReportQuarter?.year === reportModalMonth.getFullYear() && selectedReportQuarter?.quarter === q;
                      const range = getQuarterRange(reportModalMonth.getFullYear(), q);
                      return (
                        <button
                          key={q}
                          onClick={() => setSelectedReportQuarter({ year: reportModalMonth.getFullYear(), quarter: q })}
                          className={cn("px-4 py-4 rounded-xl border text-left", selected ? "bg-[#5440F6] text-white border-[#5440F6]" : "bg-slate-50 text-slate-700 border-[#Eef0f4]")}
                        >
                          <div className="font-black text-[14px]">Q{q} {reportModalMonth.getFullYear()}</div>
                          <div className={cn("text-[12px] font-semibold mt-1", selected ? "text-white/90" : "text-slate-500")}>
                            {range.start.getDate()} {MONTHS[range.start.getMonth()]} - {range.end.getDate()} {MONTHS[range.end.getMonth()]}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                )}

                {reportKind === "yearly" && (
                  <div className="grid grid-cols-4 gap-3">
                    {Array.from({ length: 8 }).map((_, idx) => {
                      const y = reportModalMonth.getFullYear() - 3 + idx;
                      const selected = selectedReportYear === y;
                      return (
                        <button
                          key={y}
                          onClick={() => setSelectedReportYear(y)}
                          className={cn("px-3 py-3 rounded-xl border text-[14px] font-black", selected ? "bg-[#5440F6] text-white border-[#5440F6]" : "bg-slate-50 text-slate-700 border-[#Eef0f4]")}
                        >
                          {y}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
            
            <div className="p-6 border-t border-slate-100 bg-white flex justify-between items-center">
              <div className="flex items-center gap-3">
                 {reportKind === "daily" && selectedReportDay && (
                   <span className="text-[13px] font-bold text-[#5440F6] bg-[#5440F6]/10 px-4 py-2 rounded-xl">
                     Выбран день: {selectedReportDay.getDate()} {MONTHS[selectedReportDay.getMonth()]} {selectedReportDay.getFullYear()}
                   </span>
                 )}
                 {reportKind === "weekly" && selectedReportWeekStart && (
                   <span className="text-[13px] font-bold text-[#5440F6] bg-[#5440F6]/10 px-4 py-2 rounded-xl">
                     Выбрана неделя: {selectedReportWeekStart.getDate()} {MONTHS[selectedReportWeekStart.getMonth()]} - {new Date(selectedReportWeekStart.getTime() + 6 * 86400000).getDate()} {MONTHS[new Date(selectedReportWeekStart.getTime() + 6 * 86400000).getMonth()]}
                   </span>
                 )}
                 {reportKind === "monthly" && selectedReportMonth && (
                   <span className="text-[13px] font-bold text-[#5440F6] bg-[#5440F6]/10 px-4 py-2 rounded-xl">
                     Выбран месяц: {MONTHS[selectedReportMonth.month]} {selectedReportMonth.year}
                   </span>
                 )}
                 {reportKind === "quarterly" && selectedReportQuarter && (
                   <span className="text-[13px] font-bold text-[#5440F6] bg-[#5440F6]/10 px-4 py-2 rounded-xl">
                     Выбран квартал: Q{selectedReportQuarter.quarter} {selectedReportQuarter.year}
                   </span>
                 )}
                 {reportKind === "yearly" && selectedReportYear && (
                   <span className="text-[13px] font-bold text-[#5440F6] bg-[#5440F6]/10 px-4 py-2 rounded-xl">
                     Выбран год: {selectedReportYear}
                   </span>
                 )}
                 {!hasReportPeriodSelected && (
                   <span className="text-[13px] font-medium text-slate-400">
                     Выберите период для формирования отчета
                   </span>
                 )}
                 {!!reportPrecheckError && hasReportPeriodSelected && (
                   <span className="text-[13px] font-bold text-red-700 bg-red-50 px-4 py-2 rounded-xl border border-red-200">
                     {reportPrecheckError}
                   </span>
                 )}
                 {reportPrecheckLoading && hasReportPeriodSelected && (
                   <span className="text-[13px] font-medium text-slate-500">
                     Проверяю последовательность отчетов...
                   </span>
                 )}
              </div>
              <div className="flex gap-3">
                <button 
                  onClick={() => setIsReportModalOpen(false)}
                  disabled={reportCreateLoading}
                  className="px-6 py-2.5 rounded-xl font-bold text-slate-600 bg-slate-100 hover:bg-slate-200 transition-colors text-[14px]">
                  Отмена
                </button>
                <button 
                  onClick={handleCreateReport}
                  disabled={!hasReportPeriodSelected || reportCreateLoading || reportPrecheckLoading || !!reportPrecheckError}
                  className="px-6 py-2.5 rounded-xl font-bold text-white bg-[#5440F6] hover:bg-[#4532db] disabled:opacity-50 disabled:bg-slate-300 disabled:shadow-none transition-all shadow-md shadow-[#5440F6]/20 text-[14px]">
                  {reportCreateLoading ? "Формирование..." : "Создать отчет"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {selectedOwnerReport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" onClick={() => setSelectedOwnerReport(null)}></div>
          <div className="relative bg-white rounded-3xl w-full max-w-4xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300">
            <div className="flex justify-between items-center p-6 border-b border-slate-100">
              <div>
                <h2 className="text-xl font-bold text-slate-900 tracking-tight">{selectedOwnerReport.title}</h2>
                <p className="text-[13px] text-slate-500 mt-1 font-medium">{selectedOwnerReport.subtitle}</p>
              </div>
              <div className="flex items-center justify-end gap-2">
                {(selectedOwnerReport.kind === "daily" || selectedOwnerReport.kind === "weekly") && (
                  <button
                    onClick={() => void openOwnerReportSend(selectedOwnerReport, "full")}
                    className="h-10 px-4 rounded-xl bg-[#5440F6] text-white font-bold text-[13px] flex items-center gap-2 shadow-md shadow-[#5440F6]/20"
                  >
                    <FileText className="w-4 h-4" strokeWidth={2.5} />
                    Отправить полный отчет
                  </button>
                )}
                {(selectedOwnerReport.kind === "daily" || selectedOwnerReport.kind === "weekly") && (
                  <button
                    onClick={() => void openOwnerReportSend(selectedOwnerReport, "recommendations")}
                    className="h-10 px-4 rounded-xl bg-white border border-[#Eef0f4] text-slate-800 font-bold text-[13px] flex items-center gap-2"
                  >
                    <Send className="w-4 h-4" strokeWidth={2.5} />
                    Отправить рекомендации
                  </button>
                )}
                <button
                  onClick={() =>
                    openReportEditor({
                      reportType: selectedOwnerReport.kind === "daily" ? "owner_daily" : "owner_weekly",
                      reportId: selectedOwnerReport.id,
                      title: selectedOwnerReport.title,
                      context: "owner",
                      fields: {
                        summary: selectedOwnerReport.summary || "",
                        dynamics_summary: selectedOwnerReport.dynamics || "",
                        risks_summary: selectedOwnerReport.risks || "",
                        recommendations: selectedOwnerReport.recommendations || "",
                        report_text: selectedOwnerReport.reportText || "",
                      },
                    })
                  }
                  className="h-10 px-4 rounded-xl bg-white border border-[#Eef0f4] text-slate-800 font-bold text-[13px] flex items-center gap-2"
                >
                  <Pencil className="w-4 h-4" strokeWidth={2.5} />
                  Редактировать
                </button>
                <button onClick={() => setSelectedOwnerReport(null)} className="w-10 h-10 flex items-center justify-center rounded-xl hover:bg-slate-100 text-slate-500 transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>
            <div className="p-6 max-h-[75vh] overflow-auto space-y-5">
              {selectedOwnerReport.summary && (
                <section>
                  <div className="mb-2 flex items-center gap-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-50 text-[#5440F6]">
                      <FileCheck className="h-4 w-4" strokeWidth={2.5} />
                    </span>
                    <h3 className="text-[14px] font-black text-slate-900">Главный вывод</h3>
                  </div>
                  <div className="rounded-2xl border border-indigo-100 bg-gradient-to-br from-white to-indigo-50/60 px-5 py-4 text-[14px] font-normal leading-7 text-slate-700 shadow-sm whitespace-pre-wrap">{selectedOwnerReport.summary}</div>
                </section>
              )}
              {selectedOwnerReport.dynamics && (
                <section>
                  <div className="mb-2 flex items-center gap-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-sky-50 text-sky-600">
                      <TrendingUp className="h-4 w-4" strokeWidth={2.5} />
                    </span>
                    <h3 className="text-[14px] font-black text-slate-900">Динамика</h3>
                  </div>
                  <div className="rounded-2xl border border-sky-100 bg-gradient-to-br from-white to-sky-50/70 px-5 py-4 text-[14px] font-normal leading-7 text-slate-700 shadow-sm whitespace-pre-wrap">{selectedOwnerReport.dynamics}</div>
                </section>
              )}
              {selectedOwnerReport.risks && (
                <section>
                  <div className="mb-2 flex items-center gap-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-orange-50 text-orange-600">
                      <AlertTriangle className="h-4 w-4" strokeWidth={2.5} />
                    </span>
                    <h3 className="text-[14px] font-black text-slate-900">Риски</h3>
                  </div>
                  <div className="rounded-2xl border border-orange-100 bg-gradient-to-br from-white to-orange-50/80 px-5 py-4 text-[14px] font-normal leading-7 text-slate-700 shadow-sm whitespace-pre-wrap">{selectedOwnerReport.risks}</div>
                </section>
              )}
              {selectedOwnerReport.recommendations && (
                <section>
                  <div className="mb-2 flex items-center gap-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600">
                      <CheckCircle2 className="h-4 w-4" strokeWidth={2.5} />
                    </span>
                    <h3 className="text-[14px] font-black text-slate-900">Рекомендации</h3>
                  </div>
                  <div className="rounded-2xl border border-emerald-100 bg-gradient-to-br from-white to-emerald-50/80 px-5 py-4 text-[14px] font-normal leading-7 text-slate-700 shadow-sm whitespace-pre-wrap">{selectedOwnerReport.recommendations}</div>
                </section>
              )}
              <section>
                <div className="mb-2 flex items-center gap-2">
                  <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-slate-100 text-slate-600">
                    <BookText className="h-4 w-4" strokeWidth={2.5} />
                  </span>
                  <h3 className="text-[14px] font-black text-slate-900">Полный текст отчета</h3>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white px-5 py-4 text-[14px] leading-7 font-normal text-slate-700 shadow-sm">
                  <ReportTextWithTables text={selectedOwnerReport.reportText || ""} />
                </div>
              </section>
            </div>
          </div>
        </div>
      )}

      {selectedOwnerReport && isOwnerReportSendOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-slate-900/35 backdrop-blur-sm" onClick={() => setIsOwnerReportSendOpen(false)}></div>
          <div className="relative bg-white rounded-3xl w-full max-w-2xl max-h-[82vh] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300 flex flex-col">
            <div className="px-6 py-5 border-b border-slate-100 flex items-start justify-between gap-4">
              <div>
                <h3 className="text-[18px] font-semibold text-slate-950">
                  {ownerReportSendMode === "full" ? "Отправить полный отчет" : "Отправить рекомендации"}
                </h3>
                <p className="text-[13px] font-semibold text-slate-500 mt-1">{selectedOwnerReport.title}</p>
              </div>
              <button onClick={() => setIsOwnerReportSendOpen(false)} className="w-10 h-10 flex items-center justify-center rounded-xl hover:bg-slate-100 text-slate-500 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-6">
              <div className="flex items-center justify-between gap-3 mb-4">
                <div>
                  <div className="text-[13px] font-semibold uppercase text-slate-500">
                    {ownerReportSendMode === "full" ? "PDF с полным отчетом" : "Адресные рекомендации"}
                  </div>
                  <div className="text-[12px] font-medium text-slate-400 mt-1">
                    {ownerReportSendMode === "full"
                      ? "Будет сформирован красивый PDF со всем текстом отчета и таблицами, затем отправлен выбранным людям."
                      : "Каждому выбранному человеку ставится задача «Рекомендации ДД.ММ» с его рекомендациями. Дедлайн — до 10:00 следующего дня, переносить нельзя."}
                  </div>
                </div>
                <div className="text-[12px] font-bold text-slate-400">Выбрано: {ownerReportRecipientIds.length}</div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {teamRows.filter((member) => member.active !== 0 && member.user_id).map((member) => {
                  const checked = ownerReportRecipientIds.includes(member.user_id);
                  const recommendationText = ownerReportRecommendationTexts[member.user_id] || "";
                  const disabled = ownerReportSendMode === "recommendations" && !recommendationText;
                  return (
                    <label
                      key={member.user_id}
                      className={cn(
                        "flex items-start gap-3 rounded-xl border px-4 py-3 cursor-pointer transition-colors",
                        disabled && "cursor-not-allowed opacity-45",
                        checked ? "border-[#5440F6] bg-[#F5F3FF]" : "border-[#Eef0f4] bg-white hover:bg-[#F8FAFC]",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={disabled}
                        onChange={() => toggleOwnerReportRecipient(member.user_id)}
                        className="mt-1 h-4 w-4 accent-[#5440F6]"
                      />
                      <span className="min-w-0">
                        <span className="block text-[14px] font-medium text-slate-900 truncate">{member.name || `ID ${member.user_id}`}</span>
                        <span className="block text-[12px] font-semibold text-slate-500 truncate">{member.work_position || member.departments_text || `Bitrix ID ${member.user_id}`}</span>
                        {recommendationText ? (
                          <span className="mt-2 line-clamp-2 block text-[12px] font-normal leading-5 text-slate-600">{recommendationText}</span>
                        ) : ownerReportSendMode === "full" ? (
                          <span className="mt-2 block text-[12px] font-normal text-slate-400">Полный PDF можно отправить этому пользователю</span>
                        ) : (
                          <span className="mt-2 block text-[12px] font-normal text-slate-400">В рекомендациях отчета не найден</span>
                        )}
                      </span>
                    </label>
                  );
                })}
              </div>
              {!teamLoading && ownerReportSendMode === "recommendations" && teamRows.length > 0 && Object.keys(ownerReportRecommendationTexts).length === 0 && (
                <div className="mt-4 rounded-xl border border-amber-100 bg-amber-50 px-4 py-3 text-[13px] font-medium text-amber-800">
                  В отчете не найдены адресные рекомендации по сотрудникам. Отправка недоступна.
                </div>
              )}
              {!teamLoading && teamRows.filter((member) => member.active !== 0).length === 0 && (
                <div className="rounded-xl border border-[#Eef0f4] bg-[#F8FAFC] px-4 py-6 text-center text-[13px] font-bold text-slate-500">
                  Список активных сотрудников пуст. Обновите команду из Bitrix.
                </div>
              )}
              {ownerReportSendMessage && (
                <div
                  className={cn(
                    "mt-4 rounded-xl border px-4 py-3 text-[13px] font-bold",
                    ownerReportSendStatus === "success"
                      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                      : ownerReportSendStatus === "error"
                        ? "border-red-200 bg-red-50 text-red-800"
                        : "border-[#Eef0f4] bg-[#F8FAFC] text-slate-700",
                  )}
                >
                  {ownerReportSendStatus === "success"
                    ? ownerReportSendMode === "full"
                      ? "Отчет успешно отправлен. "
                      : "Рекомендации поставлены задачами. "
                    : ownerReportSendStatus === "error"
                      ? "Ошибка отправки. "
                      : ""}
                  {ownerReportSendMessage}
                </div>
              )}
            </div>

            <div className="px-6 py-4 border-t border-slate-100 flex items-center justify-end gap-2 bg-white">
              <button onClick={() => setIsOwnerReportSendOpen(false)} className="h-10 px-4 rounded-xl border border-[#Eef0f4] bg-white text-slate-700 font-bold text-[13px]">
                Отмена
              </button>
              <button
                onClick={() => void sendOwnerReportToBitrix()}
                disabled={ownerReportSendLoading || ownerReportRecipientIds.length === 0}
                className="h-10 px-5 rounded-xl bg-[#5440F6] text-white font-bold text-[13px] flex items-center gap-2 disabled:opacity-60"
              >
                <Send className={cn("w-4 h-4", ownerReportSendLoading && "animate-pulse")} strokeWidth={2.5} />
                {ownerReportSendLoading
                  ? "Отправка..."
                  : ownerReportSendMode === "full"
                    ? "Отправить полный отчет"
                    : "Отправить рекомендации"}
              </button>
            </div>
          </div>
        </div>
      )}

      {editableReport && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-slate-900/45 backdrop-blur-sm" onClick={() => setEditableReport(null)}></div>
          <div className="relative bg-white rounded-3xl w-full max-w-5xl max-h-[90vh] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300 flex flex-col">
            <div className="px-6 py-5 border-b border-slate-100 flex items-start justify-between gap-4">
              <div>
                <h3 className="text-[20px] font-black text-slate-950">Редактирование отчета</h3>
                <p className="text-[13px] font-semibold text-slate-500 mt-1">{editableReport.title}</p>
              </div>
              <button onClick={() => setEditableReport(null)} className="w-10 h-10 flex items-center justify-center rounded-xl hover:bg-slate-100 text-slate-500 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-6 space-y-4 bg-[#F8FAFC]">
              {editableReport.fields.summary !== undefined && editableReport.reportType !== "chat_daily" && (
                <label className="block">
                  <div className="text-[12px] uppercase font-black text-slate-500 mb-2">Summary</div>
                  <textarea
                    value={editableReport.fields.summary || ""}
                    onChange={(event) => updateEditableReportField("summary", event.target.value)}
                    className="w-full min-h-28 rounded-2xl border border-[#Eef0f4] bg-white px-4 py-3 text-[14px] font-medium text-slate-700 outline-none focus:border-[#5440F6]"
                  />
                </label>
              )}
              {editableReport.fields.dynamics_summary !== undefined && (
                <label className="block">
                  <div className="text-[12px] uppercase font-black text-slate-500 mb-2">Dynamics</div>
                  <textarea
                    value={editableReport.fields.dynamics_summary || ""}
                    onChange={(event) => updateEditableReportField("dynamics_summary", event.target.value)}
                    className="w-full min-h-28 rounded-2xl border border-[#Eef0f4] bg-white px-4 py-3 text-[14px] font-medium text-slate-700 outline-none focus:border-[#5440F6]"
                  />
                </label>
              )}
              {editableReport.fields.positives_summary !== undefined && (
                <label className="block">
                  <div className="text-[12px] uppercase font-black text-slate-500 mb-2">Что хорошо</div>
                  <textarea
                    value={editableReport.fields.positives_summary || ""}
                    onChange={(event) => updateEditableReportField("positives_summary", event.target.value)}
                    className="w-full min-h-28 rounded-2xl border border-[#Eef0f4] bg-white px-4 py-3 text-[14px] font-medium text-slate-700 outline-none focus:border-[#5440F6]"
                  />
                </label>
              )}
              {editableReport.fields.problems_summary !== undefined && (
                <label className="block">
                  <div className="text-[12px] uppercase font-black text-slate-500 mb-2">Проблемы</div>
                  <textarea
                    value={editableReport.fields.problems_summary || ""}
                    onChange={(event) => updateEditableReportField("problems_summary", event.target.value)}
                    className="w-full min-h-28 rounded-2xl border border-[#Eef0f4] bg-white px-4 py-3 text-[14px] font-medium text-slate-700 outline-none focus:border-[#5440F6]"
                  />
                </label>
              )}
              {editableReport.fields.risks_summary !== undefined && (
                <label className="block">
                  <div className="text-[12px] uppercase font-black text-slate-500 mb-2">Risks</div>
                  <textarea
                    value={editableReport.fields.risks_summary || ""}
                    onChange={(event) => updateEditableReportField("risks_summary", event.target.value)}
                    className="w-full min-h-28 rounded-2xl border border-[#Eef0f4] bg-white px-4 py-3 text-[14px] font-medium text-slate-700 outline-none focus:border-[#5440F6]"
                  />
                </label>
              )}
              {editableReport.fields.recommendations !== undefined && (
                <label className="block">
                  <div className="text-[12px] uppercase font-black text-slate-500 mb-2">Recommendations</div>
                  <textarea
                    value={editableReport.fields.recommendations || ""}
                    onChange={(event) => updateEditableReportField("recommendations", event.target.value)}
                    className="w-full min-h-28 rounded-2xl border border-[#Eef0f4] bg-white px-4 py-3 text-[14px] font-medium text-slate-700 outline-none focus:border-[#5440F6]"
                  />
                </label>
              )}
              {editableReport.fields.report_text !== undefined && (
                <label className="block">
                  <div className="text-[12px] uppercase font-black text-slate-500 mb-2">Полный текст отчета</div>
                  <textarea
                    value={editableReport.fields.report_text || ""}
                    onChange={(event) => updateEditableReportField("report_text", event.target.value)}
                    className="w-full min-h-[320px] rounded-2xl border border-[#Eef0f4] bg-white px-4 py-3 text-[14px] leading-7 font-medium text-slate-700 outline-none focus:border-[#5440F6]"
                  />
                </label>
              )}
              {reportEditMessage && (
                <div className="rounded-xl border border-[#Eef0f4] bg-white px-4 py-3 text-[13px] font-bold text-slate-700">
                  {reportEditMessage}
                </div>
              )}
            </div>
            <div className="px-6 py-4 border-t border-slate-100 flex items-center justify-end gap-2 bg-white">
              <button onClick={() => setEditableReport(null)} disabled={reportEditSaving} className="h-10 px-4 rounded-xl border border-[#Eef0f4] bg-white text-slate-700 font-bold text-[13px] disabled:opacity-60">
                Отмена
              </button>
              <button onClick={() => void saveReportEdit()} disabled={reportEditSaving} className="h-10 px-5 rounded-xl bg-[#5440F6] text-white font-bold text-[13px] disabled:opacity-60">
                {reportEditSaving ? "Сохранение..." : "Сохранить новую версию"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
