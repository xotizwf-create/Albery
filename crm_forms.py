"""Self-hosted CRM lead questionnaires (owner Евгений, 2026-07-17).

A public form lives at /form/<token>. When a candidate submits it, the Albery app creates a
DEAL in the chosen funnel (deal category) with the answers mapped to deal fields, plus an
optional linked contact. Bitrix REST has no method to create native CRM web-forms, so we host
our own; the agent creates one via the create_crm_lead_form MCP tool (in mcp/context_server).

Design notes:
  - Public routes are auth-exempt (candidates have no admin session) — /form/ is in
    AUTH_EXEMPT_PREFIXES. The token is an unguessable slug; a disabled/unknown token 404s.
  - Deal creation calls mcp.context_server._crm_call lazily (the CRM helpers + OAuth token live
    there); a self-contained import here would be circular.
  - Untrusted input: answers are escaped on render and passed to Bitrix as field values only;
    no HTML is ever built from them via concatenation without escaping.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import secrets
from typing import Any

from flask import Response, request

from app import app, pg_connect

log = logging.getLogger("crm_forms")

# question type -> (deal userfield USER_TYPE_ID, HTML input builder key)
QUESTION_TYPES = {
    "text": "string",
    "textarea": "string",
    "number": "double",
    "select": "enumeration",
    "phone": "string",
    "email": "string",
    "url": "url",
}
_ROLE_FIELDS = {"name", "telegram", "phone", "email"}  # special roles handled on the contact / title


def public_base() -> str:
    """Public origin for the shareable form link (candidate-facing)."""
    base = (os.getenv("CRM_FORMS_PUBLIC_BASE") or "https://www.m4s.ru").strip().rstrip("/")
    return base


def form_url(token: str) -> str:
    return f"{public_base()}/form/{token}"


# --- storage --------------------------------------------------------------------------------

def save_form(row: dict[str, Any]) -> None:
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO crm_lead_forms (token, title, intro, category_id, stage_id, "
                    "pipeline_name, assigned_by_id, deal_title_tpl, questions, success_message, "
                    "create_contact, created_by) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (token) DO UPDATE SET title=EXCLUDED.title, intro=EXCLUDED.intro, "
                    "category_id=EXCLUDED.category_id, stage_id=EXCLUDED.stage_id, "
                    "pipeline_name=EXCLUDED.pipeline_name, assigned_by_id=EXCLUDED.assigned_by_id, "
                    "deal_title_tpl=EXCLUDED.deal_title_tpl, questions=EXCLUDED.questions, "
                    "success_message=EXCLUDED.success_message, create_contact=EXCLUDED.create_contact, "
                    "updated_at=now()",
                    (row["token"], row["title"], row.get("intro", ""), int(row["category_id"]),
                     row["stage_id"], row.get("pipeline_name", ""), row.get("assigned_by_id"),
                     row.get("deal_title_tpl", ""), json.dumps(row["questions"], ensure_ascii=False),
                     row.get("success_message", ""), bool(row.get("create_contact", True)),
                     row.get("created_by", "agent")),
                )


def get_form(token: str) -> dict[str, Any] | None:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM crm_lead_forms WHERE token = %s", (token,))
            row = cur.fetchone()
            return dict(row) if row else None


def list_forms() -> list[dict[str, Any]]:
    with pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT token, title, category_id, pipeline_name, stage_id, is_active, "
                        "submissions, last_submission_at, created_at FROM crm_lead_forms "
                        "ORDER BY created_at DESC")
            return [dict(r) for r in cur.fetchall()]


def update_form(token: str, changes: dict[str, Any]) -> bool:
    allowed = {"title", "intro", "success_message", "is_active", "assigned_by_id", "stage_id",
               "deal_title_tpl", "create_contact"}
    sets, vals = [], []
    for k, v in changes.items():
        if k in allowed:
            sets.append(f"{k} = %s")
            vals.append(v)
    if not sets:
        return False
    vals.append(token)
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(f"UPDATE crm_lead_forms SET {', '.join(sets)}, updated_at=now() "
                            f"WHERE token = %s RETURNING token", vals)
                return cur.fetchone() is not None


def delete_form(token: str) -> bool:
    with pg_connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM crm_lead_forms WHERE token = %s RETURNING token", (token,))
                return cur.fetchone() is not None


def _log_submission(token: str, deal_id, contact_id, data, error, ip) -> None:
    try:
        with pg_connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO crm_lead_form_submissions (form_token, deal_id, contact_id, data, error, ip) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (token, deal_id, contact_id, json.dumps(data, ensure_ascii=False), error, ip))
                    if not error:
                        cur.execute("UPDATE crm_lead_forms SET submissions = submissions + 1, "
                                    "last_submission_at = now() WHERE token = %s", (token,))
    except Exception:  # noqa: BLE001
        log.warning("crm form submission log failed", exc_info=True)


# --- deal creation on submit ----------------------------------------------------------------

def _crm(method: str, payload: dict[str, Any]) -> Any:
    from mcp.context_server import _crm_call  # lazy: CRM helpers + OAuth token live there
    return _crm_call(method, payload)


def _create_contact(name: str, phone: str, telegram: str) -> int | None:
    fields: dict[str, Any] = {"NAME": name or "Кандидат", "OPENED": "Y"}
    if phone:
        fields["PHONE"] = [{"VALUE": phone, "VALUE_TYPE": "WORK"}]
    if telegram:
        fields["IM"] = [{"VALUE": telegram, "VALUE_TYPE": "TELEGRAM"}]
    try:
        cid = _crm("crm.contact.add", {"fields": fields}).get("result")
        return int(cid) if cid else None
    except Exception:  # noqa: BLE001
        log.warning("crm form: contact.add failed", exc_info=True)
        return None


def submit_form(form: dict[str, Any], answers: dict[str, str], ip: str) -> dict[str, Any]:
    """Validate answers, create the deal (+ optional contact). Returns {ok, deal_id, ...}."""
    questions = form["questions"]
    # required-field check
    missing = [q["label"] for q in questions
               if q.get("required") and not str(answers.get(q["key"], "")).strip()]
    if missing:
        return {"ok": False, "error": "Заполните обязательные поля: " + ", ".join(missing)}

    name = phone = telegram = ""
    deal_fields: dict[str, Any] = {}
    for q in questions:
        raw = str(answers.get(q["key"], "")).strip()
        role = q.get("role") or ""
        if role == "name":
            name = raw
        elif role == "phone":
            phone = raw
        elif role == "telegram":
            telegram = raw
        if not raw:
            continue
        code = q.get("field_code")
        if code:
            if q["type"] == "number":
                num = re.sub(r"[^\d.\-]", "", raw.replace(",", "."))
                deal_fields[code] = float(num) if num not in ("", "-", ".") else 0
            elif q["type"] == "select":
                # map the chosen label to its enumeration item id
                deal_fields[code] = q.get("option_ids", {}).get(raw, raw)
            else:
                deal_fields[code] = raw

    contact_id = None
    if form.get("create_contact"):
        contact_id = _create_contact(name, phone, telegram)

    title_tpl = form.get("deal_title_tpl") or "Заявка партнёра"
    title = re.sub(r"\{(\w+)\}", lambda m: str(answers.get(m.group(1), "")).strip() or "—", title_tpl)
    title = title.strip() or "Заявка партнёра"

    fields: dict[str, Any] = {
        "TITLE": title[:250],
        "CATEGORY_ID": int(form["category_id"]),
        "STAGE_ID": form["stage_id"],
        "OPENED": "Y",
        "SOURCE_ID": "WEBFORM",
    }
    if form.get("assigned_by_id"):
        fields["ASSIGNED_BY_ID"] = int(form["assigned_by_id"])
    if contact_id:
        fields["CONTACT_ID"] = contact_id
    fields.update(deal_fields)
    # keep a human-readable copy of the whole questionnaire in the deal comment
    lines = [f"{q['label']}: {str(answers.get(q['key'], '')).strip() or '—'}" for q in questions]
    fields["COMMENTS"] = "Заявка с анкеты:\n" + "\n".join(lines)

    try:
        deal_id = _crm("crm.deal.add", {"fields": fields, "params": {"REGISTER_SONET_EVENT": "Y"}}).get("result")
        deal_id = int(deal_id) if deal_id else None
    except Exception as exc:  # noqa: BLE001
        log.warning("crm form: deal.add failed", exc_info=True)
        _log_submission(form["token"], None, contact_id, answers, str(exc)[:400], ip)
        return {"ok": False, "error": "Не удалось создать заявку. Попробуйте позже."}

    _log_submission(form["token"], deal_id, contact_id, answers, None, ip)
    return {"ok": True, "deal_id": deal_id, "contact_id": contact_id}


# --- public HTML ----------------------------------------------------------------------------

_PAGE_CSS = """
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:#f1f5f9;color:#0f172a;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  display:flex;justify-content:center;padding:24px 16px}
.card{width:min(620px,100%);background:#fff;border:1px solid #e2e8f0;border-radius:20px;
  box-shadow:0 24px 60px rgba(15,23,42,.08);overflow:hidden;height:fit-content}
.head{background:linear-gradient(135deg,#4f46e5,#6366f1);color:#fff;padding:28px 28px 24px}
.head h1{margin:0 0 8px;font-size:22px;font-weight:800;letter-spacing:-.01em}
.head p{margin:0;font-size:14px;line-height:1.5;color:#e0e7ff;white-space:pre-wrap}
form{padding:24px 28px 28px}
.field{margin-bottom:18px}
label{display:block;font-size:13px;font-weight:700;margin-bottom:7px;color:#334155}
label .req{color:#e11d48;margin-left:2px}
input,textarea,select{width:100%;padding:12px 14px;border:1px solid #cbd5e1;border-radius:12px;
  font-size:15px;font-family:inherit;background:#f8fafc;color:#0f172a;outline:none;transition:.15s}
input:focus,textarea:focus,select:focus{border-color:#6366f1;background:#fff;box-shadow:0 0 0 3px rgba(99,102,241,.15)}
textarea{resize:vertical;min-height:84px}
.hint{font-size:12px;color:#94a3b8;margin-top:5px}
button{width:100%;margin-top:8px;padding:14px;border:0;border-radius:12px;background:#4f46e5;color:#fff;
  font-size:15px;font-weight:800;cursor:pointer;transition:.15s}
button:hover{background:#4338ca}button:disabled{opacity:.6;cursor:default}
.err{background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;padding:12px 14px;border-radius:12px;
  font-size:14px;margin-bottom:18px}
.done{padding:44px 28px;text-align:center}
.done .ok{width:64px;height:64px;border-radius:50%;background:#dcfce7;color:#16a34a;display:grid;
  place-items:center;margin:0 auto 18px;font-size:34px}
.done h2{margin:0 0 8px;font-size:20px;font-weight:800}
.done p{margin:0;color:#475569;font-size:15px;line-height:1.5}
.foot{padding:0 28px 22px;text-align:center;font-size:11px;color:#cbd5e1}
"""


def _field_html(q: dict[str, Any]) -> str:
    key = html.escape(q["key"])
    label = html.escape(q["label"])
    req = ' <span class="req">*</span>' if q.get("required") else ""
    req_attr = " required" if q.get("required") else ""
    ph = html.escape(q.get("placeholder") or "")
    hint = f'<div class="hint">{html.escape(q["hint"])}</div>' if q.get("hint") else ""
    t = q["type"]
    if t == "textarea":
        ctl = f'<textarea name="{key}" placeholder="{ph}"{req_attr}></textarea>'
    elif t == "select":
        opts = '<option value="" disabled selected>Выберите…</option>' + "".join(
            f'<option value="{html.escape(o)}">{html.escape(o)}</option>' for o in (q.get("options") or []))
        ctl = f'<select name="{key}"{req_attr}>{opts}</select>'
    else:
        itype = {"number": "text", "phone": "tel", "email": "email", "url": "url"}.get(t, "text")
        mode = ' inputmode="numeric"' if t == "number" else ""
        ctl = f'<input type="{itype}" name="{key}" placeholder="{ph}"{mode}{req_attr}>'
    return f'<div class="field"><label>{label}{req}</label>{ctl}{hint}</div>'


def render_form_page(form: dict[str, Any], error: str = "") -> str:
    title = html.escape(form["title"])
    intro = html.escape(form.get("intro") or "")
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    fields = "".join(_field_html(q) for q in form["questions"])
    intro_html = f"<p>{intro}</p>" if intro else ""
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{_PAGE_CSS}</style></head><body>
<div class="card">
  <div class="head"><h1>{title}</h1>{intro_html}</div>
  <form method="post" action="/form/{html.escape(form['token'])}">
    {err}{fields}
    <button type="submit">Отправить заявку</button>
  </form>
  <div class="foot">Защищённая форма · данные попадают напрямую в CRM</div>
</div></body></html>"""


def render_success_page(form: dict[str, Any]) -> str:
    title = html.escape(form["title"])
    msg = html.escape(form.get("success_message") or
                      "Мы получили вашу заявку и свяжемся с вами после предварительного расчёта.")
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{_PAGE_CSS}</style></head><body>
<div class="card"><div class="head"><h1>{title}</h1></div>
  <div class="done"><div class="ok">✓</div><h2>Спасибо! Заявка отправлена</h2><p>{msg}</p></div>
  <div class="foot">Защищённая форма · данные попадают напрямую в CRM</div>
</div></body></html>"""


# --- routes (auth-exempt: /form/ is in AUTH_EXEMPT_PREFIXES) ---------------------------------

@app.get("/form/<token>")
def crm_form_page(token: str):
    form = get_form(token)
    if not form or not form.get("is_active"):
        return Response("<h1 style='font-family:sans-serif;text-align:center;margin-top:80px'>"
                        "Форма не найдена или отключена</h1>", status=404, mimetype="text/html")
    return Response(render_form_page(form), mimetype="text/html")


@app.post("/form/<token>")
def crm_form_submit(token: str):
    form = get_form(token)
    if not form or not form.get("is_active"):
        return Response("Форма не найдена или отключена", status=404, mimetype="text/plain")
    answers = {q["key"]: (request.form.get(q["key"]) or "") for q in form["questions"]}
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    result = submit_form(form, answers, ip)
    if not result.get("ok"):
        return Response(render_form_page(form, error=result.get("error") or "Ошибка отправки."),
                        status=200, mimetype="text/html")
    return Response(render_success_page(form), mimetype="text/html")


log.info("crm_forms loaded: /form/<token> public routes registered")


def _new_token() -> str:
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].lower()
