import { createContext } from 'react';
import type { DeliveryVerification, SendAction } from '../types';

// Verification is an explicit read-only user action, independent of task completion.
export const DeliveryVerificationContext = createContext<{
  verification?: DeliveryVerification; verifying: string | null; send: SendAction;
}>({ verifying: null, send: () => {} });
