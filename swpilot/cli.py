"""SW-Pilot command-line interface.

Exit codes: 0 success; 1 execution failure; 2 invalid command file.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from swpilot.backends.base import Backend, BackendError
from swpilot.commands.loader import (
    CommandFileError,
    ExpandedCommand,
    expand_commands,
    load_command_file,
)
from swpilot.commands.schema import CommandFile
from swpilot.executor import execute

app = typer.Typer(
    name="swpilot",
    help="JSON-driven SolidWorks automation with a CI-friendly mock backend.",
    no_args_is_help=True,
)


class BackendChoice(StrEnum):
    mock = "mock"
    solidworks = "solidworks"


def _load_or_exit(file: Path) -> tuple[CommandFile, list[ExpandedCommand]]:
    try:
        cmd_file = load_command_file(file)
        expanded = expand_commands(list(cmd_file.commands))
    except CommandFileError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    return cmd_file, expanded


@app.command()
def validate(file: Annotated[Path, typer.Argument(help="Command file (JSON)")]) -> None:
    """Validate a command file (schema + macro expansion) without executing."""
    _, expanded = _load_or_exit(file)
    typer.secho(
        f"{file}: OK ({len(expanded)} primitive command(s) after macro expansion)",
        fg=typer.colors.GREEN,
    )


@app.command()
def expand(file: Annotated[Path, typer.Argument(help="Command file (JSON)")]) -> None:
    """Print the fully-expanded primitive command list as JSON."""
    _, expanded = _load_or_exit(file)
    out = [
        {
            "op": ec.command.op,
            "params": ec.command.model_dump(exclude={"op"}),
            "source_index": ec.source_index,
            "expanded_from": ec.source_op if ec.source_op != ec.command.op else None,
        }
        for ec in expanded
    ]
    typer.echo(json.dumps(out, indent=2))


def _make_backend(
    choice: BackendChoice, visible: bool | None, template: str | None
) -> Backend:
    if choice is BackendChoice.mock:
        if template is not None or visible is not None:
            typer.secho(
                "warning: --template/--visible only affect the solidworks backend "
                "and are ignored by the mock backend",
                fg=typer.colors.YELLOW,
                err=True,
            )
        from swpilot.backends.mock.simulator import MockBackend

        return MockBackend()
    try:
        from swpilot.backends.solidworks.com_backend import SolidWorksBackend
    except ImportError as exc:
        typer.secho(
            "The 'solidworks' backend requires Windows with pywin32 and SolidWorks "
            "installed (pip install 'swpilot[windows]'). Use '--backend mock' "
            f"everywhere else.\n  detail: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc
    try:
        return SolidWorksBackend(
            visible=visible if visible is not None else True, part_template=template
        )
    except BackendError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def run(
    file: Annotated[Path, typer.Argument(help="Command file (JSON)")],
    backend: Annotated[
        BackendChoice, typer.Option(help="Execution backend")
    ] = BackendChoice.mock,
    report: Annotated[
        Path | None,
        typer.Option(help="Where to write the run report (default: <file>.report.json)"),
    ] = None,
    visible: Annotated[
        bool | None,
        typer.Option(
            "--visible/--no-visible",
            help="Show the SolidWorks window (solidworks backend; default: visible)",
        ),
    ] = None,
    template: Annotated[
        str | None,
        typer.Option(help="Part template path (solidworks backend; overrides the default)"),
    ] = None,
) -> None:
    """Execute a command file against the chosen backend."""
    cmd_file, expanded = _load_or_exit(file)
    be = _make_backend(backend, visible, template)
    try:
        run_report = execute(expanded, be, schema_version=cmd_file.schema_version)
    finally:
        be.close()

    report_path = report if report is not None else file.with_suffix(file.suffix + ".report.json")
    report_path.write_text(json.dumps(run_report.to_dict(), indent=2) + "\n", encoding="utf-8")

    ok = sum(1 for r in run_report.results if r.status == "ok")
    typer.echo(f"backend: {run_report.backend}")
    typer.echo(f"commands: {ok}/{len(run_report.results)} ok")
    for r in run_report.results:
        for w in r.warnings:
            typer.secho(f"  warning [{r.index}:{r.op}] {w}", fg=typer.colors.YELLOW)
        if r.status == "error":
            typer.secho(f"  error   [{r.index}:{r.op}] {r.error}", fg=typer.colors.RED, err=True)
    if run_report.finalize_error:
        typer.secho(
            f"  error   [finalize] {run_report.finalize_error}", fg=typer.colors.RED, err=True
        )
    typer.echo(f"report: {report_path}")
    if not run_report.success:
        raise typer.Exit(code=1)
    typer.secho("success", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
