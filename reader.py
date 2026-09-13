import pdfplumber

def read_pdf(pdf_path):
    pages = []

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):

            tables = []
            for t in page.find_tables():
                tables.append({
                    "bbox": t.bbox,
                    "rows": t.extract(),
                })

            pages.append({
                "page": i + 1,
                "text": page.extract_text() or "",
                "words": page.extract_words(),
                "tables": tables
            })

    return pages