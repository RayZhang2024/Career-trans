import { describe, expect, it, vi } from "vitest";
import { SessionApi } from "./api";

describe("SessionApi", () => {
  it("uses JSON content type for JSON request bodies", async () => {
    let options: RequestInit | undefined;
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => { options = init; return new Response(JSON.stringify({ ok: true }), { status: 200 }); });
    vi.stubGlobal("fetch", fetchMock);
    await new SessionApi("token", vi.fn()).request("/api/v1/profile", { method: "PATCH", body: JSON.stringify({ headline: "Engineer" }) });
    const headers = new Headers(options?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("does not manually set content type for FormData", async () => {
    let options: RequestInit | undefined;
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => { options = init; return new Response(JSON.stringify({ ok: true }), { status: 200 }); });
    vi.stubGlobal("fetch", fetchMock);
    const body = new FormData(); body.append("files", new File(["cv"], "cv.md", { type: "text/markdown" }));
    await new SessionApi("token", vi.fn()).request("/api/v1/cv-ingestion/upload", { method: "POST", body });
    const headers = new Headers(options?.headers);
    expect(headers.has("Content-Type")).toBe(false);
  });

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

  it("discards a delayed authenticated CV operation after session replacement", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    const api = new SessionApi("token-a", vi.fn());
    const request = api.request("/api/v1/cv-ingestion/draft-1/interpret", { method: "POST" });
    api.replaceToken("token-b");
    finish(new Response(JSON.stringify({ state: "review_ready" }), { status: 200 }));
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
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

  it("downloads authenticated binary content and exposes its safe response filename", async () => {
    let options: RequestInit | undefined;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => { options = init; return new Response("pdf", { status: 200, headers: { "Content-Disposition": 'attachment; filename="Example_CV.pdf"' } }); }));
    const result = await new SessionApi("token", vi.fn()).requestBlob("/api/v1/applications/p/cv.pdf");
    expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer token");
    expect(await result.blob.text()).toBe("pdf");
    expect(result.filename).toBe("Example_CV.pdf");
  });

  it("aborts an in-flight binary body on session replacement without triggering stale 401 side effects", async () => {
    let resolveBlob!: (blob: Blob) => void;
    const body = new Promise<Blob>((resolve) => { resolveBlob = resolve; });
    vi.stubGlobal("fetch", vi.fn(async () => ({ status: 200, ok: true, headers: new Headers(), blob: () => body })));
    const on401 = vi.fn();
    const api = new SessionApi("old", on401);
    const pending = api.requestBlob("/api/v1/applications/p/cv.pdf");
    await Promise.resolve();
    api.replaceToken("new");
    resolveBlob(new Blob(["old-session"]));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(on401).not.toHaveBeenCalled();
  });

  it("uses the authenticated 401 session-expiry boundary for binary requests", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 401 })));
    const expired = vi.fn();
    await expect(new SessionApi("token", expired).requestBlob("/api/v1/applications/p/cv.pdf")).rejects.toMatchObject({ status: 401 });
    expect(expired).toHaveBeenCalledOnce();
  });

  it("rejects binary HTTP failures as safe ApiErrors without parsing their body", async () => {
    const blob = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => ({ status: 503, ok: false, headers: new Headers(), blob })));
    await expect(new SessionApi("token", vi.fn()).requestBlob("/api/v1/applications/p/cv.pdf")).rejects.toMatchObject({ status: 503, message: "Request could not be completed." });
    expect(blob).not.toHaveBeenCalled();
  });
});
