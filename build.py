"""Compile Distillat en exécutable Windows via PyInstaller, à partir de distillat.spec.

Usage :
    python build.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SPEC_PATH = PROJECT_DIR / "distillat.spec"


def _check_pyinstaller_available() -> None:
    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            print(
                "PyInstaller n'est pas installé dans cet environnement.\n"
                "Installez les dépendances avec : pip install -r requirements.txt",
                file=sys.stderr,
            )
            sys.exit(1)


def _remove_stale_output(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def main() -> None:
    _check_pyinstaller_available()

    if not SPEC_PATH.exists():
        print(f"Fichier introuvable : {SPEC_PATH}", file=sys.stderr)
        sys.exit(1)

    # Repart de zéro à chaque build : les caches PyInstaller (build/) et un
    # dist/ précédent peuvent sinon masquer des ressources mal configurées
    # dans le .spec (fichiers restés d'une exécution antérieure).
    _remove_stale_output(PROJECT_DIR / "build")
    _remove_stale_output(PROJECT_DIR / "dist")

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC_PATH)],
        cwd=PROJECT_DIR,
    )
    if result.returncode != 0:
        sys.exit(result.returncode)

    exe_path = PROJECT_DIR / "dist" / "Distillat" / "Distillat.exe"
    print(f"\nCompilation terminée : {exe_path}")
    print("Pour distribuer l'application, copiez l'intégralité du dossier dist/Distillat/.")


if __name__ == "__main__":
    main()
