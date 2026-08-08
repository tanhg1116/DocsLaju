from __future__ import annotations

import io
import hashlib
import time
import pyperclip
import streamlit as st
from PIL import Image, ImageGrab

from src.services.state import Session, OcrEntry, SessionFile, get_active_file
from src.services.cache import (
    read_memo_markdown,
    set_cached_markdown,
    invalidate_markdown,
)
from src.mistral_client import (
    ocr_pdf_pages_markdown,
    ocr_image_markdown,
)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:12]


def _load_session_file(session: Session, *, name: str, content: bytes, is_pdf: bool, force_rerun: bool = False) -> None:
    """Register a file with the session and make it active."""
    file_id = _hash_bytes(content)

    if file_id in session.files:
        if session.active_file_id != file_id:
            session.active_file_id = file_id
            st.toast(f"Switched to {name}")
            if force_rerun:
                st.rerun()
        return

    num_pages = 1
    if is_pdf:
        try:
            import PyPDF2  # type: ignore

            reader = PyPDF2.PdfReader(io.BytesIO(content))
            num_pages = len(reader.pages)
        except Exception:
            num_pages = 1

    session_file = SessionFile(
        file_id=file_id,
        name=name,
        bytes=content,
        is_pdf=is_pdf,
        num_pages=num_pages,
        current_page=1,
    )

    session.files[file_id] = session_file
    session.active_file_id = file_id
    st.toast(f"Loaded {name}")
    # Always rerun after loading a new file to update the UI
    st.rerun()


def _resolve_active_file(session: Session) -> SessionFile | None:
    active = get_active_file(session)
    if not active:
        return None
    session.active_file_id = active.file_id
    return active


def _extract_single_pdf_page(pdf_bytes: bytes, page_number: int) -> bytes:
    """Return a one-page PDF (1-based page_number) extracted from pdf_bytes."""
    import PyPDF2  # type: ignore

    reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
    total = len(reader.pages)
    idx = max(1, min(page_number, total)) - 1

    writer = PyPDF2.PdfWriter()
    writer.add_page(reader.pages[idx])
    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.read()


def _ocr_job_key(session_id: str, file_id: str) -> str:
    return f"{session_id}:{file_id}"


def _ensure_ocr_store() -> dict:
    return st.session_state.setdefault("ocr_jobs", {})


def _missing_pages(session_file: SessionFile) -> list[int]:
    total = max(1, session_file.num_pages if session_file.is_pdf else 1)
    return [p for p in range(1, total + 1) if p not in session_file.raw_edits]


def _save_ocr_page(session: Session, session_file: SessionFile, page_number: int, markdown: str, source: str = "api") -> None:
    page_key = (session.id, session_file.file_id, page_number)
    set_cached_markdown(page_key, markdown)
    session_file.ocr_cache[page_number] = OcrEntry(markdown=markdown, source=source, updated_at=None)  # type: ignore
    session_file.raw_edits[page_number] = markdown
    st.session_state[f"raw_{session.id}_{session_file.file_id}_{page_number}"] = markdown


def _start_streaming_ocr_job(
    session: Session,
    session_file: SessionFile,
    start_page: int | None = None,
    force: bool = False,
    single_page: bool = False,
) -> dict | None:
    total = max(1, session_file.num_pages if session_file.is_pdf else 1)
    if force:
        if single_page and start_page:
            pages_to_run = [start_page]
        else:
            pages_to_run = list(range(1, total + 1))
    else:
        missing = _missing_pages(session_file)
        if not missing:
            return None
        if start_page and start_page in missing:
            pages_to_run = [start_page] + [p for p in missing if p != start_page]
        else:
            pages_to_run = missing

    store = _ensure_ocr_store()
    job_id = _ocr_job_key(session.id, session_file.file_id)
    job = {
        "status": "running",
        "started_at": time.time(),
        "total_pages": total,
        "queue": pages_to_run,
        "done": set(),
        "error": None,
        "current": None,
    }
    store[job_id] = job
    return job


def _run_ocr_tick(session: Session, session_file: SessionFile, job: dict) -> None:
    if job.get("status") != "running":
        return

    queue = job.get("queue") or []
    if not queue:
        job["status"] = "done"
        return

    page_number = int(queue[0])
    job["current"] = page_number

    try:
        if session_file.is_pdf:
            single_page_pdf = _extract_single_pdf_page(session_file.bytes, page_number)
            pages_md = ocr_pdf_pages_markdown(single_page_pdf)
            markdown = pages_md[0] if pages_md else ""
        else:
            markdown = ocr_image_markdown(session_file.bytes)

        _save_ocr_page(session, session_file, page_number, markdown, source="api")
        queue.pop(0)
        job["done"].add(page_number)
        if not queue:
            job["status"] = "done"
            job["current"] = None
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
        job["current"] = None


def render_main_panes(session: Session, active_session_id: str = None, executor = None) -> None:
    # Three-column layout: Screenshot | Rendered MD | Raw MD Editor
    col_screenshot, col_rendered, col_raw = st.columns([1, 1, 1])
    
    # Get active file first
    session_files = list(session.files.values())
    active_file = _resolve_active_file(session)
    
    # Column 1: Screenshot/Image viewer
    with col_screenshot:
        st.markdown("### 📄 Document")
        
        if active_file:
            if active_file.is_pdf:
                # Ensure num_pages is accurate
                if not active_file.num_pages or active_file.num_pages <= 1:
                    try:
                        import fitz  # type: ignore
                        doc = fitz.open(stream=active_file.bytes, filetype="pdf")
                        active_file.num_pages = len(doc)
                        doc.close()
                    except Exception:
                        try:
                            import PyPDF2  # type: ignore
                            reader = PyPDF2.PdfReader(io.BytesIO(active_file.bytes))
                            active_file.num_pages = len(reader.pages)
                        except Exception:
                            active_file.num_pages = active_file.num_pages or 1
                
                # Clamp current_page
                clamped = max(1, min(active_file.current_page or 1, active_file.num_pages or 1))
                if clamped != active_file.current_page:
                    active_file.current_page = clamped

                # Render PDF with fixed height
                from src.components.pdf_viewer.viewer import render_pdf
                render_pdf(active_file)
                
                # Navigation controls below
                c1, c2, c3 = st.columns([1, 2, 1])
                with c1:
                    if st.button("◀", disabled=active_file.current_page <= 1, help="Previous", use_container_width=True):
                        active_file.current_page = max(1, active_file.current_page - 1)
                        st.rerun()
                with c2:
                    page = st.number_input(
                        "Page",
                        min_value=1,
                        max_value=max(1, active_file.num_pages or 1),
                        value=int(active_file.current_page),
                        step=1,
                        key=f"page_{session.id}_{active_file.file_id}",
                        label_visibility="collapsed",
                        help=f"Page {active_file.current_page} of {active_file.num_pages}"
                    )
                    if page != active_file.current_page:
                        active_file.current_page = int(page)
                        st.rerun()
                with c3:
                    if st.button("▶", disabled=active_file.current_page >= (active_file.num_pages or 1), help="Next", use_container_width=True):
                        active_file.current_page = active_file.current_page + 1
                        st.rerun()
            else:
                st.image(active_file.bytes, use_container_width=True)
        
        # File selector
        if session_files:
            option_ids = [f.file_id for f in session_files]
            selected_idx = 0
            if session.active_file_id in option_ids:
                selected_idx = option_ids.index(session.active_file_id)
            else:
                session.active_file_id = option_ids[0]
            label_map = {f.file_id: f.name for f in session_files}
            chosen_id = st.selectbox(
                "Files",
                options=option_ids,
                index=selected_idx,
                format_func=lambda fid: label_map[fid],
                key=f"file_picker_{session.id}",
                label_visibility="collapsed"
            )
            # Update active file and force rerun if changed
            if chosen_id != session.active_file_id:
                session.active_file_id = chosen_id
                st.rerun()
        
        # Upload controls
        uploaded = st.file_uploader(
            label="Upload", 
            type=["pdf", "png", "jpg", "jpeg"], 
            key=f"u_{session.id}",
            label_visibility="collapsed"
        )
        if uploaded is not None:
            content = uploaded.getvalue()
            file_id = _hash_bytes(content)
            
            # Track the last processed upload to prevent re-processing
            last_upload_key = f"last_upload_{session.id}"
            last_upload_id = st.session_state.get(last_upload_key)
            
            # Only process if this is a different upload than last time
            if last_upload_id != file_id:
                st.session_state[last_upload_key] = file_id
                
                if file_id not in session.files:
                    # New file - load it
                    is_pdf = uploaded.type == "application/pdf"
                    _load_session_file(session, name=uploaded.name, content=content, is_pdf=is_pdf, force_rerun=False)
                elif session.active_file_id != file_id:
                    # Existing file but not active - switch to it
                    session.active_file_id = file_id
                    st.toast(f"Switched to {uploaded.name}")
        
        # Paste button
        paste_disabled = ImageGrab is None or Image is None
        if st.button("Paste from clipboard", disabled=paste_disabled, use_container_width=True):
            try:
                grabbed = ImageGrab.grabclipboard()  # type: ignore
            except Exception as exc:
                st.warning(f"Clipboard access failed: {exc}")
                grabbed = None

            pil_image = None
            if isinstance(grabbed, Image.Image):
                pil_image = grabbed.copy()
            elif isinstance(grabbed, list):
                for item in grabbed:
                    if isinstance(item, str):
                        try:
                            with Image.open(item) as opened:  # type: ignore
                                pil_image = opened.copy()
                            break
                        except Exception:
                            continue

            if pil_image is None:
                st.warning("Clipboard does not contain an image.")
            else:
                buffer = io.BytesIO()
                if pil_image.mode not in ("RGB", "RGBA"):
                    pil_image = pil_image.convert("RGBA")
                pil_image.save(buffer, format="PNG")
                buffer.seek(0)
                clipboard_bytes = buffer.read()
                name = f"clipboard-{time.strftime('%Y%m%d-%H%M%S')}.png"
                _load_session_file(session, name=name, content=clipboard_bytes, is_pdf=False, force_rerun=True)

    # Calculate current file/page info AFTER left column navigation has been processed
    if not active_file:
        with col_rendered:
            st.markdown("### 📝 Rendered Markdown")
            st.info("Upload a document to begin.")
        with col_raw:
            st.markdown("### ✏️ Raw Markdown Editor")
            st.info("Upload a document to begin.")
        return
    
    file_id = active_file.file_id
    current_page = active_file.current_page if active_file.is_pdf else 1
    key = (session.id, file_id, current_page)
    raw_key = f"raw_{session.id}_{file_id}_{current_page}"

    # Column 2: Rendered Markdown preview
    with col_rendered:
        # Display page number info for PDFs
        if active_file.is_pdf:
            st.markdown(f"### 📝 Rendered Markdown - Page {current_page} of {active_file.num_pages}")
        else:
            st.markdown("### 📝 Rendered Markdown")

        # OCR job state (single deterministic queue per file)
        ocr_jobs = _ensure_ocr_store()
        job_id = _ocr_job_key(session.id, file_id)

        md_text = active_file.raw_edits.get(current_page)
        if md_text is None:
            cached = read_memo_markdown(key)
            if cached is not None:
                md_text = cached
                active_file.ocr_cache[current_page] = OcrEntry(markdown=cached, source="cache", updated_at=None)  # type: ignore
                active_file.raw_edits[current_page] = cached
                notify_key = f"cache_notified_{session.id}_{file_id}_{current_page}"
                if not session.ui.get(notify_key):
                    st.toast("Cache hit")
                    session.ui[notify_key] = True
                st.session_state[raw_key] = cached
            else:
                job = ocr_jobs.get(job_id)
                if not job or job.get("status") in {"done", "cancelled", "error"}:
                    job = _start_streaming_ocr_job(session, active_file, start_page=current_page, force=False)

                if job and job.get("status") == "running":
                    _run_ocr_tick(session, active_file, job)
                    md_text = active_file.raw_edits.get(current_page, "")

                if job and job.get("status") == "error":
                    st.error(f"OCR failed: {job.get('error')}")
                    md_text = ""

        # Rendered preview with scrollbar
        display_text = st.session_state.get(raw_key, md_text or "")
        rendered_container = st.container(height=500, border=True)
        with rendered_container:
            if display_text:
                st.markdown(display_text)
            else:
                st.caption("No content yet")

        job = ocr_jobs.get(job_id)
        if job and job.get("status") == "running":
            done_count = len(job.get("done") or set())
            total_pages = int(job.get("total_pages") or 1)
            current_processing = job.get("current")
            elapsed = int(time.time() - float(job.get("started_at") or time.time()))
            status_msg = f"OCR streaming: {done_count}/{total_pages} pages done"
            if current_processing:
                status_msg += f". Processing page {current_processing}"
            status_msg += f" ({elapsed}s elapsed)"
            st.info(status_msg)
            st.progress(min(1.0, done_count / max(1, total_pages)))

            if st.button("Stop OCR", key=f"stop_ocr_{session.id}_{file_id}", use_container_width=True):
                job["status"] = "cancelled"
                st.toast("OCR stopped")
                st.rerun()

            # Keep processing remaining pages one tick at a time.
            if (job.get("queue") or []) and job.get("status") == "running":
                st.rerun()
        elif job and job.get("status") == "done":
            st.success("OCR complete for this file")
        elif job and job.get("status") == "cancelled":
            st.warning("OCR stopped. Press resume to continue remaining pages.")
            if st.button("Resume OCR", key=f"resume_ocr_{session.id}_{file_id}", use_container_width=True):
                resumed = _start_streaming_ocr_job(session, active_file, start_page=current_page, force=False)
                if resumed:
                    st.rerun()
                else:
                    st.toast("No remaining pages to OCR")
        
        # Re-OCR button
        running = (st.session_state.get("ocr_jobs", {}).get(job_id, {}).get("status") == "running")
        if st.button("Re-OCR this page", disabled=running, use_container_width=True):
            invalidate_markdown(key)
            active_file.raw_edits.pop(current_page, None)
            active_file.ocr_cache.pop(current_page, None)
            _start_streaming_ocr_job(session, active_file, start_page=current_page, force=True, single_page=True)
            st.rerun()

        if active_file.is_pdf and st.button("Re-OCR all pages", disabled=running, use_container_width=True):
            for page_num in range(1, max(1, active_file.num_pages) + 1):
                invalidate_markdown((session.id, file_id, page_num))
            active_file.raw_edits.clear()
            active_file.ocr_cache.clear()
            _start_streaming_ocr_job(session, active_file, start_page=current_page, force=True, single_page=False)
            st.rerun()
        
        # Export button
        if st.button("Export", use_container_width=True):
            st.session_state[f"show_export_dialog_{active_session_id}"] = True
        
        # Export dialog modal
        if st.session_state.get(f"show_export_dialog_{active_session_id}", False):
            with st.container(border=True):
                st.markdown("#### Export Document")
                
                # Check if export is running
                from src.services.exporter import get_job_status, cancel_export_job, start_export_job
                job = get_job_status(active_session_id) if active_session_id else None
                
                if job and job.status in {"running", "queued"}:
                    # Show progress and cancel button
                    st.info(f"Export in progress: {job.progress}%")
                    st.progress(job.progress / 100.0)
                    if st.button("Cancel Export", key=f"cancel_export_modal_{active_session_id}", use_container_width=True):
                        cancel_export_job(active_session_id)
                        st.toast("Export cancelled")
                        st.rerun()
                    # Gentle polling
                    time.sleep(1.5)
                    st.rerun()
                elif job and job.status == "done":
                    # Show download button
                    st.success("✅ Export complete!")
                    file_name = job.output_name
                    if job.output_bytes is not None:
                        if job.format == "pdf":
                            mime = "application/pdf"
                        elif job.format == "html":
                            mime = "text/html"
                        else:
                            mime = "text/markdown"
                        
                        # Show PDF conversion instructions for HTML exports
                        if job.format == "html":
                            st.info("💡 **To convert HTML to PDF:** Open the downloaded HTML file in any browser → Print → Save as PDF")
                        
                        st.download_button(
                            label=f"""📥 Download "{file_name}".""",
                            data=job.output_bytes,
                            file_name=file_name,
                            mime=mime,
                            use_container_width=True,
                            key=f"dl_modal_{active_session_id}",
                        )
                    if st.button("Close", key=f"close_export_done_{active_session_id}", use_container_width=True):
                        # Clear the job status and close dialog
                        from src.services.exporter import clear_export_job
                        clear_export_job(active_session_id)
                        st.session_state[f"show_export_dialog_{active_session_id}"] = False
                        st.rerun()
                else:
                    # Show export form
                    _num_pages = active_file.num_pages or 1
                    _col_start, _col_end = st.columns(2)
                    with _col_start:
                        export_start = st.number_input(
                            "Start page",
                            min_value=1,
                            max_value=_num_pages,
                            value=1,
                            step=1,
                            key=f"export_start_{active_session_id}",
                        )
                    with _col_end:
                        export_end = st.number_input(
                            "End page",
                            min_value=1,
                            max_value=_num_pages,
                            value=_num_pages,
                            step=1,
                            key=f"export_end_{active_session_id}",
                        )
                    page_range = f"{int(export_start)}-{int(export_end)}"

                    export_format = st.radio(
                        "Export format",
                        options=["PDF", "HTML", "Markdown"],
                        key=f"export_format_modal_{active_session_id}",
                        horizontal=True
                    )
                    
                    # Warning for PDF export with LaTeX equations
                    if export_format == "PDF":
                        st.warning("⚠️ **Note:** PDF export does not support LaTeX equations. If your content contains equations, export as HTML or Markdown instead. For HTML, you can then use Print → Save as PDF in your browser.")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("Start Export", key=f"start_export_modal_{active_session_id}", use_container_width=True):
                            if export_format == "PDF":
                                fmt = "pdf"
                            elif export_format == "HTML":
                                fmt = "html"
                            else:
                                fmt = "md"
                            try:
                                start_export_job(executor, active_session_id, page_range, fmt)
                                st.toast(f"Started {fmt.upper()} export")
                                st.rerun()
                            except ValueError as exc:
                                st.error(str(exc))
                    with col2:
                        if st.button("Cancel", key=f"cancel_dialog_{active_session_id}", use_container_width=True):
                            st.session_state[f"show_export_dialog_{active_session_id}"] = False
                            st.rerun()

    # Column 3: Raw Markdown Editor
    with col_raw:
        st.markdown("### ✏️ Raw Markdown Editor")
        
        new_md = st.text_area(
            "Edit markdown",
            value=st.session_state.get(raw_key, md_text or ""),
            height=500,
            key=raw_key,
            label_visibility="collapsed",
        )
        if new_md != md_text:
            active_file.raw_edits[current_page] = new_md
        
        # Copy button
        if st.button("Copy", use_container_width=True):
            txt = active_file.raw_edits.get(current_page, md_text or "")
            if pyperclip is None:
                st.warning("Clipboard support unavailable")
            else:
                try:
                    pyperclip.copy(txt)
                    st.toast("Copied to clipboard")
                except Exception as exc:
                    st.error(f"Copy failed: {exc}")


