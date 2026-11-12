"""All settings come from environment variables (or a local .env file).

Nothing about the client's data is hard-coded here: URLs, credentials and
folders can all be changed without touching code, so the evaluators can point
the pipeline at their own copies of the systems.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # reads .env if present; real environment variables win


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    output_dir: Path
    payroll_url: str
    payroll_api_key: str
    dispatch_url: str
    dispatch_client_id: str
    dispatch_client_secret: str
    finance_url: str
    finance_user: str
    finance_password: str
    anthropic_api_key: str
    dry_run: bool


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.getenv("DATA_DIR", "./data")),
        output_dir=Path(os.getenv("OUTPUT_DIR", "./output")),
        payroll_url=os.getenv("PAYROLL_URL", "http://localhost:8081").rstrip("/"),
        # Default credentials are the ones published in each system's API.md;
        # override them with environment variables for other copies.
        payroll_api_key=os.getenv("PAYROLL_API_KEY", "assess-7f3c9a"),
        dispatch_url=os.getenv("DISPATCH_URL", "http://localhost:8082").rstrip("/"),
        dispatch_client_id=os.getenv("DISPATCH_CLIENT_ID", "me-assess"),
        dispatch_client_secret=os.getenv("DISPATCH_CLIENT_SECRET", "d1sp4tch-s3cret"),
        finance_url=os.getenv("FINANCE_URL", "http://localhost:8083").rstrip("/"),
        finance_user=os.getenv("FINANCE_USER", "svc_assess"),
        finance_password=os.getenv("FINANCE_PASSWORD", "Q4-close!"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        dry_run=_flag("DRY_RUN", "true"),  # safe by default
    )
