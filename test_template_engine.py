import json
import os
from pathlib import Path
import tempfile
import unittest

import pymupdf

import template_engine


class TemplateEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get('DATA_DIR')
        os.environ['DATA_DIR'] = self.tmp.name

    def tearDown(self):
        if self.old_data_dir is None:
            os.environ.pop('DATA_DIR', None)
        else:
            os.environ['DATA_DIR'] = self.old_data_dir
        self.tmp.cleanup()

    def sample_pdf(self):
        font = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        doc = pymupdf.open()
        page = doc.new_page()
        page.draw_rect((20, 20, 575, 820), color=(0.1, 0.4, 0.7), width=2)
        page.draw_line((40, 650), (550, 650), color=(0.1, 0.4, 0.7), width=1)
        page.insert_text((40, 55), 'СТУДИЯ АЛЬФА', fontname='src1', fontfile=font, fontsize=18)
        page.insert_text((40, 130), 'Коммерческое предложение для Старого Клиента', fontname='src2', fontfile=font, fontsize=16)
        page.insert_textbox((40, 170, 550, 250), 'Задача\nСделать старый лендинг и рекламу.', fontname='src3', fontfile=font, fontsize=11)
        page.insert_textbox((40, 280, 550, 370), 'Решение\nРазработать старый сайт.', fontname='src4', fontfile=font, fontsize=11)
        page.insert_textbox((40, 680, 550, 760), 'Реквизиты\nИНН 7812345678\nОГРНИП 123456789012345', fontname='src5', fontfile=font, fontsize=10)
        raw = doc.tobytes()
        doc.close()
        return raw

    def test_import_preserves_layout_and_replaces_dynamic_text(self):
        source, style = template_engine.extract_proposal(self.sample_pdf(), lambda _: '')
        role_map = {}
        for slot in style['slots']:
            low = slot['text'].lower()
            if 'студия' in low:
                role_map[slot['id']] = 'agency'
            elif 'старого клиента' in low:
                role_map[slot['id']] = 'title'
            elif 'задача' in low:
                role_map[slot['id']] = 'task'
            elif 'решение' in low:
                role_map[slot['id']] = 'solution'
            elif 'инн' in low:
                role_map[slot['id']] = 'requisites'
        candidate = {
            'agency': 'СТУДИЯ АЛЬФА',
            'profile': 'СТУДИЯ АЛЬФА\nИНН 7812345678\nОГРНИП 123456789012345',
            'contact': 'hello@example.com',
            'requisites': 'ИНН 7812345678\nОГРНИП 123456789012345',
            'layout_map': json.dumps(role_map, ensure_ascii=False),
            'custom_style': style,
        }
        template_engine.finalize_candidate(candidate)
        custom = candidate['custom_style']
        self.assertTrue(Path(custom['template_pdf']).exists())
        self.assertEqual(custom['requisites'].splitlines()[0], 'ИНН 7812345678')

        state = {
            'template': 'custom',
            'custom_style': custom,
            'agency': 'СТУДИЯ АЛЬФА',
            'profile': candidate['profile'],
            'contact': 'hello@example.com',
            'price': 50000,
            'draft': {
                'title': 'КП для НОВОГО клиента',
                'client': 'Новый клиент',
                'task': 'Сделать новый сайт',
                'solution': 'Новый сайт и CRM',
                'stages': '1. Бриф\n2. Дизайн',
                'timing': '14 дней',
                'cases': 'Кейс Альфа',
            },
        }
        result = template_engine.render_layout_pdf(state)
        out = pymupdf.open(stream=result, filetype='pdf')
        text = '\n'.join(page.get_text() for page in out)
        self.assertEqual(len(out), 2)
        out.close()
        self.assertNotIn('Старого Клиента', text)
        self.assertNotIn('Сделать старый лендинг', text)
        self.assertIn('КП для НОВОГО клиента', text)
        self.assertIn('Сделать новый сайт', text)
        self.assertIn('Новый сайт и CRM', text)
        self.assertIn('ИНН 7812345678', text)
        self.assertIn('50 000 ₽', text)
        self.assertIn('hello@example.com', text)

    def test_requisites_are_structured_from_manual_profile(self):
        profile = 'ИП Иванов\nИНН 123456789012\nОГРНИП 123456789012345\nУслуги: сайты'
        contact = 'mail@example.com\nБИК 044525225'
        req = template_engine.extract_requisites(profile, contact)
        self.assertIn('ИНН 123456789012', req)
        self.assertIn('ОГРНИП 123456789012345', req)
        self.assertIn('БИК 044525225', req)
        self.assertNotIn('Услуги: сайты', req)

    def test_multi_slot_sections_are_split_not_duplicated(self):
        slots = [
            {'text': 'old one', 'bbox': [0, 0, 200, 40]},
            {'text': 'old two', 'bbox': [0, 50, 200, 90]},
        ]
        chunks = template_engine._split_for_slots('alpha beta gamma delta epsilon zeta eta theta', slots)
        self.assertEqual(len(chunks), 2)
        self.assertNotEqual(chunks[0], chunks[1])
        self.assertEqual(' '.join(chunks).split(), 'alpha beta gamma delta epsilon zeta eta theta'.split())

    def test_bad_layout_map_falls_back_safely(self):
        self.assertEqual(template_engine.parse_role_map('not json'), {})
        self.assertEqual(template_engine.parse_role_map('{"P1B1":"HACK"}'), {})


if __name__ == '__main__':
    unittest.main()
