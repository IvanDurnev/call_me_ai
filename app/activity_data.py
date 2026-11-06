"""Original, data-only Russian decks and board categories for Show & Explain."""
import json
from pathlib import Path

CATEGORIES = [
    dict(key='drawA', method='draw', label='Рисовать', icon='✎', color='amber', hex='#d8a136'),
    dict(key='explainA', method='explain', label='Объяснять', icon='☏', color='blue', hex='#548fcd'),
    dict(key='mimeA', method='mime', label='Показывать', icon='✋', color='green', hex='#6a9d61'),
    dict(key='drawB', method='draw', label='Рисовать', icon='✎', color='violet', hex='#9a79bc'),
    dict(key='explainB', method='explain', label='Объяснять', icon='☏', color='coral', hex='#d47f72'),
    dict(key='mimeB', method='mime', label='Показывать', icon='✋', color='teal', hex='#50a49b'),
]
CATEGORY_BY_KEY = {category['key']: category for category in CATEGORIES}


def validate_cards(cards, minimum=30):
    seen_ids, seen_text = set(), set()
    for card in cards:
        if (not isinstance(card, dict) or card.get('difficulty') not in (3,4,5)
                or not isinstance(card.get('id'), str) or not card.get('locale')
                or set(card.get('tasks', {})) != set(CATEGORY_BY_KEY)):
            raise ValueError('Неполная или некорректная карточка')
        if card['id'] in seen_ids: raise ValueError('Повтор идентификатора карточки')
        seen_ids.add(card['id'])
        for task in card['tasks'].values():
            if (not isinstance(task.get('id'), str) or not isinstance(task.get('text'), str)
                    or not 1<=len(task['text'].split())<=2 or task.get('isOpenRound') is not False):
                raise ValueError('Неполное задание')
            if task['id'] in seen_ids: raise ValueError('Повтор идентификатора задания')
            seen_ids.add(task['id'])
            text = task['text'].casefold().replace('ё','е').strip()
            if text in seen_text: raise ValueError('Повтор текста задания')
            seen_text.add(text)
    for difficulty in (3,4,5):
        if sum(card['difficulty']==difficulty for card in cards) < minimum:
            raise ValueError('Недостаточно карточек')


PACK = json.loads((Path(__file__).parent/'data'/'activity_ru.json').read_text())
CARDS = PACK['cards']
validate_cards(CARDS)
CARD_BY_ID = {card['id']: card for card in CARDS}
DECK_IDS = {str(d): [card['id'] for card in CARDS if card['difficulty']==d] for d in (3,4,5)}
