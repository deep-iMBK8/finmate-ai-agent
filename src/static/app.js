let currentDocumentId = "";
let currentDocumentLabel = "";
let currentUserId = "000001";
let currentSessionId = "";
let chatSearchTimer = null;
const documentLabels = new Map();

function setSystemState(text) {
  document.getElementById("systemState").textContent = text;
}
function setStatus(text) {
  const status = document.getElementById("status");
  if (status) status.textContent = text;
}
function setAnswer(text) {
  clearChat();
  appendChatMessage("assistant", text, "Finmate");
}
function clearChat() {
  document.getElementById("answer").innerHTML = "";
}
function showWelcome() {
  document.getElementById("answer").innerHTML = `
    <div class="welcome">
      <h2>무엇을 확인해볼까요?</h2>
      <p>문서를 선택한 뒤 질문하면 근거를 찾아 답변합니다.</p>
    </div>
  `;
}
function appendChatMessage(role, text, label) {
  const messages = document.getElementById("answer");
  const message = document.createElement("div");
  message.className = "message " + role;

  const caption = document.createElement("div");
  caption.className = "message-label";
  caption.textContent = label || (role === "user" ? "나" : "AI");

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;

  message.appendChild(caption);
  message.appendChild(bubble);
  messages.appendChild(message);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}
function setChatContext(text) {
  document.getElementById("chatContext").textContent = text;
}
function setActiveChatTitle(text) {
  document.getElementById("activeChatTitle").textContent = text || "새 채팅";
}
function setStep(id, text) {
  const step = document.getElementById(id);
  if (step) step.textContent = text;
}
function setBusy(buttonId, busy, label) {
  const button = document.getElementById(buttonId);
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? label : button.dataset.label;
}
function compactDocumentLabel(doc) {
  const title = doc.document_title || doc.original_filename || doc.document_id || "문서";
  const company = doc.company ? doc.company + " · " : "";
  const identifier = doc.original_filename || doc.document_id || "";
  const identifierLabel = identifier
    .replace(/\.[^.]+$/, "")
    .replace(/^\d{6}_/, "")
    .replace(/^\d{8}_\d{6}_/, "");
  const normalizedTitle = title.replace(/\.[^.]+$/, "");
  const suffix =
    identifierLabel && identifierLabel !== title && identifierLabel !== normalizedTitle
      ? " · " + identifierLabel
      : "";
  const label = company + title + suffix;
  return label.length > 58 ? label.slice(0, 57) + "…" : label;
}
function displayNameFromPath(path) {
  return (path || "")
    .split(/[\\/]/)
    .pop()
    .replace(/^\d{8}_\d{6}_/, "")
    .replace(/^\d{6}_/, "");
}
function selectedDocumentLabel(fallbackId) {
  return currentDocumentLabel || documentLabels.get(fallbackId) || "문서";
}
function setSelectedDocument(docId, label, fileInfoText) {
  currentDocumentId = docId || "";
  currentDocumentLabel = label || documentLabels.get(docId) || "";
  document.getElementById("docInfo").textContent =
    currentDocumentLabel || "아직 선택된 문서가 없습니다.";
  document.getElementById("fileInfo").textContent =
    fileInfoText || "업로드하거나 저장 문서를 불러오세요.";
}

async function postForm(url, formData) {
  const res = await fetch(url, { method: "POST", body: formData });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || JSON.stringify(data));
  return data;
}

async function postJson(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || JSON.stringify(data));
  return data;
}

async function deleteJson(url) {
  const res = await fetch(url, { method: "DELETE" });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || JSON.stringify(data));
  return data;
}

async function loadDocuments() {
  const res = await fetch("/api/documents");
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || JSON.stringify(data));

  const select = document.getElementById("documentSelect");
  select.innerHTML = "";
  if (!data.documents.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "저장된 문서가 없습니다";
    select.appendChild(option);
  } else {
    data.documents.forEach((doc) => {
      const label = compactDocumentLabel(doc);
      const option = document.createElement("option");
      option.value = doc.document_id;
      option.textContent = label;
      option.title = `${doc.document_id} · ${doc.company || ""} · ${doc.document_type || ""}`;
      option.dataset.userId = doc.user_id;
      option.dataset.label = label;
      documentLabels.set(doc.document_id, label);
      select.appendChild(option);
    });
  }

  if (currentDocumentId && documentLabels.has(currentDocumentId)) {
    setSelectedDocument(currentDocumentId, documentLabels.get(currentDocumentId), document.getElementById("fileInfo").textContent);
    setChatContext("선택 문서: " + selectedDocumentLabel(currentDocumentId) + " · 검색 범위: 선택 문서 + DB 관련 문서");
  }

  return data;
}

function renderChatSessions(sessions) {
  const list = document.getElementById("chatSessionList");
  list.innerHTML = "";

  if (!sessions.length) {
    const empty = document.createElement("div");
    empty.className = "empty-chat-list";
    empty.textContent = "최근 채팅이 없습니다.";
    list.appendChild(empty);
    return;
  }

  sessions.forEach((session) => {
    const item = document.createElement("div");
    item.className = "chat-session-row";
    if (session.session_id === currentSessionId) item.classList.add("active");

    const button = document.createElement("button");
    button.type = "button";
    button.className = "chat-session-item";
    button.dataset.sessionId = session.session_id;

    const title = document.createElement("span");
    title.className = "chat-session-title";
    title.textContent = session.title || "제목 없는 채팅";

    const preview = document.createElement("span");
    preview.className = "chat-session-preview";
    preview.textContent = session.last_message || `${session.message_count || 0}개 메시지`;

    button.appendChild(title);
    button.appendChild(preview);
    button.addEventListener("click", () => loadChatSession(session.session_id, session.title));

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "chat-session-delete";
    deleteButton.textContent = "...";
    deleteButton.title = "채팅 기록 삭제";
    deleteButton.setAttribute("aria-label", "채팅 기록 삭제");
    deleteButton.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteChatSession(session.session_id, session.title);
    });

    item.appendChild(button);
    item.appendChild(deleteButton);
    list.appendChild(item);
  });
}

async function deleteChatSession(sessionId, title) {
  if (!sessionId) return;
  const confirmed = window.confirm(`채팅 기록 "${title || "제목 없는 채팅"}"을 삭제할까요?`);
  if (!confirmed) return;

  setSystemState("채팅 기록 삭제 중");
  try {
    const params = new URLSearchParams({ user_id: currentUserId });
    await deleteJson(`/api/chat/sessions/${encodeURIComponent(sessionId)}?${params.toString()}`);
    if (currentSessionId === sessionId) {
      currentSessionId = "";
      setActiveChatTitle("새 채팅");
      showWelcome();
      setStep("answerState", "대기 중");
    }
    await loadChatSessions();
    setSystemState("채팅 기록 삭제 완료");
  } catch (err) {
    setSystemState("오류");
    setStatus("채팅 기록 삭제 오류:\\n" + err.message);
  }
}

async function loadChatSessions() {
  const query = document.getElementById("chatSearch").value.trim();
  const params = new URLSearchParams({ user_id: currentUserId });
  if (query) params.set("q", query);
  const res = await fetch(`/api/chat/sessions?${params.toString()}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || JSON.stringify(data));
  renderChatSessions(data.sessions || []);
  return data;
}

async function loadChatSession(sessionId, title) {
  if (!sessionId) return;
  const params = new URLSearchParams({ user_id: currentUserId });
  const res = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}?${params.toString()}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || JSON.stringify(data));

  currentSessionId = sessionId;
  clearChat();
  (data.messages || []).forEach((message) => {
    appendChatMessage(message.role, message.content, message.role === "user" ? "나" : "AI");
    if (message.document_id) currentDocumentId = message.document_id;
  });
  if (!(data.messages || []).length) showWelcome();
  setActiveChatTitle(title || "이전 채팅");
  if (currentDocumentId) {
    if (!documentLabels.has(currentDocumentId)) {
      await loadDocuments().catch(() => null);
    }
    const select = document.getElementById("documentSelect");
    const matchingOption = Array.from(select.options).find((option) => option.value === currentDocumentId);
    const label = matchingOption ? matchingOption.textContent : documentLabels.get(currentDocumentId);
    setSelectedDocument(currentDocumentId, label, "이 채팅에서 사용한 문서입니다.");
    setChatContext("선택 문서: " + selectedDocumentLabel(currentDocumentId) + " · 이전 채팅 불러옴");
  }
  await loadChatSessions();
}

function startNewChat() {
  currentSessionId = "";
  setActiveChatTitle("새 채팅");
  showWelcome();
  setStep("answerState", "대기 중");
  loadChatSessions().catch((err) => setStatus("채팅 목록 조회 오류:\\n" + err.message));
}

document.getElementById("uploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setBusy("uploadBtn", true, "처리 중...");
  setSystemState("문서 처리 중");
  setStep("uploadState", "파싱/청킹/저장 실행 중");
  setStep("indexState", "Chroma 저장 중");
  setStep("answerState", "대기 중");
  setStatus("문서를 읽고 검색 가능하게 저장하는 중입니다...");
  const form = event.currentTarget;
  const fd = new FormData(form);
  try {
    const data = await postForm("/api/upload", fd);
    currentUserId = data.user_id;
    const doc = data.data || {};
    const label = compactDocumentLabel({
      document_id: data.document_id,
      document_title: doc.document_title,
      original_filename: displayNameFromPath(data.uploaded_path),
      company: doc.company,
    });
    setSelectedDocument(
      data.document_id,
      label,
      "업로드 파일: " + (displayNameFromPath(data.uploaded_path) || "업로드 문서")
    );
    setChatContext("선택 문서: " + selectedDocumentLabel(data.document_id) + " · 검색 범위: 선택 문서 + DB 관련 문서");
    setActiveChatTitle("새 채팅");
    if (data.status === "partial") {
      setAnswer("문서 파싱과 청킹은 완료했지만 벡터 저장에 실패했습니다. 아래 로그의 Chroma/Vertex 오류를 확인하세요.");
      setSystemState("문서 부분 처리 완료");
    } else {
      setAnswer("문서를 읽고 검색 가능하게 저장했습니다. 이제 질문할 수 있습니다.");
      setSystemState("문서 처리 완료");
    }
    setStep("uploadState", "완료");
    setStep("indexState", data.status === "partial" ? "실패" : "완료");
    setStatus(data.log);
  } catch (err) {
    setSystemState("오류");
    setStep("uploadState", "실패");
    setStep("indexState", "실패");
    setStatus("오류:\\n" + err.message);
  } finally {
    setBusy("uploadBtn", false);
  }
});

document.getElementById("loadDocsBtn").addEventListener("click", async () => {
  setSystemState("문서 목록 조회 중");
  try {
    const data = await loadDocuments();
    setSystemState("문서 목록 조회 완료");
    setStatus(JSON.stringify(data, null, 2));
  } catch (err) {
    setSystemState("오류");
    setStatus("오류:\\n" + err.message);
  }
});

document.getElementById("selectDocBtn").addEventListener("click", async () => {
  const select = document.getElementById("documentSelect");
  const option = select.options[select.selectedIndex];
  if (!select.value) {
    setStatus("선택할 문서가 없습니다.");
    return;
  }
  currentUserId = option.dataset.userId || currentUserId;
  setSelectedDocument(select.value, option.dataset.label || option.textContent, "저장된 문서를 사용 중입니다.");
  setChatContext("선택 문서: " + selectedDocumentLabel(select.value) + " · 검색 범위: 선택 문서 + DB 관련 문서");
  setActiveChatTitle("새 채팅");
  setAnswer("저장된 문서를 불러왔습니다. 이제 질문하거나 요약할 수 있습니다.");
  setSystemState("저장 문서 선택됨");
  setStatus("질문은 선택 문서를 우선 참고하고, DB에 저장된 관련 문서도 함께 검색합니다. 요약은 선택 문서 기준으로 실행됩니다.\\n" + selectedDocumentLabel(select.value));
});

document.getElementById("deleteDocBtn").addEventListener("click", async () => {
  const select = document.getElementById("documentSelect");
  const documentId = select.value || currentDocumentId;
  if (!documentId) {
    setStatus("삭제할 문서를 선택하세요.");
    return;
  }

  const confirmed = window.confirm(
    `문서 ${documentId}를 삭제할까요?\nMySQL, Chroma, 업로드 원본, JSON, TXT 파일이 함께 삭제됩니다.`
  );
  if (!confirmed) return;

  setBusy("deleteDocBtn", true, "삭제 중...");
  setSystemState("문서 삭제 중");
  setStatus("문서를 삭제하는 중입니다...");
  try {
    const data = await deleteJson(`/api/documents/${encodeURIComponent(documentId)}?delete_chroma=true&delete_files=true`);
    if (currentDocumentId === documentId) {
      setSelectedDocument("", "", "업로드하거나 저장 문서를 불러오세요.");
      setChatContext("선택된 문서가 없습니다. 왼쪽 사이드바에서 문서를 업로드하거나 불러오세요.");
      setActiveChatTitle("새 채팅");
      clearChat();
      appendChatMessage("assistant", "선택한 문서를 삭제했습니다.", "AI");
    }
    const documents = await loadDocuments();
    setSystemState("문서 삭제 완료");
    setStatus(JSON.stringify({ delete_result: data, documents }, null, 2));
  } catch (err) {
    setSystemState("오류");
    setStatus("오류:\\n" + err.message);
  } finally {
    setBusy("deleteDocBtn", false);
  }
});

document.getElementById("askBtn").addEventListener("click", async () => {
  if (!currentDocumentId) {
    setAnswer("먼저 문서를 읽어 검색 가능하게 저장하세요.");
    return;
  }
  const questionInput = document.getElementById("question");
  const question = questionInput.value.trim();
  if (!question) {
    setAnswer("질문을 입력하세요.");
    return;
  }
  questionInput.value = "";
  questionInput.placeholder = "문서에 대해 질문해보세요";
  setBusy("askBtn", true, "답변 생성 중...");
  setSystemState("RAG 답변 생성 중");
  setStep("answerState", "질의응답 실행 중");
  appendChatMessage("user", question, "나");
  const pendingBubble = appendChatMessage("assistant", "답변을 생성하고 있습니다...", "AI");
  try {
    const data = await postJson("/api/ask", {
      document_id: currentDocumentId,
      question: question,
      user_id: currentUserId,
      session_id: currentSessionId
    });
    currentSessionId = data.session_id || currentSessionId;
    setActiveChatTitle(question.length > 34 ? question.slice(0, 33) + "…" : question);
    setSystemState("답변 완료");
    setStep("answerState", "완료");
    pendingBubble.textContent = data.answer;
    loadChatSessions().catch(() => {});
  } catch (err) {
    setSystemState("오류");
    setStep("answerState", "실패");
    pendingBubble.textContent = "오류:\\n" + err.message;
  } finally {
    setBusy("askBtn", false);
  }
});

document.getElementById("summaryBtn").addEventListener("click", async () => {
  if (!currentDocumentId) {
    setAnswer("먼저 문서를 읽어 검색 가능하게 저장하세요.");
    return;
  }
  setBusy("summaryBtn", true, "요약 생성 중...");
  setSystemState("요약 생성 중");
  setStep("answerState", "요약 실행 중");
  appendChatMessage("user", "이 문서를 요약해줘.", "나");
  const pendingBubble = appendChatMessage("assistant", "요약을 생성하고 있습니다...", "AI");
  try {
    const data = await postJson("/api/summary", {
      document_id: currentDocumentId,
      user_id: currentUserId,
      session_id: currentSessionId
    });
    currentSessionId = data.session_id || currentSessionId;
    setActiveChatTitle("문서 요약");
    setSystemState("요약 완료");
    setStep("answerState", "완료");
    pendingBubble.textContent = data.answer;
    loadChatSessions().catch(() => {});
  } catch (err) {
    setSystemState("오류");
    setStep("answerState", "실패");
    pendingBubble.textContent = "오류:\\n" + err.message;
  } finally {
    setBusy("summaryBtn", false);
  }
});

document.getElementById("newChatBtn").addEventListener("click", startNewChat);

document.getElementById("sidebarToggleBtn").addEventListener("click", () => {
  document.body.classList.add("sidebar-collapsed");
});

document.getElementById("sidebarExpandBtn").addEventListener("click", () => {
  document.body.classList.remove("sidebar-collapsed");
});

document.getElementById("chatSearch").addEventListener("input", () => {
  window.clearTimeout(chatSearchTimer);
  chatSearchTimer = window.setTimeout(() => {
    loadChatSessions().catch((err) => setStatus("채팅 검색 오류:\\n" + err.message));
  }, 180);
});

document.getElementById("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    document.getElementById("askBtn").click();
  }
});

loadDocuments()
  .catch(() => null)
  .finally(() => {
    loadChatSessions().catch((err) => setStatus("채팅 목록 조회 오류:\\n" + err.message));
  });
