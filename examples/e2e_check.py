"""End-to-end check: run a folder of invoice images through the real pipeline.

    python examples/e2e_check.py ./invoices            # summary table
    python examples/e2e_check.py ./invoices -o ./out   # also save each JSON

Needs Docling and its models (Hugging Face reachable, or
BILL_EXTRACTOR_ARTIFACTS pointing at a local model cache).

An invoice has no answer key, so its own printed totals are the check:
  * lines    - purchase lines extracted
  * sum/net  - sum of line billedAmount vs. the printed grand total
               (off by round-off only when every line was read correctly)
  * taxable  - sum of line purchaseAmount vs. the GST table's taxable total
               (the printed "Sales Value" is often before discount)
  * fill %   - share of targeted mapping columns filled across lines
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bill_extractor import extract_bill  # noqa: E402
from bill_extractor.cli import IMAGE_EXTS  # noqa: E402
from bill_extractor.column_mapping import coverage  # noqa: E402

TOLERANCE = 1.0  # rupees; invoices round the grand total to the nearest rupee


def _check(expected, got):
    if expected is None or got is None:
        return "?"
    return "ok" if abs(expected - got) <= TOLERANCE else "MISMATCH"


def _money(value):
    return "-" if value is None else f"{value:.2f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder")
    ap.add_argument("-o", "--output", help="Directory to write each invoice's JSON")
    args = ap.parse_args(argv)

    files = sorted(p for p in Path(args.folder).iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not files:
        print(f"no invoices found in {args.folder}", file=sys.stderr)
        return 1
    out_dir = Path(args.output) if args.output else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    header = f"{'file':<28} {'invoice':<16} {'lines':>5} {'grand':>9} {'sum/net':>9} {'taxable':>9} {'fill%':>6} {'sec':>5}"
    print(header)
    print("-" * len(header))
    passed = 0
    for path in files:
        start = time.time()
        try:
            inv = extract_bill(path)
        except Exception as exc:  # report and keep going
            print(f"{path.name:<28} ERROR {type(exc).__name__}: {exc}"[:160])
            continue
        lines = inv.purchase_lines
        billed = [ln.billedAmount for ln in lines]
        taxed = [ln.purchaseAmount for ln in lines]
        billed_sum = round(sum(billed), 2) if lines and None not in billed else None
        taxed_sum = round(sum(taxed), 2) if lines and None not in taxed else None
        net_ok = _check(inv.totals.grand_total, billed_sum)
        slabs = [t.taxable_amount for t in inv.tax_summary if t.taxable_amount is not None]
        tax_ok = _check(round(sum(slabs), 2) if slabs else inv.totals.taxable_amount, taxed_sum)
        cov = coverage(lines)
        fill = (100 * sum(c["filled"] for c in cov) / sum(c["total"] for c in cov)) if lines else 0
        passed += net_ok == "ok"
        print(f"{path.name[:28]:<28} {str(inv.invoice_no)[:16]:<16} {len(lines):>5} "
              f"{_money(inv.totals.grand_total):>9} {net_ok:>9} {tax_ok:>9} {fill:>6.0f} "
              f"{time.time() - start:>5.1f}")
        if out_dir:
            (out_dir / f"{path.stem}.json").write_text(inv.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n{passed}/{len(files)} invoices: line totals reconcile with the printed grand total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
