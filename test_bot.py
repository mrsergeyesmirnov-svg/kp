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

    def test_screenshot_regression_and_complete_button_flow(self):
        import json
        db = bot.database(":memory:")
        draft = dict.fromkeys(bot.FIELDS, "")
        draft.update(title="Уборка квартиры", solution="Уборка согласованных помещений", task="Подготовить квартиру")
        def send(text):
            bot.handle({"from": {"id": 1}, "chat": {"id": 1, "type": "private"}, "text": text}, db, {1})
        env = {"AI_BASE_URL": "https://ai.api.cloud.yandex.net/v1", "AI_API_KEY": "test-only",
               "AI_MODEL": "gpt://test-folder/yandexgpt/latest"}
        with patch.dict("os.environ", env), patch.object(bot, "tell") as replies, patch.object(bot, "send_pdf") as pdf:
            send("/profile")
            send("Исполнитель\nУборка квартир, 5000 рублей")
            send("hello@example.com")
            send("/generate")
            self.assertIn("уже сохранён", replies.call_args.args[1])
            self.assertNotIn("pending", bot.load(db, 1))
            send("Нужно убрать двухкомнатную квартиру к пятнице")
            response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft)}}]}
            with patch.object(bot, "request", return_value=response) as request:
                send("✨ Сформировать")
                headers = request.call_args.args[2]
                self.assertEqual(headers["OpenAI-Project"], "test-folder")
                self.assertEqual(headers["Authorization"], "Api-Key test-only")
                self.assertEqual(request.call_args.args[1]["response_format"]["type"], "json_schema")
            send("💰 Цена")
            send("5 000 ₽")
            self.assertEqual(bot.load(db, 1)["price"], 5000)
            send("✅ Утвердить")
            send("📄 PDF")
            self.assertTrue(pdf.call_args.args[1].startswith(b"%PDF"))
            send("✏️ Правки")
            with patch.object(bot, "request", side_effect=bot.urllib.error.HTTPError("https://example.com", 403, "Forbidden", {}, None)):
                with self.assertRaisesRegex(ValueError, "нет доступа"):
                    send("Сократи текст")
            self.assertEqual(bot.load(db, 1)["draft"], draft)
        db.close()

    def test_ocr_review_correction_and_acceptance(self):
        db = bot.database(":memory:")
        def send(text="", photo=False):
            message = {"from": {"id": 1}, "chat": {"id": 1, "type": "private"}, "text": text}
            if photo:
                message["photo"] = [{"file_id": "test"}]
            bot.handle(message, db, {1})
        bot.save(db, 1, {"profile": "Агентство", "brief": "Исходная задача"})
        with patch.object(bot, "tell"), patch.object(bot, "media_text", side_effect=["Нужен сайт", "Бюджет 40 000"]):
            send(photo=True)
            send(photo=True)
            self.assertEqual(bot.load(db, 1)["brief"], "Исходная задача")
            self.assertIn("Нужен сайт", bot.load(db, 1)["ocr_text"])
            with patch.object(bot, "ai") as ai:
                send("/generate")
                ai.assert_not_called()
            send("✏️ Исправить текст")
            send("Нужен сайт, бюджет 50 000")
            send("✅ Использовать текст")
            state = bot.load(db, 1)
            self.assertEqual(state["brief"], "Исходная задача\n\nНужен сайт, бюджет 50 000")
            self.assertNotIn("ocr_text", state)
            send("✅ Использовать текст")
            self.assertEqual(bot.load(db, 1)["brief"], state["brief"])
        db.close()

    def test_yandex_ocr_contract(self):
        import io
        from PIL import Image
        output = io.BytesIO()
        Image.new("RGB", (20, 20), "white").save(output, format="PNG")
        env = {"AI_BASE_URL": "https://ai.api.cloud.yandex.net/v1", "AI_API_KEY": "test-key",
               "AI_MODEL": "gpt://test-folder/yandexgpt/latest", "OCR_API_KEY": "", "OCR_FOLDER_ID": ""}
        with patch.dict("os.environ", env), patch.object(bot, "request", return_value={"result": {"textAnnotation": {"fullText": "Пример"}}}) as request:
            self.assertEqual(bot.yandex_ocr(output.getvalue()), "Пример")
            self.assertEqual(request.call_args.args[1]["mimeType"], "image/png")
            self.assertEqual(request.call_args.args[2]["x-folder-id"], "test-folder")
            with self.assertRaises(ValueError):
                bot.yandex_ocr(b"not an image")
            request.return_value = {"textAnnotation": {"fullText": ""}}
            with self.assertRaisesRegex(ValueError, "не найден"):
                bot.yandex_ocr(output.getvalue())

    def test_import_approval_and_reuse(self):
        db = bot.database(":memory:")
        def send(text="", document=False):
            msg = {"from": {"id": 1}, "chat": {"id": 1, "type": "private"}, "text": text}
            if document:
                msg["document"] = {"file_id": "sample", "mime_type": "application/pdf"}
            bot.handle(msg, db, {1})
        draft = dict.fromkeys(bot.FIELDS, "")
        draft.update(title="Новое КП", solution="Новая услуга")
        original = bot.render_pdf({"agency": "Old", "contact": "old@example.com", "draft": draft, "price": 100, "color": "#A04427"})
        text, style = bot.read_proposal(original, lambda data: self.fail("Text PDF should not need OCR"))
        self.assertIn("Новое КП", text)
        self.assertEqual(style["color"], "#A04427")
        bot.save(db, 1, {"profile": "Old", "agency": "Old", "brief": "Existing task"})
        seller = {"agency": "Studio", "profile": "Studio\nServices", "contact": "new@example.com", "questions": ""}
        with patch.object(bot, "tell"), patch.object(bot, "send_pdf"), patch.object(bot, "download_document", return_value=original), patch.object(bot, "extract_seller", return_value=seller):
            send(document=True)
            self.assertEqual(bot.load(db, 1)["profile"], "Old")
            send("Отменить импорт")
            self.assertEqual(bot.load(db, 1)["profile"], "Old")
            send(document=True)
            send("✏️ Данные продавца")
            send("Студия\nРазработка сайтов\nКОНТАКТЫ: demo@example.com")
            send("✅ Сохранить шаблон")
            state = bot.load(db, 1)
            self.assertEqual(state["contact"], "demo@example.com")
            self.assertEqual(state["brief"], "Existing task")
            self.assertEqual(state["template"], "custom")
            self.assertNotIn("import_candidate", state)
            send("/new")
            self.assertEqual(bot.load(db, 1)["custom_style"], style)
            with patch.object(bot, "media_text", return_value="Диалог нового клиента"):
                bot.handle({"from": {"id": 1}, "chat": {"id": 1, "type": "private"}, "photo": [{"file_id": "screenshot"}]}, db, {1})
            send("✅ Использовать текст")
            with patch.object(bot, "ai", return_value=draft) as ai:
                send("✨ Сформировать")
                self.assertIn("Разработка сайтов", ai.call_args.args[0][1]["content"])
            send("/price 40000")
            send("/approve")
            result = bot.render_pdf(bot.load(db, 1))
            with bot.pymupdf.open(stream=result, filetype="pdf") as pdf:
                content = "".join(page.get_text() for page in pdf)
                self.assertIn("demo@example.com", content)
                self.assertNotIn("old@example.com", content)
            self.assertEqual(bot.load(db, 1)["custom_style"], style)
            self.assertEqual(bot.load(db, 2), {})
        db.close()

    def test_bad_pdf_and_scan_ocr(self):
        with self.assertRaisesRegex(ValueError, "открыть"):
            bot.read_proposal(b"not pdf", lambda data: "")
        pdf = bot.pymupdf.open()
        pdf.new_page()
        with patch.object(bot, "yandex_ocr", return_value="Seller scan text") as ocr:
            text, style = bot.read_proposal(pdf.tobytes(), ocr)
            self.assertEqual(text, "Seller scan text")
            ocr.assert_called_once()
            self.assertEqual(style["body_size"], 10)
        for _ in range(10):
            pdf.new_page()
        with self.assertRaisesRegex(ValueError, "10 страниц"):
            bot.read_proposal(pdf.tobytes(), lambda data: "")
        pdf.close()

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
