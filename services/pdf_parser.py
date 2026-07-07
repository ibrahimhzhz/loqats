import fitz  # PyMuPDF

def extract_text_from_pdf(file_bytes):
    """
    Extracts raw text from a PDF file in memory.
    """
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        return text
    except Exception as e:
        print(f"Error parsing PDF: {e}")
        return ""