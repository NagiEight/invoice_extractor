from pathlib import Path
import subprocess


def export_git_tracked_files(output_file: str = "output.txt") -> None:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    )

    files = [file for file in result.stdout.splitlines() if file.strip()]
    Path(output_file).write_text("\n".join(files), encoding="utf-8")


if __name__ == "__main__":
    export_git_tracked_files()
