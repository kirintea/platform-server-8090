import { Loader2 } from 'lucide-react';
import { useState } from 'react';

import { chatApi } from '@/api/chat';
import { Button } from '@/components/ui/button';
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from '@/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

interface Props {
	onComplete: () => void;
	className?: string;
}

export const SetupPage = ({ onComplete, className }: Props) => {
	const [userId, setUserId] = useState(() => localStorage.getItem('user_id') ?? '');
	const [wsAddress, setWsAddress] = useState(() => {
		const saved = localStorage.getItem('ws_address');
		if (saved) return saved;
		// 默认使用当前 host 构建 WebSocket 地址
		const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
		return `${protocol}//${window.location.host}/ws/chat`;
	});
	const [checking, setChecking] = useState(false);
	const [errorMsg, setErrorMsg] = useState('');

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		const trimmedUser = userId.trim();
		const trimmedWs = wsAddress.trim().replace(/\/+$/, '');

		if (!trimmedUser) {
			setErrorMsg('请输入用户 ID');
			return;
		}

		setChecking(true);
		setErrorMsg('');

		try {
			// 验证后端可达
			await chatApi.health();

			// 保存配置
			localStorage.setItem('user_id', trimmedUser);
			localStorage.setItem('ws_address', trimmedWs);
			onComplete();
		} catch (err) {
			if (err instanceof Error) {
				setErrorMsg(`无法连接到后端: ${err.message}`);
			} else {
				setErrorMsg('连接失败，请检查地址');
			}
		} finally {
			setChecking(false);
		}
	};

	return (
		<div className="flex items-center justify-center h-full">
			<div className={cn('flex flex-col gap-6 w-full max-w-sm', className)}>
				<Card>
					<CardHeader>
						<CardTitle>连接到 AgentScope</CardTitle>
						<CardDescription>配置用户标识和 WebSocket 地址</CardDescription>
					</CardHeader>
					<CardContent>
						<form onSubmit={handleSubmit}>
							<FieldGroup>
								<Field>
									<FieldLabel htmlFor="user-id-input">用户 ID</FieldLabel>
									<Input
										id="user-id-input"
										type="text"
										placeholder="输入用户标识（如 admin、test）"
										value={userId}
										onChange={(e) => setUserId(e.target.value)}
										required
										autoFocus
									/>
									<FieldDescription>
										用于区分不同用户的会话数据
									</FieldDescription>
								</Field>
								<Field>
									<FieldLabel htmlFor="ws-address-input">
										WebSocket 地址
									</FieldLabel>
									<Input
										id="ws-address-input"
										type="text"
										placeholder="ws://localhost:8090/ws/chat"
										value={wsAddress}
										onChange={(e) => setWsAddress(e.target.value)}
									/>
									<FieldDescription>
										留空使用默认地址
									</FieldDescription>
								</Field>
								{errorMsg && (
									<div className="text-sm text-destructive bg-destructive/10 p-3 rounded-md">
										{errorMsg}
									</div>
								)}
								<Field>
									<Button type="submit" className="w-full" disabled={checking}>
										{checking && <Loader2 className="size-3.5 animate-spin" />}
										{checking ? '连接中...' : '连接'}
									</Button>
								</Field>
							</FieldGroup>
						</form>
					</CardContent>
				</Card>
				<FieldDescription className="px-6 text-center">
					首次使用请输入用户 ID，之后可在侧边栏切换用户
				</FieldDescription>
			</div>
		</div>
	);
};
