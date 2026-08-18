/**
 * 消息气泡组件 — 渲染用户/助手消息
 *
 * 支持：Markdown、思考折叠、工具调用展示
 */

import { ChevronDown, ChevronRight, Wrench } from 'lucide-react';
import { useState } from 'react';

import type { ChatMessage } from '@/api/types';
import { cn } from '@/lib/utils';

interface Props {
	message: ChatMessage;
}

export function MessageBubble({ message }: Props) {
	const isUser = message.role === 'user';

	return (
		<div className={cn('flex gap-3 py-3', isUser && 'flex-row-reverse')}>
			{/* Avatar */}
			<div
				className={cn(
					'size-8 shrink-0 rounded-lg flex items-center justify-center text-sm',
					isUser ? 'bg-primary text-primary-foreground' : 'bg-muted',
				)}
			>
				{isUser ? '👤' : '🤖'}
			</div>

			{/* Body */}
			<div className={cn('max-w-[80%] min-w-0', isUser && 'text-right')}>
				{/* Content — 有实际文本才渲染 */}
				{message.content.trimStart() && (
					<div
						className={cn(
							'rounded-2xl px-4 py-3 text-sm leading-relaxed break-words',
							isUser
								? 'bg-primary text-primary-foreground rounded-tr-md'
								: 'bg-muted rounded-tl-md',
						)}
					>
						<div className="whitespace-pre-wrap">{message.content.trimStart()}</div>
					</div>
				)}

				{/* Thinking block */}
				{message.thinking && <ThinkingBlock content={message.thinking} />}

				{/* Tool calls */}
				{message.toolCalls && message.toolCalls.length > 0 && (
					<div className="mt-2 space-y-1">
						{message.toolCalls.map((tc) => (
							<ToolCallBlock key={tc.tool_call_id} toolCall={tc} />
						))}
					</div>
				)}
			</div>
		</div>
	);
}

function ThinkingBlock({ content }: { content: string }) {
	const [expanded, setExpanded] = useState(false);

	return (
		<div className="mt-2 border-l-2 border-muted-foreground/30 pl-3">
			<button
				className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
				onClick={() => setExpanded(!expanded)}
			>
				{expanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
				💭 思考过程
			</button>
			{expanded && (
				<div className="mt-1 text-xs text-muted-foreground whitespace-pre-wrap">
					{content}
				</div>
			)}
		</div>
	);
}

function ToolCallBlock({
	toolCall,
}: {
	toolCall: NonNullable<ChatMessage['toolCalls']>[number];
}) {
	const [expanded, setExpanded] = useState(false);

	return (
		<div className="rounded-lg border border-border bg-muted/50 overflow-hidden">
			<button
				className="flex items-center gap-2 w-full px-3 py-2 text-xs hover:bg-muted transition-colors"
				onClick={() => setExpanded(!expanded)}
			>
				<Wrench className="size-3 text-muted-foreground" />
				<span className="font-mono text-foreground">{toolCall.tool_name}</span>
				{toolCall.state && (
					<span
						className={cn(
							'ml-auto text-[10px]',
							toolCall.state === 'success' && 'text-green-500',
							toolCall.state === 'error' && 'text-destructive',
						)}
					>
						{toolCall.state === 'success' ? '✓' : toolCall.state === 'error' ? '✗' : '⏳'}
					</span>
				)}
				{expanded ? (
					<ChevronDown className="size-3 text-muted-foreground" />
				) : (
					<ChevronRight className="size-3 text-muted-foreground" />
				)}
			</button>
			{expanded && (
				<div className="border-t border-border">
					{toolCall.tool_args != null && (
						<div className="px-3 py-2">
							<div className="text-[10px] text-muted-foreground mb-1">参数:</div>
							<pre className="text-xs font-mono bg-background rounded p-2 overflow-x-auto max-h-40">
								{typeof toolCall.tool_args === 'string'
									? toolCall.tool_args
									: JSON.stringify(toolCall.tool_args, null, 2)}
							</pre>
						</div>
					)}
					{toolCall.result && (
						<div className="px-3 py-2 border-t border-border">
							<div className="text-[10px] text-muted-foreground mb-1">结果:</div>
							<pre className="text-xs font-mono bg-background rounded p-2 overflow-x-auto max-h-40">
								{toolCall.result}
							</pre>
						</div>
					)}
				</div>
			)}
		</div>
	);
}
