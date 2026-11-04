"""Приёмочные проверки на учебном наборе из задания: четыре вопроса закупщика,
источники для каждого значения и независимость от имён файлов / порядка загрузки."""
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from terms_navigator import queries
from terms_navigator.model import FieldState, PendingKind
from terms_navigator.service import analyze_files

DOCS = Path(__file__).resolve().parent.parent / "documents"
CHECK = date(2026, 3, 10)


def sample_files():
    return [(p.name, p.read_bytes()) for p in sorted(DOCS.glob("*.txt"))]


class FourQuestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = analyze_files(sample_files(), CHECK)

    def test_three_contracts_in_table(self):
        self.assertEqual([c.contract_no for c in self.a.contracts], ["STN-101", "STN-102", "STN-103"])
        suppliers = [c.fields["supplier"].value for c in self.a.contracts]
        self.assertEqual(suppliers, ["ООО «Вектор Прибор»", "ООО «Контур Электро»", "ООО «ПромСнаб Сервис»"])

    def test_q1_stn101_delivery_changed_by_signed_amendment(self):
        f = self.a.contract("STN-101").fields["delivery_date"]
        self.assertEqual(f.state, FieldState.AMENDED)
        self.assertEqual(f.value, date(2026, 4, 30))
        self.assertEqual(f.evidence.file, "02_amendment_signed.txt")
        self.assertIn("30.04.2026", f.evidence.quote)
        # исходное значение сохранено в истории со ссылкой на договор
        self.assertEqual(f.history[0]["value"], date(2026, 4, 15))
        self.assertEqual(f.history[0]["evidence"].file, "01_contract.txt")
        # проект ДС № 2 не применён, но показан как несогласованное изменение
        pend = [p for p in self.a.contract("STN-101").pending if p.field == "delivery_date"]
        self.assertEqual([(p.proposed, p.kind) for p in pend], [(date(2026, 5, 10), PendingKind.DRAFT)])

    def test_q1_amount_also_amended(self):
        f = self.a.contract("STN-101").fields["amount"]
        self.assertEqual((f.state, f.value), (FieldState.AMENDED, Decimal(1_380_000)))

    def test_q2_stn102_amount_ignores_advance(self):
        f = self.a.contract("STN-102").fields["amount"]
        self.assertEqual((f.state, f.value), (FieldState.ORIGINAL, Decimal(840_000)))
        self.assertEqual(f.evidence.label, "п. 2.1")
        self.assertIn(Decimal(252_000), [m.value for m in f.other_mentions])

    def test_q2_stn102_delivery_is_not_payment_date(self):
        f = self.a.contract("STN-102").fields["delivery_date"]
        self.assertEqual(f.value, date(2026, 3, 20))

    def test_q3_stn103_amount_no_data_not_budget_limit(self):
        f = self.a.contract("STN-103").fields["amount"]
        self.assertEqual(f.state, FieldState.NO_DATA)
        self.assertIsNone(f.value)
        self.assertIsNotNone(f.evidence, "для «нет данных» показываем, что сказано в договоре")
        self.assertIn("приложении", f.evidence.quote)
        self.assertIn(Decimal(600_000), [m.value for m in f.other_mentions])
        ans = queries.amount(self.a, "STN-103")
        self.assertEqual(ans.status, "no_data")
        self.assertNotIn("600 000", ans.headline)

    def test_q4_deliveries_before_april_and_unagreed_changes(self):
        ans = queries.deliveries_before(self.a, date(2026, 4, 1))
        self.assertIn("STN-102", ans.headline)
        self.assertNotIn("STN-101", ans.headline)
        self.assertNotIn("STN-103", ans.headline)
        letter = [l for l in ans.lines if "27.03.2026" in l.text]
        self.assertEqual(len(letter), 1)
        self.assertEqual(letter[0].evidence[0].file, "05_supplier_email.txt")
        pend = self.a.contract("STN-102").pending
        self.assertEqual([(p.proposed, p.kind) for p in pend], [(date(2026, 3, 27), PendingKind.LETTER)])

    def test_every_value_has_exact_quote(self):
        """Инвариант: цитата — точная подстрока абзаца, значение лежит внутри цитаты."""
        for c in self.a.contracts:
            for fv in c.fields.values():
                if fv.value is None:
                    continue
                ev = fv.evidence
                self.assertIsNotNone(ev, f"{c.contract_no}.{fv.field} без основания")
                para = self.a.doc(ev.doc_id).paragraphs[ev.para]
                self.assertEqual(para.text[ev.start:ev.end], ev.quote)
                if ev.value_start is not None:
                    self.assertTrue(ev.start <= ev.value_start < ev.value_end <= ev.end)


class InputOrderAndNames(unittest.TestCase):
    def _snapshot(self, files):
        a = analyze_files(files, CHECK)
        return {c.contract_no: {k: (f.state, f.value) for k, f in c.fields.items()} for c in a.contracts}

    def test_reversed_upload_order(self):
        files = sample_files()
        self.assertEqual(self._snapshot(files), self._snapshot(list(reversed(files))))

    def test_file_names_do_not_matter(self):
        files = sample_files()
        # имена «перепутаны»: проект назван как подписанное ДС и наоборот
        renamed = [(f"{i:02d}_signed_final.txt" if "draft" in n else f"zz_{i}.txt", raw)
                   for i, (n, raw) in enumerate(files)]
        self.assertEqual(self._snapshot(files), self._snapshot(renamed))


if __name__ == "__main__":
    unittest.main()
