"""Generate a sample branded work-report PDF for visual review."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from board.report_pdf import write_sample_report_pdf  # noqa: E402

out = ROOT / "tmp" / "sample-report-nextgen.pdf"
path = write_sample_report_pdf(out)
print(path)
print(f"size={path.stat().st_size}")
