from pathlib import Path


def data_dir() -> Path:
    path = Path.home() / "Library/Application Support/PRBot"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def log_dir() -> Path:
    path = Path.home() / "Library/Logs/PRBot"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path
