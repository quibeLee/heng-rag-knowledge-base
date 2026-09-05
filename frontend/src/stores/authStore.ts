import { create } from 'zustand'
interface AuthState {
  user: { id: string; name: string } | null
  token: string | null
  setAuth: (user: AuthState['user'], token: string | null) => void
  logout: () => void
}
export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: null,
  setAuth: (user, token) => set({ user, token }),
  logout: () => set({ user: null, token: null }),
}))