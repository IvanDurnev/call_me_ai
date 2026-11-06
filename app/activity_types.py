"""Serializable domain contracts; rules do not depend on the UI or Flask."""
from typing import Literal, TypedDict

CategoryKey = Literal['drawA','explainA','mimeA','drawB','explainB','mimeB']
Phase = Literal['lobby','handoff','choose_difficulty','choose_task','prepare','performing','board','finished']

class Task(TypedDict):
    id: str
    text: str
    isOpenRound: bool

class Card(TypedDict):
    id: str
    locale: str
    difficulty: int
    tasks: dict[CategoryKey,Task]

class Team(TypedDict):
    name: str
    members: list[int]
    first_presenter: int
    size: int

class GameSettings(TypedDict):
    mode: Literal['teams']
    teams: list[Team]
    sizes: list[int]
    starting_side: int
    precision: str

class TurnResult(TypedDict):
    outcome: Literal['success','failure','violation']
    guesser: int | None

class MoveTransaction(TypedDict):
    turn_id: str
    before: list[int]
    after: list[int]
    advances: dict[int,int]
    raw_destinations: dict[int,int]
    bumped: list[int]
    winners: list[int]

class Player(TypedDict):
    name: str

class Participant(TypedDict):
    """A scoring side: one team connected through one shared phone."""
    name: str
    members: list[int]
    color: str
    symbol: str

class Cell(TypedDict):
    index: int
    kind: Literal['start', 'task', 'finish']
    category: CategoryKey | None

class Deck(TypedDict):
    draw: list[str]
    discard: list[str]

class Turn(TypedDict):
    id: str
    side: int
    presenter: int
    card: str | None
    category: CategoryKey | None
    difficulty: int | None
    open: bool
    draft: TurnResult | None
    expired: bool
    gave_up: bool

class GameEvent(TypedDict):
    turn_id: str
    number: int
    presenter: int
    side: int
    difficulty: int
    category: CategoryKey
    open: bool
    task: str
    outcome: Literal['success', 'failure', 'violation']
    guesser: int | None
    transaction: MoveTransaction

class GameState(TypedDict):
    game: Literal['activity']
    schemaVersion: int
    ruleset: str
    locale: str
    phase: Phase
    players: list[Player]
    revision: int
    match: int
    settings: GameSettings | None
    positions: list[int]
    cursor: int
    offsets: list[int]
    decks: dict[str, Deck]
    turn_number: int
    active: Turn | None
    deadline: float | None
    memorize_deadline: float | None
    drawing: dict
    history: list[GameEvent]
    winners: list[int]
    last_transaction: MoveTransaction | None
    checkpoint: dict | None
    undo: dict | None
