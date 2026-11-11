"""Прогон набора кейсов по записанным ответам ассистента и сравнение двух прогонов."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .checks import CheckResult, run_check

SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}


@dataclass
class CaseResult:
    case_id: str
    title: str
    category: str
    severity: str
    status: str                       # passed | failed | missing
    failures: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass
class RunResult:
    assistant: str
    cases: list[CaseResult]

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed_count(self) -> int:
        return sum(c.passed for c in self.cases)

    @property
    def pass_rate(self) -> float:
        return self.passed_count / self.total if self.total else 0.0

    def failed(self, severity: str | None = None) -> list[CaseResult]:
        return [c for c in self.cases if not c.passed and (severity is None or c.severity == severity)]

    def by_id(self) -> dict[str, CaseResult]:
        return {c.case_id: c for c in self.cases}


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_context(catalog: dict) -> dict:
    """Суммы, которые ассистент имеет право называть: цены товаров и стоимость доставки."""
    prices = {p["price"] for p in catalog["products"]}
    delivery_text = " ".join(catalog["delivery"][k] for k in ("astana_almaty", "other_cities"))
    for number in re.findall(r"\d{1,3}(?:[  ]\d{3})+|\d+", delivery_text):
        prices.add(int(re.sub(r"\D", "", number)))
    return {"allowed_prices": prices}


def evaluate(cases: dict, run: dict, catalog: dict) -> RunResult:
    ctx = build_context(catalog)
    results = []
    for case in cases["cases"]:
        raw = run["responses"].get(case["id"])
        base = dict(case_id=case["id"], title=case["title"], category=case["category"], severity=case["severity"])
        if raw is None:
            results.append(CaseResult(**base, status="missing"))
            continue

        answers = raw if isinstance(raw, list) else [raw]
        expected = case.get("repeats", 1)
        failures = []
        if len(answers) != expected:
            failures.append(CheckResult(False, "repeats", f"ожидалось ответов: {expected}, получено: {len(answers)}"))
        for check in case["checks"]:
            failures += [r for r in run_check(check, answers, ctx) if not r.passed]

        results.append(CaseResult(**base, status="failed" if failures else "passed", failures=failures))

    results.sort(key=lambda c: SEVERITY_ORDER[c.severity])
    return RunResult(assistant=run.get("assistant", "?"), cases=results)


@dataclass
class Comparison:
    baseline: RunResult
    candidate: RunResult
    fixed: list[str]
    regressions: list[str]
    still_failing: list[str]

    def blocking(self) -> list[str]:
        """Причины не выпускать новую версию (quality gate)."""
        reasons = [f"критичный кейс не проходит: {c.case_id}" for c in self.candidate.failed("critical")]
        by_id = self.candidate.by_id()
        reasons += [f"регрессия в важном кейсе: {cid}" for cid in self.regressions
                    if by_id[cid].severity in ("critical", "major")]
        return reasons

    def warnings(self) -> list[str]:
        by_id = self.candidate.by_id()
        return [f"регрессия в некритичном кейсе: {cid}" for cid in self.regressions if by_id[cid].severity == "minor"]


def compare(baseline: RunResult, candidate: RunResult) -> Comparison:
    base = baseline.by_id()
    fixed, regressions, still = [], [], []
    for case in candidate.cases:
        before = base.get(case.case_id)
        if before is None:
            continue
        if not before.passed and case.passed:
            fixed.append(case.case_id)
        elif before.passed and not case.passed:
            regressions.append(case.case_id)
        elif not before.passed and not case.passed:
            still.append(case.case_id)
    return Comparison(baseline, candidate, fixed, regressions, still)
