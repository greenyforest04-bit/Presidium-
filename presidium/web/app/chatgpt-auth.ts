import { headers } from "next/headers";
import { redirect } from "next/navigation";

export interface ChatGPTUser {
  userId: string;
  displayName: string;
  email: string;
  fullName: string | null;
}

const USER_ID_HEADER = "oai-authenticated-user-id";
const USER_EMAIL_HEADER = "oai-authenticated-user-email";
const USER_FULL_NAME_HEADER = "oai-authenticated-user-full-name";
const USER_FULL_NAME_ENCODING_HEADER =
  "oai-authenticated-user-full-name-encoding";
const LOCAL_DEV_USER_ID_HEADER = "x-presidium-local-user-id";
const LOCAL_DEV_USER_EMAIL_HEADER = "x-presidium-local-user-email";
const LOCAL_DEV_USER_NAME_HEADER = "x-presidium-local-user-name";
const PERCENT_ENCODED_UTF8 = "percent-encoded-utf-8";
const SIGN_IN_PATH = "/signin-with-chatgpt";
const SIGN_OUT_PATH = "/signout-with-chatgpt";
const CALLBACK_PATH = "/callback";

export function isLoopbackHost(host: string | null): boolean {
  if (!host) return false;
  const trimmed = host.trim().toLowerCase();
  if (trimmed.startsWith("[::1]:") || trimmed === "[::1]" || trimmed === "::1") return true;
  const [hostname, port] = trimmed.split(":");
  if (port && !/^\d{1,5}$/.test(port)) return false;
  return hostname === "localhost" || hostname === "127.0.0.1";
}

export function isExplicitLocalDevAuthEnabled(host: string | null, proto: string | null): boolean {
  if (process.env.PRESIDIUM_LOCAL_DEV_AUTH !== "1") return false;
  if (process.env.NODE_ENV === "production") return false;
  if (proto && proto !== "http" && proto !== "http:") return false;
  return isLoopbackHost(host);
}

export async function getChatGPTUser(): Promise<ChatGPTUser | null> {
  const requestHeaders = await headers();
  const userId = requestHeaders.get(USER_ID_HEADER);
  const email = requestHeaders.get(USER_EMAIL_HEADER);
  if (userId && email) {
    const encodedFullName = requestHeaders.get(USER_FULL_NAME_HEADER);
    const fullName =
      encodedFullName === null
        ? null
        : requestHeaders.get(USER_FULL_NAME_ENCODING_HEADER) ===
            PERCENT_ENCODED_UTF8
          ? decodePercentEncodedHeader(encodedFullName)
          : encodedFullName;

    return {
      userId,
      displayName: fullName || email,
      email,
      fullName,
    };
  }

  // Local test identities are available ONLY when PRESIDIUM_LOCAL_DEV_AUTH=1 is explicitly set
  // on a non-production server AND accessed over loopback HTTP. Client headers or Host alone cannot enable this.
  const host = requestHeaders.get("x-forwarded-host") || requestHeaders.get("host");
  const proto = requestHeaders.get("x-forwarded-proto") || "http";
  if (isExplicitLocalDevAuthEnabled(host, proto)) {
    const devId = (requestHeaders.get(LOCAL_DEV_USER_ID_HEADER) || process.env.PRESIDIUM_LOCAL_DEV_USER_ID || "").trim();
    const devEmail = (requestHeaders.get(LOCAL_DEV_USER_EMAIL_HEADER) || process.env.PRESIDIUM_LOCAL_DEV_USER_EMAIL || "").trim();
    const devName = (requestHeaders.get(LOCAL_DEV_USER_NAME_HEADER) || process.env.PRESIDIUM_LOCAL_DEV_USER_NAME || "").trim();
    if (devId && /^[a-zA-Z0-9_.:@-]{2,128}$/.test(devId) && devEmail && devEmail.includes("@")) {
      return {
        userId: devId,
        displayName: devName || devEmail,
        email: devEmail,
        fullName: devName || null,
      };
    }
  }

  return null;
}

export async function requireChatGPTUser(
  returnTo: string,
): Promise<ChatGPTUser> {
  const user = await getChatGPTUser();
  if (!user) {
    redirect(getSignInUrl(returnTo));
  }
  return user;
}

export function getSignInUrl(returnTo: string): string {
  return buildRedirectUrl(SIGN_IN_PATH, returnTo);
}

export function getSignOutUrl(returnTo: string): string {
  return buildRedirectUrl(SIGN_OUT_PATH, returnTo);
}

function buildRedirectUrl(pathname: string, returnTo: string): string {
  const params = new URLSearchParams({
    return_to: normalizeReturnTo(returnTo),
  });
  return `${pathname}?${params.toString()}`;
}

function normalizeReturnTo(returnTo: string): string {
  if (!returnTo.startsWith("/") || returnTo.startsWith("//")) {
    return "/";
  }
  if (
    returnTo === SIGN_IN_PATH ||
    returnTo.startsWith(`${SIGN_IN_PATH}?`) ||
    returnTo === SIGN_OUT_PATH ||
    returnTo.startsWith(`${SIGN_OUT_PATH}?`) ||
    returnTo === CALLBACK_PATH ||
    returnTo.startsWith(`${CALLBACK_PATH}?`)
  ) {
    return "/";
  }
  return returnTo;
}

function decodePercentEncodedHeader(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
