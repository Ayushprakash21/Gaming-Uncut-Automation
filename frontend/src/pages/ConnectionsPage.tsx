import React, { useEffect, useState } from 'react'
import {
  Share2,
  Youtube,
  Instagram,
  Facebook,
  Sparkles,
  Shield,
  KeyRound,
  CheckCircle2,
  AlertCircle,
  Loader2,
  FileCheck,
  Sliders,
  ExternalLink,
  Copy,
  Check,
  RotateCcw,
  X
} from 'lucide-react'
import {
  fetchConnections,
  updateConnections,
  importGoogleSecrets,
  fetchSystemLimits,
  updateSystemLimits,
  startYouTubeOAuth,
  getYouTubeOAuthStatus,
  cancelYouTubeOAuth,
  testYouTubeConnection,
  disconnectYouTubeAccount,
  testFacebookConnection
} from '../services/api'
import { ConnectionsData, SystemLimits } from '../types'

type OAuthStage = 'idle' | 'opening' | 'waiting_for_consent' | 'verifying_channel' | 'connected' | 'failed' | 'cancelled' | 'timed_out'

export const ConnectionsPage: React.FC = () => {
  const [connections, setConnections] = useState<ConnectionsData | null>(null)
  const [googlePath, setGooglePath] = useState('C:\\Users\\Ayush\\Downloads\\client_secrets.json')
  const [importingGoogle, setImportingGoogle] = useState(false)
  const [googleMsg, setGoogleMsg] = useState('')
  const [googleErr, setGoogleErr] = useState('')

  const [oauthStage, setOauthStage] = useState<OAuthStage>('idle')
  const [authUrl, setAuthUrl] = useState('')
  const [authError, setAuthError] = useState('')
  const [copied, setCopied] = useState(false)

  const [testingYt, setTestingYt] = useState(false)
  const [disconnectingYt, setDisconnectingYt] = useState(false)
  const [ytMsg, setYtMsg] = useState('')
  const [ytErr, setYtErr] = useState('')

  const [geminiKey, setGeminiKey] = useState('')
  const [metaAppId, setMetaAppId] = useState('')
  const [metaAppSecret, setMetaAppSecret] = useState('')
  const [metaPageToken, setMetaPageToken] = useState('')
  const [metaPageId, setMetaPageId] = useState('')
  const [metaIgId, setMetaIgId] = useState('')
  const [testingFb, setTestingFb] = useState(false)
  const [fbTestResult, setFbTestResult] = useState<any | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  const [limits, setLimits] = useState<SystemLimits>({
    daily_generation_target: 15,
    daily_publishing_target: 15,
    youtube_daily_limit: 15,
    instagram_daily_limit: 25,
    facebook_daily_limit: 25,
    auto_reschedule_on_quota: true
  })
  const [savingLimits, setSavingLimits] = useState(false)
  const [limitsMsg, setLimitsMsg] = useState('')

  const load = async () => {
    try {
      const data = await fetchConnections()
      setConnections(data)
      if (data.facebook?.page_id || data.meta?.page_id) {
        setMetaPageId(data.facebook?.page_id || data.meta?.page_id || '')
      }
      const limitsData = await fetchSystemLimits()
      if (limitsData) setLimits(limitsData)
    } catch (e) {
      console.error(e)
    }
  }

  useEffect(() => {
    load()
  }, [])

  // Poll OAuth status when in waiting_for_consent or verifying_channel
  useEffect(() => {
    if (oauthStage !== 'waiting_for_consent' && oauthStage !== 'verifying_channel') {
      return
    }

    const interval = setInterval(async () => {
      try {
        const res = await getYouTubeOAuthStatus()
        if (res.status === 'verifying_channel') {
          setOauthStage('verifying_channel')
        } else if (res.status === 'connected') {
          setOauthStage('connected')
          setYtMsg(`✓ Successfully authorized & verified YouTube channel: "${res.channel?.title || 'YouTube Channel'}" (ID: ${res.channel?.channel_id})`)
          setYtErr('')
          load()
        } else if (res.status === 'failed') {
          setOauthStage('failed')
          setAuthError(res.error || 'YouTube authorization failed.')
        } else if (res.status === 'cancelled') {
          setOauthStage('cancelled')
          setAuthError(res.error || 'Authorization was cancelled.')
        } else if (res.status === 'timed_out') {
          setOauthStage('timed_out')
          setAuthError('Authorization timed out waiting for Google consent. Please retry.')
        } else if (res.status === 'waiting_for_consent' && res.auth_url) {
          setAuthUrl(res.auth_url)
        }
      } catch (e) {
        // network glitch, continue polling
      }
    }, 1500)

    return () => clearInterval(interval)
  }, [oauthStage])

  const handleImportGoogle = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!googlePath.trim()) {
      setGoogleErr('Please specify the path to your client_secrets.json file.')
      return
    }

    setImportingGoogle(true)
    setGoogleMsg('')
    setGoogleErr('')

    try {
      const res = await importGoogleSecrets(googlePath.trim())
      setGoogleMsg(
        `✓ ${res.message || 'OAuth Client verified & saved'} (Project: ${res.project_id || 'gaming-uncut'}, Client: ${res.client_id_preview || 'Configured'})`
      )
      load()
    } catch (err: any) {
      setGoogleErr(err.message || 'Failed to import Google credentials')
    } finally {
      setImportingGoogle(false)
    }
  }

  const handleConnectYouTube = async () => {
    setOauthStage('opening')
    setAuthError('')
    setYtMsg('')
    setYtErr('')
    try {
      const res = await startYouTubeOAuth()
      if (res.auth_url) {
        setAuthUrl(res.auth_url)
      }
      setOauthStage('waiting_for_consent')
    } catch (err: any) {
      setOauthStage('failed')
      setAuthError(err.message || 'Failed to initiate YouTube authorization.')
    }
  }

  const handleCancelOAuth = async () => {
    try {
      await cancelYouTubeOAuth()
    } catch (e) {}
    setOauthStage('idle')
    setAuthUrl('')
    setAuthError('')
  }

  const handleCopyAuthUrl = () => {
    if (authUrl) {
      navigator.clipboard.writeText(authUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  const handleTestYouTube = async () => {
    setTestingYt(true)
    setYtMsg('')
    setYtErr('')
    try {
      const res = await testYouTubeConnection()
      if (res.connected) {
        setYtMsg(`✓ YouTube connection is ACTIVE and verified for channel: "${res.channel?.title}"`)
      } else {
        setYtErr(res.error || 'YouTube connection test failed.')
      }
      load()
    } catch (err: any) {
      setYtErr(err.message || 'YouTube test failed.')
    } finally {
      setTestingYt(false)
    }
  }

  const handleDisconnectYouTube = async () => {
    if (!confirm('Are you sure you want to disconnect this YouTube channel?')) return
    setDisconnectingYt(true)
    setYtMsg('')
    setYtErr('')
    try {
      await disconnectYouTubeAccount()
      setYtMsg('YouTube account disconnected. OAuth tokens removed from secure vault.')
      load()
    } catch (err: any) {
      setYtErr(err.message || 'Failed to disconnect YouTube.')
    } finally {
      setDisconnectingYt(false)
    }
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setMsg('')
    try {
      await updateConnections({
        gemini_api_key: geminiKey || undefined,
        meta_app_id: metaAppId || undefined,
        meta_app_secret: metaAppSecret || undefined,
        meta_page_token: metaPageToken || undefined,
        meta_page_id: metaPageId || undefined,
        meta_ig_id: metaIgId || undefined
      })
      setMsg('Credentials encrypted and securely stored in Windows Credential Manager / Vault.')
      setGeminiKey('')
      setMetaAppSecret('')
      setMetaPageToken('')
      load()
    } catch (err: any) {
      alert('Error saving credentials: ' + err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleTestFacebook = async () => {
    setTestingFb(true)
    setFbTestResult(null)
    try {
      const res = await testFacebookConnection()
      setFbTestResult(res)
    } catch (err: any) {
      setFbTestResult({ authenticated: false, message: err.message })
    } finally {
      setTestingFb(false)
    }
  }

  const handleSaveLimits = async (e: React.FormEvent) => {
    e.preventDefault()
    setSavingLimits(true)
    setLimitsMsg('')
    try {
      await updateSystemLimits(limits)
      setLimitsMsg('Production targets and platform quota limits updated successfully!')
      load()
    } catch (err: any) {
      alert('Error saving limits: ' + err.message)
    } finally {
      setSavingLimits(false)
    }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-bold font-brand tracking-wide text-slate-100 flex items-center space-x-2">
          <KeyRound className="w-5 h-5 text-purple-400" />
          <span>PLATFORM CONNECTIONS & SECURE VAULT</span>
        </h2>
        <p className="text-xs text-slate-400">
          Encrypted local storage via Windows Credential Manager / DPAPI. Secrets are never hardcoded or exposed in Git.
        </p>
      </div>

      {msg && (
        <div className="bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs px-4 py-3 rounded-xl flex items-center space-x-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
          <span>{msg}</span>
        </div>
      )}

      {/* Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* YouTube */}
        <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <Youtube className="w-5 h-5 text-red-500" />
              <span className="font-bold text-xs text-slate-200">YouTube Shorts</span>
            </div>
            {connections?.youtube.authenticated && connections?.youtube.channel ? (
              <span className="text-[10px] px-2 py-0.5 rounded-full font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                CONNECTED
              </span>
            ) : connections?.youtube.client_configured ? (
              <span className="text-[10px] px-2 py-0.5 rounded-full font-bold bg-amber-500/10 text-amber-400 border border-amber-500/30">
                AUTH REQUIRED
              </span>
            ) : (
              <span className="text-[10px] px-2 py-0.5 rounded-full font-bold bg-slate-800 text-slate-400">
                NOT CONFIGURED
              </span>
            )}
          </div>
          {connections?.youtube.authenticated && connections?.youtube.channel ? (
            <div className="space-y-1">
              <p className="text-xs font-bold text-slate-100 flex items-center space-x-1.5">
                {connections.youtube.channel.thumbnail_url && (
                  <img src={connections.youtube.channel.thumbnail_url} className="w-4 h-4 rounded-full" />
                )}
                <span className="truncate">{connections.youtube.channel.title}</span>
              </p>
              <p className="text-[10px] text-slate-400 font-mono">
                Channel ID: {connections.youtube.channel.channel_id}
              </p>
            </div>
          ) : (
            <p className="text-[11px] text-slate-400">
              {connections?.youtube.client_configured
                ? 'Desktop OAuth client ready. Click Connect below to authorize channel.'
                : 'Import client_secrets.json below to enable YouTube authorization.'}
            </p>
          )}
          <div className="text-[10px] text-slate-400 font-mono">
            Project: <span className="text-slate-300">{connections?.youtube.project_id || 'gaming-uncut'}</span>
          </div>
        </div>

        {/* Meta */}
        <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 space-y-2">
          <div className="flex items-center space-x-2">
            <Instagram className="w-5 h-5 text-pink-500" />
            <span className="font-bold text-xs text-slate-200">Instagram & Facebook</span>
          </div>
          <p className="text-[11px] text-slate-400">
            {connections?.facebook.connected
              ? 'Facebook Page & IG Connected'
              : connections?.meta.token_configured
              ? 'Page Access Token Active (Page ID required)'
              : 'Access Token Pending'}
          </p>
          <div className="text-[10px] text-slate-400 font-mono">
            Page ID: {connections?.facebook?.page_id || connections?.meta?.page_id || 'Pending'}
          </div>
          <div className="text-[10px] text-slate-400 font-mono">
            IG Account: {connections?.meta.instagram_account_id || 'Pending'}
          </div>
        </div>

        {/* Gemini AI */}
        <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 space-y-2">
          <div className="flex items-center space-x-2">
            <Sparkles className="w-5 h-5 text-amber-400" />
            <span className="font-bold text-xs text-slate-200">Gemini AI Engine</span>
          </div>
          <p className="text-[11px] text-slate-400">
            {connections?.gemini.api_key_configured ? 'API Key Configured & Ready' : 'Using Local Signal-Fusion Fallback'}
          </p>
          <div className="text-[10px] text-cyan-400 font-mono">
            Model: gemini-2.5-flash
          </div>
        </div>
      </div>

      {/* Google OAuth Import Card */}
      <form onSubmit={handleImportGoogle} className="bg-[#111827] border border-gaming-border rounded-2xl p-6 space-y-4 shadow-xl">
        <div className="flex items-center space-x-2 text-purple-400 font-semibold text-sm">
          <Youtube className="w-5 h-5 text-red-500" />
          <span>Step 1: Import Google OAuth Desktop Client Credentials</span>
        </div>
        <p className="text-xs text-slate-400">
          Enter the full Windows path to your downloaded <code className="bg-slate-800 text-purple-300 px-1.5 py-0.5 rounded font-mono">client_secrets.json</code> file to securely store and verify the Desktop client credentials in Windows Vault.
        </p>

        {googleMsg && (
          <div className="bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs px-4 py-3 rounded-xl flex items-center space-x-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
            <span>{googleMsg}</span>
          </div>
        )}

        {googleErr && (
          <div className="bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs px-4 py-3 rounded-xl flex items-center space-x-2">
            <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
            <span>{googleErr}</span>
          </div>
        )}

        <div className="space-y-2">
          <label className="block text-xs font-semibold text-slate-300">Google OAuth JSON Path</label>
          <div className="flex flex-col sm:flex-row gap-3">
            <input
              type="text"
              value={googlePath}
              onChange={(e) => setGooglePath(e.target.value)}
              placeholder="C:\Users\...\Downloads\client_secrets.json"
              className="flex-1 bg-[#0a0d14] border border-gaming-border rounded-xl px-3.5 py-2.5 text-xs text-slate-200 placeholder-slate-600 font-mono"
            />
            <button
              type="submit"
              disabled={importingGoogle}
              className="bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white font-bold text-xs px-6 py-2.5 rounded-xl transition flex items-center justify-center space-x-2 flex-shrink-0"
            >
              {importingGoogle ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Verifying & Encrypting...</span>
                </>
              ) : (
                <>
                  <FileCheck className="w-4 h-4" />
                  <span>Import Secrets into Vault</span>
                </>
              )}
            </button>
          </div>
        </div>
      </form>

      {/* YouTube Channel Authorization Card */}
      <div className="bg-[#111827] border border-gaming-border rounded-2xl p-6 space-y-4 shadow-xl">
        <div className="flex items-center justify-between pb-3 border-b border-gaming-border">
          <div className="flex items-center space-x-2.5">
            <Youtube className="w-5 h-5 text-red-500" />
            <div>
              <h3 className="font-bold text-sm text-slate-100">Step 2: YouTube Channel Authorization (Official Google OAuth 2.0)</h3>
              <p className="text-xs text-slate-400">Connect and verify your YouTube channel for official resumable video publishing.</p>
            </div>
          </div>
          {connections?.youtube.authenticated && connections?.youtube.channel ? (
            <span className="text-xs font-bold px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5" />
              Verified & Ready
            </span>
          ) : (
            <span className="text-xs font-bold px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/30 flex items-center gap-1.5">
              <AlertCircle className="w-3.5 h-3.5" />
              Not Connected
            </span>
          )}
        </div>

        {ytMsg && (
          <div className="bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs px-4 py-3 rounded-xl flex items-center space-x-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
            <span>{ytMsg}</span>
          </div>
        )}

        {ytErr && (
          <div className="bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs px-4 py-3 rounded-xl flex items-center space-x-2">
            <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
            <span>{ytErr}</span>
          </div>
        )}

        {connections?.youtube.authenticated && connections.youtube.channel ? (
          <div className="bg-[#0a0d14] border border-gaming-border rounded-xl p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div className="flex items-center space-x-3">
              {connections.youtube.channel.thumbnail_url ? (
                <img src={connections.youtube.channel.thumbnail_url} alt="Channel Thumbnail" className="w-12 h-12 rounded-full border border-slate-700" />
              ) : (
                <div className="w-12 h-12 rounded-full bg-red-600/20 border border-red-500/30 flex items-center justify-center">
                  <Youtube className="w-6 h-6 text-red-500" />
                </div>
              )}
              <div className="space-y-0.5">
                <div className="flex items-center space-x-2">
                  <span className="font-bold text-slate-100 text-sm">{connections.youtube.channel.title}</span>
                  {connections.youtube.channel.custom_url && (
                    <span className="text-xs text-purple-400 font-mono">{connections.youtube.channel.custom_url}</span>
                  )}
                </div>
                <p className="text-[11px] text-slate-400 font-mono">
                  Channel ID: <span className="text-slate-300 font-bold">{connections.youtube.channel.channel_id}</span>
                </p>
                <div className="flex items-center space-x-3 text-[11px] text-slate-500">
                  <span>Subscribers: <strong className="text-slate-300">{Number(connections.youtube.channel.subscriber_count || 0).toLocaleString()}</strong></span>
                  <span>•</span>
                  <span>Videos: <strong className="text-slate-300">{Number(connections.youtube.channel.video_count || 0).toLocaleString()}</strong></span>
                </div>
              </div>
            </div>

            <div className="flex items-center space-x-2 w-full sm:w-auto">
              <button
                type="button"
                onClick={handleTestYouTube}
                disabled={testingYt}
                className="flex-1 sm:flex-none bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold px-3.5 py-2 rounded-xl transition flex items-center justify-center space-x-1.5"
              >
                {testingYt ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileCheck className="w-3.5 h-3.5 text-emerald-400" />}
                <span>Test Connection</span>
              </button>
              <button
                type="button"
                onClick={handleDisconnectYouTube}
                disabled={disconnectingYt}
                className="flex-1 sm:flex-none bg-rose-600/20 hover:bg-rose-600/30 text-rose-300 border border-rose-500/30 text-xs font-semibold px-3.5 py-2 rounded-xl transition"
              >
                {disconnectingYt ? 'Disconnecting...' : 'Disconnect'}
              </button>
            </div>
          </div>
        ) : oauthStage === 'opening' ? (
          <div className="bg-[#0a0d14] border border-gaming-border rounded-xl p-5 space-y-3">
            <div className="flex items-center space-x-3 text-purple-400">
              <Loader2 className="w-5 h-5 animate-spin" />
              <span className="text-xs font-bold">Initializing Local Authorization Server & Google Consent...</span>
            </div>
            <p className="text-xs text-slate-400">
              Binding local loopback port on localhost and generating cryptographic CSRF state.
            </p>
          </div>
        ) : oauthStage === 'waiting_for_consent' ? (
          <div className="bg-[#0a0d14] border border-amber-500/30 rounded-xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2 text-amber-400 text-xs font-bold">
                <Loader2 className="w-4 h-4 animate-spin text-amber-400" />
                <span>Waiting for Google Authorization in Browser...</span>
              </div>
              <button
                type="button"
                onClick={handleCancelOAuth}
                className="text-slate-400 hover:text-slate-200 text-xs flex items-center space-x-1"
              >
                <X className="w-3.5 h-3.5" />
                <span>Cancel</span>
              </button>
            </div>

            {/* Progress Step Indicator */}
            <div className="flex items-center space-x-2 text-[11px]">
              <span className="px-2 py-0.5 rounded font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">
                1. Google Consent
              </span>
              <span className="text-slate-600">→</span>
              <span className="px-2 py-0.5 rounded font-medium bg-slate-800 text-slate-500">
                2. Channel Verification
              </span>
              <span className="text-slate-600">→</span>
              <span className="px-2 py-0.5 rounded font-medium bg-slate-800 text-slate-500">
                3. Connected
              </span>
            </div>

            <p className="text-xs text-slate-300">
              We attempted to launch Google's official consent screen in your default browser. If a new tab did not open automatically, click the button below to complete authorization:
            </p>

            <div className="flex flex-wrap items-center gap-3 pt-1">
              {authUrl && (
                <a
                  href={authUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="bg-red-600 hover:bg-red-500 text-white font-bold text-xs px-5 py-2.5 rounded-xl transition flex items-center space-x-2 shadow-lg shadow-red-600/20"
                >
                  <ExternalLink className="w-4 h-4" />
                  <span>Open Google Authorization in Browser ↗</span>
                </a>
              )}
              {authUrl && (
                <button
                  type="button"
                  onClick={handleCopyAuthUrl}
                  className="bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold px-4 py-2.5 rounded-xl transition flex items-center space-x-2"
                >
                  {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                  <span>{copied ? 'Link Copied!' : 'Copy Auth Link'}</span>
                </button>
              )}
              <button
                type="button"
                onClick={handleCancelOAuth}
                className="bg-slate-800/60 hover:bg-slate-800 text-slate-400 hover:text-slate-200 text-xs font-semibold px-4 py-2.5 rounded-xl transition"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : oauthStage === 'verifying_channel' ? (
          <div className="bg-[#0a0d14] border border-purple-500/30 rounded-xl p-5 space-y-3">
            <div className="flex items-center space-x-3 text-purple-400">
              <Loader2 className="w-5 h-5 animate-spin" />
              <span className="text-xs font-bold">Google Consent Received! Verifying YouTube Channel...</span>
            </div>
            <div className="flex items-center space-x-2 text-[11px]">
              <span className="px-2 py-0.5 rounded font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                ✓ 1. Consent Granted
              </span>
              <span className="text-slate-600">→</span>
              <span className="px-2 py-0.5 rounded font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30 animate-pulse">
                2. Channel Verification
              </span>
              <span className="text-slate-600">→</span>
              <span className="px-2 py-0.5 rounded font-medium bg-slate-800 text-slate-500">
                3. Connected
              </span>
            </div>
            <p className="text-xs text-slate-400">
              Exchanging authorization code for tokens and querying YouTube Data API channels.list(mine=true) to confirm channel access.
            </p>
          </div>
        ) : oauthStage === 'failed' || oauthStage === 'cancelled' || oauthStage === 'timed_out' ? (
          <div className="bg-[#0a0d14] border border-rose-500/30 rounded-xl p-5 space-y-3">
            <div className="flex items-center space-x-2 text-rose-400">
              <AlertCircle className="w-5 h-5 flex-shrink-0" />
              <span className="text-xs font-bold">
                {oauthStage === 'timed_out' ? 'Authorization Timed Out' : oauthStage === 'cancelled' ? 'Authorization Cancelled' : 'YouTube Authorization Failed'}
              </span>
            </div>
            <p className="text-xs text-rose-300/90 leading-relaxed font-mono">
              {authError || 'An error occurred during Google OAuth authorization.'}
            </p>
            <div className="flex items-center space-x-3 pt-2">
              <button
                type="button"
                onClick={handleConnectYouTube}
                className="bg-red-600 hover:bg-red-500 text-white font-bold text-xs px-5 py-2 rounded-xl transition flex items-center space-x-2"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Retry YouTube Authorization</span>
              </button>
              <button
                type="button"
                onClick={() => setOauthStage('idle')}
                className="bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold px-4 py-2 rounded-xl transition"
              >
                Dismiss
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-slate-400">
              {connections?.youtube.client_configured
                ? 'Your Google OAuth Desktop client secrets are imported and verified. Click below to launch Google’s official desktop consent screen on localhost and authorize your YouTube channel.'
                : 'Please import your Google Desktop OAuth client_secrets.json file in Step 1 first before initiating channel authorization.'}
            </p>
            <button
              type="button"
              onClick={handleConnectYouTube}
              disabled={!connections?.youtube.client_configured}
              className="bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white font-bold text-xs px-6 py-2.5 rounded-xl transition flex items-center space-x-2 shadow-lg shadow-red-600/20"
            >
              <Youtube className="w-4 h-4" />
              <span>Connect YouTube Account</span>
            </button>
          </div>
        )}
      </div>

      {/* Credential Update Form */}
      <form onSubmit={handleSave} className="bg-[#111827] border border-gaming-border rounded-2xl p-6 space-y-6 shadow-xl">
        <h3 className="text-xs font-bold uppercase tracking-wider text-purple-400">Update Platform Secrets</h3>

        {/* Gemini */}
        <div className="space-y-2">
          <label className="block text-xs font-semibold text-slate-300">Gemini API Key</label>
          <input
            type="password"
            value={geminiKey}
            onChange={(e) => setGeminiKey(e.target.value)}
            placeholder="AIzaSy... (leave blank to keep existing)"
            className="w-full bg-[#0a0d14] border border-gaming-border rounded-xl px-3.5 py-2.5 text-xs text-slate-200 placeholder-slate-600 font-mono"
          />
        </div>

        {/* Meta App */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2">
            <label className="block text-xs font-semibold text-slate-300">Meta App ID</label>
            <input
              type="text"
              value={metaAppId}
              onChange={(e) => setMetaAppId(e.target.value)}
              placeholder="e.g. 112233445566"
              className="w-full bg-[#0a0d14] border border-gaming-border rounded-xl px-3.5 py-2.5 text-xs text-slate-200 placeholder-slate-600 font-mono"
            />
          </div>

          <div className="space-y-2">
            <label className="block text-xs font-semibold text-slate-300">Meta App Secret</label>
            <input
              type="password"
              value={metaAppSecret}
              onChange={(e) => setMetaAppSecret(e.target.value)}
              placeholder="••••••••••••••••"
              className="w-full bg-[#0a0d14] border border-gaming-border rounded-xl px-3.5 py-2.5 text-xs text-slate-200 placeholder-slate-600 font-mono"
            />
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="space-y-2">
            <label className="block text-xs font-semibold text-slate-300">Meta Page Access Token</label>
            <input
              type="password"
              value={metaPageToken}
              onChange={(e) => setMetaPageToken(e.target.value)}
              placeholder="EAAB... (Long-lived token)"
              className="w-full bg-[#0a0d14] border border-gaming-border rounded-xl px-3.5 py-2.5 text-xs text-slate-200 placeholder-slate-600 font-mono"
            />
          </div>

          <div className="space-y-2">
            <label className="block text-xs font-semibold text-slate-300">Facebook Page ID</label>
            <input
              type="text"
              value={metaPageId}
              onChange={(e) => setMetaPageId(e.target.value)}
              placeholder="e.g. 109283746501928"
              className="w-full bg-[#0a0d14] border border-gaming-border rounded-xl px-3.5 py-2.5 text-xs text-slate-200 placeholder-slate-600 font-mono"
            />
          </div>

          <div className="space-y-2">
            <label className="block text-xs font-semibold text-slate-300">Instagram Professional Account ID</label>
            <input
              type="text"
              value={metaIgId}
              onChange={(e) => setMetaIgId(e.target.value)}
              placeholder="e.g. 17841400..."
              className="w-full bg-[#0a0d14] border border-gaming-border rounded-xl px-3.5 py-2.5 text-xs text-slate-200 placeholder-slate-600 font-mono"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={saving}
            className="bg-purple-600 hover:bg-purple-500 text-white font-bold text-xs px-6 py-2.5 rounded-xl transition"
          >
            {saving ? 'Encrypting & Storing...' : 'Save Secrets to Secure Vault'}
          </button>

          <button
            type="button"
            onClick={handleTestFacebook}
            disabled={testingFb}
            className="border border-blue-500/40 bg-blue-500/10 hover:bg-blue-500/20 text-blue-300 font-bold text-xs px-4 py-2.5 rounded-xl transition flex items-center space-x-2"
          >
            <Share2 className="w-3.5 h-3.5" />
            <span>{testingFb ? 'Verifying Facebook API...' : 'Test Facebook Page & Permissions'}</span>
          </button>
        </div>

        {fbTestResult && (
          <div className={`p-4 rounded-xl text-xs space-y-1.5 border ${
            fbTestResult.authenticated && !fbTestResult.missing_permissions?.length
              ? 'bg-emerald-950/30 border-emerald-500/30 text-emerald-200'
              : 'bg-rose-950/30 border-rose-500/30 text-rose-200'
          }`}>
            <div className="font-bold flex items-center space-x-2">
              <span>{fbTestResult.authenticated ? '✓ Facebook Token & Page Verified' : '✗ Facebook Verification Failed'}</span>
              {fbTestResult.error_code && (
                <span className="font-mono text-[10px] px-2 py-0.5 rounded bg-rose-900/50 text-rose-300 border border-rose-700/50">
                  Code: {fbTestResult.error_code} {fbTestResult.subcode ? `(Subcode: ${fbTestResult.subcode})` : ''}
                </span>
              )}
            </div>
            <p>{fbTestResult.message}</p>
            {fbTestResult.page_name && (
              <div className="text-[11px] text-slate-300 font-mono">
                Page: {fbTestResult.page_name} (ID: {fbTestResult.page_id})
              </div>
            )}
            {fbTestResult.permissions?.length > 0 && (
              <div className="text-[11px] text-slate-400 font-mono">
                Granted Scopes: {fbTestResult.permissions.join(', ')}
              </div>
            )}
            {fbTestResult.missing_permissions?.length > 0 && (
              <div className="text-[11px] text-amber-300 font-mono font-semibold">
                Missing Required Scopes: {fbTestResult.missing_permissions.join(', ')}
              </div>
            )}
          </div>
        )}
      </form>

      {/* Daily Limits & Production Targets Form */}
      <form onSubmit={handleSaveLimits} className="bg-[#111827] border border-gaming-border rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-gaming-border">
          <div className="flex items-center space-x-2">
            <Sliders className="w-5 h-5 text-purple-400" />
            <h3 className="font-bold text-sm text-slate-200">Daily Production Targets & Platform Limits</h3>
          </div>
          <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">
            Default: 15 clips/day
          </span>
        </div>

        <p className="text-xs text-slate-400">
          Separate video generation from publishing caps. The dashboard target is a workflow goal, NOT an approved platform quota. Publishing pauses only when actual platform restrictions or configured limits are met, automatically queuing remaining videos for the next permitted window.
        </p>

        {limitsMsg && (
          <div className="bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs px-3.5 py-2.5 rounded-xl flex items-center space-x-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
            <span>{limitsMsg}</span>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3.5">
          <div className="space-y-1.5 bg-[#0a0d14] p-3 rounded-xl border border-gaming-border">
            <label className="block text-[11px] font-bold text-purple-400 uppercase tracking-wide">
              Daily Gen Target
            </label>
            <input
              type="number"
              min={1}
              max={100}
              value={limits.daily_generation_target}
              onChange={(e) => setLimits({ ...limits, daily_generation_target: parseInt(e.target.value) || 15 })}
              className="w-full bg-[#111827] border border-gaming-border rounded-lg px-2.5 py-1.5 text-xs text-slate-200 font-mono font-bold"
            />
            <p className="text-[10px] text-slate-500">Clips created locally</p>
          </div>

          <div className="space-y-1.5 bg-[#0a0d14] p-3 rounded-xl border border-gaming-border">
            <label className="block text-[11px] font-bold text-emerald-400 uppercase tracking-wide">
              Daily Pub Target
            </label>
            <input
              type="number"
              min={1}
              max={100}
              value={limits.daily_publishing_target}
              onChange={(e) => setLimits({ ...limits, daily_publishing_target: parseInt(e.target.value) || 15 })}
              className="w-full bg-[#111827] border border-gaming-border rounded-lg px-2.5 py-1.5 text-xs text-slate-200 font-mono font-bold"
            />
            <p className="text-[10px] text-slate-500">Target uploads/day</p>
          </div>

          <div className="space-y-1.5 bg-[#0a0d14] p-3 rounded-xl border border-gaming-border">
            <label className="block text-[11px] font-bold text-red-400 uppercase tracking-wide">
              YouTube Limit
            </label>
            <input
              type="number"
              min={1}
              max={50}
              value={limits.youtube_daily_limit}
              onChange={(e) => setLimits({ ...limits, youtube_daily_limit: parseInt(e.target.value) || 15 })}
              className="w-full bg-[#111827] border border-gaming-border rounded-lg px-2.5 py-1.5 text-xs text-slate-200 font-mono font-bold"
            />
            <p className="text-[10px] text-slate-500">Daily Shorts cap</p>
          </div>

          <div className="space-y-1.5 bg-[#0a0d14] p-3 rounded-xl border border-gaming-border">
            <label className="block text-[11px] font-bold text-pink-400 uppercase tracking-wide">
              Instagram Limit
            </label>
            <input
              type="number"
              min={1}
              max={50}
              value={limits.instagram_daily_limit}
              onChange={(e) => setLimits({ ...limits, instagram_daily_limit: parseInt(e.target.value) || 25 })}
              className="w-full bg-[#111827] border border-gaming-border rounded-lg px-2.5 py-1.5 text-xs text-slate-200 font-mono font-bold"
            />
            <p className="text-[10px] text-slate-500">Daily Reels cap</p>
          </div>

          <div className="space-y-1.5 bg-[#0a0d14] p-3 rounded-xl border border-gaming-border">
            <label className="block text-[11px] font-bold text-blue-400 uppercase tracking-wide">
              Facebook Limit
            </label>
            <input
              type="number"
              min={1}
              max={50}
              value={limits.facebook_daily_limit}
              onChange={(e) => setLimits({ ...limits, facebook_daily_limit: parseInt(e.target.value) || 25 })}
              className="w-full bg-[#111827] border border-gaming-border rounded-lg px-2.5 py-1.5 text-xs text-slate-200 font-mono font-bold"
            />
            <p className="text-[10px] text-slate-500">Daily Reels cap</p>
          </div>
        </div>

        <button
          type="submit"
          disabled={savingLimits}
          className="bg-purple-600 hover:bg-purple-500 text-white font-bold text-xs px-6 py-2.5 rounded-xl transition"
        >
          {savingLimits ? 'Saving Targets...' : 'Save Targets & Platform Limits'}
        </button>
      </form>
    </div>
  )
}
