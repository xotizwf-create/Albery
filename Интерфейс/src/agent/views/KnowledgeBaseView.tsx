import { useState } from 'react';
import { Search, Plus, BookOpen, Wrench, FileText, Lock } from 'lucide-react';
import { mockKnowledge } from '../data';
import { cn } from '../../lib/utils';

export function KnowledgeBaseView() {
  const [searchQuery, setSearchQuery] = useState('');
  
  const filteredKnowledge = mockKnowledge.filter(k => 
    k.title.toLowerCase().includes(searchQuery.toLowerCase()) || 
    k.description.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 mb-1">База знаний</h1>
          <p className="text-gray-500 text-sm font-medium">Общая библиотека: каждый агент подключает свои скиллы и инструкции</p>
        </div>
        <div className="flex items-center gap-4">
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input 
              type="text" 
              placeholder="Умный поиск по базе..." 
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full sm:w-72 pl-10 pr-4 py-2.5 bg-white border border-gray-200 rounded-xl text-sm font-medium focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all shadow-sm"
            />
          </div>
          <button className="flex items-center gap-2 px-5 py-2.5 bg-indigo-600 text-white rounded-xl text-sm font-bold hover:bg-indigo-700 shadow-sm shadow-indigo-600/20 transition-all shrink-0">
            <Plus className="w-4 h-4" />
            Добавить
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap gap-2.5">
        <button className="px-4 py-2 bg-indigo-50 text-indigo-700 rounded-xl text-sm font-bold transition-colors">
          Все <span className="ml-1.5 text-indigo-400 font-semibold bg-indigo-100/50 px-1.5 py-0.5 rounded-md">{mockKnowledge.length}</span>
        </button>
        <button className="px-4 py-2 bg-white text-gray-600 hover:bg-gray-50 border border-gray-200 rounded-xl text-sm font-bold transition-colors shadow-sm">
          Инструкции <span className="ml-1.5 text-gray-400 font-semibold bg-gray-100 px-1.5 py-0.5 rounded-md">8</span>
        </button>
        <button className="px-4 py-2 bg-white text-gray-600 hover:bg-gray-50 border border-gray-200 rounded-xl text-sm font-bold transition-colors shadow-sm">
          Скиллы <span className="ml-1.5 text-gray-400 font-semibold bg-gray-100 px-1.5 py-0.5 rounded-md">4</span>
        </button>
        <button className="px-4 py-2 bg-white text-gray-600 hover:bg-gray-50 border border-gray-200 rounded-xl text-sm font-bold transition-colors shadow-sm">
          Регламенты <span className="ml-1.5 text-gray-400 font-semibold bg-gray-100 px-1.5 py-0.5 rounded-md">2</span>
        </button>
      </div>

      {/* List */}
      <div className="flex flex-col gap-3">
        {filteredKnowledge.map((item) => (
          <div key={item.id} className="bg-white p-4 sm:px-6 rounded-2xl border border-gray-200/80 shadow-sm hover:shadow-md hover:border-indigo-300 transition-all group cursor-pointer flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-4 flex-1 min-w-0">
              <div className="w-12 h-12 rounded-xl bg-slate-50 flex items-center justify-center flex-shrink-0 border border-slate-100 group-hover:scale-105 transition-transform">
                {item.type === 'Инструкция' && !item.isLocked && <span className="text-2xl drop-shadow-sm">💬</span>}
                {item.type === 'Скилл' && <span className="text-2xl drop-shadow-sm">🔧</span>}
                {item.type === 'Регламент' && <span className="text-2xl drop-shadow-sm">📋</span>}
                {item.isLocked && <span className="text-2xl drop-shadow-sm">🔒</span>}
              </div>
              <div className="flex-1 min-w-0 pr-4">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-bold text-gray-900 text-[16px] group-hover:text-indigo-600 transition-colors truncate">{item.title}</h3>
                  {item.isLocked && <Lock className="w-3.5 h-3.5 text-rose-400" />}
                </div>
                <p className="text-[13.5px] text-gray-500 font-medium truncate">
                  {item.description}
                </p>
              </div>
            </div>
            
            <div className="flex items-center justify-between sm:justify-end gap-6 shrink-0 mt-2 sm:mt-0 border-t sm:border-0 border-gray-50 pt-3 sm:pt-0">
              <div className="flex items-center gap-3">
                <span className="bg-gray-50 border border-gray-100 px-2.5 py-1 rounded-lg text-[12px] font-bold text-gray-500">{item.type}</span>
                <span className="text-[12px] font-semibold text-gray-400 hidden lg:inline-block">{item.updatedAt}</span>
              </div>
              <div className="flex gap-1.5">
                <div className="w-7 h-7 rounded-lg border border-orange-100 bg-orange-50 flex items-center justify-center text-orange-500 shadow-sm" title="Использует Основной агент">⚡</div>
                {item.type === 'Инструкция' && <div className="w-7 h-7 rounded-lg border border-blue-100 bg-blue-50 flex items-center justify-center text-blue-500 shadow-sm" title="Использует Агент склада">📦</div>}
              </div>
            </div>
          </div>
        ))}

        {filteredKnowledge.length === 0 && (
          <div className="py-12 text-center text-gray-400 font-medium bg-white rounded-2xl border border-gray-200 border-dashed">
            По запросу «{searchQuery}» ничего не найдено
          </div>
        )}

        <div className="bg-gray-50/50 border-2 border-dashed border-gray-200 p-4 rounded-2xl flex items-center justify-center text-center cursor-pointer hover:bg-gray-50 hover:border-gray-300 transition-all group">
          <Plus className="w-5 h-5 text-indigo-500 mr-2 group-hover:scale-110 transition-transform" />
          <span className="font-bold text-gray-700 text-[15px]">Новая инструкция или скилл</span>
        </div>
      </div>
    </div>
  );
}
