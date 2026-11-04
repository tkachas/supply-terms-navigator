"""Сценарии, которых нет в учебном наборе: изменённое поле, новый договор,
статусы и даты допсоглашений, кодировка, неоднозначности.

Документы собираются из шаблонов того же «общего формата», что и в задании, —
так же, как на встрече «изменим одно поле и загрузим новый договор»."""
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from terms_navigator.model import FieldState, PendingKind
from terms_navigator.service import analyze_files

DOCS = Path(__file__).resolve().parent.parent / "documents"
CHECK = date(2026, 3, 10)


def contract(no="STN-104", supplier="ООО «Альфа Трейд»", amount="2 450 000", words="два миллиона четыреста пятьдесят тысяч",
             deadline="25.03.2026", status="подписан обеими сторонами 01.03.2026.", extra=""):
    return f"""ДОГОВОР ПОСТАВКИ № {no}
Дата: 01.03.2026
Статус: {status}

1.1. Покупатель: ООО «СеверМет». Поставщик: {supplier}.
1.2. Предмет: поставка насосного оборудования.

2.1. Общая стоимость поставки составляет {amount} ({words}) рублей, включая НДС.

3.1. Поставщик обязуется поставить оборудование не позднее {deadline}.
{extra}
5.1. Изменения условий действуют после подписания дополнительного соглашения обеими сторонами и наступления указанной в нем даты вступления в силу.
"""


def amendment(no="1", contract_no="STN-104", status="подписано обеими сторонами 05.03.2026.",
              effective="Настоящее соглашение вступает в силу 05.03.2026.", deadline="15.04.2026", title_prefix=""):
    return f"""{title_prefix}ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № {no} К ДОГОВОРУ {contract_no}
Дата: 05.03.2026
Статус: {status}

1. Пункт 3.1 договора изложить в следующей редакции: «Поставщик обязуется поставить оборудование не позднее {deadline}».

2. {effective}
"""


def run(*docs, check=CHECK):
    files = [(f"doc{i}.txt", text) for i, text in enumerate(docs)]
    return analyze_files(files, check)


class NewContractAndChangedField(unittest.TestCase):
    def test_new_contract_same_format(self):
        a = run(contract())
        c = a.contract("STN-104")
        self.assertEqual(c.fields["amount"].value, Decimal(2_450_000))
        self.assertEqual(c.fields["delivery_date"].value, date(2026, 3, 25))
        self.assertEqual(c.fields["supplier"].value, "ООО «Альфа Трейд»")
        self.assertFalse(c.needs_review)

    def test_changed_field_in_sample_contract(self):
        raw = (DOCS / "03_contract.txt").read_text(encoding="utf-8")
        changed = raw.replace("840 000 (восемьсот сорок тысяч)", "910 000 (девятьсот десять тысяч)")
        a = run(changed)
        self.assertEqual(a.contract("STN-102").fields["amount"].value, Decimal(910_000))

    def test_digits_and_words_mismatch_needs_review(self):
        a = run(contract(amount="2 540 000"))  # прописью осталось «два миллиона четыреста пятьдесят тысяч»
        f = a.contract("STN-104").fields["amount"]
        self.assertEqual(f.state, FieldState.NEEDS_REVIEW)
        self.assertTrue(any("прописью" in n for n in f.notes))

    def test_amount_without_words_and_without_spaces(self):
        a = run(contract(amount="2450000", words="").replace(" () ", " "))
        self.assertEqual(a.contract("STN-104").fields["amount"].value, Decimal(2_450_000))

    def test_missing_deadline_is_no_data(self):
        text = contract().replace("3.1. Поставщик обязуется поставить оборудование не позднее 25.03.2026.\n", "")
        f = run(text).contract("STN-104").fields["delivery_date"]
        self.assertEqual((f.state, f.value), (FieldState.NO_DATA, None))

    def test_relative_deadline_needs_review(self):
        text = contract().replace("не позднее 25.03.2026", "в течение 30 календарных дней с даты подписания договора")
        f = run(text).contract("STN-104").fields["delivery_date"]
        self.assertEqual(f.state, FieldState.NEEDS_REVIEW)
        self.assertIsNone(f.value)


class AmendmentStatusAndDates(unittest.TestCase):
    def test_signed_and_effective_is_applied(self):
        f = run(contract(), amendment()).contract("STN-104").fields["delivery_date"]
        self.assertEqual((f.state, f.value), (FieldState.AMENDED, date(2026, 4, 15)))

    def test_effective_after_check_date_not_applied(self):
        a = run(contract(), amendment(effective="Настоящее соглашение вступает в силу 01.04.2026."))
        c = a.contract("STN-104")
        self.assertEqual(c.fields["delivery_date"].value, date(2026, 3, 25))
        self.assertEqual([p.kind for p in c.pending], [PendingKind.NOT_YET_EFFECTIVE])

    def test_check_date_before_signing(self):
        a = run(contract(), amendment(), check=date(2026, 3, 3))
        c = a.contract("STN-104")
        self.assertEqual(c.fields["delivery_date"].value, date(2026, 3, 25))
        self.assertEqual([p.kind for p in c.pending], [PendingKind.SIGNED_AFTER_DATE])

    def test_check_date_moves_sample_back_to_original_terms(self):
        files = [(p.name, p.read_bytes()) for p in sorted(DOCS.glob("*.txt"))]
        a = analyze_files(files, date(2026, 2, 28))
        f = a.contract("STN-101").fields["delivery_date"]
        self.assertEqual((f.state, f.value), (FieldState.ORIGINAL, date(2026, 4, 15)))

    def test_draft_title_never_applied(self):
        a = run(contract(), amendment(title_prefix="ПРОЕКТ ", status="на согласовании."))
        c = a.contract("STN-104")
        self.assertEqual(c.fields["delivery_date"].value, date(2026, 3, 25))
        self.assertEqual([p.kind for p in c.pending], [PendingKind.DRAFT])

    def test_partial_signature_needs_review(self):
        a = run(contract(), amendment(status="подписано поставщиком 05.03.2026."))
        c = a.contract("STN-104")
        self.assertEqual(c.fields["delivery_date"].value, date(2026, 3, 25))
        self.assertEqual([p.kind for p in c.pending], [PendingKind.UNCLEAR])
        self.assertTrue(c.needs_review)

    def test_missing_effective_date_needs_review(self):
        a = run(contract(), amendment(effective="Прочие условия не меняются."))
        c = a.contract("STN-104")
        self.assertEqual(c.fields["delivery_date"].value, date(2026, 3, 25))
        self.assertTrue(c.needs_review)

    def test_effective_from_signing(self):
        a = run(contract(), amendment(effective="Настоящее соглашение вступает в силу с момента подписания."))
        self.assertEqual(a.contract("STN-104").fields["delivery_date"].value, date(2026, 4, 15))

    def test_later_effective_amendment_wins_regardless_of_order(self):
        first = amendment(no="1", deadline="10.04.2026", status="подписано обеими сторонами 02.03.2026.",
                          effective="Настоящее соглашение вступает в силу 02.03.2026.")
        second = amendment(no="2", deadline="20.04.2026")
        for docs in ((contract(), first, second), (second, contract(), first)):
            f = run(*docs).contract("STN-104").fields["delivery_date"]
            self.assertEqual(f.value, date(2026, 4, 20))
            self.assertEqual(f.history[-1]["value"], date(2026, 4, 10))

    def test_conflicting_amendments_same_date_need_review(self):
        a1 = amendment(no="1", deadline="10.04.2026")
        a2 = amendment(no="2", deadline="20.04.2026")
        f = run(contract(), a1, a2).contract("STN-104").fields["delivery_date"]
        self.assertEqual(f.state, FieldState.NEEDS_REVIEW)
        self.assertIsNone(f.value)


class Linking(unittest.TestCase):
    def test_amendment_for_unknown_contract_is_reported(self):
        a = run(contract(), amendment(contract_no="STN-999"))
        self.assertEqual(a.contract("STN-104").fields["delivery_date"].value, date(2026, 3, 25))
        self.assertTrue(any("STN-999" in i.message for i in a.issues))

    def test_letter_is_never_applied(self):
        letter = """ПИСЬМО ПОСТАВЩИКА
Дата: 06.03.2026
От: ООО «Альфа Трейд»
Кому: ООО «СеверМет»
Тема: договор STN-104

Просим считать срок поставки перенесенным с 25.03.2026 на 05.04.2026. Изменение подписано нами.
"""
        c = run(contract(), letter).contract("STN-104")
        self.assertEqual(c.fields["delivery_date"].value, date(2026, 3, 25))
        self.assertEqual([(p.kind, p.proposed) for p in c.pending], [(PendingKind.LETTER, date(2026, 4, 5))])

    def test_duplicate_contract_number_reported(self):
        a = run(contract(), contract(amount="3 000 000", words="три миллиона"))
        self.assertTrue(any("дважды" in i.message for i in a.issues))

    def test_cp1251_file(self):
        a = analyze_files([("win.txt", contract().encode("cp1251"))], CHECK)
        self.assertEqual(a.contract("STN-104").fields["amount"].value, Decimal(2_450_000))

    def test_unsigned_contract_flagged(self):
        c = run(contract(status="проект, не подписан.")).contract("STN-104")
        self.assertTrue(c.needs_review)


if __name__ == "__main__":
    unittest.main()
