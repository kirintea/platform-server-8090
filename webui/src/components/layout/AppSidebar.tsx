/**
 * 应用侧边栏 — 图标导航 + 用户切换
 *
 * 底部显示当前用户，点击可切换用户
 */

import {
	BotMessageSquare,
	BookText,
	Cable,
	Languages,
	LogIn,
	Settings,
	UserRound,
} from 'lucide-react';
import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';

import { wsManager } from '@/api/ws';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarGroup,
	SidebarGroupContent,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
} from '@/components/ui/sidebar';
import { cn } from '@/lib/utils';

export function AppSidebar() {
	const navigate = useNavigate();
	const location = useLocation();
	const [showUserDialog, setShowUserDialog] = useState(false);
	const [newUserId, setNewUserId] = useState('');
	const currentUserId = wsManager.getUserId();

	const handleSwitchUser = () => {
		const trimmed = newUserId.trim();
		if (!trimmed || trimmed === currentUserId) {
			setShowUserDialog(false);
			return;
		}

		// 更新 localStorage
		localStorage.setItem('user_id', trimmed);

		// 断开旧连接并重连
		wsManager.disconnect();
		wsManager.connect({
			userId: trimmed,
			onMessage: () => {},
		});

		setShowUserDialog(false);
		setNewUserId('');

		// 刷新页面以重新加载会话
		window.location.reload();
	};

	return (
		<Sidebar
			collapsible="none"
			className="w-[calc(var(--sidebar-width-icon)+1px)]! bg-transparent"
		>
			<SidebarHeader>
				<div className="flex items-center justify-center size-8 mt-2 rounded-full bg-primary">
					<span className="text-primary-foreground text-sm font-bold">AS</span>
				</div>
			</SidebarHeader>
			<SidebarContent>
				<SidebarGroup>
					<SidebarGroupContent>
						<SidebarMenu>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: '对话', hidden: false }}
									isActive={
										location.pathname === '/chat' ||
										location.pathname.startsWith('/chat/')
									}
									onClick={() => navigate('/chat')}
									className="justify-center"
								>
									<BotMessageSquare />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: 'MCP', hidden: false }}
									isActive={location.pathname === '/mcp'}
									onClick={() => navigate('/mcp')}
									className="justify-center"
								>
									<Cable />
								</SidebarMenuButton>
							</SidebarMenuItem>
							<SidebarMenuItem>
								<SidebarMenuButton
									tooltip={{ children: 'Skill', hidden: false }}
									isActive={location.pathname === '/skill'}
									onClick={() => navigate('/skill')}
									className="justify-center"
								>
									<BookText />
								</SidebarMenuButton>
							</SidebarMenuItem>
						</SidebarMenu>
					</SidebarGroupContent>
				</SidebarGroup>
			</SidebarContent>
			<SidebarFooter>
				<SidebarMenu>
					<SidebarMenuItem>
						<SidebarMenuButton
							tooltip={{
								children: `当前用户: ${currentUserId} (点击切换)`,
								hidden: false,
							}}
							onClick={() => {
								setNewUserId(currentUserId);
								setShowUserDialog(true);
							}}
							className="justify-center"
						>
							<UserRound />
						</SidebarMenuButton>
					</SidebarMenuItem>
					<SidebarMenuItem>
						<SidebarMenuButton
							tooltip={{ children: '设置', hidden: false }}
							onClick={() => navigate('/setup')}
							className="justify-center"
						>
							<Settings />
						</SidebarMenuButton>
					</SidebarMenuItem>
				</SidebarMenu>
			</SidebarFooter>

			{/* 用户切换对话框 */}
			{showUserDialog && (
				<div
					className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
					onClick={() => setShowUserDialog(false)}
				>
					<div
						className="bg-card border border-border rounded-xl p-6 w-80"
						onClick={(e) => e.stopPropagation()}
					>
						<h3 className="text-sm font-medium mb-2">切换用户</h3>
						<p className="text-xs text-muted-foreground mb-4">
							当前用户: <span className="font-mono">{currentUserId}</span>
						</p>
						<Input
							value={newUserId}
							onChange={(e) => setNewUserId(e.target.value)}
							placeholder="输入新用户 ID"
							onKeyDown={(e) => e.key === 'Enter' && handleSwitchUser()}
							autoFocus
						/>
						<div className="flex justify-end gap-2 mt-4">
							<Button variant="outline" size="sm" onClick={() => setShowUserDialog(false)}>
								取消
							</Button>
							<Button size="sm" onClick={handleSwitchUser} disabled={!newUserId.trim()}>
								<LogIn className="size-3.5" />
								切换
							</Button>
						</div>
					</div>
				</div>
			)}
		</Sidebar>
	);
}
