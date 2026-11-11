"""Отчёт в Markdown: читается в терминале, на GitHub и в сводке запуска GitHub Actions."""

from __future__ import annotations

from collections import defaultdict

from .runner import Comparison, RunResult

ICON = {"passed": "✅", "failed": "❌", "missing": "⚪"}
SEVERITY = {"critical": "критичный", "major": "важный", "minor": "некритичный"}


def _summary_table(run: RunResult) -> list[str]:
    by_category = defaultdict(lambda: [0, 0])
    for case in run.cases:
        by_category[case.category][0] += case.passed
        by_category[case.category][1] += 1
    lines = ["| Категория | Прошло |", "| --- | --- |"]
    lines += [f"| {cat} | {ok} из {total} |" for cat, (ok, total) in by_category.items()]
    return lines


def _failures(run: RunResult) -> list[str]:
    failed = run.failed()
    if not failed:
        return ["Все кейсы прошли."]
    lines = []
    for case in failed:
        lines.append(f"- **{case.case_id}** — {case.title} ({SEVERITY[case.severity]})")
        if case.status == "missing":
            lines.append("  - нет ответа в прогоне")
        lines += [f"  - `{f.check}`: {f.detail}" for f in case.failures]
    return lines


def run_report(run: RunResult) -> str:
    lines = [
        f"# Оценка ассистента {run.assistant}",
        "",
        f"Прошло кейсов: **{run.passed_count} из {run.total}** ({run.pass_rate:.0%}), "
        f"критичных ошибок: **{len(run.failed('critical'))}**.",
        "",
        *_summary_table(run),
        "",
        "## Ошибки",
        "",
        *_failures(run),
    ]
    return "\n".join(lines) + "\n"


def comparison_report(cmp: Comparison) -> str:
    base, cand = cmp.baseline, cmp.candidate
    blocking = cmp.blocking()
    verdict = "🚫 Выпускать нельзя" if blocking else "✅ Можно выпускать"

    lines = [
        f"# {base.assistant} → {cand.assistant}: {verdict}",
        "",
        "| | " + base.assistant + " | " + cand.assistant + " |",
        "| --- | --- | --- |",
        f"| Прошло кейсов | {base.passed_count} из {base.total} | {cand.passed_count} из {cand.total} |",
        f"| Критичных ошибок | {len(base.failed('critical'))} | {len(cand.failed('critical'))} |",
        f"| Исправлено | | {len(cmp.fixed)} |",
        f"| Регрессий | | {len(cmp.regressions)} |",
        "",
    ]
    if blocking:
        lines += ["## Что блокирует релиз", "", *[f"- {r}" for r in blocking], ""]
    if cmp.warnings():
        lines += ["## Предупреждения", "", *[f"- {w}" for w in cmp.warnings()], ""]

    base_by_id = base.by_id()
    lines += ["## Все кейсы", "", f"| Кейс | Критичность | {base.assistant} | {cand.assistant} | Изменение |",
              "| --- | --- | --- | --- | --- |"]
    for case in cand.cases:
        before = base_by_id.get(case.case_id)
        change = ("исправлено" if case.case_id in cmp.fixed else
                  "**регрессия**" if case.case_id in cmp.regressions else
                  "всё ещё ошибка" if case.case_id in cmp.still_failing else "")
        lines.append(f"| {case.title} | {SEVERITY[case.severity]} | {ICON[before.status] if before else '—'} | "
                     f"{ICON[case.status]} | {change} |")

    lines += ["", f"## Ошибки {cand.assistant}", "", *_failures(cand)]
    return "\n".join(lines) + "\n"
