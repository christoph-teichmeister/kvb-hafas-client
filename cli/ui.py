"""Terminal-Bausteine: Console, Prompts, Auswahl-Menüs."""

import questionary
from rich.console import Console
from rich.markup import escape as esc
from rich.panel import Panel

from kvb_hafas import KVBHafasClient, Stop

console = Console()

# Pfeiltasten-Auswahl im gleichen Cyan wie die rich-Ausgabe.
STYLE = questionary.Style(
    [
        ("qmark", "fg:cyan bold"),
        ("question", "bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected", "fg:cyan"),
        ("answer", "fg:cyan bold"),
        ("instruction", "fg:#888888"),
    ]
)


def panel(title: str, content: object) -> Panel:
    """Ergebnis in einen Rahmen mit Titel setzen."""
    return Panel(content, title=f"[bold]{title}[/]", title_align="left", border_style="cyan", padding=(0, 1))


def prompt(question: questionary.Question) -> object | None:
    """Frage stellen; None bei Ctrl-C oder wenn kein Terminal dranhängt.

    Ohne TTY (Pipe, Redirect) wirft prompt_toolkit EOFError — das soll ein
    sauberer Abbruch sein, kein Traceback.
    """
    try:
        return question.ask()
    except EOFError:
        return None


def ask(text: str, default: str = "") -> str:
    """Texteingabe mit Default. Ctrl-C/Ctrl-D beenden das Programm."""
    answer = prompt(questionary.text(text, default=default, style=STYLE, qmark="›"))
    if answer is None:
        raise SystemExit("Abbruch.")
    return str(answer).strip()


def choose(title: str, options: list[str]) -> int | None:
    """Auswahl per Pfeiltasten oder Zifferntaste. None = abgebrochen."""
    question = questionary.select(
        title,
        choices=[questionary.Choice(opt, value=i) for i, opt in enumerate(options)],
        style=STYLE,
        qmark="›",
        instruction="(↑/↓ oder Ziffer, Enter)",
        # use_shortcuts belegt 1-9; bei mehr Optionen gäbe es doppelte Tasten.
        use_shortcuts=len(options) <= 9,
    )
    answer = prompt(question)
    return answer if isinstance(answer, int) else None


def pick_stop(client: KVBHafasClient, label: str = "Haltestelle") -> Stop | None:
    """Nach einer Haltestelle suchen und aus den Treffern auswählen."""
    while True:
        query = ask(f"{label} suchen (leer = zurück)")
        if not query:
            return None
        with console.status(f"[cyan]Suche „{esc(query)}“…"):
            stops = client.find_stops(query)
        if not stops:
            console.print(f"[dim]Keine Haltestelle für „{esc(query)}“ gefunden.[/]")
            continue
        idx = choose("Treffer", [f"{s.name}  ({s.ext_id})" for s in stops])
        if idx is not None:
            return stops[idx]
