from __future__ import annotations

import io
import zipfile
from typing import Dict

import streamlit as st

from src.services.state import create_session, delete_session, duplicate_session, get_active_session_id, set_active, Session


def _render_items_dialog(sid: str, session: Session) -> None:
    """Render the exported items dialog for a session."""
    # Create a modal-like container in the main area using session state
    st.markdown(f"### 📂 Exported Items - {session.title}")
    
    with st.container(border=True):
        if not session.exports:
            st.info("No exported items yet.")
            if st.button("Close", key=f"close_empty_items_{sid}"):
                st.session_state[f"show_items_dialog_{sid}"] = False
                st.rerun()
            return
        
        # Initialize selection state
        selection_key = f"items_selection_{sid}"
        if selection_key not in st.session_state:
            st.session_state[selection_key] = set()
        
        st.markdown(f"**{len(session.exports)} items**")
        
        # Collect current selections from checkboxes
        current_selections = set()
        
        # List all exports
        for export_id, item in list(session.exports.items()):
            cols = st.columns([0.5, 4, 1, 1, 1])
            
            with cols[0]:
                # Checkbox for selection - remove value parameter to let it be stateful
                checkbox_key = f"select_{sid}_{export_id}"
                checked = st.checkbox("", key=checkbox_key, label_visibility="collapsed")
                if checked:
                    current_selections.add(export_id)
            
            with cols[1]:
                st.text(f"📄 {item.name}")
                st.caption(f"{item.format.upper()} • {item.created_at.strftime('%Y-%m-%d %H:%M')}")
            
            with cols[2]:
                if st.button("View", key=f"view_{sid}_{export_id}", help="View content"):
                    st.session_state[f"viewing_{sid}_{export_id}"] = True
                    st.rerun()
            
            with cols[3]:
                if item.format == "pdf":
                    mime = "application/pdf"
                elif item.format == "html":
                    mime = "text/html"
                elif item.format == "md":
                    mime = "text/markdown"
                else:
                    mime = "application/octet-stream"
                st.download_button(
                    label="Download",
                    data=item.content,
                    file_name=item.name,
                    mime=mime,
                    key=f"download_{sid}_{export_id}",
                    help="Download"
                )
            
            with cols[4]:
                if st.button("Delete", key=f"delete_item_{sid}_{export_id}", help="Delete"):
                    session.exports.pop(export_id, None)
                    # Clear checkbox state for deleted item
                    checkbox_key = f"select_{sid}_{export_id}"
                    if checkbox_key in st.session_state:
                        del st.session_state[checkbox_key]
                    st.rerun()
            
            # View content dialog
            if st.session_state.get(f"viewing_{sid}_{export_id}", False):
                with st.expander(f"Viewing: {item.name}", expanded=True):
                    if item.format == "md":
                        st.markdown(item.content.decode("utf-8"))
                    else:
                        st.info("PDF preview not available. Use download button to view.")
                    if st.button("Close Preview", key=f"close_view_{sid}_{export_id}"):
                        st.session_state[f"viewing_{sid}_{export_id}"] = False
                        st.rerun()
        
        st.divider()
        
        # Batch actions - use current selections from checkboxes
        selected_count = len(current_selections)
        col1, col2, col3 = st.columns([2, 2, 2])
        
        with col1:
            # Create zip file in memory
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for export_id in current_selections:
                    if export_id in session.exports:
                        item = session.exports[export_id]
                        zip_file.writestr(item.name, item.content)
            
            zip_buffer.seek(0)
            st.download_button(
                label=f"⬇ Download Selected ({selected_count})",
                data=zip_buffer.getvalue(),
                file_name=f"{session.title.replace(' ', '_')}_exports.zip",
                mime="application/zip",
                key=f"download_zip_{sid}",
                disabled=selected_count == 0,
                use_container_width=True
            )
        
        with col2:
            if st.button(f"🗑 Delete Selected ({selected_count})",
                        disabled=selected_count == 0,
                        key=f"delete_batch_{sid}",
                        use_container_width=True):
                for export_id in current_selections:
                    session.exports.pop(export_id, None)
                    # Clear checkbox state
                    checkbox_key = f"select_{sid}_{export_id}"
                    if checkbox_key in st.session_state:
                        del st.session_state[checkbox_key]
                st.rerun()
        
        with col3:
            if st.button("Close", key=f"close_items_{sid}", use_container_width=True):
                st.session_state[f"show_items_dialog_{sid}"] = False
                # Clear all checkbox states
                for export_id in session.exports.keys():
                    checkbox_key = f"select_{sid}_{export_id}"
                    if checkbox_key in st.session_state:
                        del st.session_state[checkbox_key]
                st.rerun()


def render_session_sidebar() -> str:
    sessions: Dict[str, Session] = st.session_state["sessions"]

    with st.sidebar:
        st.header("Sessions")
        if st.button("New OCR Session", use_container_width=True):
            create_session()

        for sid, s in list(sessions.items()):
            # Track rename mode per-session
            rename_flag_key = f"rename_mode_{sid}"
            if rename_flag_key not in st.session_state:
                st.session_state[rename_flag_key] = False

            cols = st.columns([8, 1])
            with cols[0]:
                if st.session_state[rename_flag_key]:
                    # Wide, readable input; submit with Enter; cancel when empty or on blur via an auxiliary button
                    new = st.text_input(
                        "",
                        value=s.title,
                        key=f"rename_input_{sid}",
                        label_visibility="collapsed",
                        placeholder="Rename session",
                    )
                    c_a, c_b = st.columns([1, 1])
                    with c_a:
                        if st.button("Save", key=f"rename_save_{sid}"):
                            s.title = new.strip() or s.title
                            st.session_state[rename_flag_key] = False
                    with c_b:
                        if st.button("Cancel", key=f"rename_cancel_{sid}"):
                            st.session_state[rename_flag_key] = False
                else:
                    label = s.title
                    if st.session_state.get("active_session_id") == sid:
                        label = f"▶ {label}"
                    if st.button(label, key=f"select_{sid}", use_container_width=True):
                        # Close any open items dialogs
                        for session_id in sessions.keys():
                            if f"show_items_dialog_{session_id}" in st.session_state:
                                st.session_state[f"show_items_dialog_{session_id}"] = False
                        set_active(sid)
                        st.rerun()
            with cols[1]:
                with st.popover("⋮"):
                    if st.button("📂 Items", key=f"items_{sid}", use_container_width=True):
                        st.session_state[f"show_items_dialog_{sid}"] = True
                        st.rerun()
                    if st.button("✎ Rename", key=f"rename_{sid}", use_container_width=True):
                        st.session_state[rename_flag_key] = True
                        st.rerun()
                    if st.button("⎘ Duplicate", key=f"dup_{sid}", use_container_width=True):
                        duplicate_session(sid)
                        st.rerun()
                    if st.button("🗑 Delete", key=f"del_{sid}", use_container_width=True):
                        delete_session(sid)
                        st.rerun()

    return get_active_session_id()


def render_items_dialog_if_open(sessions: Dict[str, Session]) -> None:
    """Render items dialog in main area if any session has it open."""
    for sid, s in list(sessions.items()):
        if st.session_state.get(f"show_items_dialog_{sid}", False):
            _render_items_dialog(sid, s)
            return  # Only show one dialog at a time
