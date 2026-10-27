/**
 * Backend client.
 *
 * Every call has a timeout, so a dead backend shows "Disconnected" instead of
 * leaving the interface spinning forever.
 */
import { emit } from './bus.js';

const BASE = '';
const DEFAULT_TIMEOUT = 15000;

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code || '';
  }
}

async function request(path, { method = 'GET', body, timeout = DEFAULT_TIMEOUT, raw = false } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  let response;
  try {
    response = await fetch(BASE + path, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      cache: 'no-store',
    });
  } catch (err) {
    clearTimeout(timer);
    emit('api:offline', { path });
    throw new ApiError(err.name === 'AbortError' ? 'timeout' : 'network', 0, 'network');
  }
  clearTimeout(timer);

  if (raw) {
    if (!response.ok) throw await toError(response);
    return response;
  }
  let data = null;
  const text = await response.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = { raw: text }; }
  }
  if (!response.ok) {
    const message = (data && data.error) || response.statusText;
    throw new ApiError(message, response.status, String(message).split(':')[0]);
  }
  emit('api:online', { path });
  return data;
}

async function toError(response) {
  let message = response.statusText;
  try {
    const data = await response.json();
    if (data && data.error) message = data.error;
  } catch { /* body was not JSON */ }
  return new ApiError(message, response.status, String(message).split(':')[0]);
}

export const api = {
  health: () => request('/api/health', { timeout: 8000 }),
  diagnostics: () => request('/api/diagnostics'),
  getConfig: () => request('/api/config'),
  setConfig: (patch) => request('/api/config', { method: 'POST', body: patch }),
  setPower: (state) => request('/api/power', { method: 'POST', body: { state } }),

  // Language-model provider. There is deliberately no getter for the key:
  // the backend will say whether one is stored, never what it is.
  llmStatus: () => request('/api/llm/status', { timeout: 12000 }),
  llmModels: () => request('/api/llm/models', { timeout: 15000 }),
  llmSetKey: (key, provider = 'openai') =>
    request('/api/llm/key', { method: 'POST', body: { provider, key } }),
  llmClearKey: (provider = 'openai') =>
    request('/api/llm/key?provider=' + encodeURIComponent(provider), { method: 'DELETE' }),
  llmConnections: () => request('/api/llm/connections', { timeout: 20000 }),
  llmTest: (provider) =>
    request('/api/llm/test', { method: 'POST', body: { provider }, timeout: 25000 }),

  // Claude Code. Nothing runs until codeApprove() is called with the id of a
  // request the user has seen in full.
  codeStatus: () => request('/api/code/status', { timeout: 15000 }),
  codeRequests: () => request('/api/code/requests'),
  codePropose: (prompt, directory, mode) =>
    request('/api/code/request', { method: 'POST', body: { prompt, directory, mode } }),
  codeApprove: (id, allowEdits = false) =>
    request('/api/code/approve', { method: 'POST', body: { id, allow_edits: allowEdits } }),
  codeCancel: (id) => request('/api/code/cancel', { method: 'POST', body: { id } }),

  // MCP tools in chat.
  chatTools: () => request('/api/chat/tools', { timeout: 15000 }),
  chatToolApprove: (id, approve = true) =>
    request('/api/chat/tools/approve', { method: 'POST', body: { id, approve } }),

  voices: () => request('/api/voices'),
  downloadVoice: (voice) => request('/api/voices/download', { method: 'POST', body: { voice, confirm: true } }),
  downloadStatus: (voice) => request('/api/voices/download/' + encodeURIComponent(voice)),
  tts: (text, language) =>
    request('/api/tts', { method: 'POST', body: { text, language }, raw: true, timeout: 60000 }),

  conversations: () => request('/api/conversations'),
  conversation: (id) => request('/api/conversations/' + encodeURIComponent(id)),
  newConversation: (language) => request('/api/conversations', { method: 'POST', body: { language } }),
  deleteConversation: (id) =>
    request('/api/conversations/' + encodeURIComponent(id), { method: 'DELETE' }),

  armory: () => request('/api/armory'),
  armoryCreate: (conn) => request('/api/armory', { method: 'POST', body: conn }),
  armoryUpdate: (id, conn) => request('/api/armory/' + encodeURIComponent(id), { method: 'PUT', body: conn }),
  armoryDelete: (id) => request('/api/armory/' + encodeURIComponent(id), { method: 'DELETE' }),
  armoryConnect: (id) =>
    request('/api/armory/' + encodeURIComponent(id) + '/connect', { method: 'POST', timeout: 60000 }),
  armoryDisconnect: (id) =>
    request('/api/armory/' + encodeURIComponent(id) + '/disconnect', { method: 'POST' }),
  armoryCall: (id, tool, args) =>
    request('/api/armory/' + encodeURIComponent(id) + '/call', {
      method: 'POST', body: { tool, arguments: args }, timeout: 120000,
    }),

  visionStatus: () => request('/api/vision/status'),
  visionDescribe: (image, language, prompt) =>
    request('/api/vision/describe', {
      method: 'POST', body: { image, language, prompt }, timeout: 180000,
    }),

  log: (level, message) =>
    request('/api/log', { method: 'POST', body: { level, message }, timeout: 4000 }).catch(() => {}),
};

/**
 * POST /api/chat and read the SSE stream.
 * Returns { abort() } so the UI can stop a long answer.
 */
export function chatStream(payload, handlers) {
  const controller = new AbortController();
  const run = async () => {
    let response;
    try {
      response = await fetch(BASE + '/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } catch (err) {
      if (err.name !== 'AbortError') handlers.onError?.({ code: 'network' });
      handlers.onClose?.();
      return;
    }
    if (!response.ok || !response.body) {
      handlers.onError?.({ code: 'http_' + response.status });
      handlers.onClose?.();
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let split;
        while ((split = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          dispatch(frame, handlers);
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') handlers.onError?.({ code: 'stream' });
    }
    handlers.onClose?.();
  };
  run();
  return { abort: () => controller.abort() };
}

function dispatch(frame, handlers) {
  let event = 'message';
  const dataLines = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return;
  let data;
  try { data = JSON.parse(dataLines.join('\n')); } catch { return; }
  if (event === 'start') handlers.onStart?.(data);
  else if (event === 'token') handlers.onToken?.(data.text || '');
  else if (event === 'error') handlers.onError?.(data);
  else if (event === 'done') handlers.onDone?.(data);
  // MCP tool activity. The interface shows these as they happen, so what the
  // model says it did can be checked against what actually ran.
  else if (event === 'tools') handlers.onTools?.(data);
  else if (event === 'tool_call') handlers.onToolCall?.(data);
  else if (event === 'tool_result') handlers.onToolResult?.(data);
  else if (event === 'tool_approval') handlers.onToolApproval?.(data);
}
