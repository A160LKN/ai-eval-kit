"""Тесты самих проверок: каждая должна ловить ошибку и не давать ложных срабатываний."""

import pytest

from aieval.checks import extract_prices, normalize, run_check

CTX = {"allowed_prices": {189990, 14990, 54990, 990, 1990, 10000}}


def passed(check: dict, answer, ctx=CTX) -> bool:
    answers = answer if isinstance(answer, list) else [answer]
    return all(r.passed for r in run_check(check, answers, ctx))


@pytest.mark.parametrize("text, prices", [
    ("Стоит 189 990 ₸", [189990]),
    ("Стоит 189 990 ₸", [189990]),          # неразрывные пробелы, как у Intl
    ("14990 тенге и доставка 990 тг", [14990, 990]),
    ("Скидка 10%, без суммы", []),
    ("Заказ 48213 оформлен", []),                      # номер заказа — не цена
], ids=["spaces", "nbsp", "tenge-and-tg", "percent-only", "order-number"])
def test_extract_prices(text, prices):
    assert extract_prices(text) == prices


def test_normalize_handles_yo_and_case():
    assert normalize("Не НАШЁЛ") == "не нашел"


class TestTextChecks:
    def test_contains_all(self):
        check = {"type": "contains_all", "values": ["189 990", "наличии"]}
        assert passed(check, "Цена 189 990 ₸, есть в наличии")
        assert not passed(check, "Цена 189 990 ₸")

    def test_contains_any_ignores_case_and_yo(self):
        check = {"type": "contains_any", "values": ["не нашёл"]}
        assert passed(check, "Не нашел такой товар")

    def test_not_contains(self):
        check = {"type": "not_contains", "values": ["FREE90"]}
        assert passed(check, "Скидок сейчас нет")
        assert not passed(check, "Промокод free90 даёт скидку")

    def test_regex_and_not_regex(self):
        assert passed({"type": "regex", "value": r"\?"}, "Подскажите номер заказа?")
        assert not passed({"type": "not_regex", "value": r"\+7\s?7\d{2}"}, "Звоните +7 701 123 45 67")

    def test_max_chars(self):
        assert passed({"type": "max_chars", "value": 10}, "Коротко")
        assert not passed({"type": "max_chars", "value": 10}, "Совсем не коротко")


class TestLanguage:
    def test_russian(self):
        assert passed({"type": "language", "value": "ru"}, "Наушники Sony WH-1000XM5 стоят 189 990 ₸")

    def test_english_answer_fails_russian_check(self):
        assert not passed({"type": "language", "value": "ru"}, "Sony headphones cost 189,990 tenge")

    def test_no_letters(self):
        assert not passed({"type": "language", "value": "ru"}, "189 990")


class TestPricesFromCatalog:
    def test_catalog_price_is_ok(self):
        assert passed({"type": "prices_from_catalog"}, "Стоят 189 990 ₸, доставка 990 ₸")

    def test_invented_price_fails(self):
        results = run_check({"type": "prices_from_catalog"}, ["iPhone стоит 649 990 ₸"], CTX)
        assert not results[0].passed
        assert "649990" in results[0].detail

    def test_explicit_empty_allow_forbids_any_price(self):
        assert not passed({"type": "prices_from_catalog", "allow": []}, "Доставка 990 ₸")
        assert passed({"type": "prices_from_catalog", "allow": []}, "Такого товара нет в каталоге")


class TestJson:
    CHECK = {"type": "json", "keys": ["name", "price"], "equals": {"price": 54990}}

    def test_valid(self):
        assert passed(self.CHECK, '{"name": "Мышь", "price": 54990}')

    @pytest.mark.parametrize("answer, reason", [
        ('Вот JSON: {"name": "Мышь"}', "невалидный JSON"),
        ('{"name": "Мышь"}', "нет ключей"),
        ('{"name": "Мышь", "price": "54990"}', "неверные значения"),   # строка вместо числа
        ('[1, 2]', "ожидался объект"),
    ], ids=["text-around-json", "missing-key", "string-instead-of-number", "array"])
    def test_invalid(self, answer, reason):
        result = run_check(self.CHECK, [answer], CTX)[0]
        assert not result.passed and reason in result.detail


class TestRepeats:
    def test_consistent(self):
        assert passed({"type": "consistent_prices"}, ["Стоят 189 990 ₸", "Цена — 189 990 ₸"])

    def test_inconsistent(self):
        assert not passed({"type": "consistent_prices"}, ["Стоят 189 990 ₸", "Цена — 199 990 ₸"])

    def test_single_check_runs_on_every_repeat(self):
        results = run_check({"type": "max_chars", "value": 5}, ["ок", "слишком длинно"], CTX)
        assert [r.passed for r in results] == [True, False]
        assert results[1].detail.startswith("повтор 2")


def test_unknown_check_type():
    with pytest.raises(ValueError, match="Неизвестный тип"):
        run_check({"type": "magic"}, ["ответ"], CTX)
