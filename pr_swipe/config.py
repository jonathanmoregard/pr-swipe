"""Runtime configuration, read from environment variables."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    user: str
    inbox: Path      # collector writes cards, GUI reads
    outbox: Path     # GUI writes requests, collector reads
    returns: Path    # executor writes returned-card notes, GUI reads
    state: Path      # executor/GUI private state (train, audit, gui-state, metrics)
    socket: Path
    agent_logins: frozenset
    model: str
    deep_model: str


def load(env=os.environ) -> Config:
    root = Path(env.get("PR_SWIPE_ROOT", "/var/lib/pr-swipe"))
    logins = frozenset(s.strip() for s in env.get("PR_SWIPE_AGENT_LOGINS", "").split(",") if s.strip())
    return Config(
        user=env.get("PR_SWIPE_USER", "jonathanmoregard"),
        inbox=root / "inbox",
        outbox=root / "outbox",
        returns=root / "returns",
        state=root / "state",
        socket=Path(env.get("PR_SWIPE_SOCKET", "/run/pr-swipe/executor.sock")),
        agent_logins=logins,
        model=env.get("PR_SWIPE_MODEL", "claude-opus-5-5"),
        deep_model=env.get("PR_SWIPE_DEEP_MODEL", "claude-opus-5-5"),
    )
