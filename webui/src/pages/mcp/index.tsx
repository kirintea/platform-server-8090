/**
 * MCP 管理页面
 */

import { Loader2, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';

import { mcpApi } from '@/api/mcp';
import type { McpInfo } from '@/api/types';
import { Button } from '@/components/ui/button';
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from '@/components/ui/card';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';

export function MCPPage() {
	const [mcps, setMcps] = useState<McpInfo[]>([]);
	const [loading, setLoading] = useState(true);
	const [showAdd, setShowAdd] = useState(false);
	const [adding, setAdding] = useState(false);
	const [argsText, setArgsText] = useState('');

	const [form, setForm] = useState({
		name: '',
		display_name: '',
		transport: 'stdio' as 'stdio' | 'sse',
		command: '',
		url: '',
		description: '',
	});

	const loadMcps = async () => {
		setLoading(true);
		try {
			setMcps(await mcpApi.list());
		} catch (e) {
			console.error('加载 MCP 列表失败:', e);
		} finally {
			setLoading(false);
		}
	};

	useEffect(() => {
		loadMcps();
	}, []);

	const handleAdd = async () => {
		setAdding(true);
		try {
			const args = argsText.split('\n').map((s) => s.trim()).filter(Boolean);
			await mcpApi.create({
				name: form.name,
				display_name: form.display_name || undefined,
				transport: form.transport,
				command: form.transport === 'stdio' ? form.command : undefined,
				args: form.transport === 'stdio' ? args : undefined,
				url: form.transport === 'sse' ? form.url : undefined,
				description: form.description || undefined,
			});
			setShowAdd(false);
			setForm({ name: '', display_name: '', transport: 'stdio', command: '', url: '', description: '' });
			setArgsText('');
			await loadMcps();
		} catch (e) {
			console.error('添加 MCP 失败:', e);
		} finally {
			setAdding(false);
		}
	};

	const handleDelete = async (id: string) => {
		if (!confirm('确定删除此 MCP？')) return;
		try {
			await mcpApi.delete(id);
			await loadMcps();
		} catch (e) {
			console.error('删除 MCP 失败:', e);
		}
	};

	return (
		<div className="h-full flex flex-col p-6">
			<div className="flex items-center justify-between mb-6">
				<h1 className="text-xl font-semibold">MCP 管理</h1>
				<Button onClick={() => setShowAdd(true)}>
					<Plus className="size-4" />
					添加 MCP
				</Button>
			</div>

			{loading ? (
				<div className="flex-1 flex items-center justify-center">
					<Loader2 className="size-5 animate-spin text-muted-foreground" />
				</div>
			) : mcps.length === 0 ? (
				<div className="flex-1 flex items-center justify-center text-muted-foreground">
					暂无 MCP 配置
				</div>
			) : (
				<Table>
					<TableHeader>
						<TableRow>
							<TableHead>名称</TableHead>
							<TableHead>显示名</TableHead>
							<TableHead>传输方式</TableHead>
							<TableHead>描述</TableHead>
							<TableHead className="w-20">操作</TableHead>
						</TableRow>
					</TableHeader>
					<TableBody>
						{mcps.map((mcp) => (
							<TableRow key={mcp.id}>
								<TableCell className="font-mono">{mcp.name}</TableCell>
								<TableCell>{mcp.display_name || '-'}</TableCell>
								<TableCell>{mcp.transport}</TableCell>
								<TableCell className="max-w-xs truncate">{mcp.description || '-'}</TableCell>
								<TableCell>
									<Button
										variant="ghost"
										size="icon"
										className="text-destructive hover:text-destructive"
										onClick={() => handleDelete(mcp.id)}
									>
										<Trash2 className="size-4" />
									</Button>
								</TableCell>
							</TableRow>
						))}
					</TableBody>
				</Table>
			)}

			{/* 添加对话框 */}
			{showAdd && (
				<div
					className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
					onClick={() => setShowAdd(false)}
				>
					<Card className="w-[480px] max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
						<CardHeader>
							<CardTitle>添加 MCP</CardTitle>
							<CardDescription>配置新的 MCP 服务</CardDescription>
						</CardHeader>
						<CardContent>
							<FieldGroup>
								<Field>
									<FieldLabel>名称 *</FieldLabel>
									<Input
										value={form.name}
										onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
										placeholder="my-mcp"
									/>
								</Field>
								<Field>
									<FieldLabel>显示名</FieldLabel>
									<Input
										value={form.display_name}
										onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
										placeholder="我的 MCP"
									/>
								</Field>
								<Field>
									<FieldLabel>传输方式 *</FieldLabel>
									<select
										value={form.transport}
										onChange={(e) => setForm((f) => ({ ...f, transport: e.target.value as 'stdio' | 'sse' }))}
										className="w-full bg-muted border border-border rounded-md px-3 py-2 text-sm"
									>
										<option value="stdio">stdio</option>
										<option value="sse">sse</option>
									</select>
								</Field>
								{form.transport === 'stdio' && (
									<>
										<Field>
											<FieldLabel>命令</FieldLabel>
											<Input
												value={form.command}
												onChange={(e) => setForm((f) => ({ ...f, command: e.target.value }))}
												placeholder="npx"
											/>
										</Field>
										<Field>
											<FieldLabel>参数（每行一个）</FieldLabel>
											<Textarea
												value={argsText}
												onChange={(e) => setArgsText(e.target.value)}
												rows={3}
												placeholder={"-y\n@modelcontextprotocol/server-filesystem\n/path/to/dir"}
											/>
										</Field>
									</>
								)}
								{form.transport === 'sse' && (
									<Field>
										<FieldLabel>URL</FieldLabel>
										<Input
											value={form.url}
											onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
											placeholder="http://localhost:3001/sse"
										/>
									</Field>
								)}
								<Field>
									<FieldLabel>描述</FieldLabel>
									<Textarea
										value={form.description}
										onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
										rows={2}
										placeholder="MCP 描述"
									/>
								</Field>
								<div className="flex justify-end gap-2 pt-2">
									<Button variant="outline" onClick={() => setShowAdd(false)}>
										取消
									</Button>
									<Button onClick={handleAdd} disabled={!form.name || adding}>
										{adding && <Loader2 className="size-3.5 animate-spin" />}
										添加
									</Button>
								</div>
							</FieldGroup>
						</CardContent>
					</Card>
				</div>
			)}
		</div>
	);
}
