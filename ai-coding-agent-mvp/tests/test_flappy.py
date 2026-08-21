"""Pytest suite for the flappy game (core mechanics, bot, rendering)."""

import io
import json

import pytest
from flappy.__main__ import Session
from flappy.game import (
    BIRD_R,
    BIRD_X,
    CEILING_Y,
    FLAP_VELOCITY,
    MAX_GAP_JUMP,
    PIPE_GAP_MIN,
    PIPE_SPEED_MAX,
    PIPE_W,
    Game,
    Pipe,
    State,
    autopilot,
    load_best,
)
from flappy.render import Renderer


# ------------------------------------------------------------------ helpers
def alive_game(seed=5, ticks=30):
    """Bot-driven game that is guaranteed to still be mid-flight."""
    g = Game(seed=seed)
    for _ in range(ticks):
        if g.state is State.GAME_OVER:
            g.restart()
        elif autopilot(g):
            g.flap()
        g.step()
    return g


@pytest.fixture
def playing_game():
    g = alive_game()
    assert g.state is State.PLAYING
    return g


# ------------------------------------------------------------------- physics
class TestMechanics:
    def test_flap_leaves_ready(self):
        g = Game(seed=1)
        g.flap()
        assert g.state is State.PLAYING
        assert g.bird.vy == FLAP_VELOCITY

    def test_gravity_accelerates(self):
        g = Game(seed=1)
        g.flap()
        vy0 = g.bird.vy
        g.step()
        assert g.bird.vy > vy0

    def test_ready_hovers(self):
        g = Game(seed=1)
        for _ in range(30):
            g.step()
        assert g.state is State.READY
        assert 0.40 <= g.bird.y <= 0.50

    def test_ground_kills(self):
        g = Game(seed=1)
        g.flap()
        ticks = 0
        while g.state is not State.GAME_OVER:
            g.step()
            ticks += 1
        assert ticks <= 120

    def test_ceiling_clamps_and_is_safe(self):
        g = Game(seed=1)
        g.flap()
        g.bird.y = -1.0
        g.step()
        assert g.bird.y >= CEILING_Y + BIRD_R - 1e-9
        assert g.bird.vy >= 0.0
        assert g.state is State.PLAYING

    def test_pipe_collision_dying_then_game_over(self):
        g = Game(seed=1)
        g.flap()
        g.pipes.append(Pipe(x=BIRD_X - PIPE_W / 2.0, gap_c=0.10, gap_h=0.26))
        g.bird.y = 0.55  # inside the lower pipe body
        g.bird.vy = 0.0
        g.step()
        assert g.state is State.DYING
        for _ in range(300):
            if g.state is State.GAME_OVER:
                break
            g.step()
        assert g.state is State.GAME_OVER

    def test_scoring_once_per_pipe(self):
        g = Game(seed=1)
        g.flap()
        g.pipes.append(Pipe(x=BIRD_X - BIRD_R - PIPE_W, gap_c=0.5, gap_h=0.3))
        g.bird.y = 0.5
        g.bird.vy = 0.0
        g.step()
        assert g.score == 1
        g.step()
        assert g.score == 1

    def test_speed_grows_and_caps(self):
        g = Game(seed=1)
        g.flap()
        g.pipes.append(Pipe(x=BIRD_X - BIRD_R - PIPE_W, gap_c=0.5, gap_h=0.3))
        g.bird.y = 0.5
        g.bird.vy = 0.0
        g.step()
        s0 = g.speed
        g.score = 10
        g.step()
        assert g.speed > s0
        g.score = 99999
        g.step()
        assert g.speed <= PIPE_SPEED_MAX

    def test_deterministic_given_seed(self):
        def play(seed):
            g = Game(seed=seed)
            g.flap()
            for _ in range(120):
                g.step()
            return (g.state, g.score, round(g.bird.y, 12), g.tick)

        assert play(42) == play(42)

    def test_restart_resets(self, playing_game):
        playing_game.score = 5
        playing_game.restart()
        assert playing_game.state is State.READY
        assert playing_game.score == 0
        assert playing_game.pipes == []


# ---------------------------------------------------------------- persistence
class TestPersistence:
    def test_missing_file(self, tmp_path):
        assert load_best(str(tmp_path / "nope.json")) == 0

    def test_corrupt_file(self, tmp_path):
        p = tmp_path / "best.json"
        p.write_text("{not json", encoding="utf-8")
        assert load_best(str(p)) == 0

    def test_game_over_persists_higher_score(self, tmp_path):
        p = tmp_path / "best.json"
        g = Game(seed=2)
        g.best_file = str(p)
        g.best = 0
        g.prev_best = 0
        g.score = 7
        g._game_over()
        assert json.loads(p.read_text())["best"] == 7

    def test_best_never_lowered(self, tmp_path):
        p = tmp_path / "best.json"
        g = Game(seed=2)
        g.best_file = str(p)
        g.best = 9
        g.prev_best = 9
        g.score = 1
        g._game_over()
        assert json.loads(p.read_text())["best"] == 9

    def test_new_best_flag(self, tmp_path):
        p = tmp_path / "best.json"
        g = Game(seed=2)
        g.best_file = str(p)
        g.best = 3
        g.prev_best = 3
        g.score = 5
        g._game_over()
        assert g.new_best is True
        g2 = Game(seed=2)
        g2.best_file = str(p)
        g2.best = 5
        g2.prev_best = 5
        g2.score = 2
        g2._game_over()
        assert g2.new_best is False


# ------------------------------------------------------------------- autopilot
def replay(seed, ticks):
    g = Game(seed=seed)
    for _ in range(ticks):
        if g.state is State.GAME_OVER:
            g.restart()
        elif autopilot(g):
            g.flap()
        g.step()
    return g


class TestAutopilot:
    def test_bot_survives_and_scores(self):
        g = replay(1234, 2400)
        survival = 2400 if g.state is not State.GAME_OVER else g.tick - g.over_tick
        assert survival >= 1200
        assert g.score >= 3

    def test_bot_restarts_keep_playing(self):
        g = replay(777, 12000)
        assert g.score >= 3

    def test_gap_floor(self):
        g = replay(777, 12000)
        for p in g.pipes:
            assert p.gap_h >= PIPE_GAP_MIN

    def test_consecutive_gaps_reachable(self):
        g = replay(777, 12000)
        cs = [p.gap_c for p in g.pipes]
        for a, b in zip(cs, cs[1:]):
            assert abs(b - a) <= MAX_GAP_JUMP + 1e-9

    def test_bot_deterministic(self):
        a, b = replay(99, 2400), replay(99, 2400)
        assert (a.score, round(a.bird.y, 9), a.tick) == \
               (b.score, round(b.bird.y, 9), b.tick)


# -------------------------------------------------------------------- rendering
class TestRendering:
    def test_render_ready(self, playing_game):
        game = Game(seed=5)
        out = Renderer(out=io.StringIO(), cols=64, rows=24,
                       color=False).render(game)
        lines = out.splitlines()
        assert len(lines) == 24
        assert all(len(ln) <= 64 for ln in lines)
        assert "CRAZY FLAPPY BIRD" in out
        assert "Space" in out

    def test_render_playing(self, playing_game):
        out = Renderer(out=io.StringIO(), cols=64, rows=24,
                       color=False).render(playing_game)
        lines = out.splitlines()
        assert len(lines) == 24
        assert all(len(ln) <= 64 for ln in lines)
        assert "SCORE" in out
        assert sum(1 for ln in lines if ln.strip()) >= 8

    def test_render_game_over(self, playing_game):
        g = Game(seed=5)
        g.flap()
        for _ in range(400):
            g.step()
            if g.state is State.GAME_OVER:
                break
        assert g.state is State.GAME_OVER
        out = Renderer(out=io.StringIO(), cols=64, rows=24,
                       color=False).render(g)
        assert "GAME OVER" in out
        assert "play again" in out

    def test_pause_overlay(self, playing_game):
        out = Renderer(out=io.StringIO(), cols=64, rows=24,
                       color=False).render(playing_game, paused=True)
        assert "PAUSED" in out

    @pytest.mark.parametrize("w,h", [(34, 12), (50, 16)])
    def test_small_terminals(self, w, h, playing_game):
        rr = Renderer(out=io.StringIO(), cols=w, rows=h, color=False)
        for game in (Game(seed=5), playing_game):
            lines = rr.render(game).splitlines()
            assert all(len(ln) <= w for ln in lines)

    def test_bot_session_renders_every_frame(self):
        buf = io.StringIO()
        sess = Session(Game(seed=7),
                       Renderer(out=buf, cols=64, rows=20, color=False),
                       bot=True)
        frames = sess.run(max_frames=300, frame_delay=0)
        assert frames == 300
        assert buf.getvalue().count("\x1b[H") == 300


# ------------------------------------------------------------------- keys
class TestKeys:
    class DummyIn:
        def read(self, _n=1):
            return ""

    def _session(self, game, stdin=None):
        return Session(game,
                       Renderer(out=io.StringIO(), cols=64, rows=20,
                                color=False),
                       stdin=stdin if stdin is not None else self.DummyIn())

    def test_start_pause_restart_quit(self):
        g = Game(seed=3)
        s = self._session(g)
        s._handle_key(" ")
        assert g.state is State.PLAYING
        s._handle_key("p")
        assert s.paused
        s._handle_key("P")
        assert not s.paused
        s._handle_key("r")
        assert g.state is State.READY and g.score == 0
        s._handle_key("q")
        assert s._stop

    def test_game_over_keys(self):
        g = Game(seed=3)
        g._game_over()
        s = self._session(g)
        s._handle_key(" ")
        assert g.state is State.GAME_OVER
        s._handle_key("r")
        assert g.state is State.READY

    def test_escape_sequence_is_quit(self):
        class EscIn:
            def __init__(self):
                self.buf = "\x1b[A"
                self.i = 0

            def read(self, n=1):
                if self.i >= len(self.buf):
                    return ""
                ch = self.buf[self.i]
                self.i += n
                return ch

        g = Game(seed=3)
        s = self._session(g, stdin=EscIn())
        s._handle_key(s._read_key())
        assert s._stop
