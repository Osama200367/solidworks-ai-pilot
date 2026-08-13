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
    report_path = report if report is not None else file.with_suffix(file.suffix + ".report.json")
    _execute_and_report(
        cmd_file, expanded, backend, visible, template, report_path
    )


def _execute_and_report(
    cmd_file: CommandFile,
    expanded: list[ExpandedCommand],
    backend: BackendChoice,
    visible: bool | None,
    template: str | None,
    report_path: Path,
) -> None:
    """Run expanded commands against a backend, write the report, print status."""
    be = _make_backend(backend, visible, template)
    try:
        run_report = execute(expanded, be, schema_version=cmd_file.schema_version)
    finally:
        be.close()

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


# --------------------------------------------------------------------------
# Natural-language layer (v1.0)
# --------------------------------------------------------------------------


class AiMode(StrEnum):
    copy_paste = "copy-paste"
    api = "api"


@app.command()
def ai(
    description: Annotated[str, typer.Argument(help="Plain-language part/assembly description")],
    mode: Annotated[
        AiMode, typer.Option(help="copy-paste (any free chat) or api")
    ] = AiMode.copy_paste,
    out: Annotated[
        Path | None,
        typer.Option(help="Write the prompt bundle here (copy-paste mode) instead of stdout"),
    ] = None,
    backend: Annotated[
        BackendChoice, typer.Option(help="Execution backend (api mode)")
    ] = BackendChoice.mock,
    save: Annotated[
        Path | None,
        typer.Option(help="Write the validated CommandFile JSON here (api mode)"),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip the confirmation prompt before execution")
    ] = False,
) -> None:
    """Translate a description into SW-Pilot commands via an LLM.

    copy-paste (default): prints a prompt bundle to paste into ANY free AI
    chat; paste the AI's JSON back through `swpilot ai-apply`. No API key.

    api: sends the bundle to a configured OpenAI-compatible endpoint
    (SWPILOT_LLM_* env), validates with one auto-repair, then executes.
    """
    from swpilot.llm import build_bundle

    bundle = build_bundle(description)
    if mode is AiMode.copy_paste:
        if out is not None:
            out.write_text(bundle + "\n", encoding="utf-8")
            typer.secho(f"prompt bundle written to {out}", fg=typer.colors.GREEN, err=True)
            typer.secho(
                "Paste it into any AI chat, then run:\n"
                "  swpilot ai-apply <the-json-file>   (or --paste to read stdin)",
                fg=typer.colors.CYAN,
                err=True,
            )
        else:
            typer.echo(bundle)
        return

    _ai_api(description, bundle, backend, save, yes)


def _ai_api(
    description: str,
    bundle: str,
    backend: BackendChoice,
    save: Path | None,
    yes: bool,
) -> None:
    from swpilot.llm import validate_or_repair
    from swpilot.llm.client import (
        LLMConfig,
        LLMConfigError,
        LLMRequestError,
        OpenAICompatibleClient,
    )

    try:
        client = OpenAICompatibleClient(LLMConfig.from_env())
    except LLMConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    try:
        first = client.complete(bundle)
        outcome = validate_or_repair(description, first, retry=client.complete)
    except LLMRequestError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if not outcome.ok:
        typer.secho(
            "the model's JSON did not validate after one repair attempt:",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(outcome.errors or "unknown error", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if outcome.repaired:
        typer.secho("(the model's first JSON was auto-repaired)", fg=typer.colors.YELLOW, err=True)
    assert outcome.command_file is not None
    _apply_command_file(outcome.command_file, backend, save, yes)


@app.command(name="ai-apply")
def ai_apply(
    file: Annotated[
        Path | None,
        typer.Argument(help="File with the LLM's JSON response (omit with --paste)"),
    ] = None,
    paste: Annotated[
        bool, typer.Option("--paste", help="Read the LLM response from stdin instead")
    ] = False,
    backend: Annotated[
        BackendChoice, typer.Option(help="Execution backend")
    ] = BackendChoice.mock,
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip the confirmation prompt before execution")
    ] = False,
) -> None:
    """Validate an LLM's JSON response and execute it (copy-paste mode).

    Extracts the CommandFile from the pasted text (prose/fences tolerated),
    validates it; if invalid, prints a ready-to-paste repair prompt and
    exits without executing anything.
    """
    import sys

    from swpilot.llm import validate_or_repair

    if paste:
        response = sys.stdin.read()
    elif file is not None:
        response = file.read_text(encoding="utf-8-sig")
    else:
        typer.secho("give a file argument or --paste", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    outcome = validate_or_repair("(from copy-paste)", response)
    if not outcome.ok:
        typer.secho("the pasted JSON did not validate:", fg=typer.colors.RED, err=True)
        typer.secho(outcome.errors or "unknown error", fg=typer.colors.RED, err=True)
        typer.secho(
            "\nPaste this repair prompt back into the SAME chat, then run "
            "ai-apply again with the corrected JSON:\n",
            fg=typer.colors.CYAN,
            err=True,
        )
        typer.echo(outcome.repair_prompt or "")
        raise typer.Exit(code=2)
    assert outcome.command_file is not None
    _apply_command_file(outcome.command_file, backend, None, yes)


def _apply_command_file(
    cmd_file: CommandFile,
    backend: BackendChoice,
    save: Path | None,
    yes: bool,
) -> None:
    """Show the parsed commands, confirm, then expand + execute (safety gate)."""
    try:
        expanded = expand_commands(list(cmd_file.commands))
    except CommandFileError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if save is not None:
        save.write_text(
            json.dumps(cmd_file.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        typer.secho(f"validated CommandFile written to {save}", fg=typer.colors.GREEN, err=True)

    # Safety: show the parsed command list before any execution.
    typer.secho(
        f"parsed {len(cmd_file.commands)} command(s) "
        f"→ {len(expanded)} after macro expansion:",
        fg=typer.colors.CYAN,
    )
    for c in cmd_file.commands:
        params = {k: v for k, v in c.model_dump(exclude={"op"}).items() if v not in (None, [], {})}
        typer.echo(f"  {c.op} {params}")

    # The mock backend is side-effect-free; the solidworks backend touches
    # the live app, so require an explicit confirmation there.
    if (
        backend is BackendChoice.solidworks
        and not yes
        and not typer.confirm("Execute these commands in SolidWorks?", default=False)
    ):
        typer.secho("aborted (nothing executed)", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    report_path = Path("ai_run.report.json")
    _execute_and_report(cmd_file, expanded, backend, None, None, report_path)


if __name__ == "__main__":
    app()
