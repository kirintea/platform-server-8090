/**
 * Skill 管理页面
 */

import { Loader2, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';

import { skillApi } from '@/api/skill';
import type { SkillInfo } from '@/api/types';
import { Badge } from '@/components/ui/badge';
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

export function SkillPage() {
	const [skills, setSkills] = useState<SkillInfo[]>([]);
	const [loading, setLoading] = useState(true);
	const [showAdd, setShowAdd] = useState(false);
	const [adding, setAdding] = useState(false);
	const [tagsText, setTagsText] = useState('');

	const [form, setForm] = useState({
		name: '',
		display_name: '',
		description: '',
		markdown: '',
		author: '',
	});

	const loadSkills = async () => {
		setLoading(true);
		try {
			setSkills(await skillApi.list());
		} catch (e) {
			console.error('加载 Skill 列表失败:', e);
		} finally {
			setLoading(false);
		}
	};

	useEffect(() => {
		loadSkills();
	}, []);

	const handleAdd = async () => {
		setAdding(true);
		try {
			const tags = tagsText.split(',').map((s) => s.trim()).filter(Boolean);
			await skillApi.create({
				name: form.name,
				display_name: form.display_name || undefined,
				description: form.description || undefined,
				markdown: form.markdown || undefined,
				tags: tags.length ? tags : undefined,
				author: form.author || undefined,
			});
			setShowAdd(false);
			setForm({ name: '', display_name: '', description: '', markdown: '', author: '' });
			setTagsText('');
			await loadSkills();
		} catch (e) {
			console.error('添加 Skill 失败:', e);
		} finally {
			setAdding(false);
		}
	};

	const handleDelete = async (id: string) => {
		if (!confirm('确定删除此 Skill？')) return;
		try {
			await skillApi.delete(id);
			await loadSkills();
		} catch (e) {
			console.error('删除 Skill 失败:', e);
		}
	};

	return (
		<div className="h-full flex flex-col p-6">
			<div className="flex items-center justify-between mb-6">
				<h1 className="text-xl font-semibold">Skill 管理</h1>
				<Button onClick={() => setShowAdd(true)}>
					<Plus className="size-4" />
					添加 Skill
				</Button>
			</div>

			{loading ? (
				<div className="flex-1 flex items-center justify-center">
					<Loader2 className="size-5 animate-spin text-muted-foreground" />
				</div>
			) : skills.length === 0 ? (
				<div className="flex-1 flex items-center justify-center text-muted-foreground">
					暂无 Skill 配置
				</div>
			) : (
				<Table>
					<TableHeader>
						<TableRow>
							<TableHead>名称</TableHead>
							<TableHead>显示名</TableHead>
							<TableHead>标签</TableHead>
							<TableHead>描述</TableHead>
							<TableHead className="w-20">操作</TableHead>
						</TableRow>
					</TableHeader>
					<TableBody>
						{skills.map((skill) => (
							<TableRow key={skill.id}>
								<TableCell className="font-mono">{skill.name}</TableCell>
								<TableCell>{skill.display_name || '-'}</TableCell>
								<TableCell>
									{skill.tags?.length ? (
										<div className="flex gap-1 flex-wrap">
											{skill.tags.map((tag) => (
												<Badge key={tag} variant="secondary" className="text-xs">
													{tag}
												</Badge>
											))}
										</div>
									) : (
										'-'
									)}
								</TableCell>
								<TableCell className="max-w-xs truncate">{skill.description || '-'}</TableCell>
								<TableCell>
									<Button
										variant="ghost"
										size="icon"
										className="text-destructive hover:text-destructive"
										onClick={() => handleDelete(skill.id)}
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
							<CardTitle>添加 Skill</CardTitle>
							<CardDescription>配置新的 Skill</CardDescription>
						</CardHeader>
						<CardContent>
							<FieldGroup>
								<Field>
									<FieldLabel>名称 *</FieldLabel>
									<Input
										value={form.name}
										onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
										placeholder="my-skill"
									/>
								</Field>
								<Field>
									<FieldLabel>显示名</FieldLabel>
									<Input
										value={form.display_name}
										onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
										placeholder="我的 Skill"
									/>
								</Field>
								<Field>
									<FieldLabel>描述</FieldLabel>
									<Textarea
										value={form.description}
										onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
										rows={2}
										placeholder="Skill 描述"
									/>
								</Field>
								<Field>
									<FieldLabel>Markdown 内容</FieldLabel>
									<Textarea
										value={form.markdown}
										onChange={(e) => setForm((f) => ({ ...f, markdown: e.target.value }))}
										rows={6}
										placeholder="# Skill 指令..."
									/>
								</Field>
								<Field>
									<FieldLabel>标签（逗号分隔）</FieldLabel>
									<Input
										value={tagsText}
										onChange={(e) => setTagsText(e.target.value)}
										placeholder="utility, file-management"
									/>
								</Field>
								<Field>
									<FieldLabel>作者</FieldLabel>
									<Input
										value={form.author}
										onChange={(e) => setForm((f) => ({ ...f, author: e.target.value }))}
										placeholder="作者名"
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
