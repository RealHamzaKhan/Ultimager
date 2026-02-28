"""
File processor with loading indicators and actual PDF screenshots.
Uses pdf2image for high-quality screenshots instead of just pixmap conversion.
"""
import base64
import io
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple
from PIL import Image

logger = logging.getLogger(__name__)

def convert_pdf_to_screenshots(pdf_path: Path, dpi: int = 200) -> List[Dict[str, Any]]:
    """
    Convert PDF pages to actual screenshots using pdf2image.
    This creates true visual representations of each page.
    """
    images = []
    
    try:
        from pdf2image import convert_from_path
        
        logger.info(f"Converting PDF to screenshots: {pdf_path.name}")
        
        # Convert PDF pages to PIL Images
        pil_images = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            fmt='png',
            thread_count=4
        )
        
        for page_num, img in enumerate(pil_images, 1):
            try:
                # Resize if too large (max 2048x2048 for API)
                max_dimension = 2048
                if img.width > max_dimension or img.height > max_dimension:
                    img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                
                # Convert to bytes
                buffer = io.BytesIO()
                img.save(buffer, format="PNG", optimize=True)
                img_bytes = buffer.getvalue()
                
                # Convert to base64
                b64 = base64.b64encode(img_bytes).decode()
                
                images.append({
                    "page": page_num,
                    "base64": b64,
                    "media_type": "image/png",
                    "size_bytes": len(img_bytes),
                    "dimensions": {"width": img.width, "height": img.height},
                    "description": f"Page {page_num} screenshot"
                })
                
                logger.debug(f"Converted page {page_num}: {img.width}x{img.height}, {len(img_bytes)} bytes")
                
            except Exception as e:
                logger.warning(f"Failed to convert page {page_num}: {e}")
        
        logger.info(f"Successfully converted {len(images)} pages from {pdf_path.name}")
        
    except ImportError:
        logger.warning("pdf2image not installed, falling back to PyMuPDF")
        return _convert_pdf_with_pymupdf(pdf_path, dpi)
    except Exception as e:
        logger.error(f"Error converting PDF screenshots: {e}")
        return _convert_pdf_with_pymupdf(pdf_path, dpi)
    
    return images

def _convert_pdf_with_pymupdf(pdf_path: Path, dpi: int = 200) -> List[Dict[str, Any]]:
    """Fallback PDF conversion using PyMuPDF."""
    import fitz
    
    images = []
    doc = fitz.open(str(pdf_path))
    
    logger.info(f"Converting PDF with PyMuPDF: {pdf_path.name} ({len(doc)} pages)")
    
    for page_num in range(len(doc)):
        try:
            page = doc[page_num]
            zoom = dpi / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            
            # Convert to PIL Image
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
            
            # Resize if too large
            max_dimension = 2048
            if img.width > max_dimension or img.height > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            
            # Save optimized
            buffer = io.BytesIO()
            img.save(buffer, format="PNG", optimize=True)
            img_bytes = buffer.getvalue()
            
            b64 = base64.b64encode(img_bytes).decode()
            
            images.append({
                "page": page_num + 1,
                "base64": b64,
                "media_type": "image/png",
                "size_bytes": len(img_bytes),
                "dimensions": {"width": img.width, "height": img.height},
                "description": f"Page {page_num + 1}"
            })
            
        except Exception as e:
            logger.warning(f"Failed to convert page {page_num + 1}: {e}")
    
    doc.close()
    logger.info(f"Converted {len(images)} pages with PyMuPDF")
    
    return images

def process_file_with_progress(file_path: Path, progress_callback=None) -> Tuple[Any, Dict]:
    """
    Process a single file with progress reporting.
    
    Args:
        file_path: Path to the file
        progress_callback: Function(status_message) to call with progress updates
    
    Returns:
        (ExtractedContent, report_dict)
    """
    from app.services.file_parser_fixed import ExtractedContent, parse_file_with_report
    
    if progress_callback:
        progress_callback(f"Processing {file_path.name}...")
    
    ext = file_path.suffix.lower()
    
    # For PDFs, use screenshot conversion
    if ext == '.pdf':
        if progress_callback:
            progress_callback(f"Converting PDF pages to screenshots: {file_path.name}...")
        
        images = convert_pdf_to_screenshots(file_path)
        
        # Also extract text
        if progress_callback:
            progress_callback(f"Extracting text from PDF: {file_path.name}...")
        
        try:
            import fitz
            doc = fitz.open(str(file_path))
            text_parts = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                if text.strip():
                    text_parts.append(f"\n--- Page {page_num + 1} ---\n{text}")
            doc.close()
            full_text = "\n".join(text_parts)
        except Exception as e:
            logger.warning(f"Failed to extract text from PDF: {e}")
            full_text = ""
        
        has_text = len(full_text.strip()) > 50
        has_images = len(images) > 0
        
        if has_text and has_images:
            extraction_method = "mixed_screenshots_and_text"
        elif has_images:
            extraction_method = "screenshots_only"
        else:
            extraction_method = "text_only"
        
        content = ExtractedContent(
            filename=file_path.name,
            file_type="pdf",
            text_content=full_text[:50000] if len(full_text) > 50000 else full_text,
            images=images,
            extraction_method=extraction_method,
            size_bytes=file_path.stat().st_size,
            metadata={
                "total_pages": len(images),
                "pages_converted": len(images),
                "text_extracted": has_text,
                "images_extracted": has_images,
                "truncated": len(full_text) > 50000,
                "original_text_length": len(full_text),
                "conversion_method": "pdf2image_screenshots" if images else "pymupdf_fallback"
            }
        )
        
        report = {
            "filename": file_path.name,
            "size_bytes": file_path.stat().st_size,
            "extension": ext,
            "status": "parsed",
            "extraction_method": extraction_method,
            "text_length": len(full_text),
            "image_count": len(images)
        }
        
        if progress_callback:
            progress_callback(f"Completed PDF processing: {len(images)} pages converted")
        
        return content, report
    
    # For other files, use standard processing
    if progress_callback:
        progress_callback(f"Parsing {file_path.name}...")
    
    return parse_file_with_report(file_path)
