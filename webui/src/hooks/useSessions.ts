/**
 * 会话列表管理 hook
 */

import { useCallback, useEffect, useState } from 'react';

import { sessionApi } from '@/api/session';
import type { SessionInfo } from '@/api/types';

export function useSessions(userId: string) {
	const [sessions, setSessions] = useState<SessionInfo[]>([]);
	const [loading, setLoading] = useState(false);

	const refresh = useCallback(async () => {
		if (!userId) return;
		setLoading(true);
		try {
			const res = await sessionApi.list(userId);
			setSessions(res.sessions);
		} catch (e) {
			console.error('加载会话列表失败:', e);
		} finally {
			setLoading(false);
		}
	}, [userId]);

	useEffect(() => {
		refresh();
	}, [refresh]);

	const renameSession = useCallback(async (sessionId: string, title: string) => {
		try {
			await sessionApi.rename(userId, sessionId, title);
			await refresh();
		} catch (e) {
			console.error('重命名失败:', e);
		}
	}, [userId, refresh]);

	const deleteSession = useCallback(async (sessionId: string) => {
		try {
			await sessionApi.delete(userId, sessionId);
			await refresh();
		} catch (e) {
			console.error('删除失败:', e);
		}
	}, [userId, refresh]);

	const forkSession = useCallback(async (sessionId: string) => {
		try {
			const res = await sessionApi.fork(userId, sessionId);
			await refresh();
			return res.session_id;
		} catch (e) {
			console.error('Fork 失败:', e);
			return null;
		}
	}, [userId, refresh]);

	return {
		sessions,
		loading,
		refresh,
		renameSession,
		deleteSession,
		forkSession,
	};
}
