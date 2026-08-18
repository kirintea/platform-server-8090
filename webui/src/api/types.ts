/** 通用类型定义 */

// ========== 会话 ==========

export interface SessionInfo {
	session_id: string;
	user_id: string;
	title: string;
	created_at: number;
	last_active: number;
	message_count: number;
	parent_session_id?: string;
}

export interface SessionListResponse {
	sessions: SessionInfo[];
	total: number;
}

export interface SessionMessage {
	id: number;
	role: 'user' | 'assistant';
	content: string;
	created_at?: string;
}

export interface MessagesResponse {
	messages: SessionMessage[];
	has_more: boolean;
	oldest_id: number | null;
}

// ========== 对话 ==========

export interface ChatRequest {
	message: string;
	user_id?: string;
	session_id?: string;
}

export interface ChatMessage {
	id: string;
	role: 'user' | 'assistant';
	content: string;
	thinking?: string;
	toolCalls?: ToolCallInfo[];
}

export interface ToolCallInfo {
	tool_name: string;
	tool_call_id: string;
	tool_args?: unknown;
	result?: string;
	state?: string;
}

// ========== WebSocket ==========

export interface WsMessage {
	type: string;
	payload: Record<string, unknown>;
}

// ========== MCP ==========

export interface McpInfo {
	id: string;
	name: string;
	display_name?: string;
	transport: 'stdio' | 'sse';
	command?: string;
	args?: string[];
	url?: string;
	headers?: Record<string, string>;
	description?: string;
}

export interface CreateMcpRequest {
	name: string;
	display_name?: string;
	transport: 'stdio' | 'sse';
	command?: string;
	args?: string[];
	url?: string;
	headers?: Record<string, string>;
	description?: string;
}

// ========== Skill ==========

export interface SkillInfo {
	id: string;
	name: string;
	display_name?: string;
	description?: string;
	markdown?: string;
	tags?: string[];
	author?: string;
}

export interface CreateSkillRequest {
	name: string;
	display_name?: string;
	description?: string;
	markdown?: string;
	tags?: string[];
	author?: string;
}

// ========== Health ==========

export interface HealthResponse {
	status: string;
	version?: string;
	components?: Record<string, string>;
	active_sessions?: number;
}
