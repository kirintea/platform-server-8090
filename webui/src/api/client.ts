/**
 * HTTP 客户端封装
 *
 * 所有 REST API 调用通过此模块，统一错误处理和请求头。
 */

export class ApiError extends Error {
	readonly status: number;
	readonly detail: string;

	constructor(status: number, detail: string) {
		super(detail);
		this.name = 'ApiError';
		this.status = status;
		this.detail = detail;
	}
}

export const TIMEOUT_STATUS = -1;

function buildHeaders(hasBody: boolean): Record<string, string> {
	const headers: Record<string, string> = {};
	if (hasBody) headers['Content-Type'] = 'application/json';
	return headers;
}

async function extractErrorDetail(res: Response): Promise<string> {
	const text = await res.text();
	try {
		const json = JSON.parse(text) as { detail?: unknown };
		if (typeof json.detail === 'string') return json.detail;
		if (json.detail !== undefined) return JSON.stringify(json.detail);
	} catch {
		// not JSON
	}
	return text || res.statusText;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
	// 可选 API-Key 鉴权：仅当 setup 中配置了 api_key 时附带 X-API-Key 头（非破坏性）
	const apiKey = localStorage.getItem('api_key');
	if (apiKey && apiKey.trim()) {
		options = {
			...options,
			headers: {
				...(options.headers || {}),
				'X-API-Key': apiKey.trim(),
			},
		};
	}

	const res = await fetch(path, options);

	if (!res.ok) {
		const detail = await extractErrorDetail(res);
		throw new ApiError(res.status, detail);
	}

	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}

export const client = {
	get: <T>(path: string, params?: Record<string, string>) => {
		const url = new URL(path, window.location.origin);
		if (params) {
			Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
		}
		return request<T>(url.toString(), { method: 'GET' });
	},

	post: <T>(path: string, body?: unknown) =>
		request<T>(path, {
			method: 'POST',
			headers: buildHeaders(body !== undefined),
			body: body ? JSON.stringify(body) : undefined,
		}),

	patch: <T>(path: string, body?: unknown) =>
		request<T>(path, {
			method: 'PATCH',
			headers: buildHeaders(body !== undefined),
			body: body ? JSON.stringify(body) : undefined,
		}),

	delete: <T = void>(path: string) =>
		request<T>(path, { method: 'DELETE' }),
};
