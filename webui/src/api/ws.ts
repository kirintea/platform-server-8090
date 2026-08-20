/**
 * WebSocket 连接管理
 *
 * 单例模式，管理与后端的 WebSocket 连接。
 * 支持自动重连、心跳保活。
 */

import type { WsMessage } from './types';

export interface WsOptions {
	userId: string;
	sessionId?: string;
	onOpen?: () => void;
	onMessage: (msg: WsMessage) => void;
	onClose?: () => void;
	onError?: (err: Event) => void;
}

class WsManager {
	private ws: WebSocket | null = null;
	private options: WsOptions | null = null;
	private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
	private reconnectAttempts = 0;
	private maxReconnectAttempts = 5;
	private shouldReconnect = false;

	/** 获取已保存的 WebSocket 基础地址 */
	getWsBaseUrl(): string {
		const saved = localStorage.getItem('ws_address');
		if (saved && saved.trim()) {
			return saved.trim().replace(/\/+$/, '');
		}
		// 未配置时使用当前页面 host 推导默认地址，避免静默失败
		const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
		return `${protocol}://${location.host}/ws/chat`;
	}

	/** 获取已保存的用户 ID */
	getUserId(): string {
		return localStorage.getItem('user_id') || 'anonymous';
	}

	/**
	 * 建立 WebSocket 连接
	 */
	connect(options: WsOptions) {
		this.options = options;
		this.shouldReconnect = true;
		this.reconnectAttempts = 0;
		this._doConnect();
	}

	private _doConnect() {
		if (!this.options) return;

		// 关闭已有连接
		this._cleanup();

		const baseAddr = this.getWsBaseUrl();

		const { userId, sessionId } = this.options;
		let url = `${baseAddr}?user_id=${encodeURIComponent(userId)}`;
		if (sessionId) {
			url += `&session_id=${encodeURIComponent(sessionId)}`;
		}

		// 注意：API-Key 不放入 URL（浏览器 WS 无法自定义请求头，且 query 参数会
		// 泄露到代理 / 访问日志）。鉴权改为连接建立后发送首个 auth 帧（见 onopen）。

		try {
			this.ws = new WebSocket(url);
		} catch (e) {
			console.error('WebSocket 创建失败:', e);
			this.options.onError?.(new Event('error'));
			return;
		}

		this.ws.onopen = () => {
			this.reconnectAttempts = 0;
			// 鉴权优先：在心跳与 onOpen 回调之前发送 auth 帧，确保其为连接后第一条消息。
			// 服务端（api/ws_chat.py）通过首个 { type: "auth", payload: { api_key } }
			// 帧完成鉴权；未配置 api_key 时不发送，兼容未开启 AUTH_REQUIRED 的服务端。
			const apiKey = localStorage.getItem('api_key');
			if (apiKey && apiKey.trim()) {
				this.ws?.send(JSON.stringify({
					type: 'auth',
					payload: { api_key: apiKey.trim() },
				}));
			}
			this._startHeartbeat();
			this.options?.onOpen?.();
		};

		this.ws.onmessage = (event) => {
			try {
				const msg = JSON.parse(event.data) as WsMessage;
				this.options?.onMessage(msg);
			} catch (e) {
				console.error('WebSocket 消息解析失败:', e);
			}
		};

		this.ws.onclose = () => {
			this._stopHeartbeat();
			this.options?.onClose?.();
			this._scheduleReconnect();
		};

		this.ws.onerror = (err) => {
			console.error('WebSocket 错误:', err);
			this.options?.onError?.(err);
		};
	}

	private _scheduleReconnect() {
		if (!this.shouldReconnect) return;
		if (this.reconnectAttempts >= this.maxReconnectAttempts) {
			console.warn('WebSocket 重连次数超限，停止重连');
			return;
		}

		const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
		this.reconnectAttempts++;

		console.log(`WebSocket 将在 ${delay}ms 后重连 (第 ${this.reconnectAttempts} 次)`);
		this.reconnectTimer = setTimeout(() => {
			this._doConnect();
		}, delay);
	}

	private _startHeartbeat() {
		this._stopHeartbeat();
		this.heartbeatTimer = setInterval(() => {
			if (this.isConnected()) {
				this.send({ type: 'ping', payload: {} });
			}
		}, 30000);
	}

	private _stopHeartbeat() {
		if (this.heartbeatTimer) {
			clearInterval(this.heartbeatTimer);
			this.heartbeatTimer = null;
		}
	}

	private _cleanup() {
		this._stopHeartbeat();
		if (this.reconnectTimer) {
			clearTimeout(this.reconnectTimer);
			this.reconnectTimer = null;
		}
		if (this.ws) {
			this.ws.onopen = null;
			this.ws.onmessage = null;
			this.ws.onclose = null;
			this.ws.onerror = null;
			this.ws.close();
			this.ws = null;
		}
	}

	/**
	 * 发送消息
	 */
	send(msg: WsMessage) {
		if (this.ws && this.ws.readyState === WebSocket.OPEN) {
			this.ws.send(JSON.stringify(msg));
		} else {
			console.warn('WebSocket 未连接，无法发送消息');
		}
	}

	/**
	 * 断开连接（不自动重连）
	 */
	disconnect() {
		this.shouldReconnect = false;
		this._cleanup();
		this.options = null;
	}

	/**
	 * 切换用户（断开并重连）
	 */
	switchUser(newUserId: string) {
		localStorage.setItem('user_id', newUserId);
		if (this.options) {
			this.options.userId = newUserId;
			this.reconnectAttempts = 0;
			this._doConnect();
		}
	}

	/**
	 * 切换会话（断开并重连到新会话）
	 */
	switchSession(newSessionId: string) {
		if (this.options) {
			this.options.sessionId = newSessionId;
			this.reconnectAttempts = 0;
			this._doConnect();
		}
	}

	/**
	 * 是否已连接
	 */
	isConnected(): boolean {
		return this.ws?.readyState === WebSocket.OPEN;
	}

	/**
	 * 获取当前会话 ID
	 */
	getCurrentSessionId(): string | undefined {
		return this.options?.sessionId;
	}
}

export const wsManager = new WsManager();
