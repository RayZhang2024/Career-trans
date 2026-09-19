import { describe, expect, it, vi } from "vitest";
import { SessionApi } from "./api";

describe("SessionApi", () => {
  it("discards a delayed authenticated response after session replacement", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    const expired = vi.fn();
    const api = new SessionApi("token-a", expired);
    const request = api.request<{ owner: string }>("/api/v1/profile");
    api.replaceToken("token-b");
    finish(new Response(JSON.stringify({ owner: "a" }), { status: 200 }));
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(expired).not.toHaveBeenCalled();
  });

  it("handles authenticated 401 through the session-expiry boundary", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 401 })));
    const expired = vi.fn();
    const api = new SessionApi("token", expired);
    await expect(api.request("/api/v1/onboarding/status")).rejects.toMatchObject({ status: 401 });
    expect(expired).toHaveBeenCalledOnce();
  });

  it("discards data when a prior session changes during delayed body parsing", async () => {
    let resolveBody!: (value: unknown) => void;
    const body = new Promise<unknown>((resolve) => { resolveBody = resolve; });
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: () => body })));
    const api = new SessionApi("token-a", vi.fn());
    const request = api.request<{ owner: string }>("/api/v1/profile");
    await Promise.resolve();
    api.replaceToken("token-b");
    resolveBody({ owner: "a" });
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
  });
});
