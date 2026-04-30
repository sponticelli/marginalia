"""Smoke tests — confirm the package is importable and the CLI is wired up."""

import subprocess

import engine


def test_engine_has_version():
    """The engine package exposes __version__."""
    assert engine.__version__ == "0.1.0"


def test_cli_hello():
    """The marginalia CLI runs and produces output."""
    result = subprocess.run(
        ["marginalia", "hello", "sandro"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "sandro" in result.stdout.lower()


def test_cli_version():
    """The marginalia version command reports the right version."""
    result = subprocess.run(
        ["marginalia", "version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "0.1.0" in result.stdout
