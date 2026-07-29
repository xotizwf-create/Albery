import {useEffect, useMemo, useRef, useState} from 'react';
import {AnimatePresence, motion} from 'motion/react';
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Percent,
  Receipt,
  RotateCcw,
  ShieldCheck,
  TrendingUp,
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
  'https://t.me/AlberyAIManager?text=' +
  encodeURIComponent('Здравствуйте! Я рассчитал экономику ИУ и хочу обсудить сотрудничество.');

const steps = [
  {
    id: 'revenue',
    key: 'revenue',
    title: 'Цена реализации (до СПП)',
    subtitle: 'Введите цену реализации товара',
    placeholder: '1 000 000',
    icon: Wallet,
    symbol: '₽',
    maxDigits: 12,
  },
  {
    id: 'spp',
    key: 'spp',
    title: 'Процент СПП',
    subtitle: 'Введите скидку постоянного покупателя',
    placeholder: '15',
    icon: Percent,
    symbol: '%',
    maxDigits: 3,
  },
  {
    id: 'cost',
    key: 'cost',
    title: 'Себестоимость',
    subtitle: 'Введите себестоимость проданных товаров',
    placeholder: '300 000',
    icon: Receipt,
    symbol: '₽',
    maxDigits: 12,
  },
  {
    id: 'ads',
    key: 'ads',
    title: 'Реклама',
    subtitle: 'Введите затраты на рекламу',
    placeholder: '50 000',
    icon: TrendingUp,
    symbol: '₽',
    maxDigits: 12,
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
  const [step, setStep] = useState(0);
  const [formData, setFormData] = useState<FormData>(emptyForm);
  const [touched, setTouched] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const isResult = step === steps.length;
  const current = isResult ? null : steps[step];
  const currentValue = current ? formData[current.key] : '';
  const error = current ? inputError(current.key, currentValue) : '';

  useEffect(() => {
    if (!isResult) {
      inputRef.current?.focus();
    }
  }, [isResult, step]);

  const next = () => {
    setTouched(true);
    if (!current || error) {
      return;
    }
    setTouched(false);
    setStep((value) => Math.min(value + 1, steps.length));
  };

  const back = () => {
    setTouched(false);
    setStep((value) => Math.max(value - 1, 0));
  };

  const reset = () => {
    setFormData(emptyForm);
    setTouched(false);
    setStep(0);
  };

  const changeValue = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (!current) {
      return;
    }
    const value = digitsOnly(event.target.value, current.maxDigits);
    setFormData((previous) => ({...previous, [current.key]: value}));
    setTouched(true);
  };

  return (
    <main className="relative flex min-h-screen overflow-hidden bg-zinc-950 px-4 py-8 font-sans text-zinc-100 sm:px-8">
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-x-0 top-0 h-[500px] bg-gradient-to-b from-amber-500/8 to-transparent"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none fixed left-1/2 top-1/2 h-[800px] w-[800px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-amber-500/5 blur-[120px]"
      />

      <div className="relative z-10 m-auto w-full">
        {isResult ? (
          <Results data={formData} onReset={reset} />
        ) : (
          <section className="mx-auto flex w-full max-w-xl flex-col items-center">
            <header className="mb-10 flex w-full flex-col items-center gap-5 sm:mb-12">
              <p className="w-full text-center text-[10px] font-semibold uppercase leading-relaxed tracking-[0.15em] text-amber-400 sm:text-xs sm:tracking-[0.24em]">
                Калькулятор расчёта ИУ
              </p>
              <div
                aria-label={`Шаг ${step + 1} из ${steps.length}`}
                className="flex w-full max-w-[220px] items-center gap-3"
                role="progressbar"
                aria-valuemin={1}
                aria-valuemax={steps.length}
                aria-valuenow={step + 1}
              >
                {steps.map((item, index) => (
                  <div
                    key={item.id}
                    className={`h-1.5 flex-1 rounded-full transition-all duration-500 ${
                      index === step
                        ? 'scale-y-125 bg-amber-500'
                        : index < step
                          ? 'bg-amber-500/50'
                          : 'bg-zinc-800'
                    }`}
                  />
                ))}
              </div>

              <div className="flex min-h-8 max-w-lg flex-wrap justify-center gap-2">
                <AnimatePresence>
                  {steps.slice(0, step).map((item) => (
                    <motion.div
                      key={item.id}
                      initial={{opacity: 0, scale: 0.92, y: 8}}
                      animate={{opacity: 1, scale: 1, y: 0}}
                      exit={{opacity: 0}}
                      className="rounded-full border border-zinc-800/70 bg-zinc-900/70 px-3 py-1.5 text-xs text-zinc-400 backdrop-blur-sm sm:text-sm"
                    >
                      {item.title}:{' '}
                      <span className="font-medium text-zinc-200">
                        {formatInput(formData[item.key])} {item.symbol}
                      </span>
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
            </header>

            <AnimatePresence mode="wait">
              <motion.div
                key={step}
                initial={{opacity: 0, x: 20, filter: 'blur(4px)'}}
                animate={{opacity: 1, x: 0, filter: 'blur(0px)'}}
                exit={{opacity: 0, x: -20, filter: 'blur(4px)'}}
                transition={{duration: 0.35, ease: [0.16, 1, 0.3, 1]}}
                className="flex w-full flex-col items-center"
              >
                <div className="mb-7 flex h-20 w-20 items-center justify-center rounded-3xl border border-zinc-700/50 bg-gradient-to-br from-zinc-800 to-zinc-900 shadow-2xl">
                  {current && <current.icon aria-hidden="true" className="h-8 w-8 text-amber-500" />}
                </div>

                <h1 className="mb-3 text-center text-3xl font-light tracking-tight text-white sm:text-4xl">
                  {current?.title}
                </h1>
                <p className="mb-9 text-center text-lg text-zinc-400 sm:mb-11 sm:text-xl">
                  {current?.subtitle}
                </p>

                <div className="mb-3 w-full max-w-md">
                  <div className="relative">
                    <input
                      ref={inputRef}
                      aria-describedby="calculator-input-help"
                      aria-invalid={Boolean(touched && error)}
                      aria-label={current?.title}
                      autoComplete="off"
                      autoFocus
                      className={`w-full border-b-2 bg-transparent px-10 py-4 text-center text-5xl font-light text-white placeholder:text-zinc-800 focus:outline-none sm:text-6xl ${
                        touched && error
                          ? 'border-red-500/70'
                          : 'border-zinc-800 focus:border-amber-500'
                      }`}
                      inputMode="numeric"
                      maxLength={current?.maxDigits}
                      onBlur={() => setTouched(true)}
                      onChange={changeValue}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          next();
                        }
                      }}
                      pattern="[0-9 ]*"
                      placeholder={current?.placeholder}
                      type="text"
                      value={formatInput(currentValue)}
                    />
                    <span
                      aria-hidden="true"
                      className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-2xl font-light text-zinc-600 sm:text-3xl"
                    >
                      {current?.symbol}
                    </span>
                  </div>
                  <p
                    id="calculator-input-help"
                    aria-live="polite"
                    className={`mt-3 min-h-5 text-center text-sm ${
                      touched && error ? 'text-red-400' : 'text-zinc-600'
                    }`}
                  >
                    {touched && error ? error : 'Только цифры'}
                  </p>
                </div>

                <div className="mt-8 flex w-full max-w-sm items-center gap-4">
                  {step > 0 && (
                    <button
                      type="button"
                      aria-label="Вернуться к предыдущему шагу"
                      onClick={back}
                      className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full border border-zinc-800 bg-zinc-900 text-zinc-400 transition-colors hover:border-zinc-700 hover:bg-zinc-800 hover:text-white"
                    >
                      <ArrowLeft aria-hidden="true" className="h-5 w-5" />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={next}
                    className="flex h-14 flex-1 items-center justify-center gap-3 rounded-full bg-amber-500 text-lg font-medium text-zinc-950 shadow-[0_0_30px_-10px_rgba(245,158,11,0.5)] transition-colors hover:bg-amber-400"
                  >
                    <span>{step === steps.length - 1 ? 'Получить расчёт' : 'Далее'}</span>
                    {step < steps.length - 1 && (
                      <ArrowRight aria-hidden="true" className="h-5 w-5" />
                    )}
                  </button>
                </div>
              </motion.div>
            </AnimatePresence>

            <p className="mt-12 flex max-w-full items-start justify-center gap-2 px-2 text-center text-xs leading-relaxed text-zinc-600 sm:items-center sm:text-sm">
              <ShieldCheck aria-hidden="true" className="h-4 w-4 shrink-0" />
              <span>Расчёт выполняется на вашем устройстве. Данные никуда не отправляются.</span>
            </p>
          </section>
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
    <motion.section
      initial={{opacity: 0, scale: 0.97}}
      animate={{opacity: 1, scale: 1}}
      transition={{duration: 0.5, ease: [0.16, 1, 0.3, 1]}}
      className="mx-auto flex w-full max-w-3xl flex-col items-center"
    >
      <p className="mb-8 w-full text-center text-[10px] font-semibold uppercase leading-relaxed tracking-[0.15em] text-amber-400 sm:text-xs sm:tracking-[0.24em]">
        Калькулятор расчёта ИУ
      </p>
      <h1 className="mb-7 text-center text-2xl font-light tracking-tight text-zinc-200 sm:text-3xl">
        Введённые вами данные:
      </h1>

      <div className="mb-10 grid w-full grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
        {steps.map((item) => (
          <div
            key={item.id}
            className="relative overflow-hidden rounded-3xl border border-zinc-800/60 bg-zinc-900/40 p-4 backdrop-blur-sm sm:p-5"
          >
            <div className="mb-3 flex h-8 w-8 items-center justify-center rounded-full bg-zinc-800/60">
              <item.icon aria-hidden="true" className="h-4 w-4 text-amber-500/80" />
            </div>
            <span className="mb-1 block truncate text-[10px] font-medium uppercase tracking-widest text-zinc-500 sm:text-[11px]">
              {item.title.replace(' (до СПП)', '')}
            </span>
            <span className="block truncate text-lg font-light text-zinc-100 sm:text-xl">
              {formatInput(data[item.key])}
              <span className="ml-1 text-sm font-normal text-zinc-600">{item.symbol}</span>
            </span>
          </div>
        ))}
      </div>

      <section
        aria-labelledby="calculation-result-title"
        className="relative mb-10 w-full overflow-hidden rounded-[2rem] border border-amber-500/30 bg-gradient-to-br from-amber-500/12 via-zinc-900/90 to-zinc-950 p-4 shadow-[0_24px_80px_-32px_rgba(245,158,11,0.55)] sm:p-8"
      >
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-amber-400/15 blur-3xl"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-32 -left-20 h-72 w-72 rounded-full bg-amber-600/10 blur-3xl"
        />

        <div className="relative mb-8 flex flex-col items-center px-2 pt-4 text-center sm:mb-10 sm:pt-2">
          <div className="mb-5 flex h-20 w-20 items-center justify-center rounded-full border border-amber-400/30 bg-amber-400/10 shadow-[0_0_50px_-12px_rgba(245,158,11,0.6)]">
            <CheckCircle2 aria-hidden="true" className="h-10 w-10 text-amber-400" />
          </div>
          <span className="mb-3 rounded-full border border-amber-400/20 bg-amber-400/10 px-4 py-1.5 text-[10px] font-semibold uppercase tracking-[0.22em] text-amber-300 sm:text-xs">
            Ваш результат
          </span>
          <h2
            id="calculation-result-title"
            className="text-4xl font-medium tracking-tight text-white sm:text-5xl"
          >
            Расчёт готов!
          </h2>
          <p className="mt-4 text-xl font-medium text-amber-300 sm:text-2xl">
            При работе на ИУ вы получите…
          </p>
          <p className="mt-3 max-w-xl text-sm leading-relaxed text-zinc-400 sm:text-base">
            Прозрачная экономика: сразу видно комиссию, сумму к переводу и ваш
            маржинальный доход.
          </p>
        </div>

        <div className="relative grid w-full gap-3 sm:gap-4">
          <ResultCard
            title="Налогооблагаемая база"
            value={results.taxBase}
            subtitle="Цена реализации минус СПП"
          />
          <ResultCard
            title="Общая комиссия (46%)"
            value={results.commission}
            subtitle="Wildberries + эквайринг + агентская комиссия"
          />
          <ResultCard
            title="К переводу от нас"
            value={results.payout}
            subtitle="Цена реализации минус общая комиссия и реклама"
            highlight
          />
          <ResultCard
            title="Ваш маржинальный доход"
            value={results.margin}
            subtitle="Цена реализации минус общая комиссия и себестоимость"
            highlight
          />
        </div>
      </section>

      <a
        href={TELEGRAM_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="mb-7 flex w-full items-center justify-center rounded-full bg-amber-500 px-12 py-4 text-center text-lg font-medium text-zinc-950 shadow-[0_0_40px_-10px_rgba(245,158,11,0.5)] transition-all hover:scale-[1.01] hover:bg-amber-400 sm:w-auto"
      >
        Обсудить сотрудничество
      </a>
      <button
        type="button"
        onClick={onReset}
        className="group flex items-center gap-3 rounded-full border border-transparent px-6 py-3 text-zinc-400 transition-colors hover:border-zinc-800 hover:bg-zinc-900 hover:text-white"
      >
        <RotateCcw
          aria-hidden="true"
          className="h-5 w-5 transition-transform duration-500 group-hover:-rotate-180"
        />
        <span className="font-medium">Выполнить новый расчёт</span>
      </button>

      <p className="mt-9 flex max-w-full items-start justify-center gap-2 px-2 text-center text-xs leading-relaxed text-zinc-600 sm:items-center sm:text-sm">
        <ShieldCheck aria-hidden="true" className="h-4 w-4 shrink-0" />
        <span>Введённые значения остаются только в этом окне браузера.</span>
      </p>
    </motion.section>
  );
}

function ResultCard({
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
      className={`flex flex-col items-start justify-between gap-4 rounded-3xl border p-6 backdrop-blur-md sm:flex-row sm:items-center sm:p-8 ${
        highlight
          ? 'border-amber-500/30 bg-gradient-to-br from-zinc-800/80 to-zinc-900/80 shadow-[0_0_30px_-10px_rgba(245,158,11,0.15)]'
          : 'border-zinc-800/60 bg-zinc-900/40'
      }`}
    >
      <div className="flex-1">
        <h2 className={`text-lg sm:text-xl ${highlight ? 'font-medium text-zinc-100' : 'text-zinc-300'}`}>
          {title}
        </h2>
        <p className="mt-1 text-sm text-zinc-500 sm:text-base">{subtitle}</p>
      </div>
      <div
        className={`text-3xl font-light tracking-tight sm:text-4xl ${
          highlight ? 'text-amber-400' : 'text-white'
        }`}
      >
        {formatMoney(value)}
      </div>
    </div>
  );
}
