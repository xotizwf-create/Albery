import { cn } from "../lib/utils";

interface KpiCardProps {
  title: string;
  value: string;
  subValue?: string;
  className?: string;
}

export function KpiCard({ title, value, subValue, className }: KpiCardProps) {
  return (
    <div className={cn("bg-white p-5 rounded-2xl border border-slate-200/60 shadow-sm flex flex-col gap-3", className)}>
      <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
        {title}
      </div>
      <div>
        <div className="text-2xl font-bold text-slate-900">{value}</div>
        {subValue && (
          <div className="text-xs text-slate-500 mt-1 font-medium">{subValue}</div>
        )}
      </div>
    </div>
  );
}
