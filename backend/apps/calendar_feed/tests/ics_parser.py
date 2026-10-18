"""Mały czytnik iCalendar dla testów – rozwinięcie wierszy, koperty, właściwości z parametrami.

Celowo **nie** biblioteka ``icalendar`` (nie ma jej w zależnościach): czytnik robi dokładnie to, co
klient kalendarza musi zrobić z plikiem, żeby go zrozumieć (RFC 5545 §3.1–§3.3.11), i nic więcej –
więc test sprawdza, czy plik da się przeczytać według RFC, a nie czy dwie implementacje mają tę samą
interpretację.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Component:
    name: str
    properties: list[tuple[str, dict, str]] = field(default_factory=list)
    children: list[Component] = field(default_factory=list)

    def get(self, name: str) -> str | None:
        return next((value for key, _params, value in self.properties if key == name), None)

    def params(self, name: str) -> dict:
        return next((params for key, params, _value in self.properties if key == name), {})

    def all(self, name: str) -> list[Component]:
        return [child for child in self.children if child.name == name]


def unfold(text: str) -> list[str]:
    """Wiersze logiczne: CRLF rozdziela, CRLF + spacja/tab to kontynuacja (RFC 5545 §3.1)."""
    assert text.endswith("\r\n"), "plik ma się kończyć CRLF"
    physical = text[:-2].split("\r\n")
    logical: list[str] = []
    for line in physical:
        assert "\n" not in line and "\r" not in line, f"samotny LF/CR w wierszu: {line!r}"
        if line[:1] in (" ", "\t"):
            assert logical, "kontynuacja bez wiersza"
            logical[-1] += line[1:]
        else:
            logical.append(line)
    return logical


def unescape(value: str) -> str:
    """Odwrotność cytowania wartości TEXT (``\\\\``, ``\\;``, ``\\,``, ``\\n``)."""
    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            nxt = value[index + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            assert nxt in "\\;,nN", f"nieznana sekwencja \\{nxt}"
            index += 2
            continue
        # W wartości TEXT pojedynczej (SUMMARY, DESCRIPTION) goły średnik albo przecinek jest
        # separatorem – klient uciąłby na nim tekst.
        assert char not in ";,", f"niecytowany {char!r} w wartości {value!r}"
        out.append(char)
        index += 1
    return "".join(out)


def _split_property(line: str) -> tuple[str, dict, str]:
    head, _, value = line.partition(":")
    name, *raw_params = head.split(";")
    params = dict(part.split("=", 1) for part in raw_params)
    return name.upper(), params, value


def parse(text: str) -> Component:
    """Drzewo komponentów. Każde ``BEGIN`` musi mieć swoje ``END`` w poprawnej kolejności."""
    stack: list[Component] = []
    root: Component | None = None
    for line in unfold(text):
        name, params, value = _split_property(line)
        if name == "BEGIN":
            component = Component(value)
            if stack:
                stack[-1].children.append(component)
            stack.append(component)
        elif name == "END":
            assert stack and stack[-1].name == value, f"END:{value} bez pasującego BEGIN"
            finished = stack.pop()
            if not stack:
                root = finished
        else:
            assert stack, f"właściwość poza komponentem: {line!r}"
            stack[-1].properties.append((name, params, value))
    assert not stack, "niezamknięty komponent"
    assert root is not None and root.name == "VCALENDAR"
    return root
