/**
 * MCP API
 */

import { client } from './client';
import type { McpInfo, CreateMcpRequest } from './types';

export const mcpApi = {
	/** 列出已安装 MCP */
	list: () => client.get<McpInfo[]>('/mcp'),

	/** 添加 MCP */
	create: (data: CreateMcpRequest) =>
		client.post<McpInfo>('/mcp', data),

	/** 更新 MCP */
	update: (id: string, data: Partial<McpInfo>) =>
		client.patch<McpInfo>(`/mcp/${encodeURIComponent(id)}`, data),

	/** 删除 MCP */
	delete: (id: string) =>
		client.delete(`/mcp/${encodeURIComponent(id)}`),
};
