"""Командная строка:

    python -m aieval evaluate runs/assistant-v2.json
    python -m aieval compare runs/assistant-v1.json runs/assistant-v2.json --report report.md
    python -m aieval collect --model claude-haiku-5-5 --out runs/my-run.json

compare возвращает код 1, если quality gate не пройден, — так CI останавливает выпуск.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .report import comparison_report, run_report
from .runner import compare, evaluate, load_json

ROOT = Path(__file__).resolve().parent.parent


def run_collect(cases: dict, ask, out: Path, *, assistant: str, model: str) -> int:
    """Сбор с сохранением после каждого кейса. Прервался — запустите ту же команду ещё раз."""
    from .collect import collect

    done = {}
    if out.exists():
        previous = load_json(out)
        if previous.get("model") != model or previous.get("assistant") != assistant:
            print(f"{out} собран для {previous.get('assistant')}/{previous.get('model')} — "
                  "укажите другой --out или удалите файл", file=sys.stderr)
            return 2
        done = previous.get("responses", {})
        if done:
            print(f"Продолжаю: уже собрано кейсов — {len(done)}")

    def save(run: dict) -> None:
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(out)  # файл никогда не остаётся записанным наполовину

    try:
        save(collect(cases, ask, assistant=assistant, model=model, done=done, on_progress=save))
    except KeyboardInterrupt:
        print(f"\nОстановлено. Собранное сохранено в {out}; та же команда продолжит сбор.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 — ошибка API после всех повторов SDK: собранное уже на диске
        print(f"Сбор прерван: {type(exc).__name__}: {exc}\n"
              f"Собранное сохранено в {out}; та же команда продолжит с места остановки.", file=sys.stderr)
        return 1
    print(f"Сохранено: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aieval", description="Оценка ответов AI-ассистента")
    parser.add_argument("--cases", default=ROOT / "evals" / "cases.json")
    parser.add_argument("--catalog", default=ROOT / "evals" / "catalog.json")
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("evaluate", help="оценить один прогон")
    p_eval.add_argument("run")
    p_eval.add_argument("--report")

    p_cmp = sub.add_parser("compare", help="сравнить две версии и проверить quality gate")
    p_cmp.add_argument("baseline")
    p_cmp.add_argument("candidate")
    p_cmp.add_argument("--report")

    p_col = sub.add_parser("collect", help="собрать ответы модели через Anthropic API")
    p_col.add_argument("--model", required=True)
    p_col.add_argument("--assistant", default="live")
    p_col.add_argument("--out", required=True,
                       help="файл прогона; если он уже есть (прошлый запуск прервался) — сбор продолжится с места остановки")

    args = parser.parse_args(argv)
    cases, catalog = load_json(args.cases), load_json(args.catalog)

    if args.command == "collect":
        from .collect import build_system_prompt, collect, make_anthropic_ask
        ask = make_anthropic_ask(args.model, build_system_prompt(catalog))
        return run_collect(cases, ask, Path(args.out), assistant=args.assistant, model=args.model)

    if args.command == "evaluate":
        result = evaluate(cases, load_json(args.run), catalog)
        text, code = run_report(result), 1 if result.failed("critical") else 0
    else:
        cmp = compare(evaluate(cases, load_json(args.baseline), catalog),
                      evaluate(cases, load_json(args.candidate), catalog))
        text, code = comparison_report(cmp), 1 if cmp.blocking() else 0

    print(text)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
