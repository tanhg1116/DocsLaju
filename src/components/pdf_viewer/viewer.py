from __future__ import annotations

import streamlit as st

from src.services.state import SessionFile


def render_pdf(file_state: SessionFile) -> None:
    """Render a screenshot of the current PDF page (non-highlightable).

    Uses PyMuPDF (fitz) if available to rasterize the page to an image.
    """
    file_bytes = file_state.bytes
    if not file_bytes:
        st.info("No PDF loaded")
        return

    try:
        import fitz  # PyMuPDF
    except Exception:  # pragma: no cover - environment-dependent
        st.warning(
            "PDF preview requires PyMuPDF. Please install the 'PyMuPDF' package to enable image preview."
        )
        return

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as e:  # pragma: no cover
        st.error(f"Failed to open PDF: {e}")
        return

    try:
        total = len(doc)
        # Keep file_state.num_pages in sync with the actual PDF page count.
        if file_state.num_pages != total:
            file_state.num_pages = total
            if (file_state.current_page or 1) > total:
                file_state.current_page = total
        # Clamp page index
        page_num = max(1, min(file_state.current_page or 1, total))
        page = doc[page_num - 1]

        # Render with 2x zoom for clarity
        zoom = 2.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")

        st.image(img_bytes, caption=f"Page {page_num} of {total}", use_container_width=True)
    except Exception as e:  # pragma: no cover
        st.error(f"Failed to render page: {e}")
    finally:
        try:
            doc.close()
        except Exception:
            pass
