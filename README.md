# TypeOver for iTerm2

Select text in your prompt, then type. The selection gets replaced, the way it does in Google Docs, Notion, or any normal text box.

It works in **Claude Code** and in **zsh / bash / fish**.

## Why terminals don't do this already

In iTerm, a mouse selection belongs to iTerm, not to the program you're typing into. Claude Code never finds out you highlighted anything, so your keystrokes go wherever its own cursor is.

TypeOver sits in between. When you type over a selection, it:

1. moves the program's cursor to the start of the selection with arrow keys, checking the screen after each move,
2. counts the characters to the end of the selection,
3. deletes exactly that many, then
4. types your key.

It takes about a tenth of a second.

## Install

Paste this into iTerm:

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/typeover/main/install.sh | bash
```

The installer puts the script in iTerm's AutoLaunch folder and turns on iTerm's Python API. If the API was off, it offers a quick iTerm restart. The first time the script runs, iTerm asks to download its Python runtime. Click OK.

To uninstall:

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/typeover/main/uninstall.sh | bash
```

### Claude Code users: one setting

Claude Code's newer **fullscreen** renderer takes over the mouse, so iTerm never sees your selection. Pick one fix:

- Hold **Option** while you double-click or drag. That makes a native iTerm selection.
- Or keep the fullscreen renderer but let iTerm handle the mouse: `export CLAUDE_CODE_DISABLE_MOUSE=1`
- Or switch to the classic renderer: `/tui default`

## Use

- **Double-click a word, then type.** The word is replaced.
- **Drag across text, even across wrapped lines, then type.** It's replaced.
- **Select text, then press Delete.** It's removed.

## Safety

- It only acts when the selection is inside the editable prompt. That means Claude Code's input box (between the two horizontal lines) or your shell's current command line. Selecting text in Claude's output to copy it is left alone.
- It only presses Left, Right, and Backspace. It never presses Up or Down, so it can't pull up history.
- If it loses track of the cursor, or the cursor stops moving, it gives up *before* deleting anything and passes your keystroke through normally.
- It does nothing in other programs (vim, less, htop, ...).
- It only intercepts keys while a selection exists, and releases the keyboard about 0.8 s after you stop typing.

## Troubleshooting

- **Scripts → Manage → Console** shows the script's log. Set `DEBUG = True` at the top of `typeover.py` for a step-by-step trace.
- Claude Code **vim mode** isn't supported. Letters are commands in normal mode.

## Tests

```bash
pip install iterm2
python3 tests/test_sim.py
```

The tests simulate an iTerm session running a Claude-Code-style input box (word wrap, `> ` prompt, a fake caret) and a zsh-style line editor.

## License

MIT
