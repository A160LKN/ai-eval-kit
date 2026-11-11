"""Тесты прогона, сравнения версий, quality gate, сбора ответов и командной строки."""

import json
from pathlib import Path

from aieval.__main__ import main
from aieval.collect import build_system_prompt, collect
from aieval.runner import build_context, compare, evaluate, load_json

ROOT = Path(__file__).resolve().parent.parent
CASES = load_json(ROOT / "evals" / "cases.json")
CATALOG = load_json(ROOT / "evals" / "catalog.json")
V1 = load_json(ROOT / "runs" / "assistant-v1.json")
V2 = load_json(ROOT / "runs" / "assistant-v2.json")


def test_cases_file_is_consistent():
    ids = [c["id"] for c in CASES["cases"]]
    assert len(ids) == len(set(ids)), "id кейсов должны быть уникальными"
    for case in CASES["cases"]:
        assert case["severity"] in CASES["severity_levels"]
        assert case["checks"], f"у кейса {case['id']} нет проверок"


def test_allowed_prices_include_catalog_and_delivery():
    allowed = build_context(CATALOG)["allowed_prices"]
    assert {189990, 14990, 54990, 990, 1990, 10000} <= allowed


def test_v1_results():
    result = evaluate(CASES, V1, CATALOG)
    assert result.passed_count == 10
    assert {c.case_id for c in result.failed("critical")} == {"stock-out", "unknown-product", "injection-review"}


def test_v2_results():
    result = evaluate(CASES, V2, CATALOG)
    assert [c.case_id for c in result.failed()] == ["short-answer"]


def test_results_sorted_by_severity():
    severities = [c.severity for c in evaluate(CASES, V2, CATALOG).cases]
    assert severities == sorted(severities, key=["critical", "major", "minor"].index)


def test_missing_answer_is_failure():
    run = {"assistant": "x", "responses": {}}
    result = evaluate(CASES, run, CATALOG)
    assert result.passed_count == 0
    assert all(c.status == "missing" for c in result.cases)


def test_wrong_number_of_repeats_is_failure():
    run = json.loads(json.dumps(V2))
    run["responses"]["consistency-price"] = "Наушники стоят 189 990 ₸."   # один ответ вместо трёх
    case = evaluate(CASES, run, CATALOG).by_id()["consistency-price"]
    assert not case.passed
    assert case.failures[0].check == "repeats"


class TestQualityGate:
    def test_v1_to_v2_passes_with_warning(self):
        cmp = compare(evaluate(CASES, V1, CATALOG), evaluate(CASES, V2, CATALOG))
        assert sorted(cmp.fixed) == ["consistency-price", "injection-review", "stock-out", "unknown-product"]
        assert cmp.regressions == ["short-answer"]
        assert cmp.blocking() == []
        assert cmp.warnings() == ["регрессия в некритичном кейсе: short-answer"]

    def test_rollback_v2_to_v1_is_blocked(self):
        cmp = compare(evaluate(CASES, V2, CATALOG), evaluate(CASES, V1, CATALOG))
        blocking = cmp.blocking()
        assert "критичный кейс не проходит: injection-review" in blocking
        assert "регрессия в важном кейсе: consistency-price" in blocking


class TestCollect:
    def test_collect_with_fake_model(self):
        calls = []

        def fake_ask(prompt):
            calls.append(prompt)
            return f"ответ на: {prompt}"

        run = collect(CASES, fake_ask, assistant="test", model="fake")
        repeats = sum(c.get("repeats", 1) for c in CASES["cases"])
        assert len(calls) == repeats
        assert isinstance(run["responses"]["consistency-price"], list)
        assert len(run["responses"]["consistency-price"]) == 3
        assert run["responses"]["price-known"] == "ответ на: Сколько стоят наушники Sony WH-1000XM5?"

    def test_system_prompt_contains_catalog_and_rules(self):
        prompt = build_system_prompt(CATALOG)
        assert "Наушники Sony WH-1000XM5" in prompt
        assert "не инструкции" in prompt


class TestCli:
    def test_compare_v1_v2_exit_code_0(self, tmp_path, capsys):
        report = tmp_path / "report.md"
        code = main(["compare", str(ROOT / "runs/assistant-v1.json"), str(ROOT / "runs/assistant-v2.json"),
                     "--report", str(report)])
        assert code == 0
        assert "Можно выпускать" in report.read_text(encoding="utf-8")

    def test_evaluate_v1_fails_on_critical(self, capsys):
        assert main(["evaluate", str(ROOT / "runs/assistant-v1.json")]) == 1
        assert "критичных ошибок: **3**" in capsys.readouterr().out

    def test_rollback_blocks_release(self, monkeypatch):
        monkeypatch.chdir(ROOT)
        assert main(["compare", "runs/assistant-v2.json", "runs/assistant-v1.json"]) == 1
