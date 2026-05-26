// Demo / placeholder data extracted from App.tsx.
//
// These are static fixtures used by dashboard widgets that do not yet have a
// live data source (cash position by bank, WB revenue/DRR/returns charts and
// the initial cashflow registry). Keeping them here keeps App.tsx focused on
// behaviour and makes it obvious which screens still render mock data.

export type PaymentItem = {
  id: string;
  date: string;
  type: "incoming" | "outgoing";
  amount: number;
  source: "wb_api" | "manual";
  description: string;
  isProjected: boolean;
};

export const BANKS_DATA = [
  {
    id: 1,
    name: "Т-Банк",
    type: "Основной",
    amount: "4 550 000,00",
    color: "bg-[#FFDD2D]",
    textColor: "text-slate-900",
    logo: "Т",
  },
  {
    id: 2,
    name: "Сбербанк",
    type: "Резерв",
    amount: "1 150 000,00",
    color: "bg-[#21A038]",
    textColor: "text-white",
    logo: "С",
  },
  {
    id: 3,
    name: "Точка",
    type: "Р/С",
    amount: "850 000,00",
    color: "bg-[#8934EB]",
    textColor: "text-white",
    logo: "Т",
  },
];

export const WB_REVENUE_WEEKS = [
  { week: "Н13", revenue: 2.1 },
  { week: "Н14", revenue: 2.5 },
  { week: "Н15", revenue: 2.3 },
  { week: "Н16", revenue: 2.8 },
  { week: "Н17", revenue: 3.2 },
];

export const WB_DRR_DAILY = [
  { day: "21.04", drr: 4.2 },
  { day: "22.04", drr: 4.8 },
  { day: "23.04", drr: 5.4 },
  { day: "24.04", drr: 6.1 },
  { day: "25.04", drr: 4.9 },
  { day: "26.04", drr: 4.5 },
  { day: "27.04", drr: 3.8 },
];

export const WB_RETURNS_DAILY = [
  { day: "21.04", returns: 12 },
  { day: "22.04", returns: 15 },
  { day: "23.04", returns: 18 },
  { day: "24.04", returns: 16 },
  { day: "25.04", returns: 14 },
  { day: "26.04", returns: 20 },
  { day: "27.04", returns: 11 },
];

export const INITIAL_REGISTRY: PaymentItem[] = [
  {
    id: "1",
    date: "2026-04-06",
    type: "incoming",
    amount: 1200000,
    source: "wb_api",
    description: "Выплата WB (API)",
    isProjected: false,
  },
  {
    id: "2",
    date: "2026-04-13",
    type: "incoming",
    amount: 1250000,
    source: "wb_api",
    description: "Выплата WB (API)",
    isProjected: false,
  },
  {
    id: "3",
    date: "2026-04-20",
    type: "incoming",
    amount: 1180000,
    source: "wb_api",
    description: "Выплата WB (API)",
    isProjected: false,
  },
  {
    id: "4",
    date: "2026-04-25",
    type: "outgoing",
    amount: 185450,
    source: "manual",
    description: 'Транспорт ООО "ЛайнЛогистик"',
    isProjected: false,
  },
  {
    id: "5",
    date: "2026-04-27",
    type: "incoming",
    amount: 1450000,
    source: "wb_api",
    description: "Прогноз WB (API)",
    isProjected: true,
  },
  {
    id: "6",
    date: "2026-04-28",
    type: "outgoing",
    amount: 750000,
    source: "manual",
    description: "Налоги",
    isProjected: true,
  },
  {
    id: "7",
    date: "2026-05-05",
    type: "outgoing",
    amount: 8500000,
    source: "manual",
    description: "Закупка товара (Китай)",
    isProjected: true,
  },
];
