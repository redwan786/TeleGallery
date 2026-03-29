import axios, { AxiosInstance } from 'axios';
import type { Credentials } from '@/context/ConfigContext';

let instance: AxiosInstance | null = null;

export function getApi(creds: Credentials): AxiosInstance {
  if (instance) return instance;
  instance = axios.create({
    baseURL: creds.backendUrl,
    headers: {
      'X-Bot-Token': creds.botToken,
      'X-Channel-Id': creds.channelId,
      'X-Supabase-Url': creds.supabaseUrl,
      'X-Supabase-Key': creds.supabaseAnonKey,
      'X-Api-Id': creds.apiId || '',
      'X-Api-Hash': creds.apiHash || '',
    },
  });
  return instance;
}

export function getApiHeaders(creds: Credentials): Record<string, string> {
  return {
    'X-Bot-Token': creds.botToken,
    'X-Channel-Id': creds.channelId,
    'X-Supabase-Url': creds.supabaseUrl,
    'X-Supabase-Key': creds.supabaseAnonKey,
    'X-Api-Id': creds.apiId || '',
    'X-Api-Hash': creds.apiHash || '',
  };
}

export function resetApi() {
  instance = null;
}