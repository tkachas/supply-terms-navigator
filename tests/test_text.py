import unittest

from terms_navigator.numwords import words_to_int
from terms_navigator.text import split_paragraphs, split_sentences


class NumWords(unittest.TestCase):
    def test_values(self):
        cases = {
            "один миллион двести тысяч": 1_200_000,
            "восемьсот сорок тысяч": 840_000,
            "один миллион триста восемьдесят тысяч": 1_380_000,
            "двадцать одна тысяча пятьсот": 21_500,
            "тысяча": 1_000,
        }
        for text, expected in cases.items():
            self.assertEqual(words_to_int(text), expected, text)

    def test_not_a_number(self):
        self.assertIsNone(words_to_int("в т.ч. НДС"))
        self.assertIsNone(words_to_int(""))


class Sentences(unittest.TestCase):
    def _split(self, text):
        return [text[s:e] for s, e in split_sentences(text)]

    def test_dates_and_clause_numbers_do_not_split(self):
        text = "3.1. Поставщик обязуется поставить оборудование не позднее 15.04.2026. Дата приемки по п. 3.2 иная."
        self.assertEqual(self._split(text), [
            "3.1. Поставщик обязуется поставить оборудование не позднее 15.04.2026.",
            "Дата приемки по п. 3.2 иная.",
        ])

    def test_quotes_are_kept_whole(self):
        text = "1. Изложить в редакции: «Сумма — 10 рублей. Указанная сумма — итоговая». Прочее без изменений."
        parts = self._split(text)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].endswith("итоговая»."))


class Paragraphs(unittest.TestCase):
    def test_header_lines_and_clauses(self):
        text = "ДОГОВОР № X-1\nДата: 01.01.2026\nСтатус: подписан\n\n1.1. Первый пункт\nс переносом строки.\n1.2. Второй.\n"
        paras = split_paragraphs(text)
        self.assertEqual([p.label for p in paras], [None, "Дата", "Статус", "п. 1.1", "п. 1.2"])
        self.assertEqual(paras[3].text, "1.1. Первый пункт с переносом строки.")


if __name__ == "__main__":
    unittest.main()
