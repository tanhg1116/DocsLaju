const ui = {
  sessionList: document.querySelector("#sessionList"),
  projectList: document.querySelector("#projectList"),
  archivedSessionList: document.querySelector("#archivedSessionList"),
  unfiledCount: document.querySelector("#unfiledCount"),
  archivedCount: document.querySelector("#archivedCount"),
  unfiledDropZone: document.querySelector("#unfiledDropZone"),
  archiveToggle: document.querySelector("#archiveToggle"),
  newProjectButton: document.querySelector("#newProjectButton"),
  workspaceSearchInput: document.querySelector("#workspaceSearchInput"),
  searchResults: document.querySelector("#searchResults"),
  adminName: document.querySelector("#adminName"),
  adminAvatar: document.querySelector("#adminAvatar"),
  sessionMenu: document.querySelector("#sessionMenu"),
  projectMenu: document.querySelector("#projectMenu"),
  projectPicker: document.querySelector("#projectPicker"),
  pinActionLabel: document.querySelector("#pinActionLabel"),
  archiveActionLabel: document.querySelector("#archiveActionLabel"),
  sessionTitle: document.querySelector("#sessionTitle"),
  modelName: document.querySelector("#modelName"),
  documentMeta: document.querySelector("#documentMeta"),
  documentStage: document.querySelector("#documentStage"),
  documentEmpty: document.querySelector("#documentEmpty"),
  previewCanvas: document.querySelector("#previewCanvas"),
  documentPreview: document.querySelector("#documentPreview"),
  pdfViewer: document.querySelector("#pdfViewer"),
  previewToolbar: document.querySelector("#previewToolbar"),
  fitPageButton: document.querySelector("#fitPageButton"),
  fitWidthButton: document.querySelector("#fitWidthButton"),
  zoomOutButton: document.querySelector("#zoomOutButton"),
  zoomInButton: document.querySelector("#zoomInButton"),
  zoomLevel: document.querySelector("#zoomLevel"),
  pageControls: document.querySelector("#pageControls"),
  pageNumber: document.querySelector("#pageNumber"),
  pageCount: document.querySelector("#pageCount"),
  previousPage: document.querySelector("#previousPage"),
  nextPage: document.querySelector("#nextPage"),
  documentPicker: document.querySelector("#documentPicker"),
  documentPickerButton: document.querySelector("#documentPickerButton"),
  documentMenu: document.querySelector("#documentMenu"),
  documentList: document.querySelector("#documentList"),
  activeFileName: document.querySelector("#activeFileName"),
  fileStatus: document.querySelector("#fileStatus"),
  dropOverlay: document.querySelector("#dropOverlay"),
  renderedContent: document.querySelector("#renderedContent"),
  renderedLoading: document.querySelector("#renderedLoading"),
  renderedLoadingLabel: document.querySelector("#renderedLoadingLabel"),
  markdownEditor: document.querySelector("#markdownEditor"),
  wordCount: document.querySelector("#wordCount"),
  ocrBadge: document.querySelector("#ocrBadge"),
  ocrButton: document.querySelector("#ocrButton"),
  needsReviewButton: document.querySelector("#needsReviewButton"),
  approvePageButton: document.querySelector("#approvePageButton"),
  autoOcrControl: document.querySelector("#autoOcrControl"),
  autoOcrToggle: document.querySelector("#autoOcrToggle"),
  autoOcrDialog: document.querySelector("#autoOcrDialog"),
  autoOcrForm: document.querySelector("#autoOcrForm"),
  autoOcrDocumentLabel: document.querySelector("#autoOcrDocumentLabel"),
  autoOcrRange: document.querySelector("#autoOcrRange"),
  autoOcrRangeStatus: document.querySelector("#autoOcrRangeStatus"),
  autoOcrAllPages: document.querySelector("#autoOcrAllPages"),
  autoOcrCurrentPage: document.querySelector("#autoOcrCurrentPage"),
  autoOcrCancel: document.querySelector("#autoOcrCancel"),
  batchStatusArea: document.querySelector("#batchStatusArea"),
  batchProgress: document.querySelector("#batchProgress"),
  batchProgressLabel: document.querySelector("#batchProgressLabel"),
  batchProgressCount: document.querySelector("#batchProgressCount"),
  batchProgressBar: document.querySelector("#batchProgressBar"),
  batchRecovery: document.querySelector("#batchRecovery"),
  batchError: document.querySelector("#batchError"),
  retryBatchButton: document.querySelector("#retryBatchButton"),
  exportButton: document.querySelector("#exportButton"),
  extractButton: document.querySelector("#extractButton"),
  extractionDialog: document.querySelector("#extractionDialog"),
  extractionForm: document.querySelector("#extractionForm"),
  extractionProfile: document.querySelector("#extractionProfile"),
  extractionDocumentLabel: document.querySelector("#extractionDocumentLabel"),
  extractionEmpty: document.querySelector("#extractionEmpty"),
  extractionEmptyMessage: document.querySelector("#extractionEmptyMessage"),
  extractionEditor: document.querySelector("#extractionEditor"),
  extractionStatus: document.querySelector("#extractionStatus"),
  extractionModel: document.querySelector("#extractionModel"),
  extractionMessage: document.querySelector("#extractionMessage"),
  runExtractionButton: document.querySelector("#runExtractionButton"),
  rerunExtractionButton: document.querySelector("#rerunExtractionButton"),
  editExtractionButton: document.querySelector("#editExtractionButton"),
  saveExtractionButton: document.querySelector("#saveExtractionButton"),
  approveExtractionButton: document.querySelector("#approveExtractionButton"),
  extractionCsv: document.querySelector("#extractionCsv"),
  extractionClose: document.querySelector("#extractionClose"),
  extractionDynamicFields: document.querySelector("#extractionDynamicFields"),
  exportDialog: document.querySelector("#exportDialog"),
  exportForm: document.querySelector("#exportForm"),
  exportDocumentLabel: document.querySelector("#exportDocumentLabel"),
  exportStatus: document.querySelector("#exportStatus"),
  exportCancel: document.querySelector("#exportCancel"),
  copyButton: document.querySelector("#copyButton"),
  saveState: document.querySelector("#saveState"),
  importDialog: document.querySelector("#importDialog"),
  importForm: document.querySelector("#importForm"),
  importFilePicker: document.querySelector("#importFilePicker"),
  importClipboardButton: document.querySelector("#importClipboardButton"),
  importFileLabel: document.querySelector("#importFileLabel"),
  importStatus: document.querySelector("#importStatus"),
  importCancel: document.querySelector("#importCancel"),
  fileInput: document.querySelector("#fileInput"),
  toastRegion: document.querySelector("#toastRegion"),
  appShell: document.querySelector(".app-shell"),
  sidebar: document.querySelector("#sidebar"),
  toggleSidebar: document.querySelector("#toggleSidebar"),
  scrim: document.querySelector("#scrim"),
};

let state = null;
let renderTimer = null;
let renderGeneration = 0;
let saveTimer = null;
let markdownDirty = false;
let previewObjectUrl = null;
let previewView = "fit-page";
let previewZoom = 100;
let panStart = null;
let previewGeneration = 0;
let previewContext = null;
let selectedSessionId = null;
let selectedProjectId = null;
let projectsInitialized = false;
let archiveExpanded = false;
let activeJobId = null;
let jobPollTimer = null;
let lastJobCompleted = 0;
let manualOcrContext = null;
let pendingUploadFiles = [];
let searchTimer = null;
let searchGeneration = 0;
let searchHighlight = null;
let currentExtraction = null;
let extractionTemplates = [];
let currentExtractionLayout = null;
let extractionEditMode = false;
const expandedProjects = new Set();

function iconMarkup(name, extraClass = "") {
  return `<svg class="ui-icon${extraClass ? ` ${extraClass}` : ""}" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
}

function setIconButton(button, label, iconName = null, spinning = false) {
  button.setAttribute("aria-label", label);
  button.dataset.tooltip = label;
  if (iconName) button.querySelector("use")?.setAttribute("href", `#icon-${iconName}`);
  button.classList.toggle("icon-spinning", spinning);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(payload?.error || `Request failed (${response.status})`);
  return payload;
}

function activeSession() {
  return state?.sessions.find((item) => item.id === state.active_session_id) || null;
}

function activeDocument() {
  return state?.active_document || null;
}

function routeForDocument(suffix = "") {
  const session = activeSession();
  const document = activeDocument();
  return `/api/sessions/${session.id}/files/${document.id}${suffix}`;
}

function toast(message, type = "info") {
  const element = document.createElement("div");
  element.className = `toast ${type}`;
  element.textContent = message;
  ui.toastRegion.append(element);
  setTimeout(() => element.remove(), 3800);
}

function setBusy(isBusy, label = "Working…") {
  ui.saveState.textContent = isBusy ? label : "Ready";
}

function setOcrLoading(isLoading, pageNumber = null) {
  if (isLoading && pageNumber) {
    ui.renderedLoadingLabel.textContent = `Reading page ${pageNumber}…`;
  }
  ui.renderedLoading.hidden = !isLoading;
  ui.renderedContent.setAttribute("aria-busy", String(isLoading));
}

function isCurrentPreview(context) {
  const session = activeSession();
  const document = activeDocument();
  return Boolean(
    context &&
    context === previewContext &&
    context.generation === previewGeneration &&
    session?.id === context.sessionId &&
    document?.id === context.documentId &&
    document.current_page === context.pageNumber
  );
}

function emptyPreview() {
  if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
  previewObjectUrl = null;
  ui.previewCanvas.hidden = true;
  ui.previewCanvas.classList.remove("pdf-mode");
  ui.documentPreview.removeAttribute("src");
  ui.documentPreview.hidden = true;
  ui.pdfViewer.removeAttribute("src");
  ui.pdfViewer.hidden = true;
  ui.previewToolbar.hidden = true;
  ui.documentStage.classList.remove("pdf-mode", "is-pannable", "is-panning");
  ui.documentStage.removeAttribute("data-view");
  ui.documentEmpty.hidden = false;
}

function setPreviewView(view, zoom = previewZoom) {
  previewView = view;
  previewZoom = Math.max(20, Math.min(300, Math.round(zoom / 10) * 10));
  ui.documentStage.dataset.view = previewView;
  ui.fitPageButton.classList.toggle("active", previewView === "fit-page");
  ui.fitWidthButton.classList.toggle("active", previewView === "fit-width");
  ui.zoomLevel.textContent = previewView === "manual" ? `${previewZoom}%` : "Auto";

  if (previewView === "manual" && ui.documentPreview.naturalWidth) {
    const width = Math.round(ui.documentPreview.naturalWidth * previewZoom / 100);
    ui.documentStage.style.setProperty("--manual-width", `${width}px`);
  } else {
    ui.documentStage.style.removeProperty("--manual-width");
  }
  requestAnimationFrame(() => {
    updatePannableState();
  });
}

function displayedZoom() {
  if (!ui.documentPreview.naturalWidth) return 100;
  return Math.max(20, Math.min(300, ui.documentPreview.getBoundingClientRect().width / ui.documentPreview.naturalWidth * 100));
}

function changeZoom(delta) {
  const startingZoom = previewView === "manual" ? previewZoom : displayedZoom();
  setPreviewView("manual", startingZoom + delta);
}

function updatePannableState() {
  const pannable = ui.documentStage.scrollWidth > ui.documentStage.clientWidth + 2 ||
    ui.documentStage.scrollHeight > ui.documentStage.clientHeight + 2;
  ui.documentStage.classList.toggle("is-pannable", pannable);
}

async function loadRasterPreview(context) {
  try {
    const response = await fetch(context.previewUrl);
    if (!isCurrentPreview(context)) return;
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error(payload?.error || "Could not load preview");
    }
    const blob = await response.blob();
    if (!isCurrentPreview(context)) return;
    const objectUrl = URL.createObjectURL(blob);
    if (!isCurrentPreview(context)) {
      URL.revokeObjectURL(objectUrl);
      return;
    }
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = objectUrl;
    ui.documentPreview.onload = () => {
      if (!isCurrentPreview(context)) return;
      setPreviewView("fit-page");
      updatePannableState();
    };
    ui.documentPreview.src = previewObjectUrl;
    ui.documentPreview.hidden = false;
    ui.pdfViewer.removeAttribute("src");
    ui.pdfViewer.hidden = true;
    ui.previewCanvas.classList.remove("pdf-mode");
    ui.documentStage.classList.remove("pdf-mode");
    ui.previewCanvas.hidden = false;
    ui.previewToolbar.hidden = false;
    ui.documentEmpty.hidden = true;
  } catch (error) {
    if (!isCurrentPreview(context)) return;
    emptyPreview();
    toast(error.message, "error");
  }
}

function loadPdfPreview(context) {
  if (!isCurrentPreview(context)) return;
  if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
  previewObjectUrl = null;
  ui.documentPreview.removeAttribute("src");
  ui.documentPreview.hidden = true;
  ui.previewCanvas.classList.add("pdf-mode");
  ui.previewCanvas.hidden = false;
  ui.previewToolbar.hidden = true;
  ui.documentEmpty.hidden = true;
  ui.documentStage.classList.add("pdf-mode");
  ui.documentStage.classList.remove("is-pannable", "is-panning");
  ui.documentStage.removeAttribute("data-view");
  ui.pdfViewer.src = `${context.contentUrl}#page=${context.pageNumber}&zoom=page-fit`;
  ui.pdfViewer.hidden = false;
  ui.fileStatus.textContent = "Browser PDF viewer";
}

async function loadPreview() {
  const generation = ++previewGeneration;
  const session = activeSession();
  const document = activeDocument();
  if (!document || !session) {
    previewContext = null;
    if (generation !== previewGeneration) return;
    return emptyPreview();
  }

  const baseUrl = `/api/sessions/${session.id}/files/${document.id}`;
  const context = {
    generation,
    sessionId: session.id,
    documentId: document.id,
    pageNumber: document.current_page,
    contentUrl: `${baseUrl}/content`,
    previewUrl: `${baseUrl}/preview?page=${document.current_page}&v=${Date.now()}`,
  };
  previewContext = context;

  if (document.is_pdf) return loadPdfPreview(context);
  if (!isCurrentPreview(context)) return;
  return loadRasterPreview(context);
}

function closeMenus() {
  ui.sessionMenu.hidden = true;
  ui.projectMenu.hidden = true;
  ui.projectPicker.hidden = true;
  selectedSessionId = null;
  selectedProjectId = null;
}

function positionMenu(menu, anchor) {
  menu.hidden = false;
  const anchorRect = anchor.getBoundingClientRect();
  const menuRect = menu.getBoundingClientRect();
  const left = Math.min(anchorRect.right - menuRect.width, window.innerWidth - menuRect.width - 8);
  const top = Math.min(anchorRect.bottom + 5, window.innerHeight - menuRect.height - 8);
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;
}

function sessionById(id) {
  return state.sessions.find((item) => item.id === id) || null;
}

function searchTerms(value) {
  return [...new Set((value.match(/[\p{L}\p{N}]+/gu) || []).map((term) => term.toLocaleLowerCase()))].slice(0, 12);
}

function renderSearchResults(results, query) {
  ui.searchResults.replaceChildren();
  ui.searchResults.hidden = false;
  if (!results.length) {
    const empty = document.createElement("div");
    empty.className = "search-results-empty";
    empty.textContent = "No OCR text matched";
    ui.searchResults.append(empty);
    return;
  }
  for (const result of results) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "search-result";
    button.setAttribute("role", "option");
    const title = document.createElement("strong");
    title.textContent = result.document_name;
    const location = document.createElement("small");
    location.textContent = `${result.project_name ? `${result.project_name} · ` : ""}${result.session_title} · page ${result.page_number}`;
    const excerpt = document.createElement("span");
    excerpt.textContent = result.excerpt || "Matching OCR page";
    button.append(title, location, excerpt);
    button.addEventListener("click", async () => {
      try {
        searchHighlight = {
          documentId: result.document_id,
          pageNumber: result.page_number,
          terms: searchTerms(query),
        };
        state = await api("/api/search/open", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(result),
        });
        ui.searchResults.hidden = true;
        renderState();
        closeSidebar();
      } catch (error) {
        toast(error.message, "error");
      }
    });
    ui.searchResults.append(button);
  }
}

async function runWorkspaceSearch(query) {
  const generation = ++searchGeneration;
  if (!query.trim()) {
    ui.searchResults.hidden = true;
    ui.searchResults.replaceChildren();
    return;
  }
  try {
    const payload = await api(`/api/search?q=${encodeURIComponent(query.trim())}`);
    if (generation !== searchGeneration || ui.workspaceSearchInput.value.trim() !== query.trim()) return;
    renderSearchResults(payload.results, query);
  } catch (error) {
    if (generation !== searchGeneration) return;
    ui.searchResults.hidden = true;
    toast(error.message, "error");
  }
}

async function patchSession(id, payload, successMessage = "") {
  try {
    const previousActiveId = state.active_session_id;
    state = await api(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderState({ refreshPreview: previousActiveId !== state.active_session_id });
    if (successMessage) toast(successMessage);
  } catch (error) {
    toast(error.message, "error");
  }
}

function openSessionMenu(item, anchor) {
  closeMenus();
  selectedSessionId = item.id;
  ui.pinActionLabel.textContent = item.is_pinned ? "Unpin session" : "Pin session";
  ui.archiveActionLabel.textContent = item.is_archived ? "Restore session" : "Archive";
  ui.projectPicker.replaceChildren();

  const destinations = [{ id: null, name: "Unfiled", icon: "file-text" }, ...state.projects.map((project) => ({
    id: project.id,
    name: project.name,
    icon: "folder",
  }))];
  for (const destination of destinations) {
    const option = document.createElement("button");
    option.type = "button";
    option.innerHTML = `${iconMarkup(destination.icon)}<span></span>`;
    option.querySelector("span:last-child").textContent = destination.name;
    option.addEventListener("click", async (event) => {
      event.stopPropagation();
      closeMenus();
      await patchSession(item.id, { project_id: destination.id }, `Moved to ${destination.name}`);
    });
    ui.projectPicker.append(option);
  }
  positionMenu(ui.sessionMenu, anchor);
}

function openProjectMenu(project, anchor) {
  closeMenus();
  selectedProjectId = project.id;
  positionMenu(ui.projectMenu, anchor);
}

function makeDropTarget(element, projectId) {
  element.addEventListener("dragover", (event) => {
    if (!Array.from(event.dataTransfer.types || []).includes("text/plain")) return;
    event.preventDefault();
    element.classList.add("drag-over");
  });
  element.addEventListener("dragleave", () => element.classList.remove("drag-over"));
  element.addEventListener("drop", async (event) => {
    event.preventDefault();
    element.classList.remove("drag-over");
    const sessionId = event.dataTransfer.getData("text/plain");
    if (!sessionId) return;
    await patchSession(sessionId, { project_id: projectId }, "Session moved");
  });
}

function createSessionRow(item) {
  const row = document.createElement("div");
  row.className = `session-row${item.id === state.active_session_id ? " active" : ""}`;
  row.draggable = !item.is_archived;

  const button = document.createElement("button");
  button.className = "session-button";
  button.type = "button";
  button.innerHTML = `<span class="session-document-icon" aria-hidden="true">${iconMarkup("file-text")}</span><span class="session-title"></span>${item.is_pinned ? `<span class="session-pin" title="Pinned">${iconMarkup("pin")}</span>` : ""}`;
  button.querySelector(".session-title").textContent = item.title;
  button.title = item.title;
  button.addEventListener("click", () => {
    if (item.is_archived) return;
    activateSession(item.id);
  });

  const menu = document.createElement("button");
  menu.className = "session-menu-button";
  menu.type = "button";
  menu.innerHTML = iconMarkup("more-horizontal");
  menu.title = `Actions for ${item.title}`;
  menu.setAttribute("aria-label", `Actions for ${item.title}`);
  menu.addEventListener("click", (event) => {
    event.stopPropagation();
    openSessionMenu(item, menu);
  });

  row.addEventListener("dragstart", (event) => {
    row.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", item.id);
  });
  row.addEventListener("dragend", () => row.classList.remove("dragging"));
  row.append(button, menu);
  return row;
}

function renderProjects(activeSessions) {
  ui.projectList.replaceChildren();
  if (!projectsInitialized) {
    for (const project of state.projects) expandedProjects.add(project.id);
    projectsInitialized = true;
  }

  for (const project of state.projects) {
    const projectSessions = activeSessions.filter((item) => item.project_id === project.id);
    const folder = document.createElement("div");
    folder.className = `project-folder${expandedProjects.has(project.id) ? "" : " collapsed"}`;
    folder.dataset.projectId = project.id;
    makeDropTarget(folder, project.id);

    const row = document.createElement("div");
    row.className = "project-row";
    const chevron = document.createElement("button");
    chevron.className = "folder-chevron";
    chevron.type = "button";
    chevron.innerHTML = iconMarkup("chevron-down");
    chevron.setAttribute("aria-label", `Toggle ${project.name}`);
    const icon = document.createElement("span");
    icon.className = "folder-icon";
    icon.innerHTML = iconMarkup("folder");
    const name = document.createElement("span");
    name.className = "project-name";
    name.textContent = project.name;
    name.title = project.name;
    const count = document.createElement("span");
    count.className = "project-count";
    count.textContent = String(projectSessions.length);
    const menu = document.createElement("button");
    menu.className = "project-menu-button";
    menu.type = "button";
    menu.innerHTML = iconMarkup("more-horizontal");
    menu.setAttribute("aria-label", `Actions for ${project.name}`);

    const toggle = () => {
      if (expandedProjects.has(project.id)) expandedProjects.delete(project.id);
      else expandedProjects.add(project.id);
      folder.classList.toggle("collapsed", !expandedProjects.has(project.id));
    };
    chevron.addEventListener("click", toggle);
    name.addEventListener("click", toggle);
    menu.addEventListener("click", (event) => {
      event.stopPropagation();
      openProjectMenu(project, menu);
    });
    row.append(chevron, icon, name, count, menu);

    const sessionContainer = document.createElement("div");
    sessionContainer.className = "project-sessions session-list";
    if (projectSessions.length) {
      for (const item of projectSessions) sessionContainer.append(createSessionRow(item));
    } else {
      const empty = document.createElement("div");
      empty.className = "project-empty";
      empty.textContent = "Drop sessions here";
      sessionContainer.append(empty);
    }
    folder.append(row, sessionContainer);
    ui.projectList.append(folder);
  }

  if (!state.projects.length) {
    const empty = document.createElement("div");
    empty.className = "project-empty";
    empty.textContent = "Create a folder to organize your work";
    ui.projectList.append(empty);
  }
}

function renderSessions() {
  closeMenus();
  ui.adminName.textContent = state.user?.display_name || "Admin User";
  ui.adminAvatar.title = state.user?.display_name || "Admin User";
  const activeSessions = state.sessions.filter((item) => !item.is_archived);
  const unfiled = activeSessions.filter((item) => !item.project_id);
  const archived = state.sessions.filter((item) => item.is_archived);

  renderProjects(activeSessions);
  ui.sessionList.replaceChildren(...unfiled.map(createSessionRow));
  if (!unfiled.length) {
    const empty = document.createElement("div");
    empty.className = "session-empty";
    empty.textContent = "Drop a session here";
    ui.sessionList.append(empty);
  }
  ui.archivedSessionList.replaceChildren(...archived.map(createSessionRow));
  if (!archived.length) {
    const empty = document.createElement("div");
    empty.className = "session-empty";
    empty.textContent = "No archived sessions";
    ui.archivedSessionList.append(empty);
  }
  ui.unfiledCount.textContent = String(unfiled.length);
  ui.archivedCount.textContent = String(archived.length);
  ui.archivedSessionList.hidden = !archiveExpanded;
  ui.archiveToggle.setAttribute("aria-expanded", String(archiveExpanded));
}

function closeDocumentMenu() {
  ui.documentMenu.hidden = true;
  ui.documentPickerButton.setAttribute("aria-expanded", "false");
}

function renderFilePicker(session) {
  ui.documentList.replaceChildren();
  const activeFile = session.files.find((file) => file.id === session.active_file_id) || null;
  ui.activeFileName.textContent = activeFile?.name || "No uploaded documents";
  ui.documentPickerButton.disabled = !session.files.length;

  for (const file of session.files) {
    const row = document.createElement("div");
    row.className = `document-menu-row${file.id === session.active_file_id ? " active" : ""}`;
    const openButton = document.createElement("button");
    openButton.className = "document-open-button";
    openButton.type = "button";
    openButton.innerHTML = `<span aria-hidden="true">${iconMarkup(file.is_pdf ? "file-text" : "file-image")}</span><span></span>`;
    openButton.querySelector("span:last-child").textContent = file.name;
    openButton.title = file.name;
    openButton.addEventListener("click", async () => {
      closeDocumentMenu();
      if (file.id === session.active_file_id) return;
      try {
        state = await api(`/api/sessions/${session.id}/files/${file.id}/activate`, { method: "POST" });
        renderState();
      } catch (error) {
        toast(error.message, "error");
      }
    });

    const deleteButton = document.createElement("button");
    deleteButton.className = "document-delete-button";
    deleteButton.type = "button";
    deleteButton.innerHTML = iconMarkup("trash");
    deleteButton.title = `Delete ${file.name}`;
    deleteButton.setAttribute("aria-label", `Delete ${file.name}`);
    deleteButton.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!window.confirm(`Delete “${file.name}” from this session and database?`)) return;
      closeDocumentMenu();
      try {
        state = await api(`/api/sessions/${session.id}/files/${file.id}`, { method: "DELETE" });
        renderState();
        toast(`${file.name} deleted`);
      } catch (error) {
        toast(error.message, "error");
      }
    });
    row.append(openButton, deleteButton);
    ui.documentList.append(row);
  }

  if (!session.files.length) {
    const empty = document.createElement("div");
    empty.className = "document-menu-empty";
    empty.textContent = "No documents in this session";
    ui.documentList.append(empty);
  }
}

function updateBatchControls(job) {
  clearTimeout(jobPollTimer);
  jobPollTimer = null;
  const document = activeDocument();
  const active = job && ["queued", "running", "cancelling"].includes(job.status);
  const recoverable = Boolean(
    job && !active && ["failed", "interrupted", "cancelled"].includes(job.status) &&
    ((job.failed_pages || 0) + (job.cancelled_pages || 0) > 0)
  );
  const automaticOn = Boolean(job && ["queued", "running"].includes(job.status));
  ui.autoOcrToggle.checked = automaticOn;
  ui.autoOcrToggle.disabled = !document || job?.status === "cancelling";
  ui.autoOcrControl.classList.toggle("disabled", ui.autoOcrToggle.disabled);
  ui.autoOcrControl.dataset.tooltip = job?.status === "cancelling"
    ? "Stopping automatic OCR…"
    : automaticOn
      ? "Automatic OCR is on — switch off to stop"
      : "Automatic OCR is off — switch on to choose pages";
  ui.batchStatusArea.hidden = !active && !recoverable;
  ui.batchProgress.hidden = !active;
  ui.batchRecovery.hidden = !recoverable;

  if (recoverable) {
    const count = (job.failed_pages || 0) + (job.cancelled_pages || 0);
    ui.batchError.textContent = `${count} page${count === 1 ? "" : "s"} did not finish. Retry only those pages.`;
    ui.retryBatchButton.dataset.jobId = job.id;
  } else {
    delete ui.retryBatchButton.dataset.jobId;
  }

  if (!active) {
    activeJobId = null;
    lastJobCompleted = 0;
    return;
  }

  if (activeJobId !== job.id) lastJobCompleted = job.completed_pages || 0;
  activeJobId = job.id;
  lastJobCompleted = Math.max(lastJobCompleted, job.completed_pages || 0);
  const done = (job.completed_pages || 0) + (job.failed_pages || 0) + (job.cancelled_pages || 0);
  const total = job.total_pages || document.num_pages;
  const percent = total ? Math.round(done / total * 100) : 0;
  ui.batchProgressCount.textContent = `${done} / ${total}`;
  ui.batchProgressBar.style.width = `${percent}%`;
  ui.batchProgressLabel.textContent = job.status === "cancelling"
    ? "Stopping after the current request…"
    : job.current_page
      ? `Processing page ${job.current_page}…`
      : "Preparing page queue…";
  const currentPage = job.pages?.find((page) => page.page_number === document.current_page);
  if (currentPage && ["queued", "running"].includes(currentPage.status)) {
    ui.ocrButton.disabled = currentPage.status === "running" || job.status === "cancelling";
    ui.markdownEditor.disabled = true;
    setIconButton(
      ui.ocrButton,
      currentPage.status === "running" ? "Current page is processing…" : "Process current page next",
      "scan-text",
    );
    ui.ocrBadge.textContent = currentPage.status === "running" ? "Processing" : "Queued";
    ui.ocrBadge.className = "badge processing";
    ui.fileStatus.textContent = `Batch OCR · page ${document.current_page}`;
  }
  jobPollTimer = setTimeout(() => pollOcrJob(job.id), 800);
}

function updateReviewControls(document) {
  const available = Boolean(document?.has_ocr);
  const status = document?.review_status || "unreviewed";
  ui.needsReviewButton.disabled = !available;
  ui.approvePageButton.disabled = !available;
  ui.needsReviewButton.classList.toggle("active", available && status === "needs_review");
  ui.approvePageButton.classList.toggle("active", available && status === "approved");
  setIconButton(
    ui.needsReviewButton,
    status === "needs_review" ? "Clear needs-review flag" : "Mark page as needing review",
    "flag",
  );
  setIconButton(
    ui.approvePageButton,
    status === "approved" ? "Return page to review" : "Approve this page",
    "circle-check",
  );
  if (!available) return;
  const labels = {
    approved: ["Approved", "badge complete"],
    needs_review: ["Needs review", "badge needs-review"],
    unreviewed: ["Review", "badge"],
  };
  const [label, className] = labels[status] || labels.unreviewed;
  ui.ocrBadge.textContent = label;
  ui.ocrBadge.className = className;
  ui.ocrBadge.title = document.confidence_score == null
    ? ""
    : `OCR confidence ${Math.round(document.confidence_score * 100)}%`;
}

function updateWords(value) {
  const words = value.trim() ? value.trim().split(/\s+/).length : 0;
  ui.wordCount.textContent = `${words} ${words === 1 ? "word" : "words"}`;
}

function renderMath(root) {
  if (typeof globalThis.renderMathInElement !== "function") return;
  globalThis.renderMathInElement(root, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "\\(", right: "\\)", display: false },
    ],
    throwOnError: false,
    strict: "ignore",
  });
}

function formatNumberedEquations(root) {
  for (const paragraph of root.querySelectorAll("p")) {
    const elementChildren = [...paragraph.children];
    if (elementChildren.length !== 1) continue;

    const mathHost = elementChildren[0];
    const katex = mathHost.matches(".katex") ? mathHost : mathHost.querySelector(".katex");
    if (!katex || mathHost.matches(".katex-display") || mathHost.closest(".katex-display")) continue;

    const nodes = [...paragraph.childNodes];
    const mathIndex = nodes.indexOf(mathHost);
    if (mathIndex < 0) continue;
    const beforeNodes = nodes.slice(0, mathIndex);
    const afterNodes = nodes.slice(mathIndex + 1);
    if (![...beforeNodes, ...afterNodes].every((node) => node.nodeType === 3)) continue;

    const before = beforeNodes.map((node) => node.textContent).join("");
    const after = afterNodes.map((node) => node.textContent).join("");
    const labelMatch = before.match(/^\s*(\(?\d+(?:\.\d+)+\)?)\s+$/);
    const punctuationMatch = after.match(/^\s*([.,;:!?]?)\s*$/);
    if (!labelMatch || !punctuationMatch) continue;

    const label = document.createElement("span");
    label.className = "numbered-equation-label";
    label.textContent = labelMatch[1];
    const body = document.createElement("span");
    body.className = "numbered-equation-body";
    body.append(mathHost);
    if (punctuationMatch[1]) body.append(document.createTextNode(punctuationMatch[1]));
    paragraph.classList.add("numbered-equation");
    paragraph.replaceChildren(label, body);
  }
}

function orderedListEnd(list) {
  let value = Number.parseInt(list.getAttribute("start") || "1", 10) - 1;
  for (const item of [...list.children].filter((child) => child.tagName === "LI")) {
    value = item.hasAttribute("value")
      ? Number.parseInt(item.getAttribute("value"), 10)
      : value + 1;
  }
  return value;
}

function formatLetteredSubparts(root) {
  for (const paragraph of root.querySelectorAll("li p")) {
    const directText = [...paragraph.childNodes]
      .filter((node) => node.nodeType === 3)
      .map((node) => node.textContent)
      .join("");
    if (!/^\s*\([a-z]\)\s/i.test(directText)) continue;

    const rows = [];
    let body = null;
    let invalid = false;
    for (const node of [...paragraph.childNodes]) {
      if (node.nodeType !== 3) {
        if (!body) {
          invalid = true;
          break;
        }
        body.append(node);
        continue;
      }

      const text = node.textContent || "";
      const markerPattern = /\(([a-z])\)(?=\s)/gi;
      let cursor = 0;
      let match;
      while ((match = markerPattern.exec(text)) !== null) {
        const preceding = text.slice(cursor, match.index);
        if (body) body.append(document.createTextNode(preceding));
        else if (preceding.trim()) invalid = true;

        const row = document.createElement("span");
        row.className = "exercise-subpart";
        const label = document.createElement("span");
        label.className = "exercise-subpart-label";
        label.textContent = `(${match[1].toLowerCase()})`;
        body = document.createElement("span");
        body.className = "exercise-subpart-body";
        row.append(label, body);
        rows.push(row);
        cursor = markerPattern.lastIndex;
      }
      if (body) body.append(document.createTextNode(text.slice(cursor)));
      else if (text.trim()) invalid = true;
      if (invalid) break;
    }

    if (invalid || !rows.length) continue;
    paragraph.classList.add("exercise-subparts");
    paragraph.replaceChildren(...rows);
  }
}

function normalizeRenderedLists(root) {
  const lists = [...root.children].filter((child) => child.tagName === "OL");
  for (const list of lists) {
    if (!list.isConnected || list.parentElement !== root) continue;
    while (true) {
      const between = [];
      let next = list.nextElementSibling;
      let blocked = false;
      while (next && next.tagName !== "OL") {
        if (/^H[1-6]$/.test(next.tagName) || next.tagName === "HR") {
          blocked = true;
          break;
        }
        between.push(next);
        next = next.nextElementSibling;
      }
      if (blocked || !next) break;

      const nextStart = Number.parseInt(next.getAttribute("start") || "1", 10);
      if (nextStart !== orderedListEnd(list) + 1) break;
      const lastItem = list.lastElementChild;
      if (!lastItem || lastItem.tagName !== "LI") break;
      for (const block of between) lastItem.append(block);
      for (const item of [...next.children]) list.append(item);
      next.remove();
    }
  }

  for (const list of [...root.children].filter((child) => child.tagName === "OL")) {
    const lastItem = list.lastElementChild;
    if (!lastItem || !/(?:[:;,]|\b(?:then|is|are|equals|where|by))\s*$/i.test(lastItem.textContent.trim())) continue;
    let block = list.nextElementSibling;
    if (!block || block.tagName !== "P" || block.children.length !== 1 || !block.querySelector(".katex-display")) continue;
    let following = block.nextElementSibling;
    lastItem.append(block);
    block = following;
    while (block && block.tagName === "P" && /^[a-z]/.test(block.textContent.trim())) {
      following = block.nextElementSibling;
      lastItem.append(block);
      block = following;
    }
  }

  formatLetteredSubparts(root);
}

function activeSearchTerms() {
  const document = activeDocument();
  if (!searchHighlight || !document) return [];
  if (searchHighlight.documentId !== document.id || searchHighlight.pageNumber !== document.current_page) return [];
  return searchHighlight.terms || [];
}

function highlightRenderedSearch(root) {
  const terms = activeSearchTerms();
  if (!terms.length) return;
  const escaped = terms
    .sort((left, right) => right.length - left.length)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "giu");
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.parentElement?.closest("mark, .katex, code, pre")) continue;
    if (pattern.test(node.textContent || "")) nodes.push(node);
    pattern.lastIndex = 0;
  }
  for (const node of nodes) {
    const parts = (node.textContent || "").split(pattern);
    const fragment = document.createDocumentFragment();
    for (const part of parts) {
      if (!part) continue;
      if (terms.some((term) => term.toLocaleLowerCase() === part.toLocaleLowerCase())) {
        const mark = document.createElement("mark");
        mark.className = "search-highlight";
        mark.textContent = part;
        fragment.append(mark);
      } else {
        fragment.append(document.createTextNode(part));
      }
    }
    node.replaceWith(fragment);
  }
  root.querySelector("mark.search-highlight")?.scrollIntoView({ block: "center", behavior: "smooth" });
}

function selectEditorSearchMatch() {
  const terms = activeSearchTerms();
  if (!terms.length) return;
  const source = ui.markdownEditor.value.toLocaleLowerCase();
  const matches = terms
    .map((term) => ({ term, index: source.indexOf(term) }))
    .filter((match) => match.index >= 0)
    .sort((left, right) => left.index - right.index);
  if (!matches.length) return;
  const match = matches[0];
  requestAnimationFrame(() => {
    ui.markdownEditor.focus({ preventScroll: true });
    ui.markdownEditor.setSelectionRange(match.index, match.index + match.term.length);
  });
}

function renderMarkdown(value) {
  clearTimeout(renderTimer);
  const generation = ++renderGeneration;
  if (!value.trim()) {
    ui.renderedContent.innerHTML = `<div class="empty-state compact"><div class="empty-icon">${iconMarkup("sparkles")}</div><h3>No Markdown yet</h3><p>Run OCR or begin typing in the editor.</p></div>`;
    return;
  }
  renderTimer = setTimeout(async () => {
    try {
      const payload = await api("/api/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          markdown: value,
          session_id: activeSession()?.id,
          document_id: activeDocument()?.id,
        }),
      });
      if (generation !== renderGeneration) return;
      ui.renderedContent.innerHTML = payload.html;
      renderMath(ui.renderedContent);
      normalizeRenderedLists(ui.renderedContent);
      formatNumberedEquations(ui.renderedContent);
      highlightRenderedSearch(ui.renderedContent);
    } catch (error) {
      if (generation !== renderGeneration) return;
      toast(error.message, "error");
    }
  }, 120);
}

function renderState({ refreshPreview = true } = {}) {
  const session = activeSession();
  const document = activeDocument();
  renderSessions();
  ui.sessionTitle.textContent = session?.title || "OCR workspace";
  ui.modelName.textContent = state.model;

  if (session) renderFilePicker(session);

  if (!document || !session) {
    ui.documentMeta.textContent = "Original page";
    ui.pageControls.hidden = true;
    ui.fileStatus.textContent = "No document selected";
    ui.markdownEditor.value = "";
    markdownDirty = false;
    ui.markdownEditor.disabled = true;
    ui.ocrButton.disabled = true;
    ui.autoOcrToggle.checked = false;
    ui.autoOcrToggle.disabled = true;
    ui.autoOcrControl.classList.add("disabled");
    ui.extractButton.disabled = true;
    ui.exportButton.disabled = true;
    ui.copyButton.disabled = true;
    ui.ocrBadge.textContent = "Waiting";
    ui.ocrBadge.className = "badge";
    ui.ocrBadge.removeAttribute("title");
    updateReviewControls(null);
    updateWords("");
    renderMarkdown("");
    setOcrLoading(false);
    updateBatchControls(null);
    if (refreshPreview) loadPreview();
    return;
  }

  ui.documentMeta.textContent = document.is_pdf ? `PDF · ${document.num_pages} pages` : "Image · 1 page";
  ui.pageControls.hidden = !document.is_pdf;
  ui.pageNumber.value = document.current_page;
  ui.pageNumber.max = document.num_pages;
  ui.pageCount.textContent = `of ${document.num_pages}`;
  ui.previousPage.disabled = document.current_page <= 1;
  ui.nextPage.disabled = document.current_page >= document.num_pages;
  ui.fileStatus.textContent = document.has_ocr ? "OCR complete" : "Not scanned";

  ui.markdownEditor.disabled = false;
  ui.markdownEditor.value = document.markdown;
  markdownDirty = false;
  ui.ocrButton.disabled = false;
  ui.extractButton.disabled = false;
  setIconButton(ui.ocrButton, document.has_ocr ? "Re-OCR this page" : "Run OCR on this page", "scan-text");
  ui.exportButton.disabled = !document.has_ocr;
  ui.copyButton.disabled = !document.markdown;
  ui.ocrBadge.textContent = document.has_ocr ? "Complete" : "Ready";
  ui.ocrBadge.className = `badge${document.has_ocr ? " complete" : ""}`;
  updateReviewControls(document);
  updateWords(document.markdown);
  renderMarkdown(document.markdown);
  selectEditorSearchMatch();
  const manualIsCurrent = manualOcrContext &&
    manualOcrContext.sessionId === session.id &&
    manualOcrContext.documentId === document.id &&
    manualOcrContext.pageNumber === document.current_page;
  setOcrLoading(Boolean(manualIsCurrent), document.current_page);
  updateBatchControls(state.active_ocr_job || state.recent_ocr_job);
  if (refreshPreview) loadPreview();
}

async function refresh(options) {
  state = await api("/api/state");
  renderState(options);
}

async function activateSession(id) {
  try {
    setBusy(true, "Switching…");
    state = await api(`/api/sessions/${id}/activate`, { method: "POST" });
    renderState();
    closeSidebar();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function createNewSession(projectId = null) {
  try {
    state = await api("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId }),
    });
    renderState();
    closeSidebar();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function uploadFiles(files) {
  const selectedFiles = [...(files || [])];
  if (!selectedFiles.length) return false;
  const session = activeSession();
  if (!session) return false;
  let uploaded = 0;
  let duplicates = 0;
  let lastWasNew = false;
  try {
    setBusy(true, "Uploading…");
    ui.fileStatus.textContent = "Uploading";
    for (const file of selectedFiles) {
      const form = new FormData();
      form.append("file", file);
      state = await api(`/api/sessions/${session.id}/files`, { method: "POST", body: form });
      lastWasNew = !state.upload?.duplicate;
      if (lastWasNew) uploaded += 1;
      else duplicates += 1;
      ui.importStatus.textContent = `Checked ${uploaded + duplicates} of ${selectedFiles.length}…`;
    }
    renderState();
    if (duplicates && !uploaded) toast("That document is already in this session");
    else if (duplicates) toast(`${uploaded} uploaded · ${duplicates} duplicate${duplicates === 1 ? "" : "s"} skipped`);
    else toast(uploaded === 1 ? `${selectedFiles[0].name} uploaded` : `${uploaded} documents uploaded`);
    if (lastWasNew) setTimeout(() => runOcr(), 0);
    return true;
  } catch (error) {
    if (uploaded || duplicates) renderState();
    ui.importStatus.textContent = error.message;
    ui.importStatus.classList.add("error");
    toast(error.message, "error");
    return false;
  } finally {
    ui.fileInput.value = "";
    setBusy(false);
  }
}

async function changePage(page) {
  const document = activeDocument();
  const next = Math.max(1, Math.min(document.num_pages, Number(page) || 1));
  if (next === document.current_page) return;
  try {
    state = await api(routeForDocument(`/page/${next}`), { method: "POST" });
    renderState();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function runOcr() {
  const document = activeDocument();
  const session = activeSession();
  if (!document) return;
  const context = { sessionId: session.id, documentId: document.id, pageNumber: document.current_page };
  const automaticJob = state.active_ocr_job;
  if (automaticJob && ["queued", "running"].includes(automaticJob.status)) {
    try {
      ui.ocrButton.disabled = true;
      const prioritized = await api(`/api/ocr-jobs/${automaticJob.id}/prioritize/${context.pageNumber}`, {
        method: "POST",
      });
      state.active_ocr_job = prioritized;
      updateBatchControls(prioritized);
      toast(`Page ${context.pageNumber} moved to the front of the OCR queue`);
      return;
    } catch (error) {
      state = await api("/api/state");
      if (state.active_ocr_job) {
        renderState({ refreshPreview: false });
        toast(error.message, "error");
        return;
      }
    }
  }
  manualOcrContext = context;
  const endpoint = `/api/sessions/${context.sessionId}/files/${context.documentId}/ocr/${context.pageNumber}`;
  const force = document.has_ocr;
  ui.ocrButton.disabled = true;
  setIconButton(ui.ocrButton, "Reading this page…", "scan-text");
  ui.ocrBadge.textContent = "Processing";
  ui.ocrBadge.className = "badge processing";
  ui.fileStatus.textContent = "OCR running";
  setOcrLoading(true, document.current_page);
  setBusy(true, "OCR running…");
  try {
    const result = await api(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force }),
    });
    const current = activeDocument();
    if (activeSession()?.id === context.sessionId && current?.id === context.documentId && current.current_page === context.pageNumber) {
      state = await api("/api/state");
      renderState({ refreshPreview: false });
    }
    toast(`Page ${context.pageNumber} processed with ${result.model}`);
  } catch (error) {
    ui.ocrBadge.textContent = "Error";
    ui.ocrBadge.className = "badge";
    toast(error.message, "error");
  } finally {
    const current = activeDocument();
    if (current?.id === context.documentId && current.current_page === context.pageNumber) {
      setOcrLoading(false);
      ui.ocrButton.disabled = false;
      setIconButton(ui.ocrButton, current.has_ocr ? "Re-OCR this page" : "Run OCR on this page", "scan-text");
      updateBatchControls(state.active_ocr_job);
    }
    if (manualOcrContext === context) manualOcrContext = null;
    setBusy(false);
  }
}

function parseAutoOcrRange(value, totalPages) {
  const source = value.trim();
  if (!source) throw new Error("Enter a page or page range");
  const pages = new Set();
  for (const rawPart of source.split(",")) {
    const part = rawPart.trim();
    const match = part.match(/^(\d+)\s*(?:-\s*(\d+))?$/);
    if (!match) throw new Error("Use ranges such as 1-3, 5, 8-10");
    let start = Number(match[1]);
    let end = Number(match[2] || match[1]);
    if (start > end) [start, end] = [end, start];
    if (start < 1 || end > totalPages) throw new Error(`Pages must be between 1 and ${totalPages}`);
    for (let page = start; page <= end; page += 1) pages.add(page);
  }
  return [...pages].sort((a, b) => a - b);
}

function fullDocumentRange(document) {
  return document.num_pages === 1 ? "1" : `1-${document.num_pages}`;
}

function updateAutoOcrRangeStatus() {
  const document = activeDocument();
  if (!document) return false;
  try {
    const pages = parseAutoOcrRange(ui.autoOcrRange.value, document.num_pages);
    ui.autoOcrRange.classList.remove("invalid");
    ui.autoOcrRangeStatus.classList.remove("error");
    ui.autoOcrRangeStatus.textContent = `${pages.length} of ${document.num_pages} page${document.num_pages === 1 ? "" : "s"} selected`;
    return true;
  } catch (error) {
    ui.autoOcrRange.classList.add("invalid");
    ui.autoOcrRangeStatus.classList.add("error");
    ui.autoOcrRangeStatus.textContent = error.message;
    return false;
  }
}

function closeAutoOcrDialog() {
  if (ui.autoOcrDialog.open) ui.autoOcrDialog.close();
  else ui.autoOcrDialog.removeAttribute("open");
  ui.autoOcrToggle.checked = false;
}

function openAutoOcrDialog() {
  const document = activeDocument();
  if (!document) return;
  ui.autoOcrDialog.dataset.documentId = document.id;
  ui.autoOcrDocumentLabel.textContent = `${document.name} · ${document.num_pages} page${document.num_pages === 1 ? "" : "s"}`;
  ui.autoOcrRange.value = fullDocumentRange(document);
  updateAutoOcrRangeStatus();
  if (typeof ui.autoOcrDialog.showModal === "function") ui.autoOcrDialog.showModal();
  else ui.autoOcrDialog.setAttribute("open", "");
  requestAnimationFrame(() => {
    ui.autoOcrRange.focus();
    ui.autoOcrRange.select();
  });
}

async function runAllOcr(pageRange) {
  const session = activeSession();
  const document = activeDocument();
  if (!session || !document) return;
  const endpoint = `/api/sessions/${session.id}/files/${document.id}/ocr-all`;
  try {
    ui.autoOcrToggle.checked = true;
    ui.autoOcrToggle.disabled = true;
    ui.autoOcrControl.classList.add("disabled");
    const job = await api(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: false, page_range: pageRange }),
    });
    if (["queued", "running", "cancelling"].includes(job.status)) {
      state.active_ocr_job = job;
      updateBatchControls(job);
      toast(`OCR queued for ${job.total_pages} selected page${job.total_pages === 1 ? "" : "s"}`);
    } else {
      state = await api("/api/state");
      renderState({ refreshPreview: false });
      toast(job.status === "completed" ? "The selected pages already have OCR results" : (job.error || "OCR batch could not start"), job.status === "completed" ? "info" : "error");
    }
  } catch (error) {
    ui.autoOcrToggle.checked = false;
    ui.autoOcrToggle.disabled = false;
    ui.autoOcrControl.classList.remove("disabled");
    toast(error.message, "error");
  }
}

async function pollOcrJob(jobId) {
  if (activeJobId !== jobId) return;
  try {
    const job = await api(`/api/ocr-jobs/${jobId}`);
    if (activeJobId !== jobId) return;
    const completedChanged = (job.completed_pages || 0) > lastJobCompleted;
    updateBatchControls(job);
    if (completedChanged) {
      lastJobCompleted = job.completed_pages || 0;
      const currentDocumentId = activeDocument()?.id;
      state = await api("/api/state");
      if (activeDocument()?.id === currentDocumentId) renderState({ refreshPreview: false });
    }
    if (!["queued", "running", "cancelling"].includes(job.status)) {
      clearTimeout(jobPollTimer);
      jobPollTimer = null;
      activeJobId = null;
      state = await api("/api/state");
      renderState({ refreshPreview: false });
      if (job.status === "completed") toast("OCR completed for the selected pages");
      else if (job.status === "cancelled") toast("OCR batch stopped");
      else toast(job.error || "OCR batch finished with errors", "error");
    }
  } catch (error) {
    activeJobId = null;
    clearTimeout(jobPollTimer);
    toast(error.message, "error");
  }
}

async function cancelAllOcr() {
  if (!activeJobId) return;
  const jobId = activeJobId;
  ui.autoOcrToggle.checked = false;
  ui.autoOcrToggle.disabled = true;
  ui.autoOcrControl.classList.add("disabled");
  ui.autoOcrControl.dataset.tooltip = "Stopping automatic OCR…";
  try {
    const job = await api(`/api/ocr-jobs/${jobId}`, { method: "DELETE" });
    updateBatchControls(job);
  } catch (error) {
    ui.autoOcrToggle.checked = true;
    ui.autoOcrToggle.disabled = false;
    ui.autoOcrControl.classList.remove("disabled");
    toast(error.message, "error");
  }
}

function saveMarkdown(value) {
  clearTimeout(saveTimer);
  const document = activeDocument();
  if (!document) return;
  document.markdown = value;
  document.has_ocr = true;
  document.review_status = "unreviewed";
  document.reviewed_at = null;
  markdownDirty = true;
  updateWords(value);
  renderMarkdown(value);
  ui.copyButton.disabled = !value;
  ui.exportButton.disabled = false;
  updateReviewControls(document);
  ui.saveState.textContent = "Unsaved";
  const saveUrl = routeForDocument(`/markdown/${document.current_page}`);
  saveTimer = setTimeout(async () => {
    try {
      await api(saveUrl, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ markdown: value }),
      });
      const current = activeDocument();
      if (current?.id === document.id && current.current_page === document.current_page && ui.markdownEditor.value === value) {
        markdownDirty = false;
      }
      ui.saveState.textContent = "Saved";
      ui.fileStatus.textContent = "Edited";
    } catch (error) {
      ui.saveState.textContent = "Save failed";
      toast(error.message, "error");
    }
  }, 450);
}

async function savePendingMarkdown() {
  const document = activeDocument();
  if (!document || !markdownDirty) return;
  clearTimeout(saveTimer);
  const value = ui.markdownEditor.value;
  setBusy(true, "Saving before export…");
  await api(routeForDocument(`/markdown/${document.current_page}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown: value }),
  });
  markdownDirty = false;
  document.review_status = "unreviewed";
  document.reviewed_at = null;
  updateReviewControls(document);
  ui.saveState.textContent = "Saved";
}

async function setPageReviewStatus(status) {
  const document = activeDocument();
  if (!document?.has_ocr) return;
  try {
    await savePendingMarkdown();
    const result = await api(routeForDocument(`/review/${document.current_page}`), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    document.review_status = result.review_status;
    document.reviewed_at = result.reviewed_at;
    updateReviewControls(document);
    toast(status === "approved" ? `Page ${document.current_page} approved` : status === "needs_review" ? `Page ${document.current_page} flagged for review` : `Page ${document.current_page} returned to review`);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function retryFailedOcrPages() {
  const jobId = ui.retryBatchButton.dataset.jobId;
  if (!jobId) return;
  try {
    ui.retryBatchButton.disabled = true;
    const job = await api(`/api/ocr-jobs/${jobId}/retry`, { method: "POST" });
    state.active_ocr_job = job;
    state.recent_ocr_job = job;
    updateBatchControls(job);
    toast(`Retrying ${job.total_pages} page${job.total_pages === 1 ? "" : "s"}`);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    ui.retryBatchButton.disabled = false;
  }
}

function openImportDialog() {
  ui.fileInput.value = "";
  pendingUploadFiles = [];
  ui.importFileLabel.textContent = "Choose PDF or image files";
  ui.importStatus.textContent = "";
  ui.importStatus.classList.remove("error");
  ui.importDialog.classList.remove("is-busy");
  if (typeof ui.importDialog.showModal === "function") ui.importDialog.showModal();
  else ui.importDialog.setAttribute("open", "");
}

function setPendingUploadFiles(files, label = null) {
  pendingUploadFiles = [...files];
  ui.importFileLabel.textContent = label || (
    pendingUploadFiles.length === 1
      ? pendingUploadFiles[0].name
      : pendingUploadFiles.length > 1
        ? `${pendingUploadFiles.length} files selected`
        : "Choose PDF or image files"
  );
  ui.importStatus.textContent = pendingUploadFiles.length
    ? `${pendingUploadFiles.length} file${pendingUploadFiles.length === 1 ? "" : "s"} ready to upload`
    : "";
  ui.importStatus.classList.remove("error");
}

function clipboardImageFile(blob) {
  const extension = {
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/png": "png",
  }[blob.type] || "png";
  const timestamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "");
  return new File([blob], `clipboard-${timestamp}.${extension}`, { type: blob.type || "image/png" });
}

function pastedImageFiles(event) {
  const clipboard = event.clipboardData;
  if (!clipboard) return [];
  const candidates = [...(clipboard.files || [])];
  for (const item of [...(clipboard.items || [])]) {
    if (item.kind !== "file" || !item.type.startsWith("image/")) continue;
    const file = item.getAsFile();
    if (file && !candidates.includes(file)) candidates.push(file);
  }
  return candidates
    .filter((file) => file.type.startsWith("image/"))
    .map(clipboardImageFile);
}

function isTextInputTarget(target) {
  return target instanceof Element && Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
}

async function readClipboardImage() {
  if (!navigator.clipboard?.read) {
    ui.importStatus.textContent = "Press Ctrl+V to paste an image in this window";
    ui.importStatus.classList.add("error");
    return;
  }
  try {
    const clipboardItems = await navigator.clipboard.read();
    for (const item of clipboardItems) {
      const imageType = item.types.find((type) => type.startsWith("image/"));
      if (!imageType) continue;
      const image = clipboardImageFile(await item.getType(imageType));
      ui.fileInput.value = "";
      setPendingUploadFiles([image], `${image.name} · pasted image`);
      return;
    }
    throw new Error("The clipboard does not contain an image");
  } catch (error) {
    ui.importStatus.textContent = error.name === "NotAllowedError"
      ? "Clipboard access was blocked. Press Ctrl+V in this window instead."
      : error.message;
    ui.importStatus.classList.add("error");
  }
}

function closeImportDialog() {
  if (ui.importDialog.open) ui.importDialog.close();
  else ui.importDialog.removeAttribute("open");
}

function openExportDialog() {
  const document = activeDocument();
  if (!document) return;
  ui.exportDocumentLabel.textContent = document.name;
  ui.exportStatus.textContent = "";
  ui.exportStatus.classList.remove("error");
  ui.exportDialog.classList.remove("is-busy");
  ui.exportForm.elements.exportFormat.value = "markdown";
  if (typeof ui.exportDialog.showModal === "function") ui.exportDialog.showModal();
  else ui.exportDialog.setAttribute("open", "");
}

function closeExportDialog() {
  if (ui.exportDialog.open) ui.exportDialog.close();
  else ui.exportDialog.removeAttribute("open");
}

function downloadResponseBlob(response, blob) {
  const disposition = response.headers.get("content-disposition") || "";
  const utf8Name = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plainName = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  const filename = decodeURIComponent(utf8Name || plainName || "docslaju-export");
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

async function downloadExport(format) {
  const document = activeDocument();
  if (!document) return;
  const suffix = {
    markdown: "/export.zip",
    pdf: "/export.pdf",
    bundle: "/export-bundle.zip",
  }[format];
  if (!suffix) return;
  ui.exportDialog.classList.add("is-busy");
  ui.exportStatus.textContent = format === "markdown"
    ? "Packaging Markdown and assets…"
    : "Rendering the document PDF…";
  ui.exportStatus.classList.remove("error");
  try {
    await savePendingMarkdown();
    const response = await fetch(routeForDocument(suffix));
    if (!response.ok) {
      const payload = (response.headers.get("content-type") || "").includes("application/json")
        ? await response.json()
        : null;
      throw new Error(payload?.error || `Export failed (${response.status})`);
    }
    downloadResponseBlob(response, await response.blob());
    closeExportDialog();
    toast(format === "pdf" ? "PDF exported" : "Export package ready");
  } catch (error) {
    ui.exportStatus.textContent = error.message;
    ui.exportStatus.classList.add("error");
    toast(error.message, "error");
  } finally {
    ui.exportDialog.classList.remove("is-busy");
    setBusy(false);
  }
}

function selectedExtractionTemplate() {
  return extractionTemplates.find((template) => template.id === ui.extractionProfile.value) || null;
}

function extractionEndpoint(suffix = "") {
  return routeForDocument(`/extractions/${encodeURIComponent(ui.extractionProfile.value)}${suffix}`);
}

function extractionInput(definition, value, dataAttribute, dataValue) {
  const input = definition.type === "long_text" ? document.createElement("textarea") : document.createElement("input");
  if (input instanceof HTMLInputElement) {
    input.type = definition.type === "number" ? "number" : "text";
    if (input.type === "number") input.step = "any";
  } else {
    input.rows = 3;
  }
  input.dataset[dataAttribute] = dataValue;
  input.value = value ?? "";
  input.placeholder = "–";
  return input;
}

function addExtractionTableRow(table, item = {}) {
  const row = document.createElement("tr");
  row.dataset.extractionTableRow = table.key;
  for (const column of table.columns) {
    const cell = document.createElement("td");
    const input = extractionInput(column, item[column.key], "extractionColumn", column.key);
    input.setAttribute("aria-label", column.label);
    cell.append(input);
    row.append(cell);
  }
  const action = document.createElement("td");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-line-item extraction-edit-only";
  remove.innerHTML = iconMarkup("trash");
  remove.setAttribute("aria-label", "Remove line item");
  remove.addEventListener("click", () => row.remove());
  action.append(remove);
  row.append(action);
  ui.extractionDynamicFields.querySelector(`[data-extraction-table-body="${CSS.escape(table.key)}"]`)?.append(row);
}

function renderExtractionLayout(data) {
  ui.extractionDynamicFields.replaceChildren();
  if (!currentExtractionLayout) return;
  for (const section of currentExtractionLayout.sections || []) {
    const wrapper = document.createElement("section");
    wrapper.className = "extraction-layout-section";
    const heading = document.createElement("h3");
    heading.textContent = section.title;
    const fields = document.createElement("div");
    fields.className = "extraction-fields";
    for (const definition of section.fields || []) {
      const label = document.createElement("label");
      const caption = document.createElement("span");
      caption.textContent = definition.label;
      label.append(caption, extractionInput(definition, data[definition.key], "extractionField", definition.key));
      fields.append(label);
    }
    wrapper.append(heading, fields);
    ui.extractionDynamicFields.append(wrapper);
  }
  for (const table of currentExtractionLayout.tables || []) {
    const wrapper = document.createElement("section");
    wrapper.className = "extraction-layout-section";
    const heading = document.createElement("div");
    heading.className = "line-items-heading";
    const title = document.createElement("strong");
    title.textContent = table.title;
    const add = document.createElement("button");
    add.type = "button";
    add.className = "button button-quiet extraction-edit-only";
    add.textContent = "Add row";
    add.addEventListener("click", () => addExtractionTableRow(table));
    heading.append(title, add);
    const scroll = document.createElement("div");
    scroll.className = "line-items-scroll";
    const tableElement = document.createElement("table");
    tableElement.className = "line-items-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const column of table.columns) {
      const th = document.createElement("th");
      th.textContent = column.label;
      headRow.append(th);
    }
    headRow.append(document.createElement("th"));
    head.append(headRow);
    const body = document.createElement("tbody");
    body.dataset.extractionTableBody = table.key;
    tableElement.append(head, body);
    scroll.append(tableElement);
    wrapper.append(heading, scroll);
    ui.extractionDynamicFields.append(wrapper);
    for (const item of data[table.key] || []) addExtractionTableRow(table, item);
  }
  for (const list of currentExtractionLayout.lists || []) {
    const label = document.createElement("label");
    label.className = "extraction-notes";
    const caption = document.createElement("span");
    caption.textContent = list.label;
    const textarea = document.createElement("textarea");
    textarea.rows = 3;
    textarea.placeholder = "–";
    textarea.dataset.extractionList = list.key;
    textarea.value = (data[list.key] || []).join("\n");
    label.append(caption, textarea);
    ui.extractionDynamicFields.append(label);
  }
}

function setExtractionBusy(isBusy, message = "") {
  ui.extractionDialog.classList.toggle("is-busy", isBusy);
  ui.extractionMessage.textContent = message;
  ui.extractionMessage.classList.remove("error", "warning");
}

function setExtractionEditMode(isEditing) {
  extractionEditMode = Boolean(isEditing && currentExtraction);
  ui.extractionDialog.classList.toggle("is-editing", extractionEditMode);
  for (const control of ui.extractionDynamicFields.querySelectorAll("input, textarea")) {
    control.readOnly = !extractionEditMode;
  }
  for (const control of ui.extractionDynamicFields.querySelectorAll("button")) {
    control.disabled = !extractionEditMode;
  }
  ui.editExtractionButton.hidden = !currentExtraction || extractionEditMode;
  ui.saveExtractionButton.hidden = !currentExtraction || !extractionEditMode;
  ui.approveExtractionButton.disabled = extractionEditMode;
  ui.rerunExtractionButton.disabled = extractionEditMode;
  ui.extractionProfile.disabled = extractionEditMode;
}

function renderExtraction(extraction) {
  currentExtraction = extraction;
  const hasResult = Boolean(extraction?.data);
  ui.extractionEmpty.hidden = hasResult;
  ui.extractionEditor.hidden = !hasResult;
  ui.rerunExtractionButton.hidden = !hasResult;
  ui.approveExtractionButton.hidden = !hasResult;
  ui.extractionCsv.hidden = !hasResult;
  if (!hasResult) {
    ui.extractionDynamicFields.replaceChildren();
    setExtractionEditMode(false);
    return;
  }

  const data = extraction.data;
  renderExtractionLayout(data);
  const approved = extraction.status === "approved";
  ui.extractionStatus.textContent = approved ? "Approved" : "Needs review";
  ui.extractionStatus.className = `badge ${approved ? "complete" : "needs-review"}`;
  ui.extractionModel.textContent = extraction.model || "mistral-ocr-latest";
  ui.approveExtractionButton.textContent = approved ? "Return to review" : "Approve";
  setExtractionEditMode(false);
}

function numberOrNull(value) {
  return value === "" ? null : Number(value);
}

function readExtractionForm() {
  const data = { document_type: ui.extractionProfile.value };
  for (const input of ui.extractionForm.querySelectorAll("[data-extraction-field]")) {
    data[input.dataset.extractionField] = input.type === "number"
      ? numberOrNull(input.value)
      : (input.value.trim() || null);
  }
  for (const table of currentExtractionLayout?.tables || []) {
    data[table.key] = [...ui.extractionForm.querySelectorAll(`[data-extraction-table-row="${CSS.escape(table.key)}"]`)].map((row) => {
      const item = {};
      for (const input of row.querySelectorAll("[data-extraction-column]")) {
        item[input.dataset.extractionColumn] = input.type === "number" ? numberOrNull(input.value) : (input.value.trim() || null);
      }
      return item;
    });
  }
  for (const input of ui.extractionForm.querySelectorAll("[data-extraction-list]")) {
    data[input.dataset.extractionList] = input.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  }
  return data;
}

async function loadExtractionForTemplate() {
  const template = selectedExtractionTemplate();
  const document = activeDocument();
  if (!document) return;
  if (!template) {
    currentExtractionLayout = null;
    currentExtraction = null;
    renderExtraction(null);
    ui.runExtractionButton.disabled = true;
    ui.runExtractionButton.textContent = "Choose a document type";
    ui.extractionEmptyMessage.textContent = "Choose Invoice, Receipt, or CV / Résumé to load its fields and tables.";
    ui.extractionMessage.textContent = "";
    ui.extractionMessage.classList.remove("error", "warning");
    return;
  }
  currentExtractionLayout = template.layout;
  currentExtraction = null;
  setExtractionEditMode(false);
  renderExtraction(null);
  const documentSummary = activeSession()?.files.find((item) => item.id === document.id);
  const hasAnyOcr = Boolean(documentSummary?.completed_pages?.length);
  const exceedsPageLimit = document.num_pages > template.max_pages;
  ui.runExtractionButton.textContent = hasAnyOcr ? `Extract as ${template.label}` : `OCR + extract as ${template.label}`;
  ui.runExtractionButton.disabled = exceedsPageLimit;
  ui.extractionEmptyMessage.textContent = exceedsPageLimit
    ? `This template supports up to ${template.max_pages} pages. Split the document before extraction.`
    : hasAnyOcr
      ? "OCR already exists. Extraction uses one additional request and preserves the existing Markdown."
      : `One Mistral request will return both OCR Markdown and ${template.label.toLowerCase()} fields.`;
  ui.extractionMessage.textContent = "Loading saved extraction…";
  ui.extractionMessage.classList.remove("error", "warning");
  try {
    const payload = await api(extractionEndpoint());
    currentExtractionLayout = payload.layout;
    renderExtraction(payload.extraction);
    const outdated = payload.extraction && Number(payload.extraction.schema_version || 1) < Number(payload.schema_version || 1);
    ui.extractionMessage.textContent = outdated
      ? `This saved result uses ${template.label} template v${payload.extraction.schema_version || 1}. Run again to update it to v${payload.schema_version}.`
      : payload.extraction ? "Review every field before approval." : "";
    ui.extractionMessage.classList.toggle("warning", Boolean(outdated));
  } catch (error) {
    ui.extractionMessage.textContent = error.message;
    ui.extractionMessage.classList.add("error");
  }
}

async function openExtractionDialog() {
  const document = activeDocument();
  if (!document) return;
  ui.extractionDocumentLabel.textContent = `${document.name} · extracted data is stored separately from OCR Markdown`;
  if (typeof ui.extractionDialog.showModal === "function") ui.extractionDialog.showModal();
  else ui.extractionDialog.setAttribute("open", "");
  try {
    if (!extractionTemplates.length) {
      extractionTemplates = (await api("/api/extraction-templates")).templates;
      const placeholder = window.document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Choose a document type…";
      ui.extractionProfile.replaceChildren(placeholder, ...extractionTemplates.map((template) => {
        const option = window.document.createElement("option");
        option.value = template.id;
        option.textContent = template.label;
        return option;
      }));
    }
    ui.extractionProfile.value = "";
    await loadExtractionForTemplate();
  } catch (error) {
    ui.extractionMessage.textContent = error.message;
    ui.extractionMessage.classList.add("error");
  }
}

function closeExtractionDialog() {
  setExtractionEditMode(false);
  if (ui.extractionDialog.open) ui.extractionDialog.close();
  else ui.extractionDialog.removeAttribute("open");
}

async function runStructuredExtraction(confirmRerun = false) {
  const document = activeDocument();
  const documentSummary = activeSession()?.files.find((item) => item.id === document?.id);
  const hasAnyOcr = Boolean(documentSummary?.completed_pages?.length);
  const template = selectedExtractionTemplate();
  if (!template) return;
  if (confirmRerun && !window.confirm(`Run ${template.label} extraction again? This replaces the saved structured fields.`)) return;
  if (!confirmRerun && hasAnyOcr && !window.confirm("This document was already OCR'd without a structured template. Continue with one additional Mistral request? Existing Markdown will not be changed.")) return;
  setExtractionBusy(true, hasAnyOcr
    ? `Extracting ${template.label.toLowerCase()} fields while preserving existing Markdown…`
    : `Running OCR and ${template.label.toLowerCase()} extraction in one request…`);
  try {
    const payload = await api(extractionEndpoint("/run"), { method: "POST" });
    renderExtraction(payload.extraction);
    if (payload.ocr_included) {
      state = await api("/api/state");
      renderState({ refreshPreview: false });
    }
    ui.extractionMessage.textContent = payload.ocr_included
      ? "OCR and extraction completed in one request. Check every field before approval."
      : "Extraction complete. Existing Markdown was preserved; check every field before approval.";
  } catch (error) {
    ui.extractionMessage.textContent = error.message;
    ui.extractionMessage.classList.add("error");
  } finally {
    ui.extractionDialog.classList.remove("is-busy");
  }
}

async function saveStructuredExtraction(status = "needs_review") {
  if (!currentExtraction) return;
  setExtractionBusy(true, status === "approved" ? "Approving extraction…" : "Saving extraction…");
  try {
    const payload = await api(extractionEndpoint(), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data: readExtractionForm(), status }),
    });
    renderExtraction(payload.extraction);
    ui.extractionMessage.textContent = status === "approved"
      ? "Structured data approved."
      : "Structured data saved for further review.";
  } catch (error) {
    ui.extractionMessage.textContent = error.message;
    ui.extractionMessage.classList.add("error");
  } finally {
    ui.extractionDialog.classList.remove("is-busy");
  }
}

async function downloadExtractionCsv() {
  try {
    const response = await fetch(extractionEndpoint(".csv"));
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new Error(payload?.error || `CSV export failed (${response.status})`);
    }
    downloadResponseBlob(response, await response.blob());
    toast("Invoice data CSV exported");
  } catch (error) {
    toast(error.message, "error");
  }
}

function openSidebar() { ui.sidebar.classList.add("open"); ui.scrim.classList.add("open"); }
function closeSidebar() { ui.sidebar.classList.remove("open"); ui.scrim.classList.remove("open"); }
function setSidebarCollapsed(collapsed) {
  ui.appShell.classList.toggle("sidebar-collapsed", collapsed);
  setIconButton(ui.toggleSidebar, collapsed ? "Expand sidebar" : "Minimize sidebar", collapsed ? "panel-left-open" : "panel-left-close");
  try { localStorage.setItem("docslaju-sidebar-collapsed", String(collapsed)); } catch (_) { /* optional */ }
}

document.querySelector("#newSessionButton").addEventListener("click", () => createNewSession());

ui.newProjectButton.addEventListener("click", async () => {
  const name = window.prompt("Project folder name");
  if (!name?.trim()) return;
  try {
    state = await api("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    const created = state.projects.find((project) => project.name.toLowerCase() === name.trim().toLowerCase());
    if (created) expandedProjects.add(created.id);
    renderState({ refreshPreview: false });
    toast(`Project “${name.trim()}” created`);
  } catch (error) {
    toast(error.message, "error");
  }
});

ui.archiveToggle.addEventListener("click", () => {
  archiveExpanded = !archiveExpanded;
  ui.archivedSessionList.hidden = !archiveExpanded;
  ui.archiveToggle.setAttribute("aria-expanded", String(archiveExpanded));
});

ui.sessionMenu.addEventListener("click", async (event) => {
  event.stopPropagation();
  const actionButton = event.target.closest("[data-session-action]");
  if (!actionButton || !selectedSessionId) return;
  const item = sessionById(selectedSessionId);
  if (!item) return closeMenus();
  const action = actionButton.dataset.sessionAction;

  if (action === "move") {
    ui.projectPicker.hidden = !ui.projectPicker.hidden;
    return;
  }

  const sessionId = item.id;
  closeMenus();
  if (action === "rename") {
    const title = window.prompt("Rename session", item.title);
    if (title?.trim() && title.trim() !== item.title) {
      await patchSession(sessionId, { title: title.trim() }, "Session renamed");
    }
  } else if (action === "pin") {
    await patchSession(sessionId, { is_pinned: !item.is_pinned }, item.is_pinned ? "Session unpinned" : "Session pinned");
  } else if (action === "archive") {
    await patchSession(sessionId, { is_archived: !item.is_archived }, item.is_archived ? "Session restored" : "Session archived");
  } else if (action === "delete") {
    if (!window.confirm(`Delete “${item.title}” and all of its documents?`)) return;
    try {
      state = await api(`/api/sessions/${sessionId}`, { method: "DELETE" });
      renderState();
      toast("Session deleted");
    } catch (error) {
      toast(error.message, "error");
    }
  }
});

ui.projectMenu.addEventListener("click", async (event) => {
  event.stopPropagation();
  const actionButton = event.target.closest("[data-project-action]");
  if (!actionButton || !selectedProjectId) return;
  const project = state.projects.find((item) => item.id === selectedProjectId);
  if (!project) return closeMenus();
  const action = actionButton.dataset.projectAction;
  const projectId = project.id;
  closeMenus();

  if (action === "new-session") {
    await createNewSession(projectId);
  } else if (action === "rename") {
    const name = window.prompt("Rename project", project.name);
    if (!name?.trim() || name.trim() === project.name) return;
    try {
      state = await api(`/api/projects/${projectId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      renderState({ refreshPreview: false });
      toast("Project renamed");
    } catch (error) {
      toast(error.message, "error");
    }
  } else if (action === "delete") {
    if (!window.confirm(`Delete project “${project.name}”? Its sessions will move to Unfiled.`)) return;
    try {
      state = await api(`/api/projects/${projectId}`, { method: "DELETE" });
      expandedProjects.delete(projectId);
      renderState({ refreshPreview: false });
      toast("Project deleted; sessions moved to Unfiled");
    } catch (error) {
      toast(error.message, "error");
    }
  }
});

makeDropTarget(ui.unfiledDropZone, null);
makeDropTarget(ui.sessionList, null);
document.addEventListener("click", (event) => {
  if (!event.target.closest(".context-menu, .session-menu-button, .project-menu-button")) closeMenus();
  if (!event.target.closest("#documentPicker")) closeDocumentMenu();
  if (!event.target.closest("#workspaceSearch")) ui.searchResults.hidden = true;
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMenus();
  if (event.key.toLowerCase() === "n" && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.matches("input, textarea, select")) {
    event.preventDefault();
    createNewSession();
  }
});

for (const id of ["uploadButton", "emptyUploadButton"]) {
  document.querySelector(`#${id}`).addEventListener("click", openImportDialog);
}
ui.importFilePicker.addEventListener("click", () => ui.fileInput.click());
ui.fileInput.addEventListener("change", () => {
  setPendingUploadFiles(ui.fileInput.files);
});

ui.workspaceSearchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const query = ui.workspaceSearchInput.value;
  searchTimer = setTimeout(() => runWorkspaceSearch(query), 220);
});
ui.workspaceSearchInput.addEventListener("focus", () => {
  if (ui.workspaceSearchInput.value.trim() && ui.searchResults.childElementCount) {
    ui.searchResults.hidden = false;
  }
});
ui.importClipboardButton.addEventListener("click", readClipboardImage);
ui.importDialog.addEventListener("paste", (event) => {
  const pastedImages = pastedImageFiles(event);
  if (!pastedImages.length) {
    ui.importStatus.textContent = "The clipboard does not contain an image";
    ui.importStatus.classList.add("error");
    return;
  }
  event.preventDefault();
  ui.fileInput.value = "";
  setPendingUploadFiles(pastedImages, pastedImages.length === 1 ? `${pastedImages[0].name} · pasted image` : `${pastedImages.length} pasted images`);
});

document.addEventListener("paste", (event) => {
  if (activeDocument() || ui.importDialog.open || ui.importDialog.hasAttribute("open") || isTextInputTarget(event.target)) return;
  const images = pastedImageFiles(event);
  if (!images.length) return;
  event.preventDefault();
  toast(images.length === 1 ? "Pasting clipboard image…" : `Pasting ${images.length} clipboard images…`);
  uploadFiles(images);
});
ui.importCancel.addEventListener("click", closeImportDialog);
ui.importForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = [...pendingUploadFiles];
  if (!files.length) {
    ui.importStatus.textContent = "Choose one or more PDF or image files";
    ui.importStatus.classList.add("error");
    return;
  }
  ui.importDialog.classList.add("is-busy");
  ui.importStatus.classList.remove("error");
  ui.importStatus.textContent = "Uploading selected files…";
  const succeeded = await uploadFiles(files);
  ui.importDialog.classList.remove("is-busy");
  if (succeeded) {
    pendingUploadFiles = [];
    closeImportDialog();
  }
});
ui.documentPickerButton.addEventListener("click", (event) => {
  event.stopPropagation();
  if (ui.documentPickerButton.disabled) return;
  ui.documentMenu.hidden = !ui.documentMenu.hidden;
  ui.documentPickerButton.setAttribute("aria-expanded", String(!ui.documentMenu.hidden));
});
ui.previousPage.addEventListener("click", () => changePage(activeDocument().current_page - 1));
ui.nextPage.addEventListener("click", () => changePage(activeDocument().current_page + 1));
ui.pageNumber.addEventListener("change", () => changePage(ui.pageNumber.value));
ui.ocrButton.addEventListener("click", runOcr);
ui.needsReviewButton.addEventListener("click", () => {
  const status = activeDocument()?.review_status === "needs_review" ? "unreviewed" : "needs_review";
  setPageReviewStatus(status);
});
ui.approvePageButton.addEventListener("click", () => {
  const status = activeDocument()?.review_status === "approved" ? "unreviewed" : "approved";
  setPageReviewStatus(status);
});
ui.retryBatchButton.addEventListener("click", retryFailedOcrPages);
ui.autoOcrToggle.addEventListener("change", () => {
  if (ui.autoOcrToggle.checked) openAutoOcrDialog();
  else cancelAllOcr();
});
ui.autoOcrRange.addEventListener("input", updateAutoOcrRangeStatus);
ui.autoOcrAllPages.addEventListener("click", () => {
  const document = activeDocument();
  if (!document) return;
  ui.autoOcrRange.value = fullDocumentRange(document);
  updateAutoOcrRangeStatus();
});
ui.autoOcrCurrentPage.addEventListener("click", () => {
  const document = activeDocument();
  if (!document) return;
  ui.autoOcrRange.value = String(document.current_page);
  updateAutoOcrRangeStatus();
});
ui.autoOcrCancel.addEventListener("click", closeAutoOcrDialog);
ui.autoOcrDialog.addEventListener("cancel", () => {
  ui.autoOcrToggle.checked = false;
});
ui.autoOcrForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const document = activeDocument();
  if (!document || ui.autoOcrDialog.dataset.documentId !== document.id || !updateAutoOcrRangeStatus()) return;
  const pageRange = ui.autoOcrRange.value.trim();
  ui.autoOcrDialog.close();
  runAllOcr(pageRange);
});
ui.markdownEditor.addEventListener("input", (event) => saveMarkdown(event.target.value));
ui.copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(ui.markdownEditor.value);
    toast("Markdown copied");
  } catch (_) {
    ui.markdownEditor.select();
    document.execCommand("copy");
    toast("Markdown copied");
  }
});
ui.exportButton.addEventListener("click", openExportDialog);
ui.exportCancel.addEventListener("click", closeExportDialog);
ui.exportForm.addEventListener("submit", (event) => {
  event.preventDefault();
  downloadExport(ui.exportForm.elements.exportFormat.value);
});
ui.extractButton.addEventListener("click", openExtractionDialog);
ui.extractionProfile.addEventListener("change", loadExtractionForTemplate);
ui.extractionClose.addEventListener("click", closeExtractionDialog);
ui.runExtractionButton.addEventListener("click", () => runStructuredExtraction(false));
ui.rerunExtractionButton.addEventListener("click", () => runStructuredExtraction(true));
ui.editExtractionButton.addEventListener("click", () => {
  setExtractionEditMode(true);
  ui.extractionMessage.textContent = "Edit the fields and tables, then save your changes.";
  ui.extractionDynamicFields.querySelector("input, textarea")?.focus();
});
ui.saveExtractionButton.addEventListener("click", () => saveStructuredExtraction("needs_review"));
ui.approveExtractionButton.addEventListener("click", () => {
  saveStructuredExtraction(currentExtraction?.status === "approved" ? "needs_review" : "approved");
});
ui.extractionCsv.addEventListener("click", downloadExtractionCsv);
ui.fitPageButton.addEventListener("click", () => setPreviewView("fit-page"));
ui.fitWidthButton.addEventListener("click", () => setPreviewView("fit-width"));
ui.zoomOutButton.addEventListener("click", () => changeZoom(-10));
ui.zoomInButton.addEventListener("click", () => changeZoom(10));
ui.zoomLevel.addEventListener("click", () => setPreviewView("fit-page"));
ui.documentStage.addEventListener("wheel", (event) => {
  if (!(event.ctrlKey || event.metaKey) || ui.previewCanvas.hidden || !ui.pdfViewer.hidden) return;
  event.preventDefault();
  changeZoom(event.deltaY < 0 ? 10 : -10);
}, { passive: false });
ui.documentStage.addEventListener("dblclick", (event) => {
  if (ui.previewCanvas.hidden || !ui.pdfViewer.hidden) return;
  setPreviewView(previewView === "fit-width" ? "fit-page" : "fit-width");
});
ui.documentStage.addEventListener("pointerdown", (event) => {
  if (!ui.pdfViewer.hidden || !ui.documentStage.classList.contains("is-pannable") || event.button !== 0) return;
  panStart = {
    x: event.clientX,
    y: event.clientY,
    left: ui.documentStage.scrollLeft,
    top: ui.documentStage.scrollTop,
  };
  ui.documentStage.classList.add("is-panning");
  ui.documentStage.setPointerCapture(event.pointerId);
});
ui.documentStage.addEventListener("pointermove", (event) => {
  if (!panStart) return;
  ui.documentStage.scrollLeft = panStart.left - (event.clientX - panStart.x);
  ui.documentStage.scrollTop = panStart.top - (event.clientY - panStart.y);
});
function stopPanning() {
  panStart = null;
  ui.documentStage.classList.remove("is-panning");
}
ui.documentStage.addEventListener("pointerup", stopPanning);
ui.documentStage.addEventListener("pointercancel", stopPanning);

function hasDraggedFiles(event) {
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

document.addEventListener("dragover", (event) => {
  if (!hasDraggedFiles(event)) return;
  const bounds = ui.documentStage.getBoundingClientRect();
  const overPreview = event.clientX >= bounds.left && event.clientX <= bounds.right &&
    event.clientY >= bounds.top && event.clientY <= bounds.bottom;
  ui.documentStage.classList.toggle("drag-active", overPreview);
  if (overPreview) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }
}, true);

document.addEventListener("drop", (event) => {
  if (!hasDraggedFiles(event)) return;
  const isPreviewDrop = ui.documentStage.classList.contains("drag-active");
  ui.documentStage.classList.remove("drag-active");
  if (!isPreviewDrop) return;
  event.preventDefault();
  event.stopPropagation();
  const files = Array.from(event.dataTransfer.files || []);
  if (!files.length) return;
  uploadFiles(files);
}, true);

document.addEventListener("dragleave", (event) => {
  if (event.relatedTarget === null) ui.documentStage.classList.remove("drag-active");
}, true);

new ResizeObserver(() => {
  updatePannableState();
}).observe(ui.documentStage);
document.querySelector("#openSidebar").addEventListener("click", openSidebar);
document.querySelector("#closeSidebar").addEventListener("click", closeSidebar);
ui.toggleSidebar.addEventListener("click", () => {
  setSidebarCollapsed(!ui.appShell.classList.contains("sidebar-collapsed"));
});
ui.scrim.addEventListener("click", closeSidebar);

try {
  setSidebarCollapsed(localStorage.getItem("docslaju-sidebar-collapsed") === "true");
} catch (_) {
  setSidebarCollapsed(false);
}
refresh().catch((error) => toast(error.message, "error"));
