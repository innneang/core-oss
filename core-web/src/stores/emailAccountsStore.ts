import { create } from 'zustand';
import {
  getOAuthConfig,
  getEmailAccounts,
  addEmailAccount,
  removeEmailAccount,
  type EmailAccount,
} from '../api/client';
import {
  openGoogleOAuth,
  openMicrosoftOAuth,
  GOOGLE_SCOPES,
  MICROSOFT_SCOPES,
} from '../utils/oauthPopup';

const MAX_ACCOUNTS = 5;

interface EmailAccountsState {
  accounts: EmailAccount[];
  isLoading: boolean;
  isAdding: boolean;
  error: string | null;

  fetchAccounts: () => Promise<void>;
  addGoogleAccount: () => Promise<void>;
  addMicrosoftAccount: () => Promise<void>;
  addICloudAccount: (email: string, appPassword: string, displayName?: string, authEmail?: string) => Promise<void>;
  removeAccount: (id: string) => Promise<void>;
}

export const useEmailAccountsStore = create<EmailAccountsState>()((set, get) => ({
  accounts: [],
  isLoading: false,
  isAdding: false,
  error: null,

  fetchAccounts: async () => {
    set({ isLoading: true, error: null });
    try {
      const { accounts } = await getEmailAccounts();
      // Sort by account_order
      accounts.sort((a, b) => a.account_order - b.account_order);
      set({ accounts, isLoading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : 'Failed to fetch accounts',
        isLoading: false,
      });
    }
  },

  addGoogleAccount: async () => {
    if (get().accounts.length >= MAX_ACCOUNTS) {
      set({ error: `Maximum of ${MAX_ACCOUNTS} email accounts allowed` });
      return;
    }

    set({ isAdding: true, error: null });
    try {
      const config = await getOAuthConfig();
      const { code } = await openGoogleOAuth(config.google_client_id);

      const redirectUri = `${window.location.origin}/oauth/callback`;
      const account = await addEmailAccount({
        provider: 'google',
        server_auth_code: code,
        redirect_uri: redirectUri,
        scopes: GOOGLE_SCOPES,
      });

      set((state) => ({
        accounts: [...state.accounts, account].sort((a, b) => a.account_order - b.account_order),
        isAdding: false,
      }));
    } catch (err) {
      // Don't show error for user cancellation
      const message = err instanceof Error ? err.message : 'Failed to add Google account';
      if (message === 'Authentication cancelled') {
        set({ isAdding: false });
      } else {
        set({ error: message, isAdding: false });
      }
    }
  },

  addMicrosoftAccount: async () => {
    if (get().accounts.length >= MAX_ACCOUNTS) {
      set({ error: `Maximum of ${MAX_ACCOUNTS} email accounts allowed` });
      return;
    }

    set({ isAdding: true, error: null });
    try {
      const config = await getOAuthConfig();
      if (!config.microsoft_client_id) {
        throw new Error('Microsoft OAuth is not configured');
      }

      const { code, codeVerifier } = await openMicrosoftOAuth(config.microsoft_client_id);

      const redirectUri = `${window.location.origin}/oauth/callback`;
      const account = await addEmailAccount({
        provider: 'microsoft',
        server_auth_code: code,
        code_verifier: codeVerifier,
        redirect_uri: redirectUri,
        scopes: MICROSOFT_SCOPES,
      });

      set((state) => ({
        accounts: [...state.accounts, account].sort((a, b) => a.account_order - b.account_order),
        isAdding: false,
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to add Microsoft account';
      if (message === 'Authentication cancelled') {
        set({ isAdding: false });
      } else {
        set({ error: message, isAdding: false });
      }
    }
  },

  addICloudAccount: async (email, appPassword, displayName, authEmail) => {
    if (get().accounts.length >= MAX_ACCOUNTS) {
      set({ error: `Maximum of ${MAX_ACCOUNTS} email accounts allowed` });
      return;
    }

    set({ isAdding: true, error: null });
    try {
      const account = await addEmailAccount({
        provider: 'icloud',
        provider_email: email,
        auth_email: authEmail,
        provider_name: displayName,
        app_password: appPassword,
        scopes: [],
      });

      set((state) => ({
        accounts: [...state.accounts, account].sort((a, b) => a.account_order - b.account_order),
        isAdding: false,
      }));
    } catch (err) {
      const error = err instanceof Error ? err : new Error('Failed to add iCloud account');
      set({
        error: error.message,
        isAdding: false,
      });
      throw error;
    }
  },

  removeAccount: async (id: string) => {
    const account = get().accounts.find((a) => a.id === id);
    if (!account) return;

    if (account.is_primary) {
      set({ error: 'Cannot remove primary account' });
      return;
    }

    // Optimistic removal
    const previousAccounts = get().accounts;
    set((state) => ({
      accounts: state.accounts.filter((a) => a.id !== id),
      error: null,
    }));

    try {
      await removeEmailAccount(id);
    } catch (err) {
      // Revert on failure
      set({
        accounts: previousAccounts,
        error: err instanceof Error ? err.message : 'Failed to remove account',
      });
    }
  },
}));
