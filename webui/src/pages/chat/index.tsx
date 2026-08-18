/**
 * 对话主页面
 *
 * 布局：左侧会话列表 + 右侧消息区域
 */

import { format } from 'date-fns';
import {
	Ellipsis,
	MessageSquareDashed,
	Pencil,
	Plus,
	Trash2,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { wsManager } from '@/api/ws';
import { ChatInput } from '@/components/chat/ChatInput';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
	Empty,
	EmptyDescription,
	EmptyHeader,
	EmptyMedia,
	EmptyTitle,
} from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import {
	Sidebar,
	SidebarContent,
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarMenu,
	SidebarMenuAction,
	SidebarMenuBadge,
	SidebarMenuButton,
	SidebarMenuItem,
	SidebarProvider,
} from '@/components/ui/sidebar';
import { Spinner } from '@/components/ui/spinner';
import { useMessages } from '@/hooks/useMessages';
import { useSessions } from '@/hooks/useSessions';
import { cn } from '@/lib/utils';

export function ChatPage() {
	const navigate = useNavigate();
	const { sessionId: urlSessionId } = useParams<{ sessionId?: string }>();
	const userId = wsManager.getUserId();

	const {
		sessions,
		loading: sessionsLoading,
		refresh: refreshSessions,
		renameSession,
		deleteSession,
	} = useSessions(userId);

	const {
		messages,
		phase,
		connectionStatus,
		sendMessage,
		cancelGeneration,
		switchSession,
	} = useMessages(userId, urlSessionId ?? null);

	const [renameTarget, setRenameTarget] = useState<string | null>(null);
	const [renameValue, setRenameValue] = useState('');
	const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

	// 选择会话
	const handleSelectSession = (sessionId: string) => {
		navigate(`/chat/${sessionId}`);
		switchSession(sessionId);
	};

	// 新建会话 — 导航到 /chat 即可，useMessages 会自动重连（无 session_id → 后端自动分配）
	const handleNewSession = () => {
		navigate('/chat');
	};

	// 重命名
	const handleRename = async () => {
		if (renameTarget && renameValue.trim()) {
			await renameSession(renameTarget, renameValue.trim());
			setRenameTarget(null);
			setRenameValue('');
		}
	};

	// 删除
	const handleDelete = async (sessionId: string) => {
		await deleteSession(sessionId);
		if (urlSessionId === sessionId) {
			navigate('/chat');
		}
		setDeleteTarget(null);
	};

	// 当前会话标题
	const currentTitle = urlSessionId
		? sessions.find((s) => s.session_id === urlSessionId)?.title || urlSessionId.slice(0, 8)
		: '新会话';

	// 滚动到底部
	useEffect(() => {
		const container = document.getElementById('chat-messages');
		if (container) {
			container.scrollTop = container.scrollHeight;
		}
	}, [messages]);

	return (
		<div className="flex h-full w-full p-2 gap-2">
			<SidebarProvider defaultOpen>
				{/* 会话列表侧边栏 */}
				<Sidebar collapsible="none" className="rounded-[22px]">
					<SidebarContent className="my-2 overflow-hidden">
						<SidebarGroup className="px-2 py-0">
							<SidebarGroupLabel className="justify-between">
								会话
								<span className="text-[10px] text-muted-foreground font-mono">
									{sessions.length}
								</span>
							</SidebarGroupLabel>
							<SidebarGroupContent>
								<SidebarMenu className="mb-2">
									<Button onClick={handleNewSession}>
										<Plus />
										新会话
									</Button>
								</SidebarMenu>
							</SidebarGroupContent>
						</SidebarGroup>

						<SidebarGroup className="min-h-0 flex-1 px-2 py-0">
							<SidebarGroupContent className="flex min-h-0 flex-1 flex-col">
								<div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
									{sessions.length === 0 ? (
										<Empty className="border-none py-4 min-h-50">
											<EmptyHeader>
												<EmptyMedia variant="icon">
													<MessageSquareDashed />
												</EmptyMedia>
												<EmptyTitle>暂无会话</EmptyTitle>
												<EmptyDescription>
													点击上方按钮开始新对话
												</EmptyDescription>
											</EmptyHeader>
										</Empty>
									) : (
										<SidebarMenu>
											{sessions.map((session) => (
												<SidebarMenuItem key={session.session_id}>
													<SidebarMenuButton
														className="text-muted-foreground hover:text-foreground group-has-data-[sidebar=menu-action]/menu-item:pr-16"
														isActive={urlSessionId === session.session_id}
														onClick={() => handleSelectSession(session.session_id)}
													>
														<span className="truncate">
															{session.title || session.session_id.slice(0, 8)}
														</span>
													</SidebarMenuButton>
													<SidebarMenuBadge className="max-md:hidden group-hover/menu-item:hidden text-muted-foreground font-mono">
														{session.last_active > 0
															? format(new Date(session.last_active * 1000), 'MM/dd')
															: ''}
													</SidebarMenuBadge>
													<DropdownMenu>
														<DropdownMenuTrigger asChild>
															<SidebarMenuAction className="md:opacity-0 group-hover/menu-item:opacity-100">
																<Ellipsis />
															</SidebarMenuAction>
														</DropdownMenuTrigger>
														<DropdownMenuContent side="right" align="start">
															<DropdownMenuItem
																onClick={() => {
																	setRenameTarget(session.session_id);
																	setRenameValue(session.title || '');
																}}
															>
																<Pencil />
																重命名
															</DropdownMenuItem>
															<DropdownMenuItem
																variant="destructive"
																onClick={() => handleDelete(session.session_id)}
															>
																<Trash2 />
																删除
															</DropdownMenuItem>
														</DropdownMenuContent>
													</DropdownMenu>
												</SidebarMenuItem>
											))}
										</SidebarMenu>
									)}
								</div>
							</SidebarGroupContent>
						</SidebarGroup>
					</SidebarContent>
				</Sidebar>

				{/* 聊天主区域 */}
				<div className="flex flex-1 min-w-0">
					<div className="flex flex-col flex-1 rounded-[22px] bg-card shadow-panel overflow-hidden">
						{/* Header */}
						<div className="flex items-center justify-between px-6 py-3 border-b border-border">
							<h2 className="text-sm font-medium truncate">{currentTitle}</h2>
							<span
								className={cn(
									'text-xs px-2 py-0.5 rounded-full cursor-default',
									connectionStatus === 'connected' && 'bg-green-500/10 text-green-500',
									connectionStatus === 'connecting' && 'bg-yellow-500/10 text-yellow-500',
									connectionStatus === 'disconnected' && 'bg-destructive/10 text-destructive',
								)}
							>
								{connectionStatus === 'connected'
									? '已连接'
									: connectionStatus === 'connecting'
										? '连接中...'
										: '未连接'}
							</span>
						</div>

						{/* Messages */}
						<div id="chat-messages" className="flex-1 overflow-y-auto px-6 py-4">
							{sessionsLoading && messages.length === 0 ? (
								<div className="flex items-center justify-center h-full">
									<Spinner className="size-5 text-muted-foreground" />
								</div>
							) : messages.length === 0 ? (
								<div className="flex flex-col items-center justify-center h-full text-muted-foreground">
									<span className="text-4xl mb-4">🤖</span>
									<span className="text-lg">开始新的对话</span>
								</div>
							) : (
								<div className="max-w-3xl mx-auto">
									{messages.map((msg) => (
										<MessageBubble key={msg.id} message={msg} />
									))}
									{phase === 'streaming' && (
										<div className="flex gap-1 py-2">
											<span className="size-2 bg-muted-foreground rounded-full animate-bounce [animation-delay:-0.32s]" />
											<span className="size-2 bg-muted-foreground rounded-full animate-bounce [animation-delay:-0.16s]" />
											<span className="size-2 bg-muted-foreground rounded-full animate-bounce" />
										</div>
									)}
								</div>
							)}
						</div>

						{/* Input */}
						<ChatInput
							phase={phase}
							onSend={sendMessage}
							onInterrupt={cancelGeneration}
						/>
					</div>
				</div>
			</SidebarProvider>

			{/* 重命名对话框 */}
			{renameTarget && (
				<div
					className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
					onClick={() => setRenameTarget(null)}
				>
					<div
						className="bg-card border border-border rounded-xl p-6 w-80"
						onClick={(e) => e.stopPropagation()}
					>
						<h3 className="text-sm font-medium mb-4">重命名会话</h3>
						<Input
							value={renameValue}
							onChange={(e) => setRenameValue(e.target.value)}
							placeholder="输入新名称"
							onKeyDown={(e) => e.key === 'Enter' && handleRename()}
							autoFocus
						/>
						<div className="flex justify-end gap-2 mt-4">
							<Button variant="outline" size="sm" onClick={() => setRenameTarget(null)}>
								取消
							</Button>
							<Button size="sm" onClick={handleRename} disabled={!renameValue.trim()}>
								确认
							</Button>
						</div>
					</div>
				</div>
			)}
		</div>
	);
}
