/**
 * 上下文用量指示器
 *
 * 显示当前会话的 token 用量、状态颜色、压缩按钮。
 * 嵌入在对话输入框上方。
 */

import { Loader2, Minimize2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface ContextInfo {
	estimated_tokens: number;
	context_window: number;
	usage_ratio: number;
	status: 'healthy' | 'warning' | 'critical';
	message_count: number;
}

interface Props {
	userId: string;
	sessionId: string | null;
	className?: string;
}

const STATUS_COLORS = {
	healthy: 'bg-green-500',
	warning: 'bg-yellow-500',
	critical: 'bg-red-500',
};

const STATUS_TEXT = {
	healthy: '',
	warning: '上下文较长',
	critical: '建议开新会话',
};

export function ContextIndicator({ userId, sessionId, className }: Props) {
	const [info, setInfo] = useState<ContextInfo | null>(null);
	const [compressing, setCompressing] = useState(false);

	// 拉取上下文用量
	const fetchContext = useCallback(async () => {
		if (!sessionId) {
			setInfo(null);
			return;
		}
		try {
			const resp = await fetch(`/sessions/${userId}/${sessionId}/context`);
			if (resp.ok) {
				const data = await resp.json();
				setInfo(data);
			}
		} catch {
			// 静默失败
		}
	}, [userId, sessionId]);

	// 定期刷新 + 会话切换时刷新
	useEffect(() => {
		fetchContext();
		if (!sessionId) return;
		const timer = setInterval(fetchContext, 30000); // 30 秒刷新
		return () => clearInterval(timer);
	}, [fetchContext, sessionId]);

	// 手动压缩
	const handleCompress = useCallback(async () => {
		if (!sessionId || compressing) return;
		setCompressing(true);
		try {
			const resp = await fetch(
				`/sessions/${userId}/${sessionId}/compress`,
				{ method: 'POST' },
			);
			if (resp.ok) {
				await fetchContext(); // 刷新用量
			}
		} catch {
			// 静默失败
		} finally {
			setCompressing(false);
		}
	}, [userId, sessionId, compressing, fetchContext]);

	if (!info || !sessionId) return null;

	const percent = Math.round(info.usage_ratio * 100);
	const showCompress = info.status === 'warning' || info.status === 'critical';

	return (
		<div
			className={cn(
				'flex items-center gap-3 px-4 py-1.5 text-xs text-muted-foreground',
				className,
			)}
		>
			{/* 进度条 */}
			<div className="flex-1 flex items-center gap-2">
				<div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
					<div
						className={cn(
							'h-full rounded-full transition-all duration-300',
							STATUS_COLORS[info.status],
						)}
						style={{ width: `${Math.min(percent, 100)}%` }}
					/>
				</div>
				<span className="font-mono tabular-nums">
					{percent}% · {Math.round(info.estimated_tokens / 1000)}K/
					{Math.round(info.context_window / 1000)}K
				</span>
				<span>· {info.message_count} 条</span>
			</div>

			{/* 状态提示 */}
			{STATUS_TEXT[info.status] && (
				<span
					className={cn(
						'font-medium',
						info.status === 'warning' && 'text-yellow-600',
						info.status === 'critical' && 'text-red-600',
					)}
				>
					{STATUS_TEXT[info.status]}
				</span>
			)}

			{/* 压缩按钮 */}
			{showCompress && (
				<Button
					variant="outline"
					size="sm"
					className="h-6 px-2 text-xs"
					onClick={handleCompress}
					disabled={compressing}
				>
					{compressing ? (
						<Loader2 className="size-3 animate-spin" />
					) : (
						<Minimize2 className="size-3" />
					)}
					压缩上下文
				</Button>
			)}
		</div>
	);
}
