/**
 * Skill API
 */

import { client } from './client';
import type { SkillInfo, CreateSkillRequest } from './types';

export const skillApi = {
	/** 列出已安装 Skill */
	list: () => client.get<SkillInfo[]>('/skill'),

	/** 获取单个 Skill */
	get: (id: string) =>
		client.get<SkillInfo>(`/skill/${encodeURIComponent(id)}`),

	/** 添加 Skill */
	create: (data: CreateSkillRequest) =>
		client.post<SkillInfo>('/skill', data),

	/** 删除 Skill */
	delete: (id: string) =>
		client.delete(`/skill/${encodeURIComponent(id)}`),
};
