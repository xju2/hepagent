"""JFC analysis note PDF compilation tool."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agents import function_tool
from hepagent.helpers import get_repo_root


@function_tool
async def compile_analysis_note(
    analysis_root: str,
    note_markdown_path: str,
    output_pdf_path: str,
) -> str:
    """
    Compile a markdown analysis note to PDF.

    Pipeline:
      pandoc {note_markdown_path} → .tex
      postprocess_tex.py → .tex (deterministic fixes)
      tectonic → .pdf

    Returns the PDF path on success, or error message on failure.
    Uses testarea/jfc/src/conventions/postprocess_tex.py.

    Args:
        analysis_root: Absolute path to the analysis root directory.
        note_markdown_path: Path to the markdown AN file (absolute or relative to analysis_root).
        output_pdf_path: Desired PDF output path (absolute or relative to analysis_root).
    """
    root = Path(analysis_root)
    md_path = Path(note_markdown_path)
    if not md_path.is_absolute():
        md_path = root / md_path
    pdf_path = Path(output_pdf_path)
    if not pdf_path.is_absolute():
        pdf_path = root / output_pdf_path

    if not md_path.exists():
        return f"Error: markdown file not found: {md_path}"

    tex_path = md_path.with_suffix(".tex")
    preamble = get_repo_root() / "testarea" / "jfc" / "src" / "conventions" / "preamble.tex"
    postprocess = (
        get_repo_root() / "testarea" / "jfc" / "src" / "conventions" / "postprocess_tex.py"
    )

    # Step 1: pandoc → .tex
    pandoc_cmd = [
        "pandoc",
        str(md_path),
        "-o",
        str(tex_path),
        "--standalone",
        "--number-sections",
        "--toc",
    ]
    if preamble.exists():
        pandoc_cmd += ["--include-in-header", str(preamble)]
    try:
        r = subprocess.run(pandoc_cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return f"pandoc failed (exit {r.returncode}):\n{r.stdout}{r.stderr}"
    except FileNotFoundError:
        return "Error: pandoc not found. Install pandoc to compile analysis notes."
    except subprocess.TimeoutExpired:
        return "Error: pandoc timed out."

    # Step 2: postprocess_tex.py
    if postprocess.exists():
        try:
            r = subprocess.run(
                ["python", str(postprocess), str(tex_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                return f"postprocess_tex.py failed:\n{r.stdout}{r.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: postprocess_tex.py timed out."

    # Step 3: tectonic → .pdf
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["tectonic", str(tex_path), "--outdir", str(pdf_path.parent)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=md_path.parent,
        )
        if r.returncode != 0:
            return f"tectonic failed (exit {r.returncode}):\n{r.stdout}{r.stderr}"
    except FileNotFoundError:
        return "Error: tectonic not found. Install tectonic to compile to PDF."
    except subprocess.TimeoutExpired:
        return "Error: tectonic timed out after 300s."

    # tectonic outputs <stem>.pdf in outdir
    generated = pdf_path.parent / (tex_path.stem + ".pdf")
    if generated.exists() and generated != pdf_path:
        generated.rename(pdf_path)

    if pdf_path.exists():
        return str(pdf_path)
    return f"Error: PDF not found at {pdf_path} after compilation."
