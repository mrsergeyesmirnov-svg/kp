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

    def test_dialogue_and_templates(self):
        db = bot.database(":memory:")
        def send(text):
            bot.handle({"from": {"id": 1}, "chat": {"id": 1, "type": "private"}, "text": text}, db, {1})
        with patch.object(bot, "tell"), patch.object(bot, "send_pdf") as pdf:
            send("/profile")
            send("Студия\nУборка квартир")
            state = bot.load(db, 1)
            self.assertEqual(state["agency"], "Студия")
            self.assertNotIn("brief", state)
            self.assertEqual(state["pending"], "/contact")
            send("📞 Контакты")
            send("hello@example.com")
            self.assertEqual(bot.load(db, 1)["contact"], "hello@example.com")
            send("/profile\nНовое название\nУслуги")
            self.assertEqual(bot.load(db, 1)["agency"], "Новое название")
            send("/templates")
            self.assertEqual(pdf.call_count, 3)
            send("Деловой")
            self.assertEqual(bot.load(db, 1)["template"], "business")
            send("Мой дизайн")
            send("Белый фон, зелёные заголовки")
            send("/done")
            self.assertIn("зелёные", bot.load(db, 1)["design_request"]["description"])
            self.assertNotIn("brief", bot.load(db, 1))
            send("/profile")
            send("/new")
            send("Нужно убрать офис")
            self.assertEqual(bot.load(db, 1)["brief"], "Нужно убрать офис")
        db.close()

    def test_compact_navigation_preserves_brief(self):
        db = bot.database(":memory:")
        def send(text):
            bot.handle({"from": {"id": 1}, "chat": {"id": 1, "type": "private"}, "text": text}, db, {1})
        with patch.object(bot, "tg") as telegram:
            send("/start")
            keyboard = telegram.call_args.args[1]["reply_markup"]["keyboard"]
            self.assertEqual(sum(map(len, keyboard)), 3)
            send("➕ Создать КП")
            self.assertEqual(bot.load(db, 1)["pending"], "/profile")
            send("Агентство\nСайты")
            send("hello@example.com")
            send("Клиенту нужен лендинг")
            send("⚙️ Настройки")
            send("🎨 Оформление")
            send("Деловой")
            send("🏠 Главное меню")
            send("➕ Создать КП")
            self.assertEqual(bot.load(db, 1)["brief"], "Клиенту нужен лендинг")
            self.assertIn("Да, новое КП", str(telegram.call_args.args[1]))
            send("📁 Текущее КП")
            self.assertIn("✨ Сформировать", str(telegram.call_args.args[1]))
            send("Да, новое КП")
            self.assertNotIn("brief", bot.load(db, 1))
            self.assertIn("profile", bot.load(db, 1))
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
