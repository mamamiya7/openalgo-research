import { Moon, Sun } from 'lucide-react'
import { Link, Navigate, Outlet } from 'react-router'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { Layout } from './Layout'

/** Credential authentication is sufficient for retained historical evidence. */
export function ResearchLayout() {
  const user = useAuthStore((state) => state.user)
  const { mode, appMode, toggleMode } = useThemeStore()
  const themeLabel = mode === 'light' ? 'Switch to dark mode' : 'Switch to light mode'
  if (!user?.username) return <Navigate to="/login" replace />
  if (user.isLoggedIn && user.broker) return <Layout />
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b">
        <nav
          aria-label="Research navigation"
          className="container mx-auto flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-4"
        >
          <Link to="/" className="text-xl font-bold">
            OpenAlgo
          </Link>
          <Link to="/tools" className="text-sm hover:underline">
            Tools
          </Link>
          <Link to="/broker" className="text-sm hover:underline">
            Connect broker
          </Link>
          <div className="ml-auto flex min-w-0 max-w-full items-center gap-2">
            <span className="min-w-0 break-all text-sm text-muted-foreground">{user.username}</span>
            <Button
              variant="ghost"
              size="icon"
              className="h-9 w-9 shrink-0"
              onClick={toggleMode}
              disabled={appMode !== 'live'}
              title={themeLabel}
              aria-label={themeLabel}
            >
              {mode === 'light' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
            </Button>
          </div>
        </nav>
      </header>
      <main className="container mx-auto px-4 py-8">
        <Outlet />
      </main>
    </div>
  )
}
