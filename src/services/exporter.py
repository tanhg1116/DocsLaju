from __future__ import annotations

import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional

import streamlit as st
from openai import OpenAI
import nbformat
from nbformat.v4 import new_notebook, new_markdown_cell
from nbconvert import HTMLExporter
from markdown_pdf import MarkdownPdf, Section

from .state import ExportJob, OcrEntry, Session, SessionFile, get_active_file
from .cache import read_memo_markdown, set_cached_markdown
from ..mistral_client import ocr_pdf_pages_markdown, ocr_image_markdown
from ..utils.range_parse import parse_page_range

# Limit concurrent OCR API calls from export jobs (rate-limit guard)
_OCR_SEMAPHORE = threading.Semaphore(1)
_RATE_LIMIT_DELAY = 1.0  # seconds to hold the semaphore after each OCR call


class Md2Pdf(MarkdownPdf):
    """Custom MarkdownPdf class for converting markdown content to PDF."""
    def __init__(self, *args, **kwargs):
        super(Md2Pdf, self).__init__(*args, **kwargs)
    
    @property
    def content(self) -> str:
        return self._content
    
    @content.setter
    def content(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError('content must be of type string')
        self._content = value
    
    def set_content(self, content: str) -> None:
        self._content = content
    
    def makePdf(self, outFileName: str) -> None:
        self.add_section(Section(self._content))
        self.save(f'{outFileName}.pdf')


def _generate_filename_with_openai(content: str) -> Optional[str]:
    """Generate a descriptive filename using OpenAI based on content."""
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        
        client = OpenAI(api_key=api_key)
        
        prompt = f"""You are given the full text content of a file. Assign a short and precise file name that represents the main purpose or topic of the content. Follow these rules:

Identify the central subject or function of the content.

Produce a filename of 3 to 7 words.

Use lowercase and hyphens.

Avoid dates unless explicitly part of the topic.

Do not include subjective adjectives.

Output only the filename with no explanation.

Do not assume the region of interest of the content. Keep your name as general as possible. To better understand the meaning of this sentence, please refer to the following example. For example, the following shows a table of courses with course codes  and names.
Example Input:
|  Course | Course Title  |
| --- | --- |
|  EE6008 | Collaborative Research and Development Project  |
|  EE6010 | Project Management & Technopreneurship  |
|  EE6111 | 5G Communication & Beyond  |
|  EE6102 | Cyber Security and Blockchain Technology  |
|  EE6223 | Computer Control Networks  |
|  EE6228 | Process Modeling and Scheduling  |
|  EE6285 | Computational Intelligence  |
|  EE6301 | Smart Biosensors and Systems for Healthcare  |

When naming, assigning a name, even though you know that they are courses from Electrical Engineering because you saw 'EE' but you should consider "course-list" instead of  "electrical-engineering-course-list".
Example Output: course-list 

Now you should assign a name to the following input only by adhering to every given instruction. 
Input:
{content}

Output: 
<representative-filename>"""
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that generates concise, descriptive filenames based on content."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_completion_tokens=50
        )
        
        filename = response.choices[0].message.content.strip()
        # Clean up the filename - remove any markdown tags, quotes, or extra whitespace
        filename = filename.replace("<representative-filename>", "").replace("</representative-filename>", "")
        filename = filename.strip().strip('"').strip("'")
        
        # Validate filename format (should contain hyphens and be lowercase)
        if filename and "-" in filename and filename.islower():
            return filename
        
        return None
    except Exception as e:
        # If OpenAI fails, return None and fall back to default naming
        import traceback
        print(f"OpenAI filename generation failed: {e}")
        print(traceback.format_exc())
        return None


def _ocr_missing_pages(session: Session, session_file: SessionFile, page_numbers: List[int], job: ExportJob) -> None:
    """Ensure every requested page has been OCR-processed and is available in memory.

    Mistral OCR sends the whole PDF in one API call and returns all pages at once,
    so a single request covers any number of missing pages.  A semaphore prevents
    simultaneous export jobs from hitting the API concurrently (rate-limit guard).
    """
    missing = [
        p for p in page_numbers
        if p not in session_file.raw_edits and p not in session_file.ocr_cache
    ]
    # Resolve any that are in the memo/disk cache but not yet loaded into memory
    still_missing = []
    for p in missing:
        key = (session.id, session_file.file_id, p)
        cached = read_memo_markdown(key)
        if cached is not None:
            session_file.ocr_cache[p] = OcrEntry(markdown=cached, source="cache", updated_at=None)
            session_file.raw_edits[p] = cached
        else:
            still_missing.append(p)

    if not still_missing or job.cancel_flag:
        return

    job.stage = "OCR: fetching missing pages"
    with _OCR_SEMAPHORE:
        if session_file.is_pdf:
            # Mistral OCR processes the entire PDF in one request — all pages return at once.
            # No per-page threading needed; the semaphore + sleep handles rate limiting.
            all_pages_md = ocr_pdf_pages_markdown(session_file.bytes)
            for page_num, page_md in enumerate(all_pages_md, start=1):
                key = (session.id, session_file.file_id, page_num)
                set_cached_markdown(key, page_md)
                session_file.ocr_cache[page_num] = OcrEntry(markdown=page_md, source="api", updated_at=None)
                if page_num not in session_file.raw_edits:
                    session_file.raw_edits[page_num] = page_md
            if not session_file.num_pages or session_file.num_pages < len(all_pages_md):
                session_file.num_pages = len(all_pages_md)
        else:
            page_md = ocr_image_markdown(session_file.bytes)
            key = (session.id, session_file.file_id, 1)
            set_cached_markdown(key, page_md)
            session_file.ocr_cache[1] = OcrEntry(markdown=page_md, source="api", updated_at=None)
            if 1 not in session_file.raw_edits:
                session_file.raw_edits[1] = page_md
        time.sleep(_RATE_LIMIT_DELAY)


def _collect_pages_markdown(session: Session, session_file: SessionFile, page_numbers: List[int], job: ExportJob) -> List[str]:
    _ocr_missing_pages(session, session_file, page_numbers, job)
    job.stage = "Collecting pages"
    pages_md: List[str] = []
    for i, p in enumerate(page_numbers, start=1):
        md = session_file.raw_edits.get(p)
        if md is None:
            cache_entry = session_file.ocr_cache.get(p)
            md = cache_entry.markdown if cache_entry else ""
        pages_md.append(md or "")
        job.progress = int(10 + 40 * i / max(1, len(page_numbers)))
        if job.cancel_flag:
            break
    return pages_md


def _build_html_document(session: Session, session_file: SessionFile, page_numbers: List[int], job: ExportJob) -> bytes:
    """Build HTML document using nbconvert from markdown cells."""
    job.stage = "Rendering HTML"
    pages_md = _collect_pages_markdown(session, session_file, page_numbers, job)
    
    # Create notebook cells from markdown pages
    cells = []
    for idx, (md, page_num) in enumerate(zip(pages_md, page_numbers), start=1):
        # Add page header if input is PDF
        if session_file.is_pdf:
            # Add horizontal line before page header for all pages except the first
            if idx > 1:
                cell_content = f"---\n\n# Page {page_num}\n\n{md}"
            else:
                cell_content = f"# Page {page_num}\n\n{md}"
        else:
            cell_content = md
        cells.append(new_markdown_cell(cell_content))
        job.progress = int(50 + 30 * idx / max(1, len(pages_md)))
        if job.cancel_flag:
            return b""
    
    # Create in-memory notebook object
    nb = new_notebook(cells=cells)
    
    # Convert notebook to HTML
    exporter = HTMLExporter()
    body, resources = exporter.from_notebook_node(nb)
    
    return body.encode("utf-8")


def _markdown_to_pdf_bytes(pages_md: List[str], page_numbers: List[int], is_pdf_input: bool) -> bytes:
    """Convert markdown content to PDF using markdown-pdf library."""
    import tempfile
    import os
    
    # Add page headers if input is PDF
    if is_pdf_input:
        pages_with_headers = []
        for i, (md, page_num) in enumerate(zip(pages_md, page_numbers)):
            pages_with_headers.append(f"# Page {page_num}\n\n{md}")
        combined_md = "\n\n---\n\n".join(pages_with_headers)
    else:
        # Combine all markdown pages with page breaks
        combined_md = "\n\n---\n\n".join(pages_md)
    
    # Create temporary file for PDF output
    with tempfile.NamedTemporaryFile(mode='w', suffix='.pdf', delete=False) as tmp_file:
        temp_pdf_path = tmp_file.name
    
    try:
        # Remove .pdf extension from temp path as makePdf adds it
        temp_base = temp_pdf_path[:-4] if temp_pdf_path.endswith('.pdf') else temp_pdf_path
        
        # Generate PDF
        md2pdf = Md2Pdf()
        md2pdf.set_content(combined_md)
        md2pdf.makePdf(temp_base)
        
        # Read the generated PDF
        actual_pdf_path = f"{temp_base}.pdf"
        with open(actual_pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        
        # Clean up temporary file
        try:
            os.unlink(actual_pdf_path)
        except:
            pass
        
        return pdf_bytes
    finally:
        # Clean up any remaining temp files
        try:
            if os.path.exists(temp_pdf_path):
                os.unlink(temp_pdf_path)
        except:
            pass


def start_export_job(executor: ThreadPoolExecutor, session_id: str, range_text: str, fmt: str) -> str:
    sessions: Dict[str, Session] = st.session_state["sessions"]
    session = sessions[session_id]
    session_file = get_active_file(session)
    if not session_file:
        raise ValueError("No active file to export")

    job = ExportJob(session_id=session_id, format=fmt, status="queued", progress=0, stage="queued")
    st.session_state.setdefault("jobs", {})[session_id] = job
    job_id = f"job-{session_id}-{int(datetime.now().timestamp())}"

    def _run():
        job.status = "running"
        try:
            target_file = session.files.get(session_file.file_id, session_file)
            total_pages = target_file.num_pages or 1
            pages = (
                parse_page_range(range_text, total_pages=total_pages)
                if range_text.strip().lower() != "all"
                else list(range(1, total_pages + 1))
            )
            if not pages:
                raise ValueError("No pages to export")
            if job.cancel_flag:
                job.status = "cancelled"
                return

            if fmt == "pdf":
                job.stage = "Converting to PDF"
                md_pages = _collect_pages_markdown(session, target_file, pages, job)
                if job.cancel_flag:
                    job.status = "cancelled"
                    return
                
                try:
                    pdf_bytes = _markdown_to_pdf_bytes(md_pages, pages, target_file.is_pdf)
                    job.output_bytes = pdf_bytes
                    
                    # Generate intelligent filename using OpenAI
                    job.stage = "Generating filename"
                    content = "\n\n".join(md_pages)[:8000]  # Limit content to ~8k chars
                    smart_name = _generate_filename_with_openai(content)
                    
                    print(f"Generated filename: {smart_name}")
                    if smart_name:
                        job.output_name = f"{smart_name}.pdf"
                        print(f"Using smart filename: {job.output_name}")
                    else:
                        # Fallback to default naming
                        job.output_name = f"{session.title.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                        print(f"Using fallback filename: {job.output_name}")
                except Exception as e:
                    job.error = str(e)
                    job.status = "error"
                    return
            elif fmt == "html":
                html_bytes = _build_html_document(session, target_file, pages, job)
                if job.cancel_flag:
                    job.status = "cancelled"
                    return
                
                job.output_bytes = html_bytes
                
                # Generate intelligent filename using OpenAI
                job.stage = "Generating filename"
                md_pages = _collect_pages_markdown(session, target_file, pages, job)
                content = "\n\n".join(md_pages)[:8000]  # Limit content to ~8k chars
                smart_name = _generate_filename_with_openai(content)
                
                print(f"Generated filename: {smart_name}")
                if smart_name:
                    job.output_name = f"{smart_name}.html"
                    print(f"Using smart filename: {job.output_name}")
                else:
                    # Fallback to default naming
                    job.output_name = f"{session.title.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                    print(f"Using fallback filename: {job.output_name}")
            else:
                # md export: concatenate with headers
                job.stage = "Preparing Markdown"
                md_pages = _collect_pages_markdown(session, target_file, pages, job)
                combined = []
                for i, md in enumerate(md_pages, start=1):
                    # Always add page headers for markdown export (whether PDF or image input)
                    if target_file.is_pdf:
                        combined.append(f"# Page {pages[i-1]}\n\n{md}\n")
                    else:
                        combined.append(md)
                job.output_bytes = "\n\n".join(combined).encode("utf-8")
                
                # Generate intelligent filename using OpenAI
                job.stage = "Generating filename"
                content = "\n\n".join(md_pages)[:8000]  # Limit content to ~8k chars
                smart_name = _generate_filename_with_openai(content)
                
                print(f"Generated filename: {smart_name}")
                if smart_name:
                    job.output_name = f"{smart_name}.md"
                    print(f"Using smart filename: {job.output_name}")
                else:
                    # Fallback to default naming
                    job.output_name = f"{session.title.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
                    print(f"Using fallback filename: {job.output_name}")

            if not job.cancel_flag:
                job.progress = 100
                job.stage = "Ready"
                job.status = "done"
                
                # Save to session export history
                from .state import ExportedItem
                import uuid
                export_id = str(uuid.uuid4())
                exported_item = ExportedItem(
                    id=export_id,
                    name=job.output_name,
                    format=fmt,
                    content=job.output_bytes,
                    created_at=datetime.now()
                )
                session.exports[export_id] = exported_item
            else:
                job.status = "cancelled"
        except Exception as e:
            job.error = str(e)
            job.status = "error"

    executor.submit(_run)
    return job_id


def get_job_status(session_id: str) -> Optional[ExportJob]:
    return st.session_state.get("jobs", {}).get(session_id)


def cancel_export_job(session_id: str) -> None:
    job = st.session_state.get("jobs", {}).get(session_id)
    if job:
        job.cancel_flag = True


def clear_export_job(session_id: str) -> None:
    """Remove the completed export job from session state."""
    jobs = st.session_state.get("jobs", {})
    if session_id in jobs:
        jobs.pop(session_id)
