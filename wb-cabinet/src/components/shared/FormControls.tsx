import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Calendar as CalendarIcon, Check, ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '../../lib/utils';

interface BrandSelectProps {
  value: string;
  onChange: (val: string) => void;
  options: string[];
}

export function BrandSelect({ value, onChange, options }: BrandSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button 
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 text-sm font-bold text-slate-700 bg-white hover:bg-slate-50 border border-slate-200 rounded-xl px-4 py-2 transition-all shadow-sm outline-none"
      >
        {value}
        <ChevronDown className={cn("w-4 h-4 text-slate-400 transition-transform duration-200", isOpen && "rotate-180")} />
      </button>
      {isOpen && (
        <div className="absolute top-full left-0 mt-2 w-56 bg-white border border-slate-200 rounded-xl shadow-xl shadow-slate-200/50 py-1.5 z-50 animate-in fade-in zoom-in-95 duration-200 origin-top-left">
          {options.map(opt => (
            <button
              key={opt}
              onClick={() => { onChange(opt); setIsOpen(false); }}
              className="w-full text-left px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 hover:text-brand-600 transition-colors flex items-center justify-between"
            >
              {opt}
              {value === opt && <Check className="w-4 h-4 text-brand-500" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function CustomCalendar({ value, onChange }: { value: string, onChange: (val: string) => void }) {
  const parsedDate = value ? new Date(value) : new Date('2026-03-01');
  const [currentMonth, setCurrentMonth] = useState(new Date(parsedDate.getFullYear(), parsedDate.getMonth(), 1));

  const daysInMonth = new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 0).getDate();
  const firstDayOfMonth = new Date(currentMonth.getFullYear(), currentMonth.getMonth(), 1).getDay();
  // Adjust for Monday as first day of week
  const startOffset = firstDayOfMonth === 0 ? 6 : firstDayOfMonth - 1;

  const monthNames = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  const dayNames = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

  const prevMonth = () => setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() - 1, 1));
  const nextMonth = () => setCurrentMonth(new Date(currentMonth.getFullYear(), currentMonth.getMonth() + 1, 1));

  return (
    <div className="p-3 bg-white w-64 select-none">
      <div className="flex items-center justify-between mb-4">
        <div className="font-bold text-slate-900 text-sm">
          {monthNames[currentMonth.getMonth()]} {currentMonth.getFullYear()}
        </div>
        <div className="flex items-center gap-1">
          <button onClick={prevMonth} className="p-1 hover:bg-slate-100 rounded-md transition-colors text-slate-500 hover:text-slate-900">
            <ChevronLeft className="w-4 h-4" />
          </button>
          <button onClick={nextMonth} className="p-1 hover:bg-slate-100 rounded-md transition-colors text-slate-500 hover:text-slate-900">
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>
      <div className="grid grid-cols-7 gap-1 mb-2">
        {dayNames.map(day => (
          <div key={day} className="text-center text-[10px] font-bold text-slate-400 uppercase tracking-wider py-1">
            {day}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {Array.from({ length: startOffset }).map((_, i) => (
          <div key={`empty-${i}`} className="h-8"></div>
        ))}
        {Array.from({ length: daysInMonth }).map((_, i) => {
          const date = i + 1;
          const currentDateStr = `${currentMonth.getFullYear()}-${String(currentMonth.getMonth() + 1).padStart(2, '0')}-${String(date).padStart(2, '0')}`;
          const isSelected = value === currentDateStr;
          
          return (
            <button
              key={date}
              onClick={() => onChange(currentDateStr)}
              className={cn(
                "h-8 flex items-center justify-center text-sm font-medium rounded-md transition-colors",
                isSelected 
                  ? "bg-brand-600 text-white shadow-sm shadow-brand-500/20 font-bold" 
                  : "text-slate-700 hover:bg-slate-100"
              )}
            >
              {date}
            </button>
          );
        })}
      </div>
    </div>
  );
}

interface DatePickerProps {
  value: string;
  onChange: (val: string) => void;
}

export function DatePicker({ value, onChange }: DatePickerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Format date for display
  const displayDate = value ? new Date(value).toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric'
  }) : '';

  return (
    <div className="relative" ref={ref}>
      <div 
        className={cn("flex items-center group bg-white border rounded-xl px-4 py-2 transition-all shadow-sm cursor-pointer select-none", isOpen ? "border-brand-500 ring-2 ring-brand-500/20" : "border-slate-200 hover:bg-slate-50")}
        onClick={() => setIsOpen(!isOpen)}
      >
        <CalendarIcon className={cn("w-4 h-4 transition-colors mr-2 shrink-0", isOpen ? "text-brand-500" : "text-slate-400 group-hover:text-brand-500")} />
        <div className="text-sm font-bold text-slate-700 min-w-[80px]">
          {displayDate || "Выберите дату"}
        </div>
      </div>
      
      {isOpen && (
        <div className="absolute top-full left-0 mt-2 bg-white border border-slate-200 rounded-2xl shadow-xl shadow-slate-200/50 z-50 animate-in fade-in zoom-in-95 duration-200">
           <CustomCalendar 
             value={value} 
             onChange={(val) => { onChange(val); setIsOpen(false); }} 
           />
        </div>
      )}
    </div>
  );
}
