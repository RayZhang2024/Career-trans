import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { DiscoveryProvider, JobDiscoverySettings, TavilyConnectionTest } from "./api";
import { ApiError, useAuth } from "./auth";

const providerNames: Record<JobDiscoverySettings["effective_provider"], string> = {
  tavily: "Tavily", openai: "OpenAI web search", brave: "Brave Search", disabled: "Disabled", unsupported: "Unsupported deployment provider",
};

export function JobDiscoverySettingsPage() {
  const { api, user } = useAuth();
  const [settings, setSettings] = useState<JobDiscoverySettings | null>(null);
  const [override, setOverride] = useState<DiscoveryProvider | "inherit">("inherit");
  const [key, setKey] = useState("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<{ text: string; role: "status" | "alert" } | null>(null);

  async function load() {
    const found = await api.request<JobDiscoverySettings>("/api/v1/job-discovery/settings");
    setSettings(found); setOverride(found.provider_override ?? "inherit");
  }
  useEffect(() => { void load().catch((error) => setNotice({ text: detail(error, "Could not load Job Discovery settings."), role: "alert" })); }, [api, user?.id]);

  async function saveProvider(event: FormEvent) {
    event.preventDefault(); if (!settings) return;
    setPending(true); setNotice(null);
    try {
      const saved = await api.request<JobDiscoverySettings>("/api/v1/job-discovery/settings", {
        method: "PUT", body: JSON.stringify({ expected_revision: settings.revision, provider_override: override === "inherit" ? null : override }),
      });
      setSettings(saved); setNotice({ text: "Job Discovery provider settings saved.", role: "status" });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await load();
        setNotice({ text: "Settings changed elsewhere. Current settings were reloaded; review your provider choice and try again.", role: "alert" });
      } else setNotice({ text: detail(error, "Could not save provider settings."), role: "alert" });
    }
    finally { setPending(false); }
  }

  async function saveKey(event: FormEvent) {
    event.preventDefault(); if (!settings || !key.trim()) return;
    setPending(true); setNotice(null);
    try {
      const saved = await api.request<JobDiscoverySettings>("/api/v1/job-discovery/tavily-credential", {
        method: "PUT", body: JSON.stringify({ expected_revision: settings.revision, api_key: key }),
      });
      setSettings(saved); setKey(""); setNotice({ text: "Tavily key saved securely. The key is not shown again.", role: "status" });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setKey("");
        await load();
        setNotice({ text: "Settings changed elsewhere. Current settings were reloaded; enter the key again if you still want to save it.", role: "alert" });
      } else setNotice({ text: detail(error, "Could not save the Tavily key."), role: "alert" });
    }
    finally { setPending(false); }
  }

  async function removeKey() {
    if (!settings) return; setPending(true); setNotice(null);
    try {
      const saved = await api.request<JobDiscoverySettings>(`/api/v1/job-discovery/tavily-credential?expected_revision=${settings.revision}`, { method: "DELETE" });
      setSettings(saved); setNotice({ text: "Saved Tavily key removed.", role: "status" });
    } catch (error) { setNotice({ text: detail(error, "Could not remove the Tavily key."), role: "alert" }); if (error instanceof ApiError && error.status === 409) void load(); }
    finally { setPending(false); }
  }

  async function testConnection() {
    setPending(true); setNotice(null);
    try {
      const result = await api.request<TavilyConnectionTest>("/api/v1/job-discovery/tavily-connection-test", { method: "POST", body: "{}" });
      setNotice({ text: result.message, role: "status" });
    } catch (error) { setNotice({ text: detail(error, "Tavily connection test failed."), role: "alert" }); }
    finally { setPending(false); }
  }

  return <main className="workspace ai-settings-page">
    <header className="workspace-header"><div><p className="eyebrow"><Link to="/settings/ai">Settings</Link> / Job Discovery</p><h1>Job Discovery</h1><p>Choose the web-search provider used to find job listings.</p></div></header>
    <nav className="settings-tabs" aria-label="Settings"><Link to="/settings/ai">AI Models</Link><Link aria-current="page" to="/settings/discovery">Job Discovery</Link></nav>
    {notice && <p role={notice.role} className={notice.role === "alert" ? "profile-warning" : "profile-notice"}>{notice.text}</p>}
    {!settings ? <section className="card"><p role="status">Loading Job Discovery settings…</p></section> : <>
      <section className="card ai-state-card">
        <h2>Search provider</h2>
        <p>Effective provider: <strong>{providerNames[settings.effective_provider]}</strong>{settings.provider_override === null ? " (deployment default)" : " (your choice)"}.</p>
        <p className="muted">This controls job-search web results. AI Models settings independently control semantic strategy generation, extraction, and assessment.</p>
        <form onSubmit={saveProvider} className="settings-form">
          <label htmlFor="discovery-provider">Provider</label>
          <select id="discovery-provider" value={override} onChange={(event) => setOverride(event.target.value as DiscoveryProvider | "inherit")}>
            <option value="inherit">Use deployment default ({providerNames[settings.deployment_provider]})</option>
            <option value="tavily">Tavily</option><option value="openai">OpenAI web search</option><option value="disabled">Disabled</option>
          </select>
          <p className="muted">Brave Search remains available as a deployment default. No provider fallback occurs if the selected provider is unavailable.</p>
          <button type="submit" disabled={pending}>Save provider</button>
        </form>
      </section>
      <section className="card ai-state-card">
        <h2>Tavily API key</h2>
        <p>{settings.tavily_credential_configured ? `A key is configured (${settings.tavily_credential_source === "user" ? "your saved key" : "deployment key"}).` : "No Tavily key is configured."}</p>
        {!settings.tavily_user_credential_storage_available && <p role="note">User key storage is unavailable until the administrator configures the credential-encryption key. A deployment key can still be used.</p>}
        <p className="muted">Saving a personal key uses it before the deployment key whenever Tavily is selected. Keys are stored encrypted and are never displayed again.</p>
        <form onSubmit={saveKey} className="settings-form">
          <label htmlFor="tavily-api-key">{settings.tavily_credential_configured && settings.tavily_credential_source === "user" ? "Replace saved Tavily key" : "Tavily API key"}</label>
          <input id="tavily-api-key" type="password" autoComplete="new-password" value={key} onChange={(event) => setKey(event.target.value)} placeholder="Paste a Tavily API key" disabled={pending || !settings.tavily_user_credential_storage_available} />
          <div className="ai-actions"><button type="submit" disabled={pending || !key.trim() || !settings.tavily_user_credential_storage_available}>Save key</button>
            {settings.tavily_credential_source === "user" && <button type="button" className="secondary-button" onClick={() => void removeKey()} disabled={pending}>Remove saved key</button>}
            <button type="button" className="secondary-button" onClick={() => void testConnection()} disabled={pending || !settings.tavily_credential_configured}>Test connection</button></div>
        </form>
        <p className="muted">Test connection sends one Basic Search request and uses one Tavily API credit. It does not save discovery results or change a schedule.</p>
      </section>
    </>}
  </main>;
}

function detail(error: unknown, fallback: string): string {
  return error instanceof ApiError && error.detail ? error.detail : fallback;
}
