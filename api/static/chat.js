// -*- coding: utf-8 -*-
/**
 * AgentScope Chat 前端逻辑
 */

const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('sendBtn');
const stopBtn = document.getElementById('stopBtn');
const userIdEl = document.getElementById('userId');
const sessionInfoEl = document.getElementById('sessionInfo');
const sessionListEl = document.getElementById('sessionList');
const sidebarEl = document.getElementById('sidebar');

let currentSessionId = '';
let isStreaming = false;
let currentAbortController = null;

// ============ Sidebar ============

function toggleSidebar() {
  sidebarEl.classList.toggle('collapsed');
}

async function loadSessionList() {
  const userId = userIdEl.value.trim() || 'anonymous';
  try {
    const resp = await fetch(`/sessions/${encodeURIComponent(userId)}`);
    const data = await resp.json();
    renderSessionList(data.sessions || []);
  } catch (err) {
    console.error('加载会话列表失败:', err);
  }
}

function renderSessionList(sessions) {
  if (!sessions.length) {
    sessionListEl.innerHTML = '<div class="session-list-empty">暂无历史会话</div>';
    return;
  }

  sessionListEl.innerHTML = sessions.map(s => {
    const isActive = s.session_id === currentSessionId;
    const title = escapeHtml(s.title || '新会话');
    const time = formatTime(s.last_active);
    const msgCount = s.message_count || 0;
    const shortId = s.session_id.slice(0, 8);
    return `
      <div class="session-item ${isActive ? 'active' : ''}"
           onclick="switchSession('${s.user_id}', '${s.session_id}')"
           title="${escapeHtml(s.session_id)}">
        <span class="session-icon">💬</span>
        <div class="session-info">
          <div class="session-title">${title}</div>
          <div class="session-meta">${time} · ${msgCount} 条消息</div>
        </div>
        <button class="btn-delete" onclick="event.stopPropagation(); confirmDelete('${s.user_id}', '${s.session_id}', '${title}')" title="删除会话">✕</button>
      </div>
    `;
  }).join('');
}

async function switchSession(userId, sessionId) {
  if (isStreaming) return;
  if (sessionId === currentSessionId) return;

  currentSessionId = sessionId;
  sessionInfoEl.textContent = `会话: ${sessionId.slice(0, 8)}...`;
  messagesEl.innerHTML = '';

  // 加载消息历史
  try {
    const resp = await fetch(`/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/messages`);
    const data = await resp.json();

    if (data.error) {
      addMessage('error', `加载失败: ${data.error}`);
      return;
    }

    const messages = data.messages || [];
    if (messages.length === 0) {
      addSystemMessage('会话无历史消息');
    } else {
      for (const msg of messages) {
        const role = msg.role === 'user' ? 'user' : 'agent';
        addMessage(role, msg.content);
      }
    }
  } catch (err) {
    addMessage('error', `加载会话失败: ${err.message}`);
  }

  // 更新侧边栏高亮
  updateSessionListHighlight();
  inputEl.focus();
}

function updateSessionListHighlight() {
  const items = sessionListEl.querySelectorAll('.session-item');
  items.forEach(item => {
    // 通过 onclick 属性判断是否是当前会话
    const onclick = item.getAttribute('onclick') || '';
    if (onclick.includes(`'${currentSessionId}'`) && onclick.startsWith('switchSession')) {
      item.classList.add('active');
    } else {
      item.classList.remove('active');
    }
  });
}

// ============ Confirm Dialog ============

function confirmDelete(userId, sessionId, title) {
  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `
    <div class="confirm-dialog">
      <p>确定删除会话「${title}」？<br><small style="color:var(--text-dim)">此操作不可撤销</small></p>
      <div class="btn-group">
        <button class="btn-cancel" onclick="this.closest('.confirm-overlay').remove()">取消</button>
        <button class="btn-confirm-delete" onclick="doDelete('${userId}', '${sessionId}', this)">删除</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
}

async function doDelete(userId, sessionId, btn) {
  const overlay = btn.closest('.confirm-overlay');
  try {
    await fetch(`/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    });
    overlay.remove();

    // 如果删除的是当前会话，清空聊天
    if (sessionId === currentSessionId) {
      currentSessionId = '';
      messagesEl.innerHTML = '';
      sessionInfoEl.textContent = '会话: —';
      addSystemMessage('会话已删除');
    }

    await loadSessionList();
  } catch (err) {
    overlay.remove();
    addMessage('error', `删除失败: ${err.message}`);
  }
}

// ============ Time Format ============

function formatTime(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp * 1000);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();

  if (isToday) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }

  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) {
    return '昨天 ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }

  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) +
    ' ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

// ============ Session Management ============

function newSession() {
  currentSessionId = '';
  messagesEl.innerHTML = '';
  sessionInfoEl.textContent = '会话: —';
  addSystemMessage('已创建新会话，输入消息开始对话');
  updateSessionListHighlight();
  inputEl.focus();
}

// ============ Messages ============

function addMessage(role, text, extra) {
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  if (role === 'tool') {
    div.innerHTML = `<div class="msg-label">🔧 ${escapeHtml(extra || '工具调用')}</div><pre class="tool-content">${escapeHtml(text)}</pre>`;
  } else if (role === 'tool-result') {
    div.innerHTML = `<div class="msg-label">📋 工具结果</div><pre class="tool-content">${escapeHtml(text)}</pre>`;
  } else if (role === 'error') {
    div.textContent = text;
  } else {
    div.textContent = text;
  }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function addSystemMessage(text) {
  const div = document.createElement('div');
  div.style.cssText = 'text-align:center;color:#666;font-size:12px;padding:8px;';
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function escapeHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ============ SSE Event Handling ============

function handleSSEEvent(eventType, data, state) {
  switch (eventType) {
    case 'session':
      if (data.session_id && !currentSessionId) {
        currentSessionId = data.session_id;
        sessionInfoEl.textContent = `会话: ${currentSessionId.slice(0, 8)}...`;
      }
      break;

    case 'text_delta':
      if (data.delta) {
        state.agentText += data.delta;
        updateAgentMessage(state);
      }
      break;

    case 'thinking_delta':
      if (data.delta) {
        if (!state.thinkingBlock) {
          state.thinkingBlock = createThinkingBlock();
        }
        state.thinkingContent += data.delta;
        updateThinkingContent(state.thinkingBlock, state.thinkingContent);
      }
      break;

    case 'tool_call':
      console.log('Tool call event:', data);
      if (data.tool_name) {
        const toolInfo = data.tool_args ? `${data.tool_name}\n${JSON.stringify(data.tool_args, null, 2)}` : data.tool_name;
        addMessage('tool', toolInfo, data.tool_name);
      }
      break;

    case 'tool_result':
      console.log('Tool result event:', data);
      if (data.result) {
        addMessage('tool-result', typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2));
      }
      break;

    case 'reply_end':
      // 回复结束
      break;

    case 'error':
      if (data.message) {
        addMessage('error', `错误: ${data.message}`);
      }
      break;
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function updateAgentMessage(state) {
  const { agentDiv, label, thinkingBlock, agentText } = state;
  // 清空并重建内容
  agentDiv.innerHTML = '';
  agentDiv.appendChild(label);

  // 如果有思考内容，添加到前面
  if (thinkingBlock) {
    agentDiv.appendChild(thinkingBlock);
  }

  // 添加正文
  const textNode = document.createTextNode(agentText);
  agentDiv.appendChild(textNode);
}

function createThinkingBlock() {
  const details = document.createElement('details');
  details.className = 'thinking-block';
  details.open = true;

  const summary = document.createElement('summary');
  summary.className = 'thinking-header';
  summary.innerHTML = '<span class="thinking-icon">💭</span> <span class="thinking-title">思考过程</span>';

  const content = document.createElement('div');
  content.className = 'thinking-content';

  details.appendChild(summary);
  details.appendChild(content);

  // 自动收起：3秒后折叠
  setTimeout(() => {
    details.open = false;
  }, 3000);

  return details;
}

function updateThinkingContent(thinkingBlock, content) {
  const contentDiv = thinkingBlock.querySelector('.thinking-content');
  if (contentDiv) {
    contentDiv.textContent = content;
  }
}

// ============ Send Message ============

async function send() {
  const text = inputEl.value.trim();
  if (!text || isStreaming) return;

  const userId = userIdEl.value.trim() || 'anonymous';

  addMessage('user', text);
  inputEl.value = '';
  inputEl.style.height = 'auto';

  isStreaming = true;
  sendBtn.style.display = 'none';
  stopBtn.style.display = 'inline-block';

  // 添加中止控制器
  const abortController = new AbortController();
  currentAbortController = abortController;
  const timeoutId = setTimeout(() => abortController.abort(), 300000); // 5分钟超时

  const agentDiv = addMessage('agent', '');
  const label = document.createElement('div');
  label.className = 'msg-label';
  label.textContent = '🤖 Agent';
  agentDiv.prepend(label);

  // 使用状态对象管理流式回复
  const state = {
    agentDiv,
    label,
    agentText: '',
    thinkingBlock: null,
    thinkingContent: ''
  };

  try {
    const resp = await fetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        user_id: userId,
        session_id: currentSessionId || undefined,
      }),
      signal: abortController.signal,
    });

    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEventType = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEventType = line.slice(7).trim();
          continue;
        }
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            handleSSEEvent(currentEventType, data, state);
            currentEventType = '';
          } catch {}
        }
      }
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      addSystemMessage('⏹️ 已停止生成');
    } else {
      addMessage('error', `请求失败: ${err.message}`);
    }
  }

  clearTimeout(timeoutId);
  currentAbortController = null;
  isStreaming = false;
  sendBtn.style.display = 'inline-block';
  stopBtn.style.display = 'none';

  // 消息发送完成后刷新会话列表
  await loadSessionList();
  inputEl.focus();
}

function stop() {
  if (currentAbortController) {
    currentAbortController.abort();
  }
}

// ============ Input Events ============

inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
});

inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

// 用户 ID 变更时重新加载会话列表
userIdEl.addEventListener('change', () => {
  loadSessionList();
});

// ============ Init ============

addSystemMessage('欢迎使用 AgentScope Chat，输入消息开始对话');
loadSessionList();
inputEl.focus();
