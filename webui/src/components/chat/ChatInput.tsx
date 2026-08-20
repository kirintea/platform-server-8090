/**
 * 对话输入框组件
 *
 * 支持：Enter 发送、Shift+Enter 换行、自适应高度、停止按钮
 */

import { Loader2, Send, Square } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';

const MIN_SEND_INTERVAL_MS = 2000;

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import type { ReplyPhase } from '@/hooks/useMessages';

interface Props {
	phase: ReplyPhase;
	disabled?: boolean;
	onSend: (content: string) => void;
	onInterrupt?: () => void;
	className?: string;
}

export function ChatInput({ phase, disabled, onSend, onInterrupt, className }: Props) {
	const [input, setInput] = useState('');
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const lastSendTimeRef = useRef<number>(0);

	const handleSend = useCallback(() => {
		const content = input.trim();
		if (!content || phase !== 'idle') return;
		// 发送节流：2 秒内禁止连续发送，防止高频请求耗尽上下文配额
		const now = Date.now();
		if (now - lastSendTimeRef.current < MIN_SEND_INTERVAL_MS) return;
		lastSendTimeRef.current = now;
		onSend(content);
		setInput('');
		// Reset textarea height
		if (textareaRef.current) {
			textareaRef.current.style.height = 'auto';
		}
	}, [input, phase, onSend]);

	const handleKeyDown = useCallback(
		(e: React.KeyboardEvent) => {
			if (e.key === 'Enter' && !e.shiftKey) {
				e.preventDefault();
				handleSend();
			}
		},
		[handleSend],
	);

	const handleInput = useCallback(() => {
		const el = textareaRef.current;
		if (!el) return;
		el.style.height = 'auto';
		el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
	}, []);

	const isStreaming = phase === 'streaming' || phase === 'interrupting';

	return (
		<div className={cn('flex items-end gap-2 p-4', className)}>
			<div className="flex-1 flex items-end bg-muted rounded-2xl border border-border focus-within:border-primary/50 transition-colors px-4 py-3">
				<textarea
					ref={textareaRef}
					value={input}
					onChange={(e) => setInput(e.target.value)}
					onKeyDown={handleKeyDown}
					onInput={handleInput}
					placeholder={isStreaming ? '等待回复中...' : '输入消息... (Enter 发送, Shift+Enter 换行)'}
					disabled={disabled || isStreaming}
					rows={1}
					className="flex-1 bg-transparent resize-none outline-none text-sm leading-relaxed max-h-[150px] placeholder:text-muted-foreground disabled:opacity-50"
				/>
			</div>

			{isStreaming ? (
				<Button
					size="icon"
					variant="destructive"
					className="rounded-xl shrink-0"
					onClick={onInterrupt}
					disabled={phase === 'interrupting'}
					title="停止生成"
				>
					{phase === 'interrupting' ? (
						<Loader2 className="size-4 animate-spin" />
					) : (
						<Square className="size-4" />
					)}
				</Button>
			) : (
				<Button
					size="icon"
					className="rounded-xl shrink-0"
					onClick={handleSend}
					disabled={!input.trim() || disabled}
					title="发送"
				>
					<Send className="size-4" />
				</Button>
			)}
		</div>
	);
}
