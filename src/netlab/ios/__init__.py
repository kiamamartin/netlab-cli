"""IOS CLI engine: mode state machine, parser, and error rendering."""

from netlab.ios.errors import render_error
from netlab.ios.machine import CliSession, HistoryEntry, SessionExit, build_registry, run_console
from netlab.ios.parser import CommandRegistry, CommandSpec

__all__ = [
    "CliSession",
    "CommandRegistry",
    "CommandSpec",
    "HistoryEntry",
    "SessionExit",
    "build_registry",
    "render_error",
    "run_console",
]
