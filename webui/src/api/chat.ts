/**
 * 对话触发 API
 *
 * 主要通过 WebSocket 通信，此模块仅用于 HTTP 回退场景。
 */

import { client } from './client';
import type { ChatRequest } from './types';

export const chatApi = {
	/** Fire-and-Forget 触发对话 */
	trigger: (data: ChatRequest) =>
		client.post<{ status: string; session_id: string }>('/chat/', data),

	/** 健康检查 */
	health: () =>
		client.get<{ status: string; active_sessions?: number }>('/health'),
};
