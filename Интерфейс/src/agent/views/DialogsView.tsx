import { useState } from "react";
import {
  Search,
  ExternalLink,
  Filter,
  Bot,
  User,
  Check,
  Clock,
  Wrench,
  Package,
  Zap,
  BookOpen,
  Crown,
} from "lucide-react";
import { mockChats, mockMessages, mockAgents } from "../data";
import { cn } from "../../lib/utils";

export function DialogsView() {
  const [activeChat, setActiveChat] = useState(mockChats[0].id);
  const [activeAgentId, setActiveAgentId] = useState(mockAgents[0].id);

  const activeChatDetails =
    mockChats.find((c) => c.id === activeChat) || mockChats[0];
  const activeAgent =
    mockAgents.find((a) => a.id === activeAgentId) || mockAgents[0];

  return (
    <div className="flex h-[calc(100vh-6rem)] bg-white rounded-3xl shadow-sm border border-gray-200/60 overflow-hidden">
      {/* Leftmost Pane - Agents */}
      <div className="w-[280px] border-r border-gray-100 flex flex-col bg-slate-50/50 shrink-0">
        <div className="p-4 border-b border-gray-100 h-16 flex items-center shrink-0">
          <h2 className="font-bold text-gray-900 text-[15px]">Агенты</h2>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
          {mockAgents
            .filter((a) => a.isActive)
            .map((agent) => {
              const isActive = agent.id === activeAgentId;
              const Icon =
                agent.iconType === "zap"
                  ? Zap
                  : agent.iconType === "book"
                    ? BookOpen
                    : agent.iconType === "crown"
                      ? Crown
                      : Package;

              return (
                <button
                  key={agent.id}
                  onClick={() => setActiveAgentId(agent.id)}
                  className={cn(
                    "w-full flex items-center gap-3 p-3 rounded-2xl transition-all text-left group",
                    isActive
                      ? "bg-white shadow-sm border border-gray-200/80"
                      : "hover:bg-white/60 border border-transparent",
                  )}
                >
                  <div
                    className={cn(
                      "w-10 h-10 rounded-xl flex items-center justify-center shrink-0 shadow-sm transition-transform",
                      agent.iconBg,
                      isActive && "scale-105",
                    )}
                  >
                    <Icon className="w-5 h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-gray-900 text-[14px] truncate">
                      {agent.name}
                    </div>
                    <div className="text-[12px] text-gray-500 font-medium truncate mt-0.5">
                      {agent.type}
                    </div>
                  </div>
                </button>
              );
            })}
        </div>
      </div>

      {/* Middle Pane - Dialogs List */}
      <div className="w-[320px] border-r border-gray-100 flex flex-col bg-white shrink-0">
        <div className="p-4 border-b border-gray-100 space-y-4 bg-white shrink-0">
          <div className="flex gap-2">
            <button className="flex-1 px-3 py-2 text-sm font-bold bg-white border border-gray-200 text-gray-700 rounded-xl hover:bg-gray-50 shadow-sm transition-colors">
              Bitrix{" "}
              <span className="text-gray-400 ml-1.5 bg-gray-100 px-1.5 py-0.5 rounded-md">
                12
              </span>
            </button>
            <button className="flex-1 px-3 py-2 text-sm font-bold bg-gray-50 text-gray-500 rounded-xl hover:bg-gray-100 transition-colors border border-transparent">
              Telegram{" "}
              <span className="ml-1.5 bg-white px-1.5 py-0.5 rounded-md">
                3
              </span>
            </button>
          </div>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Поиск по переписке..."
              className="w-full pl-10 pr-4 py-2 bg-gray-50 border-none rounded-xl text-[13.5px] font-medium focus:bg-white focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 outline-none transition-all shadow-sm"
            />
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-hide [&::-webkit-scrollbar]:hidden">
            {[
              "Все",
              "С ошибками",
              "Все функции (ops)",
              "База знаний (faq)",
            ].map((filter, i) => (
              <button
                key={i}
                className={cn(
                  "whitespace-nowrap px-3 py-1.5 rounded-lg text-xs font-bold transition-colors",
                  i === 0
                    ? "bg-indigo-50 text-indigo-700"
                    : "bg-gray-50 text-gray-600 hover:bg-gray-100",
                )}
              >
                {filter}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
          {mockChats.map((chat) => (
            <button
              key={chat.id}
              onClick={() => setActiveChat(chat.id)}
              className={cn(
                "w-full p-4 flex items-start gap-3 border-b border-gray-50 transition-all text-left relative",
                activeChat === chat.id ? "bg-indigo-50/30" : "hover:bg-gray-50",
              )}
            >
              {activeChat === chat.id && (
                <div className="absolute left-0 top-0 bottom-0 w-1 bg-indigo-600 rounded-r-full" />
              )}
              <div
                className={cn(
                  "w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-[13px] shrink-0 shadow-sm",
                  chat.avatarColor,
                )}
              >
                {chat.avatarInitials}
              </div>
              <div className="flex-1 min-w-0 pt-0.5">
                <div className="flex justify-between items-center mb-1">
                  <span className="font-bold text-[14px] text-gray-900 truncate pr-2">
                    {chat.userName}
                  </span>
                  <span className="text-[11px] font-bold text-gray-400 shrink-0">
                    {chat.time}
                  </span>
                </div>
                <div className="text-[13px] text-gray-500 font-medium mb-2 truncate">
                  {chat.lastMessage}
                </div>
                <div className="flex items-center gap-1.5">
                  {chat.tag === "ошибка" && (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-md text-rose-600 bg-rose-50 border border-rose-100 uppercase tracking-wider">
                      ошибка
                    </span>
                  )}
                  {chat.tag === "ops" && (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-md text-emerald-600 bg-emerald-50 border border-emerald-100 uppercase tracking-wider">
                      ops
                    </span>
                  )}
                  {chat.tag === "faq" && (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-md text-indigo-600 bg-indigo-50 border border-indigo-100 uppercase tracking-wider">
                      faq
                    </span>
                  )}
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Right Chat Area */}
      <div className="flex-1 flex flex-col bg-white min-w-0">
        {/* Header */}
        <div className="h-16 border-b border-gray-100 flex items-center justify-between px-6 shrink-0 bg-white">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                "w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-[13px] shadow-sm",
                activeChatDetails.avatarColor,
              )}
            >
              {activeChatDetails.avatarInitials}
            </div>
            <div>
              <h2 className="font-bold text-[15px] text-gray-900 leading-tight">
                {activeChatDetails.userName}
              </h2>
              <div className="text-[12px] text-gray-500 font-medium flex items-center gap-1 mt-0.5">
                Пользователь • агент:
                <Bot className="w-3.5 h-3.5 text-indigo-500 ml-1" />
                <span className="text-indigo-600 font-bold">
                  {activeAgent.name}
                </span>
              </div>
            </div>
          </div>
          <button className="flex items-center gap-2 px-4 py-2 text-[13px] font-bold text-gray-700 bg-white border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors shadow-sm">
            Открыть в Bitrix
            <ExternalLink className="w-4 h-4" />
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 min-h-0 overflow-y-auto p-6 space-y-6 bg-slate-50/50 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full">
          <div className="flex justify-center">
            <span className="text-[11px] font-bold text-gray-400 bg-white border border-gray-200 px-3 py-1 rounded-lg shadow-sm">
              Сегодня
            </span>
          </div>

          {[...mockMessages, ...mockMessages, ...mockMessages].map(
            (msg, idx) => (
              <div
                key={`${msg.id}-${idx}`}
                className={cn(
                  "flex flex-col max-w-[85%]",
                  msg.sender === "user" ? "items-start" : "items-end ml-auto",
                )}
              >
                {msg.text && (
                  <div
                    className={cn(
                      "px-5 py-3.5 rounded-2xl text-[14px] font-medium leading-relaxed shadow-sm",
                      msg.sender === "user"
                        ? "bg-white text-gray-800 rounded-tl-sm border border-gray-100"
                        : "bg-indigo-600 text-white rounded-tr-sm",
                    )}
                  >
                    {msg.text}
                  </div>
                )}

                {msg.card && (
                  <div
                    className={cn(
                      "p-5 rounded-2xl w-[420px] shadow-sm border",
                      msg.card.status === "success"
                        ? "bg-indigo-600 border-indigo-500"
                        : "bg-indigo-500 border-indigo-400",
                      "text-white",
                    )}
                  >
                    <div className="flex items-center gap-2.5 mb-4">
                      <span className="font-bold text-[15px]">
                        {msg.card.title}
                      </span>
                      {msg.card.status === "success" && (
                        <div className="w-5 h-5 rounded-full bg-indigo-500 flex items-center justify-center border border-indigo-400">
                          <Check className="w-3.5 h-3.5 text-emerald-300" />
                        </div>
                      )}
                    </div>
                    {msg.card.lines.length > 0 && (
                      <div className="space-y-2 text-[13.5px] text-indigo-50 mb-5 font-medium leading-relaxed">
                        {msg.card.lines.map((line, i) => (
                          <div key={i}>{line}</div>
                        ))}
                      </div>
                    )}
                    <div className="flex items-center gap-2 text-[11px] font-bold text-indigo-200 uppercase tracking-wider">
                      {msg.card.meta}
                    </div>
                  </div>
                )}
              </div>
            ),
          )}
        </div>

        {/* Footer info */}
        <div className="p-3.5 text-center border-t border-gray-100 bg-white text-[12px] text-gray-400 font-bold uppercase tracking-wider">
          👁 Режим просмотра — вы видите переписку сотрудника с агентом
        </div>
      </div>
    </div>
  );
}
