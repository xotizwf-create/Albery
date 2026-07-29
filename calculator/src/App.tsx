import {useMemo, useRef, useState} from 'react';
import {
  Calculator,
  Check,
  Megaphone,
  Percent,
  Receipt,
  RotateCcw,
  ShieldCheck,
  Wallet,
} from 'lucide-react';

import {calculateResults} from './calculations';

type FormData = {
  revenue: string;
  spp: string;
  cost: string;
  ads: string;
};

type FormKey = keyof FormData;

const MAX_AMOUNT = 999_999_999_999;
const TELEGRAM_URL =
  'https://t.me/Albery_AI2_Bot?text=' +
  encodeURIComponent(
    'Здравствуйте! Я рассчитал экономику ИУ и хочу обсудить условия подключения.',
  );

const fields = [
  {
    key: 'revenue',
    label: 'Цена реализации до СПП',
    hint: 'Введите общую цену реализации товара',
    placeholder: '1 000 000',
    symbol: '₽',
    maxDigits: 12,
    icon: Wallet,
  },
  {
    key: 'spp',
    label: 'Процент СПП',
    hint: 'Скидка постоянного покупателя',
    placeholder: '15',
    symbol: '%',
    maxDigits: 3,
    icon: Percent,
  },
  {
    key: 'cost',
    label: 'Себестоимость',
    hint: 'Себестоимость проданных товаров',
    placeholder: '300 000',
    symbol: '₽',
    maxDigits: 12,
    icon: Receipt,
  },
  {
    key: 'ads',
    label: 'Затраты на рекламу',
    hint: 'Расходы на продвижение товара',
    placeholder: '50 000',
    symbol: '₽',
    maxDigits: 12,
    icon: Megaphone,
  },
] as const;

const emptyForm: FormData = {
  revenue: '',
  spp: '',
  cost: '',
  ads: '',
};

function digitsOnly(value: string, maxDigits: number): string {
  return value.replace(/\D/g, '').slice(0, maxDigits);
}

function numericValue(value: string): number {
  return Number(value.replace(/\s/g, '')) || 0;
}

function formatInput(value: string): string {
  return value.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

function formatMoney(value: number): string {
  return new Intl.NumberFormat('ru-RU', {
    style: 'currency',
    currency: 'RUB',
    maximumFractionDigits: 0,
  }).format(value);
}

function inputError(key: FormKey, value: string): string {
  if (!value) {
    return 'Заполните поле';
  }
  const number = numericValue(value);
  if (key === 'revenue' && number <= 0) {
    return 'Цена реализации должна быть больше нуля';
  }
  if (key === 'spp' && number > 100) {
    return 'СПП не может быть больше 100%';
  }
  if (key !== 'spp' && number > MAX_AMOUNT) {
    return 'Слишком большое значение';
  }
  return '';
}

export default function App() {
  const [formData, setFormData] = useState<FormData>(emptyForm);
  const [submitted, setSubmitted] = useState(false);
  const [showResult, setShowResult] = useState(false);
  const resultRef = useRef<HTMLDivElement>(null);

  const errors = useMemo(
    () =>
      Object.fromEntries(
        fields.map((field) => [
          field.key,
          inputError(field.key, formData[field.key]),
        ]),
      ) as Record<FormKey, string>,
    [formData],
  );

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitted(true);
    if (Object.values(errors).some(Boolean)) {
      setShowResult(false);
      return;
    }
    setShowResult(true);
    window.setTimeout(() => {
      resultRef.current?.scrollIntoView({behavior: 'smooth', block: 'start'});
    }, 80);
  };

  const changeValue = (
    key: FormKey,
    maxDigits: number,
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const value = digitsOnly(event.target.value, maxDigits);
    setFormData((previous) => ({...previous, [key]: value}));
    setShowResult(false);
  };

  const reset = () => {
    setFormData(emptyForm);
    setSubmitted(false);
    setShowResult(false);
    window.scrollTo({top: 0, behavior: 'smooth'});
  };

  return (
    <main className="min-h-screen bg-white px-4 py-10 font-sans text-slate-900 sm:px-6 sm:py-16">
      <div className="mx-auto w-full max-w-[520px]">
        <header className="mb-9 text-center">
          <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-2xl bg-sky-50 text-sky-500">
            <Calculator aria-hidden="true" className="h-6 w-6" />
          </div>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl">
            Калькулятор расчёта ИУ
          </h1>
          <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-slate-500 sm:text-base">
            Укажите параметры товара — калькулятор покажет экономику работы на
            индивидуальных условиях.
          </p>
        </header>

        <form noValidate onSubmit={submit} className="space-y-3">
          {fields.map((field) => {
            const error = submitted ? errors[field.key] : '';
            return (
              <label
                key={field.key}
                className={`group block rounded-xl border bg-slate-50 px-4 py-3 transition ${
                  error
                    ? 'border-rose-300 bg-rose-50/40'
                    : 'border-slate-200 hover:border-slate-300 focus-within:border-sky-400 focus-within:bg-white focus-within:shadow-[0_0_0_3px_rgba(14,165,233,0.10)]'
                }`}
              >
                <span className="flex items-center gap-2 text-xs font-medium text-slate-500">
                  <field.icon
                    aria-hidden="true"
                    className={`h-3.5 w-3.5 ${error ? 'text-rose-400' : 'text-sky-500'}`}
                  />
                  {field.label}
                  <span aria-hidden="true" className="text-rose-400">
                    *
                  </span>
                </span>
                <span className="mt-1 flex items-center gap-3">
                  <input
                    aria-describedby={`${field.key}-help`}
                    aria-invalid={Boolean(error)}
                    autoComplete="off"
                    className="min-w-0 flex-1 bg-transparent text-lg font-medium text-slate-900 outline-none placeholder:font-normal placeholder:text-slate-300"
                    inputMode="numeric"
                    maxLength={field.maxDigits}
                    onChange={(event) =>
                      changeValue(field.key, field.maxDigits, event)
                    }
                    pattern="[0-9 ]*"
                    placeholder={field.placeholder}
                    type="text"
                    value={formatInput(formData[field.key])}
                  />
                  <span className="text-sm font-medium text-slate-400">
                    {field.symbol}
                  </span>
                </span>
                <span
                  id={`${field.key}-help`}
                  className={`mt-1 block min-h-4 text-[11px] ${
                    error ? 'text-rose-500' : 'text-slate-400'
                  }`}
                >
                  {error || field.hint}
                </span>
              </label>
            );
          })}

          <button
            type="submit"
            className="mt-5 flex h-14 w-full items-center justify-center gap-2 rounded-xl bg-sky-500 px-6 text-base font-semibold text-white shadow-sm transition hover:bg-sky-600 focus-visible:outline-sky-500 active:translate-y-px"
          >
            Рассчитать
          </button>
        </form>

        <p className="mt-5 flex items-start justify-center gap-2 text-center text-xs leading-relaxed text-slate-400">
          <ShieldCheck aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
          Расчёт выполняется на вашем устройстве. Данные никуда не отправляются.
        </p>

        {showResult && (
          <div ref={resultRef} className="scroll-mt-6 pt-12">
            <Results data={formData} onReset={reset} />
          </div>
        )}
      </div>
    </main>
  );
}

function Results({data, onReset}: {data: FormData; onReset: () => void}) {
  const results = useMemo(() => {
    const revenue = numericValue(data.revenue);
    const spp = numericValue(data.spp);
    const cost = numericValue(data.cost);
    const ads = numericValue(data.ads);
    return calculateResults({revenue, spp, cost, ads});
  }, [data]);

  return (
    <section
      aria-labelledby="calculation-result-title"
      className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_12px_40px_-24px_rgba(15,23,42,0.25)]"
    >
      <div className="border-b border-slate-100 bg-slate-50/80 px-5 py-5 sm:px-6">
        <p className="text-xs font-medium uppercase tracking-[0.12em] text-slate-400">
          Введённые вами данные
        </p>
        <div className="mt-3 grid grid-cols-2 gap-x-5 gap-y-3">
          {fields.map((field) => (
            <div key={field.key}>
              <span className="block text-[11px] text-slate-400">
                {field.label}
              </span>
              <span className="mt-0.5 block text-sm font-medium text-slate-700">
                {formatInput(data[field.key])} {field.symbol}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="px-5 py-7 sm:px-6 sm:py-8">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-sky-50 text-sky-500">
            <Check aria-hidden="true" className="h-5 w-5" />
          </span>
          <div>
            <h2
              id="calculation-result-title"
              className="text-2xl font-semibold tracking-tight text-slate-900"
            >
              Расчёт готов!
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              При работе на ИУ вы получите:
            </p>
          </div>
        </div>

        <div className="mt-6 divide-y divide-slate-100 rounded-xl border border-slate-200">
          <ResultRow
            title="Налогооблагаемая база"
            value={results.taxBase}
            subtitle="Цена реализации минус СПП"
          />
          <ResultRow
            title="Общая комиссия (46%)"
            value={results.commission}
            subtitle="Wildberries, эквайринг и агентская комиссия"
          />
          <ResultRow
            title="К переводу от нас"
            value={results.payout}
            subtitle="После комиссии и затрат на рекламу"
            highlight
          />
          <ResultRow
            title="Ваш маржинальный доход"
            value={results.margin}
            subtitle="После комиссии и себестоимости"
            highlight
          />
        </div>

        <a
          href={TELEGRAM_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-6 flex h-13 w-full items-center justify-center rounded-xl bg-sky-500 px-5 text-center text-sm font-semibold text-white transition hover:bg-sky-600"
        >
          Обсудить условия
        </a>

        <button
          type="button"
          onClick={onReset}
          className="mx-auto mt-4 flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-50 hover:text-slate-700"
        >
          <RotateCcw aria-hidden="true" className="h-4 w-4" />
          Выполнить новый расчёт
        </button>
      </div>
    </section>
  );
}

function ResultRow({
  title,
  value,
  subtitle,
  highlight = false,
}: {
  title: string;
  value: number;
  subtitle: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`px-4 py-4 sm:flex sm:items-center sm:justify-between sm:gap-5 ${
        highlight ? 'bg-sky-50/70' : 'bg-white'
      }`}
    >
      <div>
        <h3
          className={`text-sm font-medium ${
            highlight ? 'text-sky-800' : 'text-slate-700'
          }`}
        >
          {title}
        </h3>
        <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>
      </div>
      <p
        className={`mt-2 whitespace-nowrap text-xl font-semibold sm:mt-0 ${
          highlight ? 'text-sky-600' : 'text-slate-900'
        }`}
      >
        {formatMoney(value)}
      </p>
    </div>
  );
}
