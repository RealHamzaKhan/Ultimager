#!/usr/bin/env python3
"""Test PDF processing to verify images are extracted and sent to LLM."""
import sys
import tempfile
from pathlib import Path
import base64

sys.path.insert(0, str(Path(__file__).parent))

from app.services.file_parser_fixed import _parse_pdf_mixed

def test_pdf_extraction():
    """Test that PDF extraction creates proper images."""
    # Create a test PDF
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            pdf_path = Path(tmp.name)
        
        # Create a simple PDF with text
        c = canvas.Canvas(str(pdf_path), pagesize=letter)
        c.drawString(100, 700, "Test Question 1")
        c.drawString(100, 650, "This is a test answer")
        c.showPage()
        c.drawString(100, 700, "Page 2 Content")
        c.drawString(100, 650, "More handwritten text here")
        c.showPage()
        c.save()
        
        print(f"Created test PDF: {pdf_path}")
        
        # Parse the PDF
        result = _parse_pdf_mixed(pdf_path, max_text_chars=50000)
        
        print(f"\nExtraction Results:")
        print(f"  File type: {result.file_type}")
        print(f"  Extraction method: {result.extraction_method}")
        print(f"  Text extracted: {len(result.text_content) if result.text_content else 0} chars")
        print(f"  Images extracted: {len(result.images)}")
        
        for i, img in enumerate(result.images):
            print(f"\n  Image {i+1}:")
            print(f"    Page: {img.get('page')}")
            print(f"    Size: {img.get('size_bytes')} bytes")
            print(f"    Media type: {img.get('media_type')}")
            print(f"    Base64 length: {len(img.get('base64', ''))}")
            print(f"    Has valid base64: {bool(img.get('base64'))}")
        
        pdf_path.unlink(missing_ok=True)
        
    except ImportError:
        print("reportlab not installed, skipping PDF creation test")

if __name__ == "__main__":
    test_pdf_extraction()
