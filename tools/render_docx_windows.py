"""Convierte un DOCX a PDF y PNG con LibreOffice de forma segura en Windows.

La ruta del perfil temporal se entrega como URI ``file:///C:/...``. Usar
``file://C:\\...`` hace que LibreOffice interprete mal el arranque y puede
mostrar el aviso engañoso de que ``bootstrap.ini`` está dañado.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


def find_soffice() -> Path:
    candidates = [
        shutil.which("soffice.com"),
        shutil.which("soffice.exe"),
        r"C:\Program Files\LibreOffice\program\soffice.com",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("No se ha encontrado LibreOffice (soffice).")


def convert_to_pdf(source: Path, output_dir: Path, timeout: int) -> Path:
    soffice = find_soffice()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{source.stem}.pdf"
    pdf_path.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="soffice_profile_") as profile:
        profile_uri = Path(profile).resolve().as_uri()
        command = [
            str(soffice),
            f"-env:UserInstallation={profile_uri}",
            "--headless",
            "--nologo",
            "--nodefault",
            "--norestore",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(output_dir),
            str(source),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    if result.returncode != 0 or not pdf_path.is_file():
        detail = "\n".join(part for part in (result.stdout, result.stderr) if part)
        raise RuntimeError(
            f"LibreOffice no pudo convertir {source} (código {result.returncode}).\n{detail}"
        )
    return pdf_path


def render_pages(pdf_path: Path, output_dir: Path, dpi: int) -> int:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - depende del runtime documental
        raise RuntimeError(
            "Falta pypdfium2. Ejecute este script con el runtime documental de Codex."
        ) from exc

    document = pdfium.PdfDocument(str(pdf_path))
    scale = dpi / 72
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                image = page.render(scale=scale).to_pil()
                image.save(output_dir / f"page-{index + 1:03d}.png")
            finally:
                page.close()
        return len(document)
    finally:
        document.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Renderiza un DOCX con LibreOffice y PDFium en Windows."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--pdf-only",
        action="store_true",
        help="Genera el PDF sin rasterizar sus páginas.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    if not source.is_file() or source.suffix.lower() != ".docx":
        raise FileNotFoundError(f"DOCX no encontrado: {source}")

    output_dir = args.output_dir.resolve()
    pdf_path = convert_to_pdf(source, output_dir, args.timeout)
    pages = 0 if args.pdf_only else render_pages(pdf_path, output_dir, args.dpi)
    print(f"PDF: {pdf_path}")
    if not args.pdf_only:
        print(f"Páginas renderizadas: {pages}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
