import hashlib
import json
import os
from collections import Counter
from pathlib import Path
import re

import pymupdf

ROLE_NAMES = {
    'agency','title','client','task','solution','stages','timing','cases',
    'price','contact','requisites','static','ignore'
}
DYNAMIC_ROLES = ROLE_NAMES - {'static','ignore'}
LABEL_HINTS = {
    'client': ('для кого','клиент','заказчик'),
    'task': ('задача','цель','что нужно'),
    'solution': ('предложение','решение','что предлагаем'),
    'stages': ('этапы','план работ','процесс'),
    'timing': ('срок','тайминг'),
    'cases': ('кейс','опыт','портфолио'),
    'price': ('стоимость','цена','бюджет'),
    'contact': ('контакт','связаться','телефон','email','e-mail'),
    'requisites': ('реквизит','инн','кпп','огрн','р/с','расчетный счет'),
}


def _template_dir():
    root = Path(os.getenv('DATA_DIR', './data')) / 'templates'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _rgb_int_to_tuple(value):
    return tuple(((value >> shift) & 255) / 255 for shift in (16, 8, 0))


def _block_text(block):
    lines = []
    for line in block.get('lines', []):
        text = ''.join(span.get('text', '') for span in line.get('spans', [])).strip()
        if text:
            lines.append(text)
    return '\n'.join(lines).strip()


def _slot_from_block(page_index, block_index, block):
    text = _block_text(block)
    spans = [s for line in block.get('lines', []) for s in line.get('spans', []) if s.get('text', '').strip()]
    if spans:
        weighted = [(float(s.get('size', 10)), max(1, len(s.get('text', '')))) for s in spans]
        total = sum(w for _, w in weighted)
        size = sum(v*w for v, w in weighted) / total
        colors = Counter()
        for s in spans:
            colors[int(s.get('color', 0))] += max(1, len(s.get('text', '')))
        color = _rgb_int_to_tuple(colors.most_common(1)[0][0]) if colors else (0, 0, 0)
        font = max(spans, key=lambda s: len(s.get('text', ''))).get('font', '')
    else:
        size, color, font = 10, (0, 0, 0), ''
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    prefix = ''
    if len(lines) > 1 and (len(lines[0]) <= 38 or lines[0].endswith(':')):
        low = lines[0].lower().rstrip(':')
        if any(any(h in low for h in hints) for hints in LABEL_HINTS.values()):
            prefix = lines[0]
    return {
        'id': f'P{page_index+1}B{block_index+1}',
        'page': page_index,
        'bbox': [round(float(v), 2) for v in block.get('bbox', (0,0,0,0))],
        'text': text[:3000],
        'font_size': round(float(size), 2),
        'color': [round(float(c), 4) for c in color],
        'font': font[:120],
        'prefix': prefix[:120],
    }


def extract_proposal(data, ocr, limit=24000):
    try:
        document = pymupdf.open(stream=data, filetype='pdf')
    except (RuntimeError, ValueError):
        raise ValueError('Не удалось открыть PDF. Экспортируйте КП в PDF и отправьте снова.') from None
    with document:
        if document.needs_pass or not 1 <= len(document) <= 10:
            raise ValueError('Нужен PDF без пароля, от 1 до 10 страниц.')
        digest = hashlib.sha256(data).hexdigest()[:20]
        source_path = _template_dir() / f'{digest}.source.pdf'
        source_path.write_bytes(data)
        chunks, slots, spans_all = [], [], []
        for pno, page in enumerate(document):
            text = page.get_text(sort=True).strip()
            if len(text) < 40:
                text = ocr(page.get_pixmap(dpi=130).tobytes('png'))
            blocks = page.get_text('dict').get('blocks', [])
            page_lines = [f'=== PAGE {pno+1} ===']
            for bno, block in enumerate(blocks):
                if block.get('type') != 0:
                    continue
                slot = _slot_from_block(pno, bno, block)
                if not slot['text']:
                    continue
                slots.append(slot)
                page_lines.append(f"[{slot['id']}] {slot['text']}")
                for line in block.get('lines', []):
                    spans_all.extend(s for s in line.get('spans', []) if s.get('text', '').strip())
            if len(page_lines) == 1:
                page_lines.append(text)
            chunks.append('\n'.join(page_lines))
        source = '\n\n'.join(chunks)
        if len(source) > limit:
            raise ValueError('В КП больше 24 000 символов. Пришлите сокращённый образец.')
        if not source.strip():
            raise ValueError('В PDF не найден текст.')
        sizes, accents = Counter(), Counter()
        for span in spans_all:
            weight = max(1, len(span.get('text','')))
            sizes[round(float(span.get('size',10)))] += weight
            rgb = tuple((int(span.get('color',0)) >> shift) & 255 for shift in (16,8,0))
            if max(rgb)-min(rgb) > 35 and min(rgb) < 190:
                accents[int(span.get('color',0))] += weight
        first = document[0].rect
        body = max(9, min(13, sizes.most_common(1)[0][0] if sizes else 10))
        main_font = max(spans_all, key=lambda s: len(s.get('text',''))).get('font','').lower() if spans_all else ''
        style = {
            'color': '#%06X' % (accents.most_common(1)[0][0] if accents else 0x2878B5),
            'body_size': body,
            'heading_size': max(12, min(17, body+3)),
            'title_size': max(20, min(34, max(sizes, default=24))),
            'serif': any(n in main_font for n in ('times','serif','georgia')) and 'sans' not in main_font,
            'page_size': [float(first.width), float(first.height)],
            'margin': max(32, min(70, min((s['bbox'][0] for s in slots), default=44))),
            'layout_version': 1,
            'source_pdf': str(source_path),
            'template_pdf': '',
            'slots': slots,
            'role_map': {},
            'requisites': '',
        }
        return source, style


def parse_role_map(value):
    if isinstance(value, dict):
        raw = value
    else:
        try:
            raw = json.loads(value or '{}')
        except Exception:
            raw = {}
    out = {}
    for key, role in raw.items():
        role = str(role).strip().lower()
        if role in ROLE_NAMES:
            out[str(key)] = role
    return out


def infer_role_map(slots):
    out = {}
    for slot in slots:
        low = slot.get('text','').lower()
        role = 'static'
        if re.search(r'\b\d{10,12}\b', low) and ('инн' in low or 'кпп' in low or 'огрн' in low):
            role = 'requisites'
        else:
            for candidate, hints in LABEL_HINTS.items():
                if any(h in low for h in hints):
                    role = candidate
                    break
        out[slot['id']] = role
    return out


def extract_requisites(profile='', contact=''):
    lines = []
    for line in (profile + '\n' + contact).splitlines():
        low = line.lower()
        if any(token in low for token in ('инн','кпп','огрн','огрнип','р/с','расчетный счет','расчётный счёт','бик','корр.','к/с')):
            lines.append(line.strip())
    return '\n'.join(dict.fromkeys(x for x in lines if x))[:4000]


def _sample_background(page, rect):
    r = pymupdf.Rect(rect)
    probes = []
    for x, y in ((r.x0+1,r.y0+1),(r.x1-2,r.y0+1),(r.x0+1,r.y1-2),(r.x1-2,r.y1-2)):
        clip = pymupdf.Rect(max(page.rect.x0,x-1), max(page.rect.y0,y-1), min(page.rect.x1,x+2), min(page.rect.y1,y+2))
        if clip.is_empty:
            continue
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1,1), clip=clip, alpha=False)
        if pix.width and pix.height:
            px = pix.pixel(max(0,pix.width//2), max(0,pix.height//2))[:3]
            probes.append(px)
    if not probes:
        return (1,1,1)
    med = []
    for i in range(3):
        vals = sorted(p[i] for p in probes)
        med.append(vals[len(vals)//2]/255)
    return tuple(med)


def finalize_candidate(candidate):
    style = candidate.get('custom_style') or {}
    slots = style.get('slots') or []
    role_map = parse_role_map(candidate.get('layout_map'))
    inferred = infer_role_map(slots)
    if not role_map:
        role_map = inferred
    else:
        for sid, role in inferred.items():
            if sid not in role_map and role != 'static':
                role_map[sid] = role
    valid = {s['id'] for s in slots}
    role_map = {k:v for k,v in role_map.items() if k in valid}
    style['role_map'] = role_map
    style['requisites'] = (extract_requisites(candidate.get('profile',''), candidate.get('contact','')) or candidate.get('requisites') or '').strip()
    source = Path(style.get('source_pdf',''))
    if source.exists():
        template = source.with_name(source.name.replace('.source.pdf', '.template.pdf'))
        doc = pymupdf.open(source)
        try:
            by_page = {}
            slot_by_id = {s['id']: s for s in slots}
            for sid, role in role_map.items():
                if role not in DYNAMIC_ROLES:
                    continue
                slot = slot_by_id.get(sid)
                if slot:
                    by_page.setdefault(slot['page'], []).append(slot)
            for pno, page_slots in by_page.items():
                page = doc[pno]
                for slot in page_slots:
                    rect = pymupdf.Rect(slot['bbox'])
                    fill = _sample_background(page, rect)
                    page.add_redact_annot(rect + (-1,-1,1,1), fill=fill)
                page.apply_redactions(images=0, graphics=0, text=0)
            doc.save(template, garbage=4, deflate=True)
        finally:
            doc.close()
        style['template_pdf'] = str(template)
    candidate['custom_style'] = style
    return candidate


def _font_path(bold=False, serif=False):
    root = Path(os.getenv('FONT_DIR', '/usr/share/fonts/truetype/dejavu'))
    if serif:
        name = 'DejaVuSerif-Bold.ttf' if bold else 'DejaVuSerif.ttf'
    else:
        name = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    return str(root / name)


def _fit_text(page, rect, text, size, color, prefix='', serif=False):
    if prefix and not text.lstrip().lower().startswith(prefix.lower()):
        text = prefix + '\n' + text
    text = text.strip()
    if not text:
        return True
    fontfile = _font_path(False, serif)
    for current in [size, size-0.75, size-1.5, size-2.25, size-3, 7.5, 6.5]:
        current = max(6.5, current)
        rc = page.insert_textbox(rect, text, fontname='KPDyn', fontfile=fontfile,
                                 fontsize=current, lineheight=1.15, color=tuple(color), overlay=True)
        if rc >= 0:
            return True
    return False


def _state_values(state, requisites=''):
    draft = state.get('draft') or {}
    price = state.get('price')
    current_requisites = extract_requisites(state.get('profile',''), state.get('contact','')) or requisites
    return {
        'agency': state.get('agency',''),
        'title': draft.get('title',''),
        'client': draft.get('client',''),
        'task': draft.get('task',''),
        'solution': draft.get('solution',''),
        'stages': draft.get('stages',''),
        'timing': draft.get('timing',''),
        'cases': draft.get('cases',''),
        'price': (f'{int(price):,} ₽'.replace(',', ' ') if price else ''),
        'contact': state.get('contact',''),
        'requisites': current_requisites,
    }


def render_layout_pdf(state):
    style = state.get('custom_style') or {}
    template = Path(style.get('template_pdf') or '')
    if not template.exists():
        raise ValueError('Файл фирменного шаблона не найден. Загрузите исходное КП ещё раз.')
    doc = pymupdf.open(template)
    try:
        slots = {s['id']: s for s in style.get('slots', [])}
        role_map = parse_role_map(style.get('role_map'))
        values = _state_values(state, style.get('requisites',''))
        placed = set()
        overflow = []
        serif = bool(style.get('serif'))
        for sid, role in role_map.items():
            if role not in values or not values[role]:
                continue
            slot = slots.get(sid)
            if not slot or slot['page'] >= len(doc):
                continue
            page = doc[slot['page']]
            ok = _fit_text(page, pymupdf.Rect(slot['bbox']), values[role],
                           float(slot.get('font_size') or style.get('body_size',10)),
                           slot.get('color') or (0,0,0), slot.get('prefix',''), serif)
            if ok:
                placed.add(role)
            else:
                overflow.append((role, values[role]))
        mandatory = ['title','client','task','solution','stages','timing','cases','price','contact','requisites']
        missing = [(r, values[r]) for r in mandatory if values.get(r) and r not in placed and all(x[0] != r for x in overflow)]
        overflow.extend(missing)
        if overflow:
            page_size = style.get('page_size') or [595.28, 841.89]
            page = doc.new_page(width=float(page_size[0]), height=float(page_size[1]))
            font = _font_path(False, serif)
            bold = _font_path(True, serif)
            y = 44
            labels = {'title':'Коммерческое предложение','client':'Для кого','task':'Задача','solution':'Предложение','stages':'Этапы работы','timing':'Сроки','cases':'Релевантный опыт','price':'Стоимость','contact':'Контакты и условия','requisites':'Реквизиты'}
            for role, text in overflow:
                if y > page.rect.height - 110:
                    page = doc.new_page(width=float(page_size[0]), height=float(page_size[1])); y = 44
                page.insert_text((44,y), labels.get(role,role), fontname='KPBold', fontfile=bold, fontsize=11, color=(0.15,0.15,0.15)); y += 18
                box = pymupdf.Rect(44,y,page.rect.width-44,min(page.rect.height-44,y+150))
                page.insert_textbox(box, text, fontname='KPBody', fontfile=font, fontsize=9.5, lineheight=1.25, color=(0.1,0.1,0.1))
                y += max(46, min(160, 20 + len(text)/4))
        return doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()
