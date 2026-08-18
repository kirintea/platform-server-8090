import { useState } from 'react';
import { createBrowserRouter, Navigate, RouterProvider, useNavigate } from 'react-router-dom';
import { Toaster } from 'sonner';

import { AppLayout } from '@/components/layout/AppLayout';
import { ChatPage } from '@/pages/chat';
import { MCPPage } from '@/pages/mcp';
import { SetupPage } from '@/pages/setup';
import { SkillPage } from '@/pages/skill';

function SetupPageRoute() {
	const navigate = useNavigate();
	return (
		<>
			<div className="h-screen">
				<SetupPage onComplete={() => navigate('/')} />
			</div>
			<Toaster richColors position="top-right" />
		</>
	);
}

const router = createBrowserRouter(
	[
		{
			element: <AppLayout />,
			children: [
				{ path: '/', element: <Navigate to="/chat" replace /> },
				{ path: '/chat/:sessionId?', element: <ChatPage /> },
				{ path: '/mcp', element: <MCPPage /> },
				{ path: '/skill', element: <SkillPage /> },
			],
		},
		{ path: '/setup', element: <SetupPageRoute /> },
	],
	{ basename: '/webui' },
);

function App() {
	const [ready, setReady] = useState(() => !!localStorage.getItem('user_id'));

	if (!ready) {
		return (
			<>
				<div className="h-screen">
					<SetupPage onComplete={() => setReady(true)} />
				</div>
				<Toaster richColors position="top-right" />
			</>
		);
	}

	return (
		<>
			<RouterProvider router={router} />
			<Toaster richColors position="top-right" />
		</>
	);
}

export default App;
