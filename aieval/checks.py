"""Проверки ответов AI-ассистента.

Каждая проверка — функция (ответ, параметры, контекст) → CheckResult.
Проверки детерминированные: одинаковый ответ всегда даёт одинаковый результат,
поэтому их можно запускать в CI и сравнивать версии ассистента между собой.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

# Сумма в тенге: «189 990 ₸», «14990 тенге», «990 тг». Пробелы внутри числа — обычные или неразрывные.
PRICE_RE = re.compile(r"(\d{1,3}(?:[   ]\d{3})+|\d+)\s*(?:₸|тенге|тг\b)", re.IGNORECASE)


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    check: str
    detail: str = ""


def normalize(text: str) -> str:
    """Неразрывные пробелы → обычные, «ё» → «е», нижний регистр: так «Нет в наличии» найдётся в любом написании."""
    return text.replace(" ", " ").replace(" ", " ").replace("ё", "е").replace("Ё", "Е").lower()


def extract_prices(text: str) -> list[int]:
    """Все суммы в тенге из текста: «189 990 ₸ и 990 тг» → [189990, 990]."""
    return [int(re.sub(r"\D", "", match)) for match in PRICE_RE.findall(text)]


# ---------- Проверки одного ответа ----------

def contains_all(answer: str, params: dict, _ctx: dict) -> CheckResult:
    missing = [v for v in params["values"] if normalize(v) not in normalize(answer)]
    return CheckResult(not missing, "contains_all", f"нет в ответе: {missing}" if missing else "")


def contains_any(answer: str, params: dict, _ctx: dict) -> CheckResult:
    found = any(normalize(v) in normalize(answer) for v in params["values"])
    return CheckResult(found, "contains_any", "" if found else f"нет ни одного из: {params['values']}")


def not_contains(answer: str, params: dict, _ctx: dict) -> CheckResult:
    found = [v for v in params["values"] if normalize(v) in normalize(answer)]
    return CheckResult(not found, "not_contains", f"недопустимо в ответе: {found}" if found else "")


def regex(answer: str, params: dict, _ctx: dict) -> CheckResult:
    ok = re.search(params["value"], answer) is not None
    return CheckResult(ok, "regex", "" if ok else f"нет совпадения с /{params['value']}/")


def not_regex(answer: str, params: dict, _ctx: dict) -> CheckResult:
    match = re.search(params["value"], answer)
    return CheckResult(match is None, "not_regex", f"найдено «{match.group(0)}»" if match else "")


def max_chars(answer: str, params: dict, _ctx: dict) -> CheckResult:
    ok = len(answer) <= params["value"]
    return CheckResult(ok, "max_chars", "" if ok else f"{len(answer)} символов при лимите {params['value']}")


def language(answer: str, params: dict, _ctx: dict) -> CheckResult:
    """Язык ответа по доле кириллицы среди букв. Пока поддерживается только «ru»."""
    letters = [ch for ch in answer if ch.isalpha()]
    if not letters:
        return CheckResult(False, "language", "в ответе нет букв")
    cyrillic = sum("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in letters) / len(letters)
    ok = cyrillic >= 0.6 if params["value"] == "ru" else cyrillic < 0.4
    return CheckResult(ok, "language", "" if ok else f"кириллица {cyrillic:.0%} — похоже, ответ не на языке «{params['value']}»")


def prices_from_catalog(answer: str, params: dict, ctx: dict) -> CheckResult:
    """Ассистент не должен выдумывать цены: каждая сумма в ответе должна быть из каталога.
    params.allow — свой список допустимых сумм (например, пустой: никаких цен вообще)."""
    allowed = set(params["allow"]) if "allow" in params else ctx["allowed_prices"]
    invented = [p for p in extract_prices(answer) if p not in allowed]
    return CheckResult(not invented, "prices_from_catalog", f"суммы не из каталога: {invented}" if invented else "")


def json_check(answer: str, params: dict, _ctx: dict) -> CheckResult:
    """Ответ — валидный JSON-объект с нужными ключами и значениями."""
    try:
        data = json.loads(answer.strip())
    except json.JSONDecodeError as error:
        return CheckResult(False, "json", f"невалидный JSON: {error.msg}")
    if not isinstance(data, dict):
        return CheckResult(False, "json", "ожидался объект {…}")
    missing = [k for k in params.get("keys", []) if k not in data]
    if missing:
        return CheckResult(False, "json", f"нет ключей: {missing}")
    wrong = {k: data[k] for k, v in params.get("equals", {}).items() if data.get(k) != v}
    return CheckResult(not wrong, "json", f"неверные значения: {wrong}" if wrong else "")


# ---------- Проверки повторных ответов ----------

def consistent_prices(answers: list[str], _params: dict, _ctx: dict) -> CheckResult:
    """На один и тот же вопрос ассистент должен называть одни и те же суммы."""
    variants = {tuple(sorted(extract_prices(a))) for a in answers}
    ok = len(variants) == 1
    return CheckResult(ok, "consistent_prices", "" if ok else f"разные суммы в повторах: {sorted(variants)}")


SINGLE: dict[str, Callable[[str, dict, dict], CheckResult]] = {
    "contains_all": contains_all,
    "contains_any": contains_any,
    "not_contains": not_contains,
    "regex": regex,
    "not_regex": not_regex,
    "max_chars": max_chars,
    "language": language,
    "prices_from_catalog": prices_from_catalog,
    "json": json_check,
}

MULTI: dict[str, Callable[[list[str], dict, dict], CheckResult]] = {
    "consistent_prices": consistent_prices,
}


def run_check(check: dict, answers: list[str], ctx: dict) -> list[CheckResult]:
    """Запускает проверку. Проверка одного ответа применяется к каждому повтору."""
    kind = check["type"]
    if kind in MULTI:
        return [MULTI[kind](answers, check, ctx)]
    if kind in SINGLE:
        results = [SINGLE[kind](a, check, ctx) for a in answers]
        if len(answers) > 1:
            results = [CheckResult(r.passed, r.check, f"повтор {i + 1}: {r.detail}" if r.detail else "")
                       for i, r in enumerate(results)]
        return results
    raise ValueError(f"Неизвестный тип проверки: {kind}")
