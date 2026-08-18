/**
 * 会话 API
 */

import { client } from './client';
import type { SessionListResponse, MessagesResponse } from './types';

export const sessionApi = {
	/** 列出用户历史会话 */
	list: (userId: string) =>
		client.get<SessionListResponse>(`/sessions/${encodeURIComponent(userId)}`),

	/** 获取会话消息历史 */
	messages: (userId: string, sessionId: string, params?: { before_id?: number; limit?: number }) =>
		client.get<MessagesResponse>(
			`/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/messages`,
			Object.fromEntries(
				Object.entries(params ?? {})
					.filter(([, v]) => v != null)
					.map(([k, v]) => [k, String(v)]),
			),
		),

	/** 重命名会话 */
	rename: (userId: string, sessionId: string, title: string) =>
		client.post(
			`/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/rename`,
			{ title },
		),

	/** 删除会话 */
	delete: (userId: string, sessionId: string) =>
		client.post(
			`/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/delete`,
		),

	/** Fork 会话 */
	fork: (userId: string, sessionId: string) =>
		client.post<{ session_id: string; parent_session_id: string; title: string }>(
			`/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}/fork`,
		),
};
