#!/usr/bin/env python3
"""
TypeOver for iTerm2
===================

Select text in your prompt, start typing, and the selection gets replaced,
the way it works in Google Docs, Notion, or any normal text box.

Why this is needed
------------------
In a terminal, a mouse selection belongs to iTerm, not to the program you're
typing into. Claude Code (or zsh, bash...) never learns that you selected
anything, so your keystrokes go wherever *its* cursor happens to be.

How it works
------------
1. Polls the active session's selection (~every 60 ms).
2. When a selection sits inside an editable prompt, it temporarily intercepts
   printable keys for that session (KeystrokeFilter + KeystrokeMonitor).
3. On the next printable key or Delete, it:
     - walks the program's cursor to the start of the selection with
       Left/Right arrows, checking the cursor position after every batch,
     - walks Right to the end of the selection, counting exactly how many
       characters that took,
     - sends that many Backspaces, then the key you typed.
4. Once no selection is left and you've stopped typing for a moment, it lets
   go of the keyboard again.

It only sends Left, Right, and Backspace, never Up or Down, so it can't
trigger shell or Claude Code history. If anything looks off (it can't find the
cursor, or the cursor stops moving), it gives up without deleting anything and
passes your keystroke through.

Where it acts
-------------
* Claude Code: the input box between the two horizontal rules. Selections can
  span wrapped lines.
* Shells (zsh, bash, fish, sh): the command line holding the cursor, including
  soft-wrapped continuation rows.
* Everywhere else (vim, less, htop, ...): it does nothing.

Install: see README.md. It goes in
~/Library/Application Support/iTerm2/Scripts/AutoLaunch/
"""

import asyncio
import time

import iterm2

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
POLL_INTERVAL = 0.06          # seconds between selection checks
IDLE_RELEASE = 0.8            # release the keyboard after this much idle time
SETTLE_STEP = 0.012           # how often to re-read the screen while waiting
SETTLE_TIMEOUT = 0.35         # max wait for the app to redraw after keys
MAX_STEPS = 120               # safety cap on navigation loop iterations
SHELLS = {"zsh", "bash", "fish", "sh", "-zsh", "-bash", "-fish", "-sh"}
DEBUG = False                 # True prints a trace to the Script Console

LEFT, RIGHT, BACKSPACE = "\x1b[D", "\x1b[C", "\x7f"
PRINTABLE = [chr(c) for c in range(32, 127)]
RULE_CHARS = set("─━═╭╮╰╯┌┐└┘")
DECOR = set(" │┃")
PROMPT_MARKS = set(">❯›")


def log(*args):
    if DEBUG:
        print("[typeover]", *args, flush=True)


# --------------------------------------------------------------------------
# Screen helpers (pure functions over iterm2 LineContents-like objects)
# --------------------------------------------------------------------------
def cell(line, x):
    """Text of cell x, or '' past the end or for the right half of a wide char."""
    if x < 0:
        return ""
    try:
        return line.string_at(x)
    except IndexError:
        return ""


def cells_len(line, width):
    n = 0
    for x in range(width):
        try:
            line.string_at(x)
            n = x + 1
        except IndexError:
            break
    return n


def chars_between(line, x0, x1):
    """Number of characters (not cells) in [x0, x1) on this row."""
    return sum(1 for x in range(max(0, x0), max(0, x1)) if cell(line, x) != "")


def text_end(line, width):
    """Cell index just past the last real text character (ignores trailing
    spaces and a right-hand box border)."""
    last = 0
    for x in range(cells_len(line, width)):
        c = cell(line, x)
        if c and c not in DECOR:
            last = x + 1
    return last


def text_start(line, width):
    """Rough first text cell: skips indentation, a left border, and a '> '
    prompt marker. Only used for step-size estimates, never for deleting."""
    n = cells_len(line, width)
    x = 0
    while x < n and cell(line, x) in DECOR:
        x += 1
    if x < n and cell(line, x) in PROMPT_MARKS and cell(line, x + 1) in ("", " "):
        x += 2
    return x


def is_rule(line):
    s = line.string.strip()
    if len(s) < 10:
        return False
    return sum(1 for ch in s if ch in RULE_CHARS) >= 0.8 * len(s)


def is_inverse(line, x):
    try:
        st = line.style_at(x)
    except Exception:
        return False
    return bool(st is not None and st.inverse)


class Snapshot:
    """One read of the screen, with the editable region and caret located."""

    def __init__(self, contents, width, mode, sel_rows=None):
        self.contents = contents
        self.width = width
        self.mode = mode
        self.above = contents.number_of_lines_above_screen
        self.n = contents.number_of_lines
        cur = contents.cursor_coord          # absolute coordinates
        self.term_cursor = (cur.y, cur.x)
        self.region = None                   # (top_abs, bottom_abs) inclusive
        self.caret = None                    # (y_abs, x)
        if mode == "claude":
            self._locate_claude(sel_rows)
        elif mode == "shell":
            self._locate_shell()

    def line(self, y_abs):
        i = y_abs - self.above
        if 0 <= i < self.n:
            return self.contents.line(i)
        return None

    def _locate_claude(self, sel_rows):
        # Anchor on the selection if we have one, else on the terminal cursor.
        anchor = sel_rows[0] if sel_rows else self.term_cursor[0]
        a = anchor - self.above
        if not (0 <= a < self.n):
            return
        top = next((i for i in range(a, -1, -1)
                    if self.contents.line(i) and is_rule(self.contents.line(i))), None)
        bot = next((i for i in range(a, self.n)
                    if is_rule(self.contents.line(i))), None)
        if top is None or bot is None or top == a or bot - top < 2 or bot - top > 80:
            return
        self.region = (self.above + top + 1, self.above + bot - 1)
        # Caret: the real terminal cursor if the app parked it inside the box,
        # otherwise the single inverse-video cell the app draws as a caret.
        ty, tx = self.term_cursor
        if self.region[0] <= ty <= self.region[1]:
            self.caret = (ty, tx)
            return
        hits = []
        for y in range(self.region[0], self.region[1] + 1):
            ln = self.line(y)
            for x in range(self.width):
                if is_inverse(ln, x):
                    hits.append((y, x))
        if len(hits) == 1:
            self.caret = hits[0]

    def _locate_shell(self):
        ty, tx = self.term_cursor
        i = ty - self.above
        if not (0 <= i < self.n):
            return
        top = i
        while top > 0 and not self.contents.line(top - 1).hard_eol:
            top -= 1
        bot = i
        while bot < self.n - 1 and not self.contents.line(bot).hard_eol:
            bot += 1
        self.region = (self.above + top, self.above + bot)
        self.caret = (ty, tx)

    def contains(self, pos):
        return self.region is not None and self.region[0] <= pos[0] <= self.region[1]


# --------------------------------------------------------------------------
# The replacer
# --------------------------------------------------------------------------
class GiveUp(Exception):
    pass


class Replacer:
    def __init__(self, session):
        self.s = session

    async def mode(self):
        job = (await self.s.async_get_variable("jobName") or "").lower()
        cmd = (await self.s.async_get_variable("commandLine") or "").lower()
        # The native Claude Code binary reports its version (e.g. "2.1.283")
        # as the job name, so also check the command that started it.
        prog = cmd.split()[0].rsplit("/", 1)[-1] if cmd.strip() else ""
        if "claude" in job or "claude" in prog or ("node" in job and "claude" in cmd):
            return "claude"
        if job in SHELLS:
            return "shell"
        return None

    async def snapshot(self, mode, sel_rows=None):
        contents = await self.s.async_get_screen_contents()
        return Snapshot(contents, self.s.grid_size.width, mode, sel_rows)

    async def settle(self, mode, prev_caret, sel_rows):
        """Wait until the caret moves away from prev_caret and stays still."""
        deadline = time.monotonic() + SETTLE_TIMEOUT
        last = None
        while time.monotonic() < deadline:
            await asyncio.sleep(SETTLE_STEP)
            snap = await self.snapshot(mode, sel_rows)
            if snap.caret is None:
                continue
            if snap.caret != prev_caret:
                if last is not None and snap.caret == last.caret:
                    return snap            # moved and stable across two reads
                last = snap
        if last is not None:
            return last
        return await self.snapshot(mode, sel_rows)

    @staticmethod
    def selection_range(sel, snap):
        """Normalize the iTerm selection to [(y, x) start, (y, x) end) in
        absolute coords, clamped to real text."""
        if len(sel.sub_selections) != 1:
            return None
        r = sel.sub_selections[0].windowed_coord_range.coordRange
        start, end = (r.start.y, r.start.x), (r.end.y, r.end.x)
        if end[1] == 0 and end[0] > start[0]:
            end = (end[0] - 1, snap.width)
        ln = snap.line(end[0])
        if ln is None:
            return None
        end = (end[0], min(end[1], text_end(ln, snap.width)))
        if end <= start:
            return None
        return start, end

    async def eligible(self, sel):
        """Return (mode, start, end, snapshot) if this selection is replaceable."""
        mode = await self.mode()
        if mode is None:
            return None
        r0 = sel.sub_selections[0].windowed_coord_range.coordRange if sel.sub_selections else None
        if r0 is None:
            return None
        rows = (r0.start.y, r0.end.y)
        snap = await self.snapshot(mode, rows)
        rng = self.selection_range(sel, snap)
        if rng is None or snap.caret is None:
            return None
        start, end = rng
        if not (snap.contains(start) and snap.contains(end)):
            return None
        return mode, start, end, snap

    async def send(self, text):
        await self.s.async_send_text(text)

    async def goto(self, target, snap, mode, rows):
        """Move the caret to target using arrows, re-reading after each batch."""
        for _ in range(MAX_STEPS):
            c = snap.caret
            if c is None or not snap.contains(c):
                raise GiveUp("lost caret")
            if c == target:
                return snap
            ln = snap.line(c[0])
            if c[0] == target[0]:
                if c[1] > target[1]:
                    k, key = chars_between(ln, target[1], c[1]), LEFT
                else:
                    k, key = chars_between(ln, c[1], target[1]), RIGHT
            elif c > target:
                k, key = chars_between(ln, text_start(ln, snap.width), c[1]), LEFT
            else:
                k, key = chars_between(ln, c[1], text_end(ln, snap.width)), RIGHT
            k = max(1, k)
            log("goto", c, "->", target, key == LEFT and "L" or "R", k)
            await self.send(key * k)
            nxt = await self.settle(mode, c, rows)
            if nxt.caret == c:
                raise GiveUp("caret did not move")
            snap = nxt
        raise GiveUp("goto did not converge")

    async def count_to(self, target, snap, mode, rows):
        """From the current caret, press Right until reaching target. Returns
        the exact number of characters crossed. Never overshoots by more than
        it can walk back."""
        presses = 0
        for _ in range(MAX_STEPS):
            c = snap.caret
            if c is None or not snap.contains(c):
                raise GiveUp("lost caret while counting")
            if c == target:
                return presses, snap
            if c > target:                      # overshot (wide chars etc.)
                await self.send(LEFT)
                presses -= 1
                nxt = await self.settle(mode, c, rows)
                if nxt.caret == c:
                    raise GiveUp("stuck walking back")
                snap = nxt
                continue
            ln = snap.line(c[0])
            if c[0] == target[0]:
                k = chars_between(ln, c[1], target[1])
            else:
                k = chars_between(ln, c[1], text_end(ln, snap.width))
            k = max(1, k)
            log("count", c, "->", target, k)
            await self.send(RIGHT * k)
            presses += k
            nxt = await self.settle(mode, c, rows)
            if nxt.caret == c:
                raise GiveUp("caret did not move while counting")
            snap = nxt
        raise GiveUp("count did not converge")

    async def replace(self, sel, typed):
        info = await self.eligible(sel)
        if info is None:
            return False
        mode, start, end, snap = info
        rows = (start[0], end[0])
        # Clear iTerm's highlight first so it doesn't linger over new text.
        await self.s.async_set_selection(iterm2.Selection([]))
        try:
            snap = await self.goto(start, snap, mode, rows)
            n, snap = await self.count_to(end, snap, mode, rows)
        except GiveUp as e:
            log("gave up:", e)
            return False
        if n <= 0:
            return False
        out = BACKSPACE * n + ("" if typed == BACKSPACE else typed)
        log("replace", n, "chars with", repr(typed))
        await self.send(out)
        return True


# --------------------------------------------------------------------------
# Keyboard arming / disarming
# --------------------------------------------------------------------------
def _pattern():
    p = iterm2.KeystrokePattern()
    p.forbidden_modifiers = [iterm2.Modifier.COMMAND,
                             iterm2.Modifier.CONTROL,
                             iterm2.Modifier.OPTION]
    p.characters = PRINTABLE + [BACKSPACE]
    return p


def _is_filtered(ks):
    mods = set(ks.modifiers)
    if mods & {iterm2.Modifier.COMMAND, iterm2.Modifier.CONTROL, iterm2.Modifier.OPTION}:
        return False
    return ks.characters in PRINTABLE or ks.characters == BACKSPACE


class Armed:
    """Intercepts printable keys in one session while a selection exists."""

    def __init__(self, connection, session):
        self.conn = connection
        self.session = session
        self.replacer = Replacer(session)
        self.monitor = None
        self.filter = None
        self.task = None
        self.last_activity = time.monotonic()
        self.busy = False

    async def start(self):
        self.monitor = iterm2.KeystrokeMonitor(self.conn, self.session.session_id)
        await self.monitor.__aenter__()
        self.filter = iterm2.KeystrokeFilter(self.conn, [_pattern()], self.session.session_id)
        await self.filter.__aenter__()
        self.task = asyncio.ensure_future(self._loop())
        log("armed", self.session.session_id)

    async def stop(self):
        # Release the filter first so keys flow natively again, then stop
        # listening. Anything queued before that point was intercepted and
        # still gets delivered.
        try:
            await self.filter.__aexit__(None, None, None)
        finally:
            await self.monitor.__aexit__(None, None, None)
            if self.task:
                self.task.cancel()
        log("disarmed", self.session.session_id)

    async def _loop(self):
        while True:
            ks = await self.monitor.async_get()
            self.last_activity = time.monotonic()
            if not _is_filtered(ks):
                continue                     # iTerm handled it natively
            self.busy = True
            try:
                handled = False
                try:
                    sel = await self.session.async_get_selection()
                    if sel.sub_selections:
                        handled = await self.replacer.replace(sel, ks.characters)
                except Exception as e:   # never eat a keystroke on errors
                    log("error:", repr(e))
                if not handled:
                    await self.session.async_send_text(ks.characters)
            finally:
                self.busy = False
                self.last_activity = time.monotonic()


async def main(connection):
    app = await iterm2.async_get_app(connection)
    armed = None
    last_sig = None

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            win = app.current_terminal_window
            tab = win.current_tab if win else None
            session = tab.current_session if tab else None

            if armed is not None:
                idle = time.monotonic() - armed.last_activity
                switched = session is None or session.session_id != armed.session.session_id
                if not armed.busy and idle > IDLE_RELEASE:
                    sel = await armed.session.async_get_selection()
                    if switched or not sel.sub_selections:
                        await armed.stop()
                        armed = None
                continue

            if session is None:
                continue
            sel = await session.async_get_selection()
            if not sel.sub_selections:
                last_sig = None
                continue
            r = sel.sub_selections[0].windowed_coord_range.coordRange
            sig = (session.session_id, r.start.x, r.start.y, r.end.x, r.end.y)
            if sig == last_sig:
                continue
            last_sig = sig
            if await Replacer(session).eligible(sel):
                armed = Armed(connection, session)
                await armed.start()
        except Exception as e:
            log("poll error:", repr(e))
            if armed is not None:
                try:
                    await armed.stop()
                except Exception:
                    pass
                armed = None


if __name__ == "__main__":
    iterm2.run_forever(main)
