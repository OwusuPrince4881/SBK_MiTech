from dataclasses import dataclass, field

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """One finding. `code` is a stable identifier that tests and callers can rely on."""

    severity: str
    code: str
    message: str
    location: str = ""

    def format(self) -> str:
        where = f" {self.location}:" if self.location else ""
        return f"{self.severity.upper()} [{self.code}]{where} {self.message}"


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)

    def error(self, code: str, message: str, location: str = "") -> None:
        self.issues.append(Issue(ERROR, code, message, location))

    def warn(self, code: str, message: str, location: str = "") -> None:
        self.issues.append(Issue(WARNING, code, message, location))

    def extend(self, other: "Report") -> None:
        self.issues.extend(other.issues)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> set[str]:
        return {issue.code for issue in self.issues}

    def format(self, max_issues: int = 100) -> str:
        """Errors first, then warnings, capped so a bad manifest cannot flood a terminal."""
        ordered = self.errors + self.warnings
        lines = [issue.format() for issue in ordered[:max_issues]]
        hidden = len(ordered) - len(lines)
        if hidden > 0:
            lines.append(f"... and {hidden} more finding(s) not shown.")
        return "\n".join(lines)


def summarise_ids(ids: list[str], limit: int = 5) -> str:
    """Render a short, readable list of identifiers for a message."""
    shown = ", ".join(repr(value) for value in ids[:limit])
    extra = len(ids) - limit
    return f"{shown} and {extra} more" if extra > 0 else shown
