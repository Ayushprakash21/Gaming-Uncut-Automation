import React, { useState, useEffect } from 'react'
import {
  Compass,
  Search,
  RefreshCw,
  Play,
  Pause,
  Plus,
  Trash2,
  CheckCircle2,
  AlertCircle,
  Clock,
  Eye,
  ExternalLink,
  ShieldAlert,
  ShieldCheck,
  Film,
  Sparkles,
  Sliders,
  CheckSquare,
  Square,
  FileCheck,
  UploadCloud,
  Layers,
  ChevronRight,
  ChevronLeft,
  XCircle,
  HelpCircle
} from 'lucide-react'
import {
  searchYouTubeDiscovery,
  importDiscoveredVideos,
  fetchDiscoveryQueue,
  authorizeDiscoveredVideo,
  processDiscoveredVideo,
  retryDiscoveredVideo,
  skipDiscoveredVideo,
  fetchDiscoverySources,
  saveDiscoverySource,
  deleteDiscoverySource,
  fetchDiscoveryChannels,
  saveDiscoveryChannel,
  deleteDiscoveryChannel,
  fetchDiscoverySchedule,
  updateDiscoverySchedule,
  triggerDiscoveryScanNow,
  pauseDiscoverySchedule,
  resumeDiscoverySchedule
} from '../services/api'
import {
  DiscoveredVideoItem,
  ManualSearchVideoResult,
  DiscoverySourceItem,
  DiscoveryChannelItem,
  DiscoveryScheduleData
} from '../types'

interface DiscoveryPageProps {
  onNavigate?: (tab: string) => void
}

const POPULAR_GAMES = [
  'Grand Theft Auto V',
  'Minecraft',
  'Valorant',
  'Call of Duty: Warzone',
  'Cyberpunk 2077',
  'Elden Ring',
  'Forza Horizon 5',
  'Apex Legends',
  'Fortnite',
  'Counter-Strike 2'
]

export const DiscoveryPage: React.FC<DiscoveryPageProps> = ({ onNavigate }) => {
  const [activeTab, setActiveTab] = useState<'search' | 'queue' | 'settings'>('search')

  // Search State
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedGames, setSelectedGames] = useState<string[]>([])
  const [customGameInput, setCustomGameInput] = useState('')
  const [channelFilter, setChannelFilter] = useState('')
  const [uploadDateFilter, setUploadDateFilter] = useState<'any' | 'today' | 'week' | 'month'>('week')
  const [durationFilter, setDurationFilter] = useState<'any' | 'short' | 'medium' | 'long'>('medium')
  const [minViews, setMinViews] = useState(0)
  const [isSearching, setIsSearching] = useState(false)
  const [searchResults, setSearchResults] = useState<ManualSearchVideoResult[]>([])
  const [searchTotal, setSearchTotal] = useState(0)
  const [nextPageToken, setNextPageToken] = useState<string | undefined>()
  const [prevPageToken, setPrevPageToken] = useState<string | undefined>()
  const [searchQuotaUsed, setSearchQuotaUsed] = useState(0)
  const [selectedVids, setSelectedVids] = useState<Record<string, ManualSearchVideoResult>>({})
  const [isImporting, setIsImporting] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)

  // Queue State
  const [queueItems, setQueueItems] = useState<DiscoveredVideoItem[]>([])
  const [queueTotal, setQueueTotal] = useState(0)
  const [queueStatusFilter, setQueueStatusFilter] = useState<string>('ALL')
  const [queueSearchQuery, setQueueSearchQuery] = useState('')
  const [isLoadingQueue, setIsLoadingQueue] = useState(false)
  const [actionLoading, setActionLoading] = useState<Record<number, string>>({})

  // Settings & Automation State
  const [schedule, setSchedule] = useState<DiscoveryScheduleData | null>(null)
  const [sources, setSources] = useState<DiscoverySourceItem[]>([])
  const [channels, setChannels] = useState<DiscoveryChannelItem[]>([])
  const [isSavingSchedule, setIsSavingSchedule] = useState(false)
  const [isTriggeringScan, setIsTriggeringScan] = useState(false)
  const [notification, setNotification] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  // Modals
  const [authorizingVideo, setAuthorizingVideo] = useState<DiscoveredVideoItem | null>(null)
  const [authType, setAuthType] = useState('creator_authorized')
  const [authorizedBy, setAuthorizedBy] = useState('')
  const [sourceOwner, setSourceOwner] = useState('')
  const [permittedScope, setPermittedScope] = useState('shorts_clips_derivative')
  const [evidenceUrl, setEvidenceUrl] = useState('')
  const [authNotes, setAuthNotes] = useState('')
  const [localFilePath, setLocalFilePath] = useState('')
  const [autoEnqueueNow, setAutoEnqueueNow] = useState(true)
  const [isSubmittingAuth, setIsSubmittingAuth] = useState(false)

  // Add Source Modal
  const [showAddSourceModal, setShowAddSourceModal] = useState(false)
  const [newSourceGame, setNewSourceGame] = useState('')
  const [newSourceName, setNewSourceName] = useState('')
  const [newSourceKeywords, setNewSourceKeywords] = useState('')

  // Add Channel Modal
  const [showAddChannelModal, setShowAddChannelModal] = useState(false)
  const [newChannelId, setNewChannelId] = useState('')
  const [newChannelTitle, setNewChannelTitle] = useState('')
  const [newChannelAutoAuth, setNewChannelAutoAuth] = useState(false)

  const showNotify = (message: string, type: 'success' | 'error' = 'success') => {
    setNotification({ type, message })
    setTimeout(() => setNotification(null), 6000)
  }

  // Load Initial Discovery Data
  const loadQueue = async () => {
    setIsLoadingQueue(true)
    try {
      const res = await fetchDiscoveryQueue(queueStatusFilter, queueSearchQuery)
      setQueueItems(res.items)
      setQueueTotal(res.total)
    } catch (e: any) {
      console.error('Queue load error:', e)
    } finally {
      setIsLoadingQueue(false)
    }
  }

  const loadSettings = async () => {
    try {
      const [schedRes, sourcesRes, chansRes] = await Promise.all([
        fetchDiscoverySchedule(),
        fetchDiscoverySources(),
        fetchDiscoveryChannels()
      ])
      setSchedule(schedRes)
      setSources(sourcesRes)
      setChannels(chansRes)
    } catch (e: any) {
      console.error('Settings load error:', e)
    }
  }

  useEffect(() => {
    if (activeTab === 'queue') {
      loadQueue()
      const t = setInterval(loadQueue, 5000)
      return () => clearInterval(t)
    } else if (activeTab === 'settings') {
      loadSettings()
    }
  }, [activeTab, queueStatusFilter, queueSearchQuery])

  useEffect(() => {
    loadSettings()
  }, [])

  // Execute Manual Search
  const handleSearch = async (token?: string) => {
    setIsSearching(true)
    setSearchError(null)
    try {
      const games = [...selectedGames]
      if (customGameInput.trim() && !games.includes(customGameInput.trim())) {
        games.push(customGameInput.trim())
      }

      const res = await searchYouTubeDiscovery({
        query: searchQuery,
        game_titles: games.length > 0 ? games : undefined,
        channel_id: channelFilter.trim() || undefined,
        published_after: uploadDateFilter !== 'any' ? uploadDateFilter : undefined,
        duration_filter: durationFilter !== 'any' ? durationFilter : undefined,
        min_views: minViews,
        page_token: token,
        max_results: 15
      })

      setSearchResults(res.items)
      setSearchTotal(res.total_results)
      setNextPageToken(res.next_page_token)
      setPrevPageToken(res.prev_page_token)
      setSearchQuotaUsed((q) => q + res.quota_used)
    } catch (e: any) {
      setSearchError(e.message || 'YouTube search failed.')
    } finally {
      setIsSearching(false)
    }
  }

  const toggleGameSelection = (game: string) => {
    if (selectedGames.includes(game)) {
      setSelectedGames(selectedGames.filter((g) => g !== game))
    } else {
      setSelectedGames([...selectedGames, game])
    }
  }

  const toggleSelectVideo = (vid: ManualSearchVideoResult) => {
    setSelectedVids((prev) => {
      const copy = { ...prev }
      if (copy[vid.youtube_video_id]) {
        delete copy[vid.youtube_video_id]
      } else {
        copy[vid.youtube_video_id] = vid
      }
      return copy
    })
  }

  const handleSelectAllOnPage = () => {
    if (Object.keys(selectedVids).length === searchResults.length) {
      setSelectedVids({})
    } else {
      const all: Record<string, ManualSearchVideoResult> = {}
      searchResults.forEach((r) => {
        all[r.youtube_video_id] = r
      })
      setSelectedVids(all)
    }
  }

  const handleImportSelected = async () => {
    const vids = Object.values(selectedVids)
    if (vids.length === 0) return

    setIsImporting(true)
    try {
      const res = await importDiscoveredVideos(vids)
      showNotify(`Successfully added ${res.imported_count} video(s) to discovery queue.`, 'success')
      setSelectedVids({})
      // Refresh current search results to reflect in_queue
      handleSearch()
    } catch (e: any) {
      showNotify(e.message || 'Import failed.', 'error')
    } finally {
      setIsImporting(false)
    }
  }

  // Queue Item Actions
  const openAuthorizeModal = (video: DiscoveredVideoItem) => {
    setAuthorizingVideo(video)
    setAuthorizedBy('')
    setSourceOwner(video.channel_title || '')
    setPermittedScope('shorts_clips_derivative')
    setEvidenceUrl('')
    setAuthNotes('')
    setLocalFilePath('')
    setAutoEnqueueNow(true)
  }

  const handleConfirmAuthorization = async () => {
    if (!authorizingVideo) return
    setIsSubmittingAuth(true)
    try {
      await authorizeDiscoveredVideo(authorizingVideo.id, {
        auth_type: authType,
        authorized_by: authorizedBy || 'Creator Authorization',
        source_owner: sourceOwner,
        permitted_scope: permittedScope,
        evidence_url: evidenceUrl,
        notes: authNotes,
        local_file_path: localFilePath.trim() || undefined,
        enqueue_now: autoEnqueueNow
      })
      showNotify(
        `Source authorization saved for #${authorizingVideo.id}. ${autoEnqueueNow ? 'Enqueued for AI production!' : ''}`,
        'success'
      )
      setAuthorizingVideo(null)
      loadQueue()
    } catch (e: any) {
      showNotify(e.message || 'Authorization failed.', 'error')
    } finally {
      setIsSubmittingAuth(false)
    }
  }

  const handleProcessVideo = async (videoId: number) => {
    setActionLoading((prev) => ({ ...prev, [videoId]: 'processing' }))
    try {
      const res = await processDiscoveredVideo(videoId)
      showNotify(`Discovered video dispatched to AI pipeline as Job #${res.job_id}.`, 'success')
      loadQueue()
    } catch (e: any) {
      showNotify(e.message || 'Failed to dispatch video to AI pipeline.', 'error')
    } finally {
      setActionLoading((prev) => {
        const copy = { ...prev }
        delete copy[videoId]
        return copy
      })
    }
  }

  const handleRetryVideo = async (videoId: number) => {
    setActionLoading((prev) => ({ ...prev, [videoId]: 'retrying' }))
    try {
      await retryDiscoveredVideo(videoId)
      showNotify(`Video #${videoId} reset and enqueued.`, 'success')
      loadQueue()
    } catch (e: any) {
      showNotify(e.message || 'Retry failed.', 'error')
    } finally {
      setActionLoading((prev) => {
        const copy = { ...prev }
        delete copy[videoId]
        return copy
      })
    }
  }

  const handleSkipVideo = async (videoId: number) => {
    setActionLoading((prev) => ({ ...prev, [videoId]: 'skipping' }))
    try {
      await skipDiscoveredVideo(videoId)
      showNotify(`Video #${videoId} skipped.`, 'success')
      loadQueue()
    } catch (e: any) {
      showNotify(e.message || 'Skip failed.', 'error')
    } finally {
      setActionLoading((prev) => {
        const copy = { ...prev }
        delete copy[videoId]
        return copy
      })
    }
  }

  // Automation / Schedule Controls
  const handleToggleSchedule = async () => {
    if (!schedule) return
    setIsSavingSchedule(true)
    try {
      if (schedule.is_active) {
        const res = await pauseDiscoverySchedule()
        setSchedule((s) => (s ? { ...s, is_active: false, last_scan_status: 'paused' } : null))
        showNotify('Scheduled discovery paused.', 'success')
      } else {
        const res = await resumeDiscoverySchedule()
        setSchedule((s) => (s ? { ...s, is_active: true, last_scan_status: 'idle', next_scan_at: res.next_scan_at } : null))
        showNotify('Scheduled discovery resumed.', 'success')
      }
    } catch (e: any) {
      showNotify(e.message || 'Failed to toggle schedule.', 'error')
    } finally {
      setIsSavingSchedule(false)
    }
  }

  const handleTriggerScanNow = async () => {
    setIsTriggeringScan(true)
    try {
      await triggerDiscoveryScanNow()
      showNotify('Discovery scan initiated in background.', 'success')
      setTimeout(loadSettings, 3000)
    } catch (e: any) {
      showNotify(e.message || 'Scan trigger failed.', 'error')
    } finally {
      setIsTriggeringScan(false)
    }
  }

  const handleUpdateScheduleRules = async (updates: Partial<DiscoveryScheduleData>) => {
    setIsSavingSchedule(true)
    try {
      const res = await updateDiscoverySchedule(updates)
      setSchedule((s) => (s ? { ...s, ...res.schedule } : null))
      showNotify('Discovery settings updated.', 'success')
    } catch (e: any) {
      showNotify(e.message || 'Failed to update settings.', 'error')
    } finally {
      setIsSavingSchedule(false)
    }
  }

  const handleAddSource = async () => {
    if (!newSourceGame.trim()) return
    try {
      const kws = newSourceKeywords.split(',').map((k) => k.trim()).filter(Boolean)
      await saveDiscoverySource({
        game_title: newSourceGame.trim(),
        name: newSourceName.trim() || `${newSourceGame} Highlights`,
        keywords: kws.length > 0 ? kws : ['gameplay', 'highlights', 'clutch'],
        is_active: true,
        priority: 1
      })
      setShowAddSourceModal(false)
      setNewSourceGame('')
      setNewSourceName('')
      setNewSourceKeywords('')
      showNotify('Monitored game source added.', 'success')
      loadSettings()
    } catch (e: any) {
      showNotify(e.message || 'Failed to add source.', 'error')
    }
  }

  const handleDeleteSource = async (id: number) => {
    try {
      await deleteDiscoverySource(id)
      showNotify('Source removed.', 'success')
      loadSettings()
    } catch (e: any) {
      showNotify(e.message || 'Failed to remove source.', 'error')
    }
  }

  const handleAddChannel = async () => {
    if (!newChannelId.trim() || !newChannelTitle.trim()) return
    try {
      await saveDiscoveryChannel({
        channel_id: newChannelId.trim(),
        channel_title: newChannelTitle.trim(),
        auto_authorize_sources: newChannelAutoAuth,
        is_approved: true
      })
      setShowAddChannelModal(false)
      setNewChannelId('')
      setNewChannelTitle('')
      setNewChannelAutoAuth(false)
      showNotify('Approved channel added.', 'success')
      loadSettings()
    } catch (e: any) {
      showNotify(e.message || 'Failed to add channel.', 'error')
    }
  }

  const handleDeleteChannel = async (id: number) => {
    try {
      await deleteDiscoveryChannel(id)
      showNotify('Channel removed.', 'success')
      loadSettings()
    } catch (e: any) {
      showNotify(e.message || 'Failed to remove channel.', 'error')
    }
  }

  // Format Helper
  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'authorization_required':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/30">
            <ShieldAlert className="w-3 h-3" /> Auth Required
          </span>
        )
      case 'authorized':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
            <ShieldCheck className="w-3 h-3" /> Authorized
          </span>
        )
      case 'queued':
      case 'analyzing':
      case 'highlights_detected':
      case 'rendering':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-purple-500/10 text-purple-400 border border-purple-500/30">
            <RefreshCw className="w-3 h-3 animate-spin" /> {status.replace('_', ' ').toUpperCase()}
          </span>
        )
      case 'review_required':
      case 'ready_to_publish':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">
            <Sparkles className="w-3 h-3" /> READY
          </span>
        )
      case 'published':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-green-500/10 text-green-400 border border-green-500/30">
            <CheckCircle2 className="w-3 h-3" /> PUBLISHED
          </span>
        )
      case 'failed':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/30">
            <XCircle className="w-3 h-3" /> FAILED
          </span>
        )
      case 'skipped':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-700/50 text-slate-400 border border-slate-600">
            SKIPPED
          </span>
        )
      default:
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-800 text-slate-300">
            {status.toUpperCase()}
          </span>
        )
    }
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12 animate-in fade-in duration-300">
      {/* Toast Notification */}
      {notification && (
        <div
          className={`fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl shadow-2xl flex items-center gap-2.5 text-xs font-medium border backdrop-blur-md transition ${
            notification.type === 'success'
              ? 'bg-emerald-950/90 text-emerald-200 border-emerald-500/40'
              : 'bg-rose-950/90 text-rose-200 border-rose-500/40'
          }`}
        >
          {notification.type === 'success' ? (
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          ) : (
            <AlertCircle className="w-4 h-4 text-rose-400" />
          )}
          <span>{notification.message}</span>
        </div>
      )}

      {/* Header & Tabs */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-gaming-border pb-5">
        <div>
          <div className="flex items-center space-x-2.5">
            <div className="w-9 h-9 rounded-xl bg-purple-600/20 border border-purple-500/30 flex items-center justify-center text-purple-400 shadow-sm">
              <Compass className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
                Gameplay Discovery Engine
                <span className="bg-purple-500/20 text-purple-300 text-[10px] font-semibold px-2 py-0.5 rounded-full border border-purple-500/30">
                  YouTube Data API v3
                </span>
              </h1>
              <p className="text-xs text-slate-400 mt-0.5">
                Autonomous and manual gameplay discovery with verified source acquisition and AI clipping pipeline integration.
              </p>
            </div>
          </div>
        </div>

        {/* Global Controls & Status */}
        <div className="flex items-center gap-2.5">
          {schedule && (
            <div className="flex items-center gap-2 bg-[#121826] border border-gaming-border px-3 py-1.5 rounded-xl text-xs">
              <div className="flex items-center gap-1.5 text-slate-300">
                <span
                  className={`w-2 h-2 rounded-full ${
                    schedule.is_active ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'
                  }`}
                />
                <span className="font-semibold text-[11px]">
                  {schedule.is_active ? 'AUTO-SCAN ACTIVE' : 'PAUSED'}
                </span>
              </div>
              <span className="text-slate-600">|</span>
              <span className="text-[11px] text-slate-400">
                Next: {schedule.next_scan_at ? new Date(schedule.next_scan_at).toLocaleTimeString() : 'N/A'}
              </span>
            </div>
          )}

          <button
            onClick={handleTriggerScanNow}
            disabled={isTriggeringScan}
            className="bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-xs font-semibold px-3 py-1.5 rounded-xl flex items-center gap-1.5 transition shadow"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isTriggeringScan ? 'animate-spin' : ''}`} />
            <span>Scan Now</span>
          </button>
        </div>
      </div>

      {/* Navigation Pills */}
      <div className="flex items-center space-x-2 border-b border-gaming-border/60 pb-3">
        <button
          onClick={() => setActiveTab('search')}
          className={`flex items-center space-x-2 px-4 py-2 rounded-xl text-xs font-semibold transition ${
            activeTab === 'search'
              ? 'bg-purple-600/20 text-purple-300 border border-purple-500/30'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
          }`}
        >
          <Search className="w-3.5 h-3.5" />
          <span>Manual YouTube Search</span>
        </button>

        <button
          onClick={() => setActiveTab('queue')}
          className={`flex items-center space-x-2 px-4 py-2 rounded-xl text-xs font-semibold transition ${
            activeTab === 'queue'
              ? 'bg-purple-600/20 text-purple-300 border border-purple-500/30'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
          }`}
        >
          <Layers className="w-3.5 h-3.5" />
          <span>Discovery Queue</span>
          {queueTotal > 0 && (
            <span className="bg-purple-500/20 text-purple-300 text-[10px] px-1.5 py-0.2 rounded-full font-bold">
              {queueTotal}
            </span>
          )}
        </button>

        <button
          onClick={() => setActiveTab('settings')}
          className={`flex items-center space-x-2 px-4 py-2 rounded-xl text-xs font-semibold transition ${
            activeTab === 'settings'
              ? 'bg-purple-600/20 text-purple-300 border border-purple-500/30'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
          }`}
        >
          <Sliders className="w-3.5 h-3.5" />
          <span>Automated Monitoring & Rules</span>
        </button>
      </div>

      {/* =========================================================================
          TAB 1: MANUAL SEARCH
      ========================================================================= */}
      {activeTab === 'search' && (
        <div className="space-y-6 animate-in fade-in">
          {/* Search Box & Filters */}
          <div className="bg-[#0d111c] border border-gaming-border rounded-2xl p-5 space-y-4">
            {/* Multi-game selector */}
            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-300 flex items-center justify-between">
                <span>Select Games to Target (Multi-Game Discovery)</span>
                <span className="text-[11px] text-slate-500 font-normal">
                  {selectedGames.length} selected
                </span>
              </label>
              <div className="flex flex-wrap gap-1.5">
                {POPULAR_GAMES.map((game) => {
                  const isSelected = selectedGames.includes(game)
                  return (
                    <button
                      key={game}
                      onClick={() => toggleGameSelection(game)}
                      className={`text-xs px-2.5 py-1 rounded-lg font-medium transition ${
                        isSelected
                          ? 'bg-purple-600 text-white shadow'
                          : 'bg-slate-800/70 text-slate-300 hover:bg-slate-700'
                      }`}
                    >
                      {game}
                    </button>
                  )
                })}
              </div>

              {/* Custom game input */}
              <div className="pt-1">
                <input
                  type="text"
                  placeholder="Or enter custom game title..."
                  value={customGameInput}
                  onChange={(e) => setCustomGameInput(e.target.value)}
                  className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-purple-500"
                />
              </div>
            </div>

            {/* Keyword search & Channel filter */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-2">
              <div className="md:col-span-2">
                <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                  Search Keywords / Gameplay Events
                </label>
                <div className="relative">
                  <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
                  <input
                    type="text"
                    placeholder="e.g. clutch, 1v5 ace, insane graphics, boss fight, ray tracing..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                    className="w-full bg-[#121826] border border-gaming-border rounded-xl pl-9 pr-3 py-2 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-purple-500"
                  />
                </div>
              </div>

              <div>
                <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                  Filter by Specific YouTube Channel ID
                </label>
                <input
                  type="text"
                  placeholder="Optional channel ID (UC...)"
                  value={channelFilter}
                  onChange={(e) => setChannelFilter(e.target.value)}
                  className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-purple-500"
                />
              </div>
            </div>

            {/* Date, Duration & Action */}
            <div className="flex flex-wrap items-center justify-between gap-4 pt-2 border-t border-slate-800/60">
              <div className="flex flex-wrap items-center gap-4 text-xs">
                {/* Upload Date */}
                <div className="flex items-center gap-1.5">
                  <Clock className="w-3.5 h-3.5 text-slate-400" />
                  <span className="text-slate-400 text-[11px]">Upload Date:</span>
                  <div className="flex bg-[#121826] p-0.5 rounded-lg border border-gaming-border">
                    {(['any', 'today', 'week', 'month'] as const).map((d) => (
                      <button
                        key={d}
                        onClick={() => setUploadDateFilter(d)}
                        className={`px-2 py-0.5 rounded text-[10px] font-semibold transition ${
                          uploadDateFilter === d
                            ? 'bg-purple-600 text-white'
                            : 'text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        {d.toUpperCase()}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Duration */}
                <div className="flex items-center gap-1.5">
                  <Film className="w-3.5 h-3.5 text-slate-400" />
                  <span className="text-slate-400 text-[11px]">Duration:</span>
                  <div className="flex bg-[#121826] p-0.5 rounded-lg border border-gaming-border">
                    {(['any', 'short', 'medium', 'long'] as const).map((dur) => (
                      <button
                        key={dur}
                        onClick={() => setDurationFilter(dur)}
                        className={`px-2 py-0.5 rounded text-[10px] font-semibold transition ${
                          durationFilter === dur
                            ? 'bg-purple-600 text-white'
                            : 'text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        {dur.toUpperCase()}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Submit Search */}
              <button
                onClick={() => handleSearch()}
                disabled={isSearching}
                className="bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-xs font-semibold px-5 py-2 rounded-xl flex items-center gap-2 transition shadow-lg shadow-purple-600/20"
              >
                <Search className={`w-3.5 h-3.5 ${isSearching ? 'animate-spin' : ''}`} />
                <span>{isSearching ? 'Searching YouTube...' : 'Search Gameplay'}</span>
              </button>
            </div>
          </div>

          {/* Error Message */}
          {searchError && (
            <div className="bg-rose-950/40 border border-rose-500/40 rounded-xl p-4 text-xs text-rose-300 flex items-start gap-2.5">
              <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold">YouTube Search Failed</p>
                <p className="mt-0.5 opacity-90">{searchError}</p>
              </div>
            </div>
          )}

          {/* Multi-Select Action Bar */}
          {searchResults.length > 0 && (
            <div className="flex items-center justify-between bg-[#121826] border border-gaming-border px-4 py-2.5 rounded-xl text-xs">
              <div className="flex items-center gap-3">
                <button
                  onClick={handleSelectAllOnPage}
                  className="flex items-center gap-1.5 text-slate-300 hover:text-white font-medium"
                >
                  {Object.keys(selectedVids).length === searchResults.length ? (
                    <CheckSquare className="w-4 h-4 text-purple-400" />
                  ) : (
                    <Square className="w-4 h-4 text-slate-500" />
                  )}
                  <span>Select All on Page</span>
                </button>
                <span className="text-slate-500">|</span>
                <span className="text-slate-400 font-mono">
                  {Object.keys(selectedVids).length} video(s) selected
                </span>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={handleImportSelected}
                  disabled={Object.keys(selectedVids).length === 0 || isImporting}
                  className="bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-xs font-semibold px-4 py-1.5 rounded-xl flex items-center gap-1.5 transition shadow"
                >
                  <Plus className={`w-3.5 h-3.5 ${isImporting ? 'animate-spin' : ''}`} />
                  <span>
                    {isImporting
                      ? 'Importing...'
                      : `Add Selected (${Object.keys(selectedVids).length}) to Queue`}
                  </span>
                </button>
              </div>
            </div>
          )}

          {/* Results Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {searchResults.map((item) => {
              const isSelected = !!selectedVids[item.youtube_video_id]
              return (
                <div
                  key={item.youtube_video_id}
                  onClick={() => toggleSelectVideo(item)}
                  className={`bg-[#0d111c] border rounded-2xl overflow-hidden cursor-pointer transition flex flex-col justify-between ${
                    isSelected
                      ? 'border-purple-500 shadow-md shadow-purple-900/20 ring-1 ring-purple-500'
                      : 'border-gaming-border hover:border-slate-700'
                  }`}
                >
                  <div>
                    {/* Thumbnail */}
                    <div className="relative aspect-video bg-slate-900 overflow-hidden">
                      <img
                        src={item.thumbnail_url}
                        alt={item.title}
                        className="w-full h-full object-cover"
                        loading="lazy"
                      />
                      <div className="absolute top-2 left-2">
                        {isSelected ? (
                          <div className="w-5 h-5 rounded bg-purple-600 flex items-center justify-center text-white shadow">
                            <CheckSquare className="w-3.5 h-3.5" />
                          </div>
                        ) : (
                          <div className="w-5 h-5 rounded bg-slate-900/70 border border-slate-600" />
                        )}
                      </div>
                      <div className="absolute bottom-2 right-2 bg-black/80 px-1.5 py-0.5 rounded text-[10px] font-mono font-bold text-white">
                        {item.duration_formatted}
                      </div>
                      {item.in_queue && (
                        <div className="absolute top-2 right-2 bg-emerald-950/80 border border-emerald-500/40 text-emerald-300 px-2 py-0.5 rounded-full text-[10px] font-semibold backdrop-blur-sm">
                          IN QUEUE
                        </div>
                      )}
                    </div>

                    {/* Metadata */}
                    <div className="p-3.5 space-y-2">
                      <h3 className="font-semibold text-xs text-white line-clamp-2 leading-snug">
                        {item.title}
                      </h3>
                      <div className="flex items-center justify-between text-[11px] text-slate-400">
                        <span className="truncate max-w-[160px] font-medium text-slate-300">
                          {item.channel_title}
                        </span>
                        <span className="flex items-center gap-1 font-mono">
                          <Eye className="w-3 h-3 text-slate-500" />
                          {item.view_count.toLocaleString()}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Card Footer */}
                  <div className="p-3 pt-0 flex items-center justify-between border-t border-slate-800/50 mt-2 text-[10px] text-slate-500">
                    <span className="text-purple-400 font-semibold">{item.game_title}</span>
                    <a
                      href={item.youtube_url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="text-slate-400 hover:text-white flex items-center gap-1"
                    >
                      <span>YouTube</span>
                      <ExternalLink className="w-2.5 h-2.5" />
                    </a>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Pagination */}
          {(nextPageToken || prevPageToken) && (
            <div className="flex items-center justify-center gap-3 pt-4">
              <button
                onClick={() => handleSearch(prevPageToken)}
                disabled={!prevPageToken || isSearching}
                className="bg-[#121826] border border-gaming-border px-3.5 py-1.5 rounded-xl text-xs font-semibold text-slate-300 hover:text-white disabled:opacity-40 flex items-center gap-1"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
                <span>Previous</span>
              </button>
              <button
                onClick={() => handleSearch(nextPageToken)}
                disabled={!nextPageToken || isSearching}
                className="bg-[#121826] border border-gaming-border px-3.5 py-1.5 rounded-xl text-xs font-semibold text-slate-300 hover:text-white disabled:opacity-40 flex items-center gap-1"
              >
                <span>Next Page</span>
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
        </div>
      )}

      {/* =========================================================================
          TAB 2: DISCOVERY QUEUE
      ========================================================================= */}
      {activeTab === 'queue' && (
        <div className="space-y-4 animate-in fade-in">
          {/* Status Filters & Search */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 bg-[#0d111c] border border-gaming-border p-4 rounded-2xl">
            {/* Status Pills */}
            <div className="flex flex-wrap items-center gap-1.5">
              {[
                'ALL',
                'authorization_required',
                'authorized',
                'queued',
                'analyzing',
                'ready_to_publish',
                'published',
                'failed'
              ].map((st) => (
                <button
                  key={st}
                  onClick={() => setQueueStatusFilter(st)}
                  className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold transition ${
                    queueStatusFilter === st
                      ? 'bg-purple-600 text-white shadow'
                      : 'bg-slate-800/70 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {st.replace('_', ' ').toUpperCase()}
                </button>
              ))}
            </div>

            {/* Keyword Filter */}
            <div className="relative w-full sm:w-64">
              <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-slate-500" />
              <input
                type="text"
                placeholder="Search queue..."
                value={queueSearchQuery}
                onChange={(e) => setQueueSearchQuery(e.target.value)}
                className="w-full bg-[#121826] border border-gaming-border rounded-xl pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-purple-500"
              />
            </div>
          </div>

          {/* Queue Items Table */}
          <div className="bg-[#0d111c] border border-gaming-border rounded-2xl overflow-hidden shadow">
            {isLoadingQueue ? (
              <div className="p-12 text-center text-slate-400 text-xs">
                <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2 text-purple-400" />
                Loading discovery queue...
              </div>
            ) : queueItems.length === 0 ? (
              <div className="p-12 text-center text-slate-400 text-xs">
                No discovered videos found in this queue state.
              </div>
            ) : (
              <div className="divide-y divide-slate-800/70">
                {queueItems.map((item) => (
                  <div
                    key={item.id}
                    className="p-4 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-slate-800/30 transition"
                  >
                    {/* Video info */}
                    <div className="flex items-start gap-3.5 min-w-0">
                      <div className="relative w-28 h-16 rounded-lg overflow-hidden bg-slate-900 flex-shrink-0">
                        {item.thumbnail_url ? (
                          <img
                            src={item.thumbnail_url}
                            alt=""
                            className="w-full h-full object-cover"
                          />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center text-slate-600">
                            <Film className="w-5 h-5" />
                          </div>
                        )}
                        <span className="absolute bottom-1 right-1 bg-black/80 px-1 py-0.2 rounded text-[9px] font-mono text-white font-bold">
                          {Math.floor(item.duration_sec / 60)}:
                          {Math.floor(item.duration_sec % 60)
                            .toString()
                            .padStart(2, '0')}
                        </span>
                      </div>

                      <div className="space-y-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <h4 className="font-semibold text-xs text-white truncate max-w-lg">
                            {item.title}
                          </h4>
                          {getStatusBadge(item.status)}
                        </div>

                        <div className="flex items-center gap-3 text-[11px] text-slate-400">
                          <span className="text-purple-400 font-semibold">{item.game_title}</span>
                          <span>&bull;</span>
                          <span className="truncate max-w-[150px]">{item.channel_title}</span>
                          <span>&bull;</span>
                          <span className="font-mono text-slate-500">
                            {item.discovery_mode === 'automatic' ? 'Auto-Scan' : 'Manual'}
                          </span>
                          {item.clips_count > 0 && (
                            <>
                              <span>&bull;</span>
                              <span className="text-cyan-400 font-semibold">
                                {item.clips_count} Clip(s) Produced
                              </span>
                            </>
                          )}
                        </div>

                        {item.error_message && (
                          <div className="text-[11px] text-rose-400 bg-rose-950/30 px-2 py-0.5 rounded border border-rose-500/20">
                            Error: {item.error_message}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2 flex-shrink-0">
                      {item.status === 'authorization_required' && (
                        <button
                          onClick={() => openAuthorizeModal(item)}
                          className="bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold px-3 py-1.5 rounded-xl flex items-center gap-1.5 transition shadow"
                        >
                          <ShieldCheck className="w-3.5 h-3.5" />
                          <span>Authorize Source</span>
                        </button>
                      )}

                      {item.status === 'authorized' && (
                        <button
                          onClick={() => handleProcessVideo(item.id)}
                          disabled={actionLoading[item.id] === 'processing'}
                          className="bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-xs font-semibold px-3 py-1.5 rounded-xl flex items-center gap-1.5 transition shadow"
                        >
                          <Sparkles
                            className={`w-3.5 h-3.5 ${
                              actionLoading[item.id] === 'processing' ? 'animate-spin' : ''
                            }`}
                          />
                          <span>Process Reel</span>
                        </button>
                      )}

                      {item.status === 'failed' && (
                        <button
                          onClick={() => handleRetryVideo(item.id)}
                          disabled={actionLoading[item.id] === 'retrying'}
                          className="bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-xs font-semibold px-3 py-1.5 rounded-xl flex items-center gap-1.5 transition shadow"
                        >
                          <RefreshCw
                            className={`w-3.5 h-3.5 ${
                              actionLoading[item.id] === 'retrying' ? 'animate-spin' : ''
                            }`}
                          />
                          <span>Retry</span>
                        </button>
                      )}

                      {['authorization_required', 'failed'].includes(item.status) && (
                        <button
                          onClick={() => handleSkipVideo(item.id)}
                          disabled={actionLoading[item.id] === 'skipping'}
                          className="bg-slate-800 hover:bg-slate-700 text-slate-400 text-xs px-2.5 py-1.5 rounded-xl transition"
                        >
                          Skip
                        </button>
                      )}

                      <a
                        href={item.youtube_url}
                        target="_blank"
                        rel="noreferrer"
                        className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800"
                        title="Open on YouTube"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* =========================================================================
          TAB 3: AUTOMATED MONITORING & RULES
      ========================================================================= */}
      {activeTab === 'settings' && (
        <div className="space-y-6 animate-in fade-in">
          {/* Active Schedule & Control Card */}
          <div className="bg-[#0d111c] border border-gaming-border rounded-2xl p-5 space-y-4">
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-slate-800/60 pb-4">
              <div>
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                  <span>Persistent Scheduled Discovery</span>
                  {schedule?.is_active ? (
                    <span className="bg-emerald-500/20 text-emerald-400 text-[10px] font-semibold px-2 py-0.5 rounded-full border border-emerald-500/30">
                      RUNNING
                    </span>
                  ) : (
                    <span className="bg-slate-700 text-slate-300 text-[10px] font-semibold px-2 py-0.5 rounded-full">
                      PAUSED
                    </span>
                  )}
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Automated background scanner searches YouTube for new gameplay footage on a persistent schedule.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={handleToggleSchedule}
                  disabled={isSavingSchedule}
                  className={`text-xs font-semibold px-4 py-1.5 rounded-xl flex items-center gap-1.5 transition shadow ${
                    schedule?.is_active
                      ? 'bg-slate-800 hover:bg-slate-700 text-slate-300'
                      : 'bg-emerald-600 hover:bg-emerald-500 text-white'
                  }`}
                >
                  {schedule?.is_active ? (
                    <>
                      <Pause className="w-3.5 h-3.5" />
                      <span>Pause Schedule</span>
                    </>
                  ) : (
                    <>
                      <Play className="w-3.5 h-3.5" />
                      <span>Resume Schedule</span>
                    </>
                  )}
                </button>
              </div>
            </div>

            {/* Schedule KPIs */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="bg-[#121826] p-3 rounded-xl border border-gaming-border">
                <span className="text-[11px] text-slate-400 block">Scan Frequency</span>
                <span className="text-sm font-bold text-purple-400 font-mono">
                  Every {schedule?.interval_minutes ? Math.round(schedule.interval_minutes / 60) : 3} Hours
                </span>
              </div>
              <div className="bg-[#121826] p-3 rounded-xl border border-gaming-border">
                <span className="text-[11px] text-slate-400 block">Last Scan Status</span>
                <span className="text-sm font-bold text-emerald-400 uppercase font-mono">
                  {schedule?.last_scan_status || 'IDLE'}
                </span>
              </div>
              <div className="bg-[#121826] p-3 rounded-xl border border-gaming-border">
                <span className="text-[11px] text-slate-400 block">Last Scan Time</span>
                <span className="text-sm font-bold text-slate-200 font-mono">
                  {schedule?.last_scan_at ? new Date(schedule.last_scan_at).toLocaleTimeString() : 'Never'}
                </span>
              </div>
              <div className="bg-[#121826] p-3 rounded-xl border border-gaming-border">
                <span className="text-[11px] text-slate-400 block">Next Scan Time</span>
                <span className="text-sm font-bold text-cyan-400 font-mono">
                  {schedule?.next_scan_at ? new Date(schedule.next_scan_at).toLocaleTimeString() : 'N/A'}
                </span>
              </div>
            </div>
          </div>

          {/* Monitored Games Configuration */}
          <div className="bg-[#0d111c] border border-gaming-border rounded-2xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-bold text-white">Monitored Gaming Sources</h3>
                <p className="text-xs text-slate-400">
                  Games and keyword targets scanned automatically by the background engine.
                </p>
              </div>
              <button
                onClick={() => setShowAddSourceModal(true)}
                className="bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold px-3 py-1.5 rounded-xl flex items-center gap-1.5 transition"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Add Monitored Game</span>
              </button>
            </div>

            <div className="border border-gaming-border rounded-xl overflow-hidden divide-y divide-slate-800">
              {sources.length === 0 ? (
                <div className="p-6 text-center text-xs text-slate-500">
                  No monitored sources configured yet. Click "Add Monitored Game" above.
                </div>
              ) : (
                sources.map((src) => (
                  <div
                    key={src.id}
                    className="p-3.5 flex items-center justify-between text-xs hover:bg-slate-800/40"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-white">{src.game_title}</span>
                        <span className="text-slate-500">({src.name})</span>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {src.keywords.map((kw, idx) => (
                          <span
                            key={idx}
                            className="bg-slate-800 text-slate-400 text-[10px] px-1.5 py-0.5 rounded"
                          >
                            #{kw}
                          </span>
                        ))}
                      </div>
                    </div>

                    <div className="flex items-center gap-3">
                      <span className="text-[11px] text-slate-400 font-mono">
                        Max {src.max_results_per_scan} / scan
                      </span>
                      <button
                        onClick={() => handleDeleteSource(src.id)}
                        className="text-slate-500 hover:text-rose-400 p-1"
                        title="Delete source"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Approved YouTube Channels Allowlist */}
          <div className="bg-[#0d111c] border border-gaming-border rounded-2xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-bold text-white">Approved YouTube Channels (Allowlist)</h3>
                <p className="text-xs text-slate-400">
                  Partners and approved creators whose footage is auto-authorized upon discovery.
                </p>
              </div>
              <button
                onClick={() => setShowAddChannelModal(true)}
                className="bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold px-3 py-1.5 rounded-xl flex items-center gap-1.5 transition"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Add Approved Channel</span>
              </button>
            </div>

            <div className="border border-gaming-border rounded-xl overflow-hidden divide-y divide-slate-800">
              {channels.length === 0 ? (
                <div className="p-6 text-center text-xs text-slate-500">
                  No approved channels added yet. Videos discovered from third-party channels will require explicit authorization.
                </div>
              ) : (
                channels.map((chan) => (
                  <div
                    key={chan.id}
                    className="p-3.5 flex items-center justify-between text-xs hover:bg-slate-800/40"
                  >
                    <div>
                      <span className="font-semibold text-white">{chan.channel_title}</span>
                      <span className="text-[11px] text-slate-500 font-mono ml-2">
                        ID: {chan.channel_id}
                      </span>
                      {chan.auto_authorize_sources && (
                        <span className="bg-emerald-500/20 text-emerald-400 text-[10px] font-semibold px-1.5 py-0.5 rounded ml-2">
                          Auto-Authorize Sources
                        </span>
                      )}
                    </div>
                    <button
                      onClick={() => handleDeleteChannel(chan.id)}
                      className="text-slate-500 hover:text-rose-400 p-1"
                      title="Remove channel"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Safety & Quota Compliance Rules */}
          <div className="bg-[#0d111c] border border-gaming-border rounded-2xl p-5 space-y-4">
            <h3 className="text-sm font-bold text-white">Automation Safety & Publishing Controls</h3>

            {/* Caution Banner for Auto-Publishing */}
            <div className="bg-amber-950/30 border border-amber-500/40 rounded-xl p-3.5 text-xs text-amber-200 flex items-start gap-2.5">
              <ShieldAlert className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold">Compliance & Authorization Safety Rule</p>
                <p className="mt-0.5 opacity-90">
                  Automatic publishing must remain disabled by default. Discovered footage without written creator consent is held for manual review to protect your channel from copyright violations.
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
              <div className="flex items-center justify-between bg-[#121826] p-3 rounded-xl border border-gaming-border">
                <div>
                  <span className="font-semibold text-white block">Auto-Approve Produced Clips</span>
                  <span className="text-[11px] text-slate-400">
                    Bypasses manual review if AI quality validation passes.
                  </span>
                </div>
                <input
                  type="checkbox"
                  checked={schedule?.auto_approve_clips || false}
                  onChange={(e) => handleUpdateScheduleRules({ auto_approve_clips: e.target.checked })}
                  className="rounded bg-slate-800 border-slate-700 text-purple-600 focus:ring-0 w-4 h-4 cursor-pointer"
                />
              </div>

              <div className="flex items-center justify-between bg-[#121826] p-3 rounded-xl border border-gaming-border">
                <div>
                  <span className="font-semibold text-white block">Automatic Publishing Dispatch</span>
                  <span className="text-[11px] text-slate-400">
                    Publishes authorized reels immediately. (Disabled by default).
                  </span>
                </div>
                <input
                  type="checkbox"
                  checked={schedule?.auto_publish || false}
                  onChange={(e) => handleUpdateScheduleRules({ auto_publish: e.target.checked })}
                  className="rounded bg-slate-800 border-slate-700 text-purple-600 focus:ring-0 w-4 h-4 cursor-pointer"
                />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* =========================================================================
          MODAL: SOURCE AUTHORIZATION & FILE ACQUISITION
      ========================================================================= */}
      {authorizingVideo && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#0d111c] border border-gaming-border rounded-2xl max-w-xl w-full p-6 space-y-4 shadow-2xl animate-in zoom-in-95">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <ShieldCheck className="w-5 h-5 text-emerald-400" />
                  <span>Source Authorization & Acquisition</span>
                </h3>
                <p className="text-xs text-slate-400 mt-0.5 line-clamp-1">
                  Video: {authorizingVideo.title}
                </p>
              </div>
              <button
                onClick={() => setAuthorizingVideo(null)}
                className="text-slate-400 hover:text-white"
              >
                &times;
              </button>
            </div>

            {/* Explanatory Policy Alert */}
            <div className="bg-purple-950/30 border border-purple-500/30 rounded-xl p-3 text-xs text-purple-200">
              <p className="font-semibold">Acquisition & Copyright Compliance</p>
              <p className="mt-0.5 opacity-90">
                YouTube search results do not grant permission to republish video files. Please attach a creator-provided video file or document explicit creator consent.
              </p>
            </div>

            {/* Inputs */}
            <div className="space-y-3 text-xs">
              <div>
                <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                  Local / Cloud Source Video File Path (Optional)
                </label>
                <input
                  type="text"
                  placeholder="e.g. D:\Footage\authorized_gameplay.mp4"
                  value={localFilePath}
                  onChange={(e) => setLocalFilePath(e.target.value)}
                  className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-purple-500"
                />
                <span className="text-[10px] text-slate-500 mt-0.5 block">
                  Leave empty if acquiring via creator-authorized YouTube stream.
                </span>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                    Authorization Type
                  </label>
                  <select
                    value={authType}
                    onChange={(e) => setAuthType(e.target.value)}
                    className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-slate-200 focus:outline-none"
                  >
                    <option value="creator_authorized">Creator Authorized</option>
                    <option value="user_owned">User Owned Footage</option>
                    <option value="licensed">Licensed Commercial</option>
                    <option value="creative_commons">Creative Commons (CC-BY)</option>
                  </select>
                </div>

                <div>
                  <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                    Authorized By (Creator / Contact)
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. Shroud / Ninja / Team Owner"
                    value={authorizedBy}
                    onChange={(e) => setAuthorizedBy(e.target.value)}
                    className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-slate-200 focus:outline-none"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                    Permitted Scope
                  </label>
                  <select
                    value={permittedScope}
                    onChange={(e) => setPermittedScope(e.target.value)}
                    className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-slate-200 focus:outline-none"
                  >
                    <option value="shorts_clips_derivative">Shorts & Reels Derivative Works</option>
                    <option value="full_commercial">Full Commercial Production</option>
                    <option value="editorial">Editorial Highlights</option>
                  </select>
                </div>

                <div>
                  <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                    Evidence URL / Agreement Link
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. Email consent URL, contract link"
                    value={evidenceUrl}
                    onChange={(e) => setEvidenceUrl(e.target.value)}
                    className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-slate-200 focus:outline-none"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2 pt-2">
                <input
                  type="checkbox"
                  id="enqueue_toggle"
                  checked={autoEnqueueNow}
                  onChange={(e) => setAutoEnqueueNow(e.target.checked)}
                  className="rounded bg-slate-800 border-slate-700 text-purple-600 focus:ring-0 w-4 h-4 cursor-pointer"
                />
                <label htmlFor="enqueue_toggle" className="text-slate-300 font-medium cursor-pointer">
                  Queue for AI Video Clipping immediately upon saving authorization
                </label>
              </div>
            </div>

            {/* Modal Actions */}
            <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-slate-800">
              <button
                onClick={() => setAuthorizingVideo(null)}
                className="bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs px-4 py-2 rounded-xl transition"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmAuthorization}
                disabled={isSubmittingAuth}
                className="bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-xs font-semibold px-4 py-2 rounded-xl flex items-center gap-1.5 transition shadow"
              >
                <FileCheck className={`w-3.5 h-3.5 ${isSubmittingAuth ? 'animate-spin' : ''}`} />
                <span>{isSubmittingAuth ? 'Saving...' : 'Confirm Authorization'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: ADD MONITORED GAME */}
      {showAddSourceModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#0d111c] border border-gaming-border rounded-2xl max-w-md w-full p-6 space-y-4 shadow-2xl animate-in zoom-in-95">
            <h3 className="text-sm font-bold text-white">Add Monitored Gaming Source</h3>
            <div className="space-y-3 text-xs">
              <div>
                <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                  Game Title *
                </label>
                <input
                  type="text"
                  placeholder="e.g. Valorant, Elden Ring, Forza Horizon 5"
                  value={newSourceGame}
                  onChange={(e) => setNewSourceGame(e.target.value)}
                  className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-slate-200 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                  Display Preset Name
                </label>
                <input
                  type="text"
                  placeholder="e.g. Valorant Ranked Highlights"
                  value={newSourceName}
                  onChange={(e) => setNewSourceName(e.target.value)}
                  className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-slate-200 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                  Keywords (comma separated)
                </label>
                <input
                  type="text"
                  placeholder="gameplay, clutch, highlights, 4k"
                  value={newSourceKeywords}
                  onChange={(e) => setNewSourceKeywords(e.target.value)}
                  className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-slate-200 focus:outline-none"
                />
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
              <button
                onClick={() => setShowAddSourceModal(false)}
                className="bg-slate-800 text-slate-300 text-xs px-3 py-1.5 rounded-xl"
              >
                Cancel
              </button>
              <button
                onClick={handleAddSource}
                className="bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold px-4 py-1.5 rounded-xl shadow"
              >
                Save Source
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: ADD APPROVED CHANNEL */}
      {showAddChannelModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#0d111c] border border-gaming-border rounded-2xl max-w-md w-full p-6 space-y-4 shadow-2xl animate-in zoom-in-95">
            <h3 className="text-sm font-bold text-white">Add Approved YouTube Channel</h3>
            <div className="space-y-3 text-xs">
              <div>
                <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                  Channel ID (UC...) *
                </label>
                <input
                  type="text"
                  placeholder="e.g. UCxxxxxxxxxxxxxxxxxx"
                  value={newChannelId}
                  onChange={(e) => setNewChannelId(e.target.value)}
                  className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-slate-200 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                  Channel Name *
                </label>
                <input
                  type="text"
                  placeholder="e.g. Official Creator Channel"
                  value={newChannelTitle}
                  onChange={(e) => setNewChannelTitle(e.target.value)}
                  className="w-full bg-[#121826] border border-gaming-border rounded-xl px-3 py-2 text-slate-200 focus:outline-none"
                />
              </div>
              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="auto_auth_toggle"
                  checked={newChannelAutoAuth}
                  onChange={(e) => setNewChannelAutoAuth(e.target.checked)}
                  className="rounded bg-slate-800 border-slate-700 text-purple-600 focus:ring-0 w-4 h-4 cursor-pointer"
                />
                <label htmlFor="auto_auth_toggle" className="text-slate-300 cursor-pointer">
                  Auto-authorize footage discovered from this channel
                </label>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
              <button
                onClick={() => setShowAddChannelModal(false)}
                className="bg-slate-800 text-slate-300 text-xs px-3 py-1.5 rounded-xl"
              >
                Cancel
              </button>
              <button
                onClick={handleAddChannel}
                className="bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold px-4 py-1.5 rounded-xl shadow"
              >
                Add Channel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
