"""Simulation tests: a fake iTerm session running a fake Claude-Code-style
input box (word wrap, '> ' prompt, indented continuation rows, rule lines) and
a fake shell line editor (hard char wrap). Run: python3 tests/test_sim.py"""
import asyncio
import sys
import types
from pathlib import Path

# ---- minimal stub of the iterm2 module ------------------------------------
it = types.ModuleType("iterm2")


class Selection:
    def __init__(self, subs):
        self.sub_selections = subs


it.Selection = Selection
sys.modules["iterm2"] = it
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import typeover  # noqa: E402

typeover.SETTLE_STEP = 0
typeover.SETTLE_TIMEOUT = 0.02

ABOVE = 500  # pretend there is scrollback above the screen


class P:
    def __init__(self, x, y):
        self.x, self.y = x, y


def mksel(y0, x0, y1, x1):
    r = types.SimpleNamespace(start=P(x0, y0 + ABOVE), end=P(x1, y1 + ABOVE))
    sub = types.SimpleNamespace(windowed_coord_range=types.SimpleNamespace(coordRange=r))
    return Selection([sub])


class Line:
    def __init__(self, text, inverse_at=None, hard=True):
        self.string = text
        self.inv = inverse_at
        self.hard_eol = hard

    def string_at(self, x):
        if x >= len(self.string):
            raise IndexError
        return self.string[x]

    def style_at(self, x):
        return types.SimpleNamespace(inverse=(x == self.inv))


class Contents:
    def __init__(self, lines, cursor):
        self.lines = lines
        self.number_of_lines = len(lines)
        self.number_of_lines_above_screen = ABOVE
        self.cursor_coord = P(cursor[1], cursor[0] + ABOVE)

    def line(self, i):
        return self.lines[i]


class FakeEditor:
    """Holds text + caret, interprets keys, renders a screen."""

    def __init__(self, text, caret, width=40, style="claude", fake_caret=False):
        self.text, self.caret = text, caret
        self.width, self.style, self.fake_caret = width, style, fake_caret

    def keys(self, s):
        i = 0
        while i < len(s):
            if s.startswith("\x1b[D", i):
                self.caret = max(0, self.caret - 1); i += 3
            elif s.startswith("\x1b[C", i):
                self.caret = min(len(self.text), self.caret + 1); i += 3
            elif s[i] == "\x7f":
                if self.caret:
                    self.text = self.text[:self.caret - 1] + self.text[self.caret:]
                    self.caret -= 1
                i += 1
            else:
                self.text = self.text[:self.caret] + s[i] + self.text[self.caret:]
                self.caret += 1; i += 1

    def layout_claude(self):
        """Greedy word wrap; spaces at a wrap point are swallowed.
        Returns rows (list of (start_idx, end_idx)) and caret (row, col_in_text)."""
        w = self.width - 4
        rows, i, t = [], 0, self.text
        while True:
            if len(t) - i <= w:
                rows.append((i, len(t))); break
            cut = t.rfind(" ", i, i + w + 1)
            if cut <= i:
                rows.append((i, i + w)); i += w
            else:
                rows.append((i, cut)); i = cut + 1
        for r, (a, b) in enumerate(rows):
            if a <= self.caret <= b and not (self.caret == b and r + 1 < len(rows) and rows[r + 1][0] == b):
                return rows, (r, self.caret - a)
        return rows, (len(rows) - 1, self.caret - rows[-1][0])

    def render(self):
        if self.style == "claude":
            rows, (cr, cc) = self.layout_claude()
            lines = [Line("some earlier output from claude"), Line(""), Line("─" * self.width)]
            first = len(lines)
            for r, (a, b) in enumerate(rows):
                prefix = "> " if r == 0 else "  "
                lines.append(Line(prefix + self.text[a:b]))
            lines.append(Line("─" * self.width))
            lines.append(Line("  ? for shortcuts"))
            cy, cx = first + cr, 2 + cc
            if self.fake_caret:
                ln = lines[cy]
                if len(ln.string) <= cx:
                    ln.string = ln.string + " " * (cx - len(ln.string) + 1)
                ln.inv = cx
                return Contents(lines, (len(lines) - 1, 0))   # real cursor parked
            return Contents(lines, (cy, cx))
        # shell: hard char wrap, soft EOLs between rows
        full = "user@mac ~ % " + self.text
        w = self.width
        chunks = [full[i:i + w] for i in range(0, max(len(full), 1), w)] or [""]
        lines = [Line("previous command output")]
        first = len(lines)
        for k, ch in enumerate(chunks):
            lines.append(Line(ch, hard=(k == len(chunks) - 1)))
        pos = 13 + self.caret
        return Contents(lines, (first + pos // w, pos % w))


class FakeSession:
    def __init__(self, ed, job, cmd=None):
        self.ed, self.job, self.cmd = ed, job, job if cmd is None else cmd
        self.grid_size = types.SimpleNamespace(width=ed.width)
        self.session_id = "s1"

    async def async_get_variable(self, name):
        return {"jobName": self.job, "commandLine": self.cmd}.get(name, "")

    async def async_get_screen_contents(self):
        return self.ed.render()

    async def async_send_text(self, s):
        self.ed.keys(s)

    async def async_set_selection(self, sel):
        pass


def find(ed, word):
    """Screen (row, col) span of word in rendered output (first match)."""
    c = ed.render()
    for i in range(c.number_of_lines):
        j = c.line(i).string.find(word)
        if j >= 0:
            return i, j, j + len(word)
    raise AssertionError(word)


async def run(ed, job, sel, typed, cmd=None):
    s = FakeSession(ed, job, cmd)
    ok = await typeover.Replacer(s).replace(sel, typed)
    return ok, ed.text


def check(name, got, want):
    status = "PASS" if got == want else "FAIL"
    print(f"{status}  {name}\n      got:  {got!r}" + ("" if got == want else f"\n      want: {want!r}"))
    return got == want


async def main():
    ok = True

    # 1. Claude, single word, caret at end of text
    ed = FakeEditor("please fix the bug in the parser", 32)
    r, a, b = find(ed, "bug")
    ok &= check("claude: replace word, caret at end", await run(ed, "claude", mksel(r, a, r, b), "X"),
                (True, "please fix the X in the parser"))

    # 2. Claude, fake inverse caret (terminal cursor parked below)
    ed = FakeEditor("please fix the bug in the parser", 5, fake_caret=True)
    r, a, b = find(ed, "fix")
    ok &= check("claude: inverse-cell caret", await run(ed, "claude", mksel(r, a, r, b), "Z"),
                (True, "please Z the bug in the parser"))

    # 3. Claude, multi-row selection across a word-wrap
    txt = "the quick brown fox jumps over the lazy dog and keeps running far away"
    ed = FakeEditor(txt, len(txt))
    r1, a1, _ = find(ed, "over")
    r2, _, b2 = find(ed, "running")
    assert r1 != r2, "test needs a wrapped selection"
    ok &= check("claude: selection spans wrapped rows", await run(ed, "claude", mksel(r1, a1, r2, b2), "Q"),
                (True, "the quick brown fox jumps Q far away"))

    # 4. Claude, Delete key removes selection
    ed = FakeEditor("remove this word please", 0)
    r, a, b = find(ed, "this ")
    ok &= check("claude: delete key", await run(ed, "claude", mksel(r, a, r, b), "\x7f"),
                (True, "remove word please"))

    # 5. Claude, selection outside the input box -> not handled, text untouched
    ed = FakeEditor("hello world", 11)
    r, a, b = find(ed, "earlier")
    ok &= check("claude: selection in output is ignored", await run(ed, "claude", mksel(r, a, r, b), "X"),
                (False, "hello world"))

    # 6. Shell, soft-wrapped long command, caret at end
    cmd = "git commit -m 'this is a long message that wraps around the terminal'"
    ed = FakeEditor(cmd, len(cmd), width=30, style="shell")
    c = ed.render()
    # select 'long' (find absolute column in wrapped rows)
    full = "user@mac ~ % " + cmd
    pos = full.index("long")
    r, x = 1 + pos // 30, pos % 30
    ok &= check("shell: replace word in wrapped command", await run(ed, "zsh", mksel(r, x, r, x + 4), "S"),
                (True, cmd.replace("long", "S")))

    # 7. Shell, selecting the prompt itself -> gives up without deleting
    ed = FakeEditor("ls -la", 6, style="shell")
    ok &= check("shell: selecting prompt text gives up", await run(ed, "zsh", mksel(1, 0, 1, 4), "X"),
                (False, "ls -la"))

    # 8. Unknown app (vim) -> ignored
    ed = FakeEditor("hello", 5, style="shell")
    ok &= check("vim: ignored", await run(ed, "vim", mksel(1, 13, 1, 18), "X"), (False, "hello"))

    # 9. Claude, selection of last word in text, caret at start of text
    ed = FakeEditor("rename the variable foo", 0)
    r, a, b = find(ed, "foo")
    ok &= check("claude: last word, caret at start", await run(ed, "claude", mksel(r, a, r, b), "b"),
                (True, "rename the variable b"))

    # 10. Native Claude binary: job name is its version, command line is "claude"
    ed = FakeEditor("fix the right bug", 17)
    r, a, b = find(ed, "right")
    ok &= check("claude: native binary (version as job name)",
                await run(ed, "2.1.283", mksel(r, a, r, b), "X", cmd="claude"),
                (True, "fix the X bug"))

    # 11. A shell command that only mentions claude is still a shell
    ed = FakeEditor("vim claude.md", 13, style="shell")
    r, a, b = find(ed, "claude")
    ok &= check("shell: 'claude' in an argument stays shell mode",
                await typeover.Replacer(FakeSession(ed, "zsh", "vim claude.md")).mode(), "shell")

    print("\nALL PASS" if ok else "\nSOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
