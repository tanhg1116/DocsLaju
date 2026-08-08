from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Any

import streamlit as st


@dataclass
class OcrEntry:
    markdown: str
    source: str  # "cache" | "api"
    updated_at: Optional[datetime]


@dataclass
class ExportedItem:
    """Represents a completed export saved in session history."""
    id: str
    name: str
    format: str  # 'pdf' | 'md'
    content: bytes
    created_at: datetime


@dataclass
class Session:
    id: str
    title: str
    files: Dict[str, "SessionFile"] = field(default_factory=dict)
    active_file_id: Optional[str] = None
    ui: Dict[str, Any] = field(default_factory=dict)  # cross-file UI flags
    exports: Dict[str, "ExportedItem"] = field(default_factory=dict)  # export history


@dataclass
class ExportJob:
    session_id: str
    format: str  # 'pdf' | 'md'
    status: str  # 'queued'|'running'|'done'|'error'|'cancelled'
    progress: int = 0
    stage: str = "queued"
    output_name: str = ""
    output_bytes: Optional[bytes] = None
    error: Optional[str] = None
    cancel_flag: bool = False


def ensure_app_state() -> None:
    if "sessions" not in st.session_state:
        st.session_state["sessions"] = {}
    if "active_session_id" not in st.session_state:
        # Create an initial session
        sid = new_session_id()
        st.session_state["sessions"][sid] = Session(id=sid, title="Session 1")
        st.session_state["active_session_id"] = sid
    if "jobs" not in st.session_state:
        st.session_state["jobs"] = {}


def new_session_id() -> str:
    return hashlib.sha1(str(time.time()).encode()).hexdigest()[:8]


def create_session(title: str | None = None) -> str:
    sid = new_session_id()
    sessions: Dict[str, Session] = st.session_state["sessions"]
    sessions[sid] = Session(id=sid, title=title or f"Session {len(sessions)+1}")
    st.session_state["active_session_id"] = sid
    return sid


def duplicate_session(session_id: str) -> str:
    s = st.session_state["sessions"][session_id]
    sid = new_session_id()
    st.session_state["sessions"][sid] = Session(
        id=sid,
        title=f"{s.title} (copy)",
        files={fid: file.copy() for fid, file in s.files.items()},
        active_file_id=s.active_file_id,
        ui=s.ui.copy(),
    )
    st.session_state["active_session_id"] = sid
    return sid


def delete_session(session_id: str) -> None:
    sessions: Dict[str, Session] = st.session_state["sessions"]
    if session_id in sessions:
        del sessions[session_id]
    if not sessions:
        # Always keep at least one session
        create_session("Session 1")
    else:
        st.session_state["active_session_id"] = next(iter(sessions.keys()))


def set_active(session_id: str) -> None:
    sessions: Dict[str, Session] = st.session_state["sessions"]
    if session_id in sessions:
        st.session_state["active_session_id"] = session_id


def get_active_session_id() -> str:
    return st.session_state["active_session_id"]


@dataclass
class SessionFile:
    file_id: str
    name: str
    bytes: bytes
    is_pdf: bool
    num_pages: int = 1
    current_page: int = 1
    ocr_cache: Dict[int, OcrEntry] = field(default_factory=dict)
    raw_edits: Dict[int, str] = field(default_factory=dict)
    ui: Dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "SessionFile":
        return SessionFile(
            file_id=self.file_id,
            name=self.name,
            bytes=self.bytes,
            is_pdf=self.is_pdf,
            num_pages=self.num_pages,
            current_page=self.current_page,
            ocr_cache=self.ocr_cache.copy(),
            raw_edits=self.raw_edits.copy(),
            ui=self.ui.copy(),
        )


def get_active_file(session: Session) -> Optional[SessionFile]:
    if session.active_file_id and session.active_file_id in session.files:
        return session.files[session.active_file_id]
    if session.files:
        first_id = next(iter(session.files))
        session.active_file_id = first_id
        return session.files[first_id]
    return None
