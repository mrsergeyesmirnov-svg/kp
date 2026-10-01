import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import bot


class BotTest(unittest.TestCase):
    def test_pilot_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"
            db = bot.database(path)
            def send(text, uid=1):
                return bot.handle({"from": {"id": uid}, "chat": {"id": uid, "type": "private"}, "text": text}, db, {1, 2})
            sample = dict.fromkeys(bot.FIELDS, "")
            sample.update(title="Сайт для кофейни", client="Кофейня «Утро»", task="Показать меню",
                          solution="Лендинг с меню и формой заявки", stages="Бриф → прототип → дизайн → запуск",
                          timing="Уточнить после согласования", email="Добрый день! Отправляю предложение.")
            with patch.object(bot, "tell"), patch.object(bot, "ai", return_value=sample):
                send("/profile Студия Пример\nСайты")
                send("/contact hello@example.com. Предоплата 50%.")
                send("Нужен сайт для кофейни")
                send("/generate")
                self.assertNotIn("draft", bot.load(db, 2))
                with self.assertRaises(ValueError):
                    send("/pdf")
                for bad in ("-1", "1.5", "abc", "0"):
                    with self.assertRaises(ValueError):
                        send("/price " + bad)
                send("/price 50000")
                send("/approve")
                self.assertTrue(bot.load(db, 1)["approved"])
                pdf = bot.render_pdf(bot.load(db, 1))
                self.assertTrue(pdf.startswith(b"%PDF"))
                with patch.object(bot, "tg") as telegram:
                    send("/pdf")
                    self.assertEqual(telegram.call_args.args[0], "sendDocument")
                send("/edit Сделай короче")
                self.assertFalse(bot.load(db, 1)["approved"])
                send("/approve")
                send("Дополнение: нужен каталог")
                self.assertNotIn("draft", bot.load(db, 1))
                db.close()
                db = bot.database(path)
                self.assertIn("каталог", bot.load(db, 1)["brief"])
                with patch.dict("os.environ", {"DAILY_LIMIT": "2"}):
                    with self.assertRaises(ValueError):
                        bot.consume(db, 1, bot.load(db, 1))
                send("/profile Чужие данные", uid=3)
                self.assertEqual(bot.load(db, 3), {})
                send("/delete_me")
                self.assertEqual(bot.load(db, 1), {})
                db.close()

    def test_untrusted_model_output(self):
        with self.assertRaises(ValueError):
            bot.validate({"title": "неполный ответ"})
        draft = dict.fromkeys(bot.FIELDS, "Тест <script>& текст")
        self.assertEqual(bot.validate(draft), draft)
        draft["solution"] = []
        with self.assertRaises(ValueError):
            bot.validate(draft)


if __name__ == "__main__":
    unittest.main()
