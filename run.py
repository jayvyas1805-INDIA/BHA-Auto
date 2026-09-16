#!/usr/bin/env python3
"""
CLI entrypoint.

    python run.py upload/REPORT.pdf
    python run.py upload/REPORT.pdf output/custom_name.json
    USE_LLM_NORMALIZATION=true python run.py upload/REPORT.pdf
"""
import sys

from bha.pipeline import run


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        pdf_path = input("Path to PDF: ").strip()
    else:
        pdf_path = sys.argv[1]
    json_output = sys.argv[2] if len(sys.argv) > 2 else None
    run(pdf_path, json_output)


if __name__ == "__main__":
    main()
