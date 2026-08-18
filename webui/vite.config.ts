import path from 'path';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [react(), tailwindcss()],
	base: '/webui/',
	server: {
		port: 5173,
		proxy: {
			'/chat': { target: 'http://localhost:8090', changeOrigin: true },
			'/sessions': { target: 'http://localhost:8090', changeOrigin: true },
			'/mcp': { target: 'http://localhost:8090', changeOrigin: true },
			'/skill': { target: 'http://localhost:8090', changeOrigin: true },
			'/health': { target: 'http://localhost:8090', changeOrigin: true },
			'/ws/chat': {
				target: 'ws://localhost:8090',
				ws: true,
			},
		},
	},
	resolve: {
		alias: {
			'@': path.resolve(__dirname, './src'),
		},
	},
	build: {
		outDir: 'dist',
		emptyOutDir: true,
	},
});
