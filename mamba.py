# pylint: disable=missing-class-docstring,missing-function-docstring,missing-module-docstring,redefined-outer-name,consider-using-in,invalid-name,fixme

import contextlib
import os
import select
import sys
import termios
import random
import threading
from collections import deque
from itertools import count
from time import sleep, monotonic

ESC = "\x1b"

KEY_UP = ESC + "[A"
KEY_DOWN = ESC + "[B"
KEY_LEFT = ESC + "[D"
KEY_RIGHT = ESC + "[C"

SPIDER = "\N{SPIDER}\N{VARIATION SELECTOR-16}"
SNAKE_HEAD = "\N{FROG FACE}"
SNAKE_BODY_1 = "\N{LARGE GREEN SQUARE}"
SNAKE_BODY_2 = "\N{LARGE RED SQUARE}"
SNAKE_BODY_3 = "\N{LARGE YELLOW SQUARE}"
HOLE = "\N{HOLE}\N{VARIATION SELECTOR-16}"
WALL = "\N{BRICK}"
THREAD = "\N{SPIDER WEB}\N{VARIATION SELECTOR-16}"
WEB = "\N{MILKY WAY}"

LEFT_TURN = {
    0-1j: -1+0j,
    1+0j: 0-1j,
    0+1j: 1+0j,
    -1+0j: 0+1j,
}
RIGHT_TURN = {v: k for k, v in LEFT_TURN.items()}


class GameOver(Exception):
    pass


class Win(Exception):
    pass


class SingleCharInput:
    def __init__(self):
        self._old = None

    def __enter__(self):
        self._old = termios.tcgetattr(sys.stdin)
        self._setup()
        return self

    def _setup(self):
        cbreak = termios.tcgetattr(sys.stdin)
        cbreak[3] &= ~(termios.ECHO | termios.ICANON)
        cbreak[6][termios.VMIN] = 1
        cbreak[6][termios.VTIME] = 0
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, cbreak)
        print("\x1b[?25l", flush=True, end="")

    def _teardown(self):
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old)
        print("\x1b[?25h", flush=True, end="")

    @contextlib.contextmanager
    def pause(self):
        if not self._old:
            raise ValueError()
        self._teardown()
        try:
            yield
        finally:
            self._setup()

    def __exit__(self, exc_type, exc_value, traceback):
        self._teardown()
        self._old = None


def get_char():
    ch = os.read(sys.stdin.fileno(), 1).decode("utf8")  # may block
    if ch == ESC:  # ESC might be ESC itself, or escape sequence
        #ch = "ESC-"
        ready = select.select([sys.stdin.fileno()], [], [], 0.01)
        while ready[0]:
            c = os.read(sys.stdin.fileno(), 1).decode("utf8")
            ch += c
            ready = select.select([sys.stdin.fileno()], [], [], 0.01)
    return ch


def get_terminal_size():
    tsize = os.get_terminal_size()
    return tsize.columns, tsize.lines


def esc(seq):
    print("\x1b"+seq, end="", flush=True)


def beep():
    print("\x07", end="", flush=True)


def clear():
    esc("[2J")


def print_at(pos, text):
    esc(f"[{int(pos.imag)+1};{(int(pos.real)*2)+1}H{text}")  # terminal coords are 1-based


# def print_at(pos, text):
#     print("at", pos.imag+1, (pos.real)+1, text)


class Snake:
    def __init__(self, starting_position, *, variant=SNAKE_BODY_1, lookahead=0, speed=2, prey_weight=5):
        self.game = None
        self.variant = variant
        self.lookahead = lookahead
        self.speed = speed
        self.prey_weight = prey_weight
        self.max_length = 10
        self.body = deque([starting_position])

    def tick(self, *, go_into_hole=False):
        if go_into_hole:
            if self.body:
                print_at(self.body[0], HOLE)
                old_tail_pos = self.body.pop()
                #self.game.reset_at(old_tail_pos)  # TODO test
                print_at(old_tail_pos, " ")
            else:
                raise Win
            return
        new_position = self.choose_position()
        if new_position is None:
            # Nowhere to go -> reverse direction
            print_at(self.body[0], self.variant)
            print_at(self.body[-1], SNAKE_HEAD)
            self.body.reverse()
            return
        if len(self.body) >= self.max_length:
            old_tail_pos = self.body.pop()
            print_at(old_tail_pos, " ")
        print_at(self.body[0], self.variant)
        self.body.appendleft(new_position)
        print_at(self.body[0], SNAKE_HEAD)

    def can_move_to(self, position):
        if not (
            self.game.in_bounds(position, variant="snake")
            and self.game.tile_category(position) in ("board", "thread")
        ):
            return False
        for entity in self.game.entities_on_tile(position):
            if not isinstance(entity, Spider):
                return False
        return True

    def choose_position(self):
        if len(self.body) < 2:
            head = self.body[0]
            choices = [
                (head + 1, 1),
                (head - 1, 1),
                (head + 0+1j, 1),
                (head + 0-1j, 1),
            ]
        else:
            head, neck = self.body[0], self.body[1]
            straight_ahead = head - neck
            turn_left = LEFT_TURN[straight_ahead]
            turn_right = RIGHT_TURN[straight_ahead]
            choices = []
            for possibility in (straight_ahead, turn_left, turn_right):
                if not self.can_move_to(head + possibility):
                    continue
                base_weight = 5 if possibility == straight_ahead else 1

                prey = self.game.spider.position + self.lookahead * self.game.spider.direction
                if abs(prey - (head + possibility)) < abs(prey - head):
                    # new position would be close to spider than current one
                    prey_weight = self.prey_weight
                else:
                    prey_weight = 0
                weight = base_weight + prey_weight
                choices.append(
                    (head + possibility, weight)
                )
        if choices:
            return random.choices(choices, weights=[c[1] for c in choices])[0][0]
        return None  # turn around


class Spider:
    def __init__(self, *, speed=2):
        self.game = None
        self.position = 1+1j
        self.direction = 0+0j
        self.threading = True
        self.speed = speed

    def tick(self):
        self.game.reset_at(self.position)
        new_position = self.position + self.direction
        if (
            self.game.min_x <= new_position.real <= self.game.max_x
            and self.game.min_y <= new_position.imag <= self.game.max_y
        ):
            if self.threading and self.game.tile_category(self.position) == "board":
                self.game.thread.append(self.position)
                print_at(self.position, THREAD)
            self.position = new_position
            if self.game.is_safe_spot(self.position):
                if self.threading:
                    self.game.convert_thread()
                self.threading = True
                self.game.thread.clear()
        print_at(self.position, SPIDER)


class Level:
    def __init__(self, *, spider, snakes):
        self.running = False
        self.spider = spider
        self.spider.game = self
        self.thread = []
        self.web = set()
        self.level = ""
        self.snakes = snakes
        for snake in self.snakes:
            snake.game = self
        self.min_x, self.max_x, self.min_y, self.max_y = 1, 40, 1, 40
        self.won = False

    def setup(self):
        print_at(20+0j, f"LEVEL {self.level}")
        for i in range(self.min_x, self.max_x + 1):
            print_at(complex(i, self.min_y), WALL)
            print_at(complex(i, self.max_y), WALL)
        for i in range(self.min_y, self.max_y + 1):
            print_at(complex(self.min_x, i), WALL)
            print_at(complex(self.max_x, i), WALL)
        self.running = True

    def convert_thread(self):
        if not self.thread:
            return
        group_a = set()
        group_b = set()
        for x in range(self.min_x, self.max_x + 1):
            for y in range(self.min_y, self.max_y + 1):
                position = complex(x, y)
                if self.tile_category(position) in ("web", "wall", "thread"):
                    continue
                if not group_a:
                    group_a = self.connected_tiles(position)
                if position in group_a:
                    continue
                group_b.add(position)
        for group in (group_a, group_b):
            for snake in self.snakes:
                if set(snake.body) & group:
                    break
            else:
                if group - set(self.thread):
                    # The enclosed area is not just the thread itself
                    self.web.update(group)
                    self.web.update(self.thread)

        old_thread = self.thread.copy()
        self.thread.clear()
        for tile in old_thread:
            self.reset_at(tile)
        for tile in self.web:
            print_at(tile, WEB)
        if len(self.web) / self.n_tiles > 0.7:
            self.won = True

    @property
    def n_tiles(self):
        return (self.max_x - self.min_x + 1) * (self.max_y - self.min_y + 1)

    def connected_tiles(self, position):
        # stops at wall and web and thread
        seen = set()
        todo = [position]
        while todo:
            pos = todo.pop()
            seen.add(pos)
            for direction in (0+1j, 0-1j, 1+0j, -1+0j):
                new_position = pos+direction
                if not self.in_bounds(new_position, variant="board"):
                    continue
                if self.tile_category(new_position) != "board":
                    continue
                if new_position not in seen:
                    todo.append(new_position)
        return seen

    def in_bounds(self, position, variant="spider"):
        if variant == "snake":
            return self.min_x < position.real < self.max_x and self.min_y < position.imag < self.max_y
        elif variant in ("spider", "board"):
            return self.min_x <= position.real <= self.max_x and self.min_y <= position.imag <= self.max_y
        raise

    def tile_category(self, position):
        if (
            position.real in (self.min_x, self.max_x)
            or position.imag in (self.min_y, self.max_y)
        ):
            return "wall"
        if position in self.web:
            return "web"
        if position in self.thread:
            return "thread"
        return "board"

    def entities_on_tile(self, position):
        for snake in self.snakes:
            if position in snake.body:
                yield snake
                break
        if self.spider.position == position:
            yield self.spider

    def is_safe_spot(self, position):
        return self.tile_category(position) in ("wall", "web")

    def tick(self, i):
        for snake in self.snakes:
            if i % snake.speed != 0:
                continue
            snake.tick(go_into_hole=self.won)
            if self.won:
                continue
            if snake.body[0] in self.thread:
                self.spider.threading = False
                broken_thread = self.thread.copy()
                self.thread.clear()
                for tile in broken_thread:
                    self.reset_at(tile)
            if self.spider.position in snake.body:
                raise GameOver
        if self.won:
            return
        if i % self.spider.speed == 0:
            self.spider.tick()

    def reset_at(self, position):
        print_at(position, {
            "board": " ",
            "web": WEB,
            "thread": THREAD,
            "wall": WALL,
        }[self.tile_category(position)])

def read_inputs(game):
    while game.running:
        key = get_char()
        if key in (KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, "h", "j", "k", "l", "w", "a", "s", "d"):
            game.spider.direction = {
                KEY_UP: 0-1j,
                "k": 0-1j,
                "w": 0-1j,
                KEY_DOWN: 0+1j,
                "j": 0+1j,
                "s": 0+1j,
                KEY_RIGHT: 1+0j,
                "l": 1+0j,
                "d": 1+0j,
                KEY_LEFT: -1+0j,
                "h": -1+0j,
                "a": -1+0j,
            }[key]


LEVELS = [
    Level(
        spider=Spider(),
        snakes=[
            Snake(7+7j, speed=5),
        ],
    ),
    Level(
        spider=Spider(speed=3),
        snakes=[
            Snake(7+7j, speed=3),
        ],
    ),
    Level(
        spider=Spider(),
        snakes=[
            Snake(7+7j, speed=3, lookahead=10, prey_weight=10),
        ],
    ),
    Level(
        spider=Spider(speed=3),
        snakes=[
            Snake(7+7j, speed=3, lookahead=10, prey_weight=10),
            Snake(3+3j, variant=SNAKE_BODY_2, speed=4, prey_weight=3),
        ],
    ),
    Level(
        spider=Spider(speed=4),
        snakes=[
            Snake(7+7j, variant=SNAKE_BODY_3, speed=3, lookahead=10, prey_weight=10),
            Snake(3+3j, variant=SNAKE_BODY_2, speed=2, prey_weight=3),
        ],
    ),
    Level(
        spider=Spider(),
        snakes=[
            Snake(7+7j, speed=3, lookahead=5),
            Snake(9+11j, variant=SNAKE_BODY_2, speed=2, prey_weight=1),
            Snake(3+4j, variant=SNAKE_BODY_3, lookahead=10, speed=4, prey_weight=10),
        ],
    ),
    Level(
        spider=Spider(),
        snakes=[
            Snake(7+7j, speed=1, lookahead=5),
            Snake(9+11j, variant=SNAKE_BODY_2, speed=2, prey_weight=1),
            Snake(3+4j, variant=SNAKE_BODY_3, lookahead=10, speed=4, prey_weight=10),
        ],
    ),
    Level(
        spider=Spider(speed=1),
        snakes=[
            Snake(7+7j, speed=1, lookahead=5),
            Snake(9+11j, variant=SNAKE_BODY_2, speed=1, prey_weight=1),
            Snake(3+4j, variant=SNAKE_BODY_3, lookahead=10, speed=1, prey_weight=10),
        ],
    ),
]


with SingleCharInput():
    try:
        for n, level in enumerate(LEVELS, start=1):
            clear()
            try:
                start = monotonic()
                level.level = n
                level.setup()
                t = threading.Thread(target=read_inputs, args=(level,))
                t.start()
                for i in count():
                    level.tick(i)
                    sleep(0.05)
            except Win:
                level.running = False
                clear()
                print_at(0+0j, "")
                print("Won in", int(monotonic() - start), "seconds")
                print("Press any key to continue to the next level")
                t.join()
                clear()
    except GameOver:
        level.running = False
        clear()
        print_at(0+0j, "")
        print("You lost after", int(monotonic() - start), "seconds")
        print("Press any key to exit")
        t.join()
    else:
        print("Congratulations! You won the entire game!")
